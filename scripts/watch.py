#!/usr/bin/env python3
"""
Surveillance des Toyota/Lexus hybrides d'occasion, cinq pays.

Deux plateformes :
  - Toyota Europe (usc-webcomponents.toyota-europe.com) : FR, BE, DE, ES.
    Meme API, un code distributeur et un format d'URL de fiche par pays.
  - Louwman (occasions.toyota.nl) : NL. API .NET distincte, entierement
    differente, avec un vrai tri par date.

Aucune des deux ne sert de mecanisme fiable de "nouveaute" : une annonce est
nouvelle uniquement si sa clef "PAYS:id" est absente de data/state.json.
L'etat versionne dans git est donc le mecanisme de detection.

Sorties :
  data/state.json  etat, historique de prix, donnees d'affichage
  data/cars.csv    export courant trie par prix
  docs/cars.json   donnees de la page, rechargees sans cache
  docs/index.html  interface mobile (GitHub Pages)
  notifications ntfy pour les nouveautes et les baisses de prix
"""

import json, os, csv, sys, time, html, re, unicodedata, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "data", "state.json")
CSV_PATH   = os.path.join(ROOT, "data", "cars.csv")
HTML_PATH  = os.path.join(ROOT, "docs", "index.html")
JSON_PATH  = os.path.join(ROOT, "docs", "cars.json")

# Liste francaise d'origine, alignee sur tous les pays Toyota Europe.
# Yaris (YA) volontairement absent ; YB = Yaris Cross.
TME_MODELS = ["AU", "xTY_AUTS", "CM", "CO", "CR", "xTY_CTS", "CTS", "CT",
              "IS", "LB", "NX", "RA", "RE", "RX", "CH", "CB", "UX", "YB"]

# Meme liste, dans le vocabulaire de l'API Louwman (facette Model, en clair).
# Pas de Lexus sur occasions.toyota.nl (site separe).
NL_MODELS = ["auris", "camry", "corolla", "corolla cross", "c-hr", "rav4",
             "yaris cross"]

SOURCES = {
    "FR": {"kind": "tme", "flag": "\U0001F1EB\U0001F1F7", "cc": "fr", "lang": "fr",
           "dist": "94102", "brands": ["38", "22"], "extra": [],
           "detail": "https://www.toyota.fr/occasions/voiture/{id}"},
    "BE": {"kind": "tme", "flag": "\U0001F1E7\U0001F1EA", "cc": "be", "lang": "fr",
           "dist": "94031", "brands": ["38", "22"], "extra": [],
           "detail": "https://fr.toyota.be/occasions/pdp", "slug": True},
    "DE": {"kind": "tme", "flag": "\U0001F1E9\U0001F1EA", "cc": "de", "lang": "de",
           "dist": "94272", "brands": ["38", "22"],
           # Filtres propres a l'Allemagne, tels que configures sur le site :
           # 2016-2025 et vehicules certifies Toyota ("warranty any" est la
           # case "Afficher uniquement les vehicules d'occasion certifies").
           "extra": [{"filterId": "usedCarYear", "min": 2016, "max": 2025},
                     {"filterId": "usedCarWarranty", "valueIds": ["any"]}],
           "detail": "https://www.toyota.de/gebrauchtwagen/pdp", "slug": True},
    "ES": {"kind": "tme", "flag": "\U0001F1EA\U0001F1F8", "cc": "es", "lang": "es",
           "dist": "94244", "brands": ["38", "22"], "extra": [],
           "detail": "https://www.toyota.es/coches-segunda-mano/ficha/{id}"},
    "NL": {"kind": "louwman", "flag": "\U0001F1F3\U0001F1F1",
           "detail": "https://occasions.toyota.nl/auto/{id}"},
}

