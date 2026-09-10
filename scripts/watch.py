#!/usr/bin/env python3
"""
Surveillance des Toyota/Lexus hybrides d'occasion (toyota.fr).

L'API ne fournit aucune date de mise en ligne : une annonce est "nouvelle"
uniquement si son id est absent de data/state.json. L'etat versionne dans git
est donc le mecanisme de detection, pas un simple cache.

Sorties :
  data/state.json  etat + historique de prix (commite par la CI)
  data/cars.csv    export courant trie par prix
  docs/index.html  interface mobile (GitHub Pages)
  notifications ntfy pour les nouveautes et les baisses de prix
"""

import json, os, csv, sys, time, html, urllib.request, urllib.error
from datetime import datetime, timezone

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "data", "state.json")
CSV_PATH   = os.path.join(ROOT, "data", "cars.csv")
HTML_PATH  = os.path.join(ROOT, "docs", "index.html")

API = "https://usc-webcomponents.toyota-europe.com/v1/api/usedcars/results/fr/fr?brand=toyota"
QUERY = {
    "uscEnv": "production",
    "filters": [
        {"filterId": "usedCarBrand", "valueIds": ["38", "22"]},
        {"filterId": "usedCarModel", "valueIds": [
            "AU", "xTY_AUTS", "CM", "CO", "CR", "xTY_CTS", "CTS", "CT",
            "IS", "LB", "NX", "RA", "RE", "RX", "CH", "CB", "UX"]},
        {"filterId": "usedCarFuelType", "valueIds": ["5"]},
    ],
    "filterContext": "used",
    "offset": 0,
    "resultCount": 100,
    "sortOrder": "cashAsc",
    "distributorCode": "94102",
    "enableExperimentalTotalCountQuery": True,
}

DETAIL_URL   = "https://www.toyota.fr/occasions/voiture/{}"
NTFY_SERVER  = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC   = os.environ.get("NTFY_TOPIC", "")
ALERT_PRICE  = int(os.environ.get("ALERT_PRICE", "20000"))
DROP_MIN     = int(os.environ.get("DROP_MIN", "500"))   # baisse mini pour notifier
MAX_LOUD     = int(os.environ.get("MAX_LOUD", "10"))    # anti-spam par run
QUICK_PAGES  = int(os.environ.get("QUICK_PAGES", "5"))  # 500 moins cheres
REGIONS      = [r.strip().lower() for r in os.environ.get("ALERT_REGIONS", "").split(",") if r.strip()]

now = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- API

def fetch(max_pages=None):
    """Pagine (au plus max_pages pages) en tri cashAsc.

    Dedoublonne : les ex aequo de prix se reordonnent entre deux appels et
    reapparaissent aux frontieres de page.

    Il n'existe aucun tri par date exploitable. sortOrder=published trie
    d'abord par nombre de photos, createdAt n'est que departage, et ce champ
    n'est jamais renvoye dans la reponse. D'ou la detection par diff d'etat.
    En mode quick on se limite aux N premieres pages : en cashAsc elles
    couvrent toute la zone sous le seuil d'alerte."""
    cars, offset, total, seen, pages = [], 0, None, set(), 0
    while True:
        body = dict(QUERY, offset=offset)
        data = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    API, data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json",
                             "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.load(r)
                break
            except Exception as e:
                print(f"  retry offset={offset} ({e})", file=sys.stderr)
                time.sleep(2 ** attempt)
        if data is None:
            raise RuntimeError(f"echec definitif a offset={offset}")

        if total is None:
            total = data.get("totalResultCount") or 0
            print(f"total annonce par l'API : {total}")
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
    print(f"recupere : {len(cars)} vehicules uniques ({pages} page(s))")
    return cars, total


def slim(v):
    """Ne garde que ce qui sert a l'affichage et aux alertes."""
    p   = v.get("product") or {}
    eng = p.get("engine") or {}
    dlr = v.get("dealer") or {}
    adr = dlr.get("address") or {}
    return {
        "id":    v["id"],
        "model": (p.get("model") or {}).get("description") or v.get("title") or "",
        "vers":  p.get("versionName") or "",
        "year":  p.get("modelYear") or (v.get("history") or {}).get("registrationDate", "")[:4],
        "km":    (v.get("mileage") or {}).get("value"),
        "price": (v.get("price") or {}).get("sellingPriceInclVAT"),
        "fuel":  eng.get("displayFuelType") or "",
        "gear":  (p.get("transmission") or {}).get("name") or "",
        "body":  p.get("bodyType") or "",
        "color": v.get("exteriorColour") or "",
        "deal":  dlr.get("name") or "",
        "city":  adr.get("city") or "",
        "zip":   adr.get("zip") or "",
        "reg":   adr.get("region") or "",
        "phone": dlr.get("primaryPhone") or dlr.get("phone") or "",
        "url":   DETAIL_URL.format(v["id"]),
    }


# ---------------------------------------------------------------- etat

def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


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
    return f"{n:,}".replace(",", " ") + " EUR" if n is not None else "?"


def km_fmt(n):
    return f"{n:,}".replace(",", " ") + " km" if n is not None else "? km"


def is_loud(car):
    if car["price"] is None or car["price"] >= ALERT_PRICE:
        return False
    if REGIONS and not any(r in (car["reg"] or "").lower() for r in REGIONS):
        return False
    return True


