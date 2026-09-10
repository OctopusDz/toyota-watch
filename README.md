# Surveillance Toyota / Lexus hybrides d'occasion

Surveille le stock d'occasions hybrides de toyota.fr, notifie l'iPhone a chaque
nouvelle annonce ou baisse de prix, et publie une interface mobile sur GitHub Pages.

Objectif : reperer et appeler vite sur les annonces les moins cheres.

## Ce que fait le systeme

| | |
|---|---|
| Toutes les 15 min | balayage des 500 moins cheres (5 requetes) -> alerte rapide |
| Toutes les 6 h | balayage complet (~2 750 vehicules, 28 requetes) -> page web, baisses de prix, annonces disparues |
| Notification | ntfy : un tap ouvre directement l'annonce |
| Interface | `https://<user>.github.io/<repo>/` — tri et filtres, tout le stock |

Alerte sonore (priorite max) sous le seuil de prix ; au-dessus, une seule
notification silencieuse groupee. Tout reste visible sur la page web.

## Pourquoi une comparaison d'etat plutot qu'un tri par date

L'API **n'expose aucune date de mise en ligne**. Verifie :

- `registrationDate` est la 1re immatriculation, `productionDate` est vide.
- `lastEnrichment` est un batch (1 508 vehicules retraites le meme jour) : inexploitable.
- `sortOrder=published` — le tri par defaut du site — trie **par nombre de photos**
  decroissant, `createdAt` n'etant que le departage. Les premiers resultats sont
  les annonces les mieux illustrees, pas les plus recentes.
- `createdAt` existe dans l'index Elasticsearch et est triable, mais **n'est jamais
  renvoye** dans les documents.
- Les seuls `sortOrder` valides sont `published`, `cashAsc`/`Desc`,
  `mileageAsc`/`Desc`, `monthlyAsc`/`Desc`, `yearAsc`/`Desc`. Toute autre valeur
  donne `sort: null`.

Une annonce est donc « nouvelle » uniquement si son id est absent de
`data/state.json`. **L'etat versionne dans git est le mecanisme de detection.**
D'ou l'amorcage silencieux au premier run : sans lui, 2 750 notifications d'un coup.

Consequence : « NOUVEAU » sur la page signifie *premiere apparition dans le suivi*,
pas *date de publication*.

## Installation

### 1. ntfy sur l'iPhone

1. Installer **ntfy** depuis l'App Store.
2. Choisir un nom de topic **long et aleatoire** — c'est la seule protection,
   quiconque le connait recoit (et peut envoyer) les notifications :
   ```bash
   python3 -c "import secrets;print('toyota-'+secrets.token_hex(8))"
   ```
3. Dans l'app : **+** → coller le topic → *Subscribe*.

### 2. Le depot

```bash
gh repo create toyota-watch --public --source=. --remote=origin --push
gh secret set NTFY_TOPIC --body "<le-topic-choisi>"
```

Le topic va dans un **secret**, jamais dans le code.

### 3. Activer Pages

Repo → *Settings* → *Pages* → **Source : GitHub Actions**.

### 4. Premier run

```bash
gh workflow run "Surveillance Toyota occasions" -f mode=full
```

Il amorce l'etat en silence. Les suivants notifient.

## Reglages

Repo → *Settings* → *Secrets and variables* → *Actions* → onglet **Variables** :

| Variable | Defaut | Role |
|---|---|---|
| `ALERT_PRICE` | `20000` | seuil de l'alerte sonore (EUR) |
| `ALERT_REGIONS` | vide | limite l'alerte sonore a certaines regions, ex. `bretagne,pays-de-la-loire` (la page reste nationale) |
| `QUICK_PAGES` | `5` | pages balayees en mode rapide (5 = 500 moins cheres) |
| `NTFY_SERVER` | `https://ntfy.sh` | serveur ntfy auto-heberge le cas echeant |

Reperes sur le stock actuel : sous 18 000 EUR = 4 % du parc, sous 20 000 = 10 %,
sous 25 000 = 33 %. Prix median 28 190 EUR.

Le kilometrage ne merite pas de filtre : mediane 32 650 km, maximum 187 495 km,
**zero** vehicule au-dela de 200 000 km. L'API ne sait de toute facon pas filtrer
par plage (filtres `term` uniquement, pas d'agregation `mileage`). Le tri et le
filtre km sont donc faits sur la page.

## En local

```bash
python3 scripts/watch.py           # balayage complet
python3 scripts/watch.py --quick   # 500 moins cheres
NTFY_TOPIC=xxx ALERT_PRICE=22000 python3 scripts/watch.py
```

Sans `NTFY_TOPIC`, les notifications sont affichees dans le terminal au lieu
d'etre envoyees.

## A savoir

- **Le cron GitHub n'est pas ponctuel** : 5 a 20 min de retard, runs sautes en
  periode de charge. Detection reelle sous ~30 min.
- **GitHub desactive les workflows planifies apres 60 jours sans activite humaine**
  sur le depot ; les commits du bot ne comptent pas. Un push manuel de temps en
  temps, ou un commit depuis l'interface, suffit a relancer le compteur.
- **Doublons de pagination** : les ex aequo de prix se reordonnent entre deux
  appels et reapparaissent aux frontieres de page. Dedoublonnage par id — ce qui
  implique que quelques vehicules peuvent etre manques a un balayage donne, sans
  tri a departage stable cote API. Le balayage suivant les rattrape.

## Fichiers

```
scripts/watch.py       recuperation, diff, notifications, generation
scripts/template.html  gabarit de l'interface
data/state.json        etat + historique de prix (commite par la CI)
data/cars.csv          export courant trie par prix
docs/index.html        interface publiee
```