NTFY_SERVER  = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC   = os.environ.get("NTFY_TOPIC", "")
ALERT_PRICE  = int(os.environ.get("ALERT_PRICE", "20000"))
DROP_MIN     = int(os.environ.get("DROP_MIN", "500"))   # baisse mini pour notifier
MAX_LOUD     = int(os.environ.get("MAX_LOUD", "10"))    # anti-spam par run
QUICK_PAGES  = int(os.environ.get("QUICK_PAGES", "10")) # 1000 moins cheres / pays
REGIONS      = [r.strip().lower() for r in os.environ.get("ALERT_REGIONS", "").split(",") if r.strip()]
# Pays surveilles ; vide = tous. Ex. "FR,BE" pour se limiter.
COUNTRIES    = [c.strip().upper() for c in os.environ.get("COUNTRIES", "").split(",") if c.strip()] or list(SOURCES)

now = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- API

def _post(url, body, label):
    data = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Accept": "application/json",
                         "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            print(f"  retry {label} ({e})", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"echec definitif : {label}")


def _sweep_tme(src, sort_order, max_pages=None):
    """Un balayage pagine de l'API Toyota Europe, pour un pays."""
    url = (f"https://usc-webcomponents.toyota-europe.com/v1/api/usedcars/"
           f"results/{src['cc']}/{src['lang']}?brand=toyota")
    filters = [{"filterId": "usedCarBrand", "valueIds": src["brands"]},
               {"filterId": "usedCarModel", "valueIds": TME_MODELS},
               {"filterId": "usedCarFuelType", "valueIds": ["5"]}] + src["extra"]
    cars, offset, total, seen, pages = [], 0, None, set(), 0
    while True:
        body = {"uscEnv": "production", "filters": filters, "filterContext": "used",
                "offset": offset, "resultCount": 100, "sortOrder": sort_order,
                "distributorCode": src["dist"], "enableExperimentalTotalCountQuery": True}
        data = _post(url, body, f"{src['cc']} {sort_order} offset={offset}")
        if total is None:
            total = data.get("totalResultCount") or 0
        batch = data.get("results") or []
        for v in batch:
            if v["id"] not in seen:
                seen.add(v["id"])
                cars.append(v)
        pages += 1
        if not batch or len(seen) >= total:
            break
        if max_pages and pages >= max_pages:
            break
        offset += 100
        time.sleep(0.3)
    print(f"    {sort_order:<9} -> {len(cars):>5} ids ({pages} page(s))")
    return cars, total


def _sweep_nl(src, sort_order, max_pages=None):
    """Un balayage pagine de l'API Louwman (occasions.toyota.nl).

    POST /api/search?source=toyota avec {filter:[{name,values}], limits:{start,
    limit}, sort}. Le serveur dedoublonne apres decoupage : une page de 100
    en rend ~80, et "count" est gonfle d'autant. On avance donc de "limit"
    a chaque page, jamais du nombre recu, et on s'arrete sur page vide.
    """
    url = "https://occasions.toyota.nl/api/search?source=toyota"
    filters = [{"name": "FuelType", "values": ["hybride"]},
               {"name": "Brand", "values": ["toyota"]},
               {"name": "Model", "values": NL_MODELS}]
    cars, start, total, seen, pages = [], 0, None, set(), 0
    while True:
        body = {"filter": filters, "limits": {"start": start, "limit": 100},
                "sort": sort_order}
        data = _post(url, body, f"nl {sort_order} start={start}")
        if total is None:
            total = data.get("count") or 0
        batch = data.get("occasions") or []
        for v in batch:
            if v["id"] not in seen:
                seen.add(v["id"])
                cars.append(v)
        pages += 1
        if not batch or start + 100 >= total:
            break
        if max_pages and pages >= max_pages:
            break
        start += 100
        time.sleep(0.3)
    print(f"    {sort_order:<9} -> {len(cars):>5} ids ({pages} page(s))")
    return cars, total


SORTS = {"tme":     {"asc": "cashAsc",  "desc": "cashDesc"},
         "louwman": {"asc": "PriceAsc", "desc": "PriceDesc"}}