def notify_new(cars):
    loud = [c for c in cars if is_loud(c)]
    quiet = [c for c in cars if not is_loud(c)]

    for c in sorted(loud, key=lambda x: x["price"])[:MAX_LOUD]:
        push(f"{eur(c['price'])} - {c['model']} {c['year']}",
             f"{c['vers']}\n{km_fmt(c['km'])} - {c['city']} ({c['zip']})\n{c['deal']}",
             url=c["url"], priority=5, tags=["rotating_light", "car"])
    if len(loud) > MAX_LOUD:
        push(f"+{len(loud) - MAX_LOUD} autres sous {eur(ALERT_PRICE)}",
             "Voir la liste complete", url=pages_url(), priority=4, tags=["car"])

    if quiet:
        cheapest = min(quiet, key=lambda c: c["price"] if c["price"] is not None else 10**9)
        push(f"{len(quiet)} nouvelle(s) annonce(s)",
             f"La moins chere : {eur(cheapest['price'])} - {cheapest['model']} "
             f"{cheapest['year']}, {km_fmt(cheapest['km'])}, {cheapest['city']}",
             url=pages_url(), priority=1, tags=["car"])


def notify_drops(drops):
    for c, old in sorted(drops, key=lambda x: x[0]["price"])[:MAX_LOUD]:
        delta = old - c["price"]
        crossed = old >= ALERT_PRICE > c["price"]
        push(f"-{eur(delta)} : {c['model']} {c['year']} a {eur(c['price'])}",
             f"Ancien prix {eur(old)}\n{km_fmt(c['km'])} - {c['city']}\n{c['deal']}",
             url=c["url"],
             priority=5 if (crossed or is_loud(c)) else 3,
             tags=["chart_with_downwards_trend", "car"])


def pages_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "https://www.toyota.fr/occasions/recherche"


# ---------------------------------------------------------------- sorties

def write_csv(cars):
    cols = ["price", "model", "vers", "year", "km", "fuel", "gear", "body",
            "color", "deal", "city", "zip", "reg", "url"]
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in sorted(cars, key=lambda c: (c["price"] is None, c["price"] or 0)):
            w.writerow(c)


def write_html(cars, state):
    cars = sorted(cars, key=lambda c: (c["price"] is None, c["price"] or 0))
    meta = state["cars"]
    payload = []
    for c in cars:
        m = meta.get(c["id"], {})
        payload.append({**c,
                        "seeded": bool(m.get("seeded")),
                        "first": (m.get("first_seen") or "")[:10],
                        "min": m.get("min_price"),
                        "prev": m.get("prev_price")})
    tpl = open(os.path.join(ROOT, "scripts", "template.html"), encoding="utf-8").read()
    out = (tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
              .replace("__UPDATED__", html.escape(state["last_run"]))
              .replace("__ALERT__", str(ALERT_PRICE)))
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(out)


# ---------------------------------------------------------------- main

def main():
    mode = "quick" if "--quick" in sys.argv else "full"
    raw, total = fetch(QUICK_PAGES if mode == "quick" else None)
    cars = [slim(v) for v in raw]
    by_id = {c["id"]: c for c in cars}

    state = load_state()
    seeding = state is None
    if seeding and mode == "quick":
        print("Aucun etat : le mode quick exige un amorcage complet d'abord.")
        raw, total = fetch(None)
        cars = [slim(v) for v in raw]
        by_id = {c["id"]: c for c in cars}
        mode = "full"
    print(f"mode : {mode}")
    if seeding:
        state = {"version": 1, "last_run": None, "cars": {}}
        print("PREMIER RUN : amorcage silencieux, aucune notification.")

    known = state["cars"]
    new_cars, drops = [], []
    ts = now()

    for cid, c in by_id.items():
        rec = known.get(cid)
        if rec is None:
            known[cid] = {"first_seen": ts, "last_seen": ts,
                          "price": c["price"], "min_price": c["price"],
                          "prev_price": None}
            if seeding:
                # Amorcage : ces vehicules etaient deja en stock, ils ne sont
                # pas "nouveaux". Sans ce marqueur toute la page serait badgee.
                known[cid]["seeded"] = True
            if not seeding:
                new_cars.append(c)
        else:
            old = rec.get("price")
            rec["last_seen"] = ts
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
        gone = [cid for cid in known if cid not in by_id]
        for cid in gone:
            known[cid]["gone_since"] = known[cid].get("gone_since") or ts
        for cid in by_id:
            known[cid].pop("gone_since", None)
        state["stock"] = len(cars)
        state["last_full"] = ts

    state["last_run"] = ts
    print(f"nouvelles : {len(new_cars)} | baisses : {len(drops)} | "
          f"disparues (cumul) : {len(gone)} | vus : {len(cars)} | api_total : {total}")

    if new_cars:
        notify_new(new_cars)
    if drops:
        notify_drops(drops)

    # L'etat est ecrit en dernier : si la generation echoue, le prochain run
    # rejouera le diff au lieu de considerer les nouveautes comme deja vues.
    if mode == "full":
        write_csv(cars)
        write_html(cars, state)
    save_state(state)

    if seeding:
        print(f"Amorcage termine sur {len(cars)} vehicules. "
              f"Les prochains runs notifieront les nouveautes.")


if __name__ == "__main__":
    main()