def fetch(code, max_pages=None, both=False):
    """Recupere le catalogue d'un pays, en unissant deux ordres de tri.

    Ni Toyota Europe ni Louwman n'ont de clef de departage unique : les
    ex aequo de prix se reordonnent entre deux requetes et quelques vehicules
    tombent entre deux pages. Balayer aussi en ordre inverse les place
    ailleurs : mesure faite, l'union des deux converge (FR 2747/2747,
    NL 3803 -- un troisieme tri n'ajoute plus rien).

    Il n'existe aucun tri par date exploitable cote Toyota Europe :
    sortOrder=published trie d'abord par nombre de photos, et createdAt
    n'est jamais renvoye. D'ou la detection par diff d'etat.
    """
    src = SOURCES[code]
    sweep = _sweep_tme if src["kind"] == "tme" else _sweep_nl
    names = SORTS[src["kind"]]
    orders = [names["asc"], names["desc"]] if both else [names["asc"]]
    print(f"  {src['flag']} {code}")
    merged, total = {}, None
    for so in orders:
        cars, total = sweep(src, so, max_pages)
        for c in cars:
            merged.setdefault(c["id"], c)
        if total and len(merged) >= total:
            break
    if max_pages:
        print(f"    total API : {total} | balayage partiel : {len(merged)}")
    else:
        manque = (total or 0) - len(merged)
        print(f"    total API : {total} | recupere : {len(merged)}"
              + (f" | manque {manque}" if manque > 0 else " | complet"))
    return list(merged.values()), total


def _num(x):
    """Les prix arrivent parfois en decimal (ex. 15992.17)."""
    if x is None:
        return None
    f = float(x)
    return int(f) if f.is_integer() else f


def _slug_part(e):
    """Transcription fidele de getUscUrl : NFD, retrait de "N/A", des accents
    et des parentheses ; espaces, virgules, slash -> "-" ; "+" -> "plus" ;
    points retires."""
    e = unicodedata.normalize("NFD", e).replace("N/A", "")
    e = "".join(ch for ch in e if not (0x300 <= ord(ch) <= 0x36F))
    e = re.sub(r"[()]", "", e)
    e = re.sub(r"[\s,/]+", "-", e)
    return e.replace("+", "plus").replace(".", "")


def detail_url_tme(v, code):
    """URL de fiche.

    FR et ES acceptent /{id} et redirigent cote serveur vers le slug
    canonique : fiable. BE et DE n'ont pas cette redirection, et pdp.{id}
    y est instable (la meme URL rend 404 puis 200 a une minute d'ecart) :
    on reconstruit le slug exactement comme le composant du site, a partir
    de marque, modele, annee de 1re immatriculation, carrosserie, boite,
    carburant marketing et id. Verifie 6/6 en Allemagne.
    """
    src = SOURCES[code]
    if not src.get("slug"):
        return src["detail"].format(id=v["id"])
    p = v.get("product") or {}
    manu  = (p.get("brand") or {}).get("description") or ""
    model = (p.get("model") or {}).get("description") or ""
    reg   = (v.get("history") or {}).get("registrationDate") or ""
    parts = ["" if model.lower().startswith(manu.lower()) else manu,
             model, reg[:4], p.get("bodyType") or "",
             ((p.get("transmission") or {}).get("transmissionType") or {}).get("description") or "",
             ((p.get("engine") or {}).get("marketingFuelType") or {}).get("description") or "",
             v["id"]]
    slug = re.sub(r"-+", "-", "-".join(_slug_part(x) for x in parts if x)).lower()
    return f"{src['detail']}.{urllib.parse.quote(slug, safe='-')}"


def slim_tme(v, code):
    """Ne garde que ce qui sert a l'affichage et aux alertes."""
    p   = v.get("product") or {}
    eng = p.get("engine") or {}
    dlr = v.get("dealer") or {}
    adr = dlr.get("address") or {}
    return {
        "id":    v["id"],
        "cc":    code,
        "model": (p.get("model") or {}).get("description") or v.get("title") or "",
        "vers":  p.get("versionName") or "",
        "year":  p.get("modelYear") or (v.get("history") or {}).get("registrationDate", "")[:4],
        "km":    (v.get("mileage") or {}).get("value"),
        "price": _num((v.get("price") or {}).get("sellingPriceInclVAT")),
        "fuel":  eng.get("displayFuelType") or "",
        "gear":  (p.get("transmission") or {}).get("name") or "",
        "body":  p.get("bodyType") or "",
        "color": v.get("exteriorColour") or "",
        "deal":  dlr.get("name") or "",
        "city":  adr.get("city") or "",
        "zip":   adr.get("zip") or "",
        "reg":   adr.get("region") or "",
        "phone": dlr.get("primaryPhone") or dlr.get("phone") or "",
        "url":   detail_url_tme(v, code),
    }


def slim_nl(v):
    dlr = v.get("dealer") or {}
    return {
        "id":    str(v["id"]),
        "cc":    "NL",
        "model": f"{v.get('brand') or ''} {v.get('model') or ''}".strip(),
        "vers":  v.get("type") or "",
        "year":  str(v.get("year") or ""),
        "km":    v.get("mileage"),
        "price": _num(v.get("price")),
        "fuel":  v.get("fuelType") or "",
        "gear":  v.get("transmission") or "",
        "body":  v.get("body") or "",
        "color": v.get("color") or "",
        "deal":  dlr.get("name") or "",
        "city":  dlr.get("city") or "",
        "zip":   dlr.get("postalCode") or "",
        "reg":   "",
        "phone": dlr.get("phone") or "",
        "url":   SOURCES["NL"]["detail"].format(id=v["id"]),
    }


def slim(v, code):
    return slim_nl(v) if SOURCES[code]["kind"] == "louwman" else slim_tme(v, code)


def key_of(c):
    """Clef d'etat : les ids ne sont uniques qu'au sein d'une plateforme."""
    return f"{c['cc']}:{c['id']}"


# ---------------------------------------------------------------- etat

def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as f:
        state = json.load(f)
    # Migration v1 -> v2 : les clefs etaient des UUID francais nus.
    if state.get("version", 1) < 2:
        state["cars"] = {("FR:" + k if ":" not in k else k): v
                         for k, v in state["cars"].items()}
        for rec in state["cars"].values():
            d = rec.get("d")
            if d is not None:
                d.setdefault("cc", "FR")
        state["version"] = 2
        print("etat migre en v2 (clefs prefixees par le pays)")
    return state


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------- ntfy

def push(title, message, url=None, priority=3, tags=None):
    if not NTFY_TOPIC:
        print(f"[ntfy absent] {title} | {message}")
        return
    payload = {"topic": NTFY_TOPIC, "title": title, "message": message,
               "priority": priority}
    if url:
        payload["click"] = url
    if tags:
        payload["tags"] = tags
    try:
        req = urllib.request.Request(
            NTFY_SERVER, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"  echec ntfy : {e}", file=sys.stderr)


def eur(n):
    return f"{round(n):,}".replace(",", " ") + " EUR" if n is not None else "?"


def km_fmt(n):
    return f"{round(n):,}".replace(",", " ") + " km" if n is not None else "? km"


def flag(c):
    return SOURCES.get(c.get("cc", "FR"), {}).get("flag", "")


def is_loud(car):
    if car["price"] is None or car["price"] >= ALERT_PRICE:
        return False
    if REGIONS and not any(r in (car["reg"] or "").lower() for r in REGIONS):
        return False
    return True


def notify_new(cars):
    """Uniquement les vehicules sous le seuil. Au-dessus : rien, pas meme une
    notification groupee -- sur iOS ntfy l'affiche quand meme, sans son, et
    c'etait percu comme une alerte de trop."""
    loud = sorted((c for c in cars if is_loud(c)), key=lambda x: x["price"])
    for c in loud[:MAX_LOUD]:
        push(f"{flag(c)} {eur(c['price'])} - {c['model']} {c['year']}",
             f"{c['vers']}\n{km_fmt(c['km'])} - {c['city']} ({c['zip']})\n{c['deal']}",
             url=c["url"], priority=5, tags=["rotating_light", "car"])
    if len(loud) > MAX_LOUD:
        push(f"+{len(loud) - MAX_LOUD} autres sous {eur(ALERT_PRICE)}",
             "Voir la liste complete", url=pages_url(), priority=4, tags=["car"])
    if len(cars) - len(loud):
        print(f"  {len(cars) - len(loud)} nouveaute(s) au-dessus du seuil : non notifiees")


def notify_drops(drops):
    """Une baisse ne vaut alerte que si le nouveau prix est sous le seuil --
    y compris quand c'est la baisse qui l'y fait entrer."""
    hot = [(c, old) for c, old in drops if is_loud(c)]
    for c, old in sorted(hot, key=lambda x: x[0]["price"])[:MAX_LOUD]:
        push(f"{flag(c)} -{eur(old - c['price'])} : {c['model']} {c['year']} a {eur(c['price'])}",
             f"Ancien prix {eur(old)}\n{km_fmt(c['km'])} - {c['city']}\n{c['deal']}",
             url=c["url"], priority=5, tags=["chart_with_downwards_trend", "car"])
    if len(drops) - len(hot):
        print(f"  {len(drops) - len(hot)} baisse(s) au-dessus du seuil : non notifiees")


def pages_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "https://www.toyota.fr/occasions/recherche"


# ---------------------------------------------------------------- sorties

def active_cars(state):
    """Tous les vehicules encore en vente, d'apres l'etat accumule.

    Le mode quick ne voit que les 500 moins cheres : generer la page depuis
    le seul balayage courant la reduirait a 500 lignes. On part donc de
    l'etat, en rafraichissant au passage ce que le balayage vient de revoir.
    """
    out = []
    for cid, rec in state["cars"].items():
        if rec.get("gone_since") or not rec.get("d"):
            continue
        d = dict(rec["d"])
        # "Toyota C-HR" cote Toyota Europe, "Toyota Auris" cote Louwman,
        # "Corolla" nu ailleurs : sans normalisation le filtre modele compte
        # la meme voiture sous deux libelles.
        d["model"] = (d.get("model") or "").removeprefix("Toyota ").strip()
        out.append({**d, "id": cid.split(":", 1)[-1],
                    "seeded": bool(rec.get("seeded")),
                    "first": (rec.get("first_seen") or "")[:10],
                    "min": rec.get("min_price"),
                    "prev": rec.get("prev_price")})
    return sorted(out, key=lambda c: (c["price"] is None, c["price"] or 0))


def write_csv(cars):
    cols = ["cc", "price", "model", "vers", "year", "km", "fuel", "gear", "body",
            "color", "deal", "city", "zip", "reg", "url", "id"]
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in sorted(cars, key=lambda c: (c["price"] is None, c["price"] or 0)):
            w.writerow(c)


def write_html(payload, state):
    # Donnees dans un fichier a part : GitHub Pages sert le HTML avec
    # cache-control max-age=600, et une app ajoutee a l'ecran d'accueil le
    # garde plus longtemps encore. La page recharge cars.json avec un
    # parametre anti-cache, donc elle ne peut pas afficher de stock perime.
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated": state["last_run"],
                   "alert": ALERT_PRICE,
                   "countries": {k: v["flag"] for k, v in SOURCES.items() if k in COUNTRIES},
                   "cars": payload}, f, ensure_ascii=False, separators=(",", ":"))

    tpl = open(os.path.join(ROOT, "scripts", "template.html"), encoding="utf-8").read()
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(tpl)


# ---------------------------------------------------------------- main

def render_only():
    """Regenere docs/index.html depuis les donnees locales, sans appel API.
    Sert a iterer sur scripts/template.html."""
    state = load_state()
    if state is None:
        sys.exit("Aucun etat : lancer d'abord un balayage complet.")
    page = active_cars(state)
    write_html(page, state)
    print(f"page regeneree depuis {len(page)} vehicules locaux")


def main():
    if "--render" in sys.argv:
        return render_only()
    # --seed : absorbe les inconnues sans notifier. A utiliser apres avoir
    # elargi la liste des modeles, sinon tout le nouveau parc part en alerte.
    seed_new = "--seed" in sys.argv
    mode = "quick" if "--quick" in sys.argv else "full"

    state = load_state()
    seeding = state is None
    if seeding:
        mode = "full"
        state = {"version": 2, "last_run": None, "cars": {}}
        print("PREMIER RUN : amorcage silencieux, aucune notification.")
    print(f"mode : {mode} | pays : {', '.join(COUNTRIES)}")

    # Un pays en echec ne doit ni faire tomber le run, ni faire passer ses
    # vehicules pour retires : on note ceux effectivement balayes.
    cars, totals, swept = [], {}, set()
    for code in COUNTRIES:
        try:
            raw, total = fetch(code, None if mode == "full" else QUICK_PAGES,
                               both=(mode == "full"))
        except Exception as e:
            print(f"  {code} : ECHEC, ignore ce passage ({e})", file=sys.stderr)
            continue
        cars += [slim(v, code) for v in raw]
        totals[code] = total
        swept.add(code)
    by_id = {key_of(c): c for c in cars}

    known = state["cars"]
    new_cars, drops = [], []
    ts = now()

    for cid, c in by_id.items():
        rec = known.get(cid)
        if rec is None:
            known[cid] = {"first_seen": ts, "last_seen": ts,
                          "price": c["price"], "min_price": c["price"],
                          "prev_price": None}
            known[cid]["d"] = {k: v for k, v in c.items() if k != "id"}
            if seeding or seed_new:
                # Amorcage : ces vehicules etaient deja en stock, ils ne sont
                # pas "nouveaux". Sans ce marqueur toute la page serait badgee.
                known[cid]["seeded"] = True
            else:
                new_cars.append(c)
        else:
            old = rec.get("price")
            rec["last_seen"] = ts
            # Rafraichi a chaque passage : le mode quick peut ainsi regenerer
            # la page complete sans avoir balaye tout le catalogue.
            # L'API renvoie parfois une fiche amputee (dealer absent, d'ou
            # ville et concession vides) : on conserve alors ce qu'on savait
            # deja plutot que de degrader l'affichage.
            neuf = {k: v for k, v in c.items() if k != "id"}
            ancien = rec.get("d") or {}
            rec["d"] = {k: (v if v not in ("", None) else ancien.get(k, v))
                        for k, v in neuf.items()}
            if c["price"] is not None and old is not None and c["price"] < old - DROP_MIN + 1 and c["price"] < old:
                if not seeding:
                    drops.append((c, old))
                rec["prev_price"] = old
            if c["price"] is not None:
                rec["price"] = c["price"]
                if rec.get("min_price") is None or c["price"] < rec["min_price"]:
                    rec["min_price"] = c["price"]

    # En mode quick, une annonce absente est simplement hors des N premieres
    # pages : seul un balayage complet peut conclure a une disparition.
    gone = []
    if mode == "full":
        # Un vehicule doit manquer a DEUX balayages complets consecutifs avant
        # d'etre declare retire : un seul absent peut n'etre qu'une derive.
        for cid, rec in known.items():
            if cid in by_id:
                rec.pop("gone_since", None)
                rec.pop("missed", None)
                continue
            if cid.split(":", 1)[0] not in swept:
                continue          # pays non balaye ce passage : on ne sait pas
            rec["missed"] = rec.get("missed", 0) + 1
            if rec["missed"] >= 2:
                rec["gone_since"] = rec.get("gone_since") or ts
                gone.append(cid)
        state["stock"] = len(cars)
        state["last_full"] = ts

    state["last_run"] = ts
    print(f"nouvelles : {len(new_cars)} | baisses : {len(drops)} | "
          f"disparues (cumul) : {len(gone)} | vus : {len(cars)} | "
          f"api_total : {sum(totals.values())}")

    if new_cars:
        notify_new(new_cars)
    if drops:
        notify_drops(drops)

    # L'etat est ecrit en dernier : si la generation echoue, le prochain run
    # rejouera le diff au lieu de considerer les nouveautes comme deja vues.
    # Regenere a chaque passage, quick compris : sinon la page resterait
    # figee 6 h alors que les notifications, elles, partent toutes les 15 min.
    page = active_cars(state)
    write_csv(page)
    write_html(page, state)
    print(f"page : {len(page)} vehicules")
    save_state(state)

    if seeding:
        print(f"Amorcage termine sur {len(cars)} vehicules. "
              f"Les prochains runs notifieront les nouveautes.")


if __name__ == "__main__":
    main()
