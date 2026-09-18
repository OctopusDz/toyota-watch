# Surveillance Toyota / Lexus hybrides d'occasion -- 5 pays

Surveille le stock d'occasions hybrides Toyota en France, Belgique, Allemagne,
Espagne et Pays-Bas, notifie l'iPhone a chaque nouvelle annonce ou baisse de
prix, et publie une interface mobile sur GitHub Pages avec un filtre par pays.

Objectif : reperer et appeler vite sur les annonces les moins cheres.

## Ce que fait le systeme

| | |
|---|---|
| Toutes les 15 min | les 1 000 moins cheres de chaque pays (~45 requetes) -> alerte rapide, page regeneree |
| Toutes les 6 h | balayage complet des 5 pays, deux tris chacun (~260 requetes) -> baisses de prix, annonces retirees |
| Notification | ntfy : un tap ouvre directement l'annonce |
| Interface | `https://<user>.github.io/<repo>/` — recherche, filtres modele / region / prix max / km max / annee min et max, 5 tris |

Notification (priorite max, son) uniquement sous le seuil de prix -- nouvelle
annonce, ou baisse dont le nouveau prix passe sous le seuil. **Au-dessus :
rien du tout**, pas meme une notification silencieuse (iOS l'affiche quand
meme). Tout reste visible sur la page web.

## Les cinq pays

| | Plateforme | Filtre | URL de fiche |
|---|---|---|---|
| FR | Toyota Europe, `fr/fr`, distributeur 94102 | 18 modeles, hybride | `toyota.fr/occasions/voiture/{id}` |
| BE | Toyota Europe, `be/fr`, 94031 | idem | `fr.toyota.be/occasions/pdp.{slug}-{id}` |
| DE | Toyota Europe, `de/de`, 94272 | idem **+ 2016-2025 + certifie Toyota** | `toyota.de/gebrauchtwagen/pdp.{slug}-{id}` |
| ES | Toyota Europe, `es/es`, 94244 | idem | `toyota.es/coches-segunda-mano/ficha/{id}` |
| NL | **Louwman**, `occasions.toyota.nl/api/search` | memes modeles en clair, hybride | `occasions.toyota.nl/auto/{id}` |

Les quatre premiers partagent l'API de Toyota Europe : meme corps de requete,
seuls changent le chemin pays/langue, le `distributorCode` (lu dans le HTML
de chaque site) et le format d'URL de fiche.

**URL de fiche.** FR et ES acceptent `/{id}` et redirigent cote serveur vers
le slug canonique : fiable. BE et DE n'ont pas cette redirection, et
`pdp.{id}` y est instable -- la meme URL rend 404 puis 200 a une minute
d'ecart. Pour ces deux pays le slug est reconstruit exactement comme le fait
le composant du site (`getUscUrl`) : marque (omise si le modele la contient
deja), modele, annee de 1re immatriculation, carrosserie, type de boite,
carburant marketing, id ; NFD, accents et parentheses retires, espaces ->
`-`, `+` -> `plus`, points retires. Verifie identique aux liens du site.

Le site belge n'ouvre pas certaines de ses propres fiches (redirection vers
la liste, quel que soit le format d'URL) : c'est un defaut de toyota.be, pas
du lien genere.

La liste de modeles est celle de la France, appliquee partout ("aligne tout
sur la France"). L'Allemagne y ajoute les filtres configures sur le site :
`usedCarYear` en plage `{min, max}` et `usedCarWarranty: ["any"]`, qui est la
case « Afficher uniquement les vehicules d'occasion certifies Toyota ».
Contrairement a ce qui etait suppose au depart, l'API accepte bien des plages
(`usedCarYear`, `usedCarPrice`, `usedCarMileage`) : elles n'apparaissent
simplement pas dans les agregations.

Les Pays-Bas sont une autre plateforme (Louwman, l'importateur). API .NET :
`POST /api/search?source=toyota`, corps `{filter:[{name, values}],
limits:{start, limit}, sort}`, resultats sous `occasions`. Le serveur
dedoublonne apres decoupage -- une page de 100 en rend ~80 et `count` est
gonfle d'autant -- d'ou une avancee par `limit` et un arret sur page vide.
Elle offre un vrai `updatedDate` et un tri `RecentDesc`, mais on garde le diff
d'etat pour rester homogene.

Les ids ne sont uniques qu'au sein d'une plateforme : l'etat est indexe par
`PAYS:id`. La migration depuis l'ancien format (UUID nus) est automatique.

Pour ajouter un pays : une entree dans `SOURCES`, puis **obligatoirement**
`python3 scripts/watch.py --seed` avant de pousser, sinon tout son parc part
en notification d'un coup.

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
| `ALERT_PRICE` | `20000` (regle a 15000) | seuil en dessous duquel on notifie (EUR). Rien n'est envoye au-dessus. |
| `ALERT_REGIONS` | vide | limite l'alerte sonore a certaines regions, ex. `bretagne,pays-de-la-loire` (la page reste nationale) |
| `QUICK_PAGES` | `10` | pages balayees en mode rapide, par pays (10 = 1 000 moins cheres). A garder assez large pour couvrir `ALERT_PRICE` avec de la marge. |
| `COUNTRIES` | vide = tous | ex. `FR,BE` pour restreindre la surveillance. |
| `NTFY_SERVER` | `https://ntfy.sh` | serveur ntfy auto-heberge le cas echeant |

Reperes sur le parc actuel (4 991 vehicules) : 360 sous 20 000 EUR (7 %), la
moins chere a 12 990 EUR.

Modeles suivis : les 17 d'origine plus `YB` (Yaris Cross, 2 213 vehicules,
a partir de 17 390 EUR). Le Yaris `YA` (1 852 vehicules des 11 990 EUR) et
l'Aygo X `AX` restent volontairement hors surveillance. Les modeles Lexus
absents de la liste (GS, L5, LM, RC, RZ) n'ont aucun hybride en stock.

Apres tout elargissement de la liste des modeles, lancer une fois
`python3 scripts/watch.py --seed` : les vehicules inconnus sont absorbes sans
notification. Sans cela, tout le parc ajoute part en alerte d'un coup.

Le kilometrage ne merite pas de filtre : mediane 32 650 km, maximum 187 495 km,
**zero** vehicule au-dela de 200 000 km. L'API ne sait de toute facon pas filtrer
par plage (filtres `term` uniquement, pas d'agregation `mileage`). Le tri et le
filtre km sont donc faits sur la page.

## En local

```bash
python3 scripts/watch.py           # balayage complet
python3 scripts/watch.py --quick   # 500 moins cheres
python3 scripts/watch.py --render  # regenere la page seule, sans appel API
NTFY_TOPIC=xxx ALERT_PRICE=22000 python3 scripts/watch.py
```

Sans `NTFY_TOPIC`, les notifications sont affichees dans le terminal au lieu
d'etre envoyees.

## A savoir

- **La page est regeneree a chaque passage, quick compris.** Le mode quick ne
  balaie que 500 vehicules, mais la page est construite depuis `data/state.json`
  (qui garde les champs d'affichage de chaque annonce), pas depuis le balayage
  courant. Sans cela la page serait restee figee 6 h alors que les
  notifications, elles, partent toutes les 15 min : on pouvait recevoir une
  alerte et ne pas trouver la voiture sur la page.
- **Cache** : GitHub Pages sert les fichiers avec `cache-control: max-age=600`,
  et une app ajoutee a l'ecran d'accueil les garde plus longtemps encore. Les
  donnees sont donc dans `docs/cars.json`, recharge avec un parametre anti-cache
  a chaque ouverture, au retour sur l'app (`visibilitychange`) et via le bouton
  « actualiser ». La page ne peut pas afficher un stock perime.
- **Pages est deploye depuis le workflow de surveillance, pas par un workflow
  separe.** Un push signe `GITHUB_TOKEN` ne declenche aucun autre workflow
  (garde-fou anti-boucle de GitHub) : un `pages.yml` declenche sur `push` ne
  partait donc que sur un push humain, et la page publiee restait figee entre
  deux interventions manuelles pendant que le robot collectait correctement.
- **Le cron GitHub n'est pas ponctuel** : 5 a 20 min de retard, runs sautes en
  periode de charge. Detection reelle sous ~30 min.
- **GitHub desactive les workflows planifies apres 60 jours sans activite humaine**
  sur le depot ; les commits du bot ne comptent pas. Un push manuel de temps en
  temps, ou un commit depuis l'interface, suffit a relancer le compteur.
- **Derive de pagination, et sa correction.** Le tri `cashAsc` repose sur un
  script Painless sur le prix, sans clef de departage unique. Les ex aequo se
  reordonnent entre deux requetes et quelques vehicules tombent entre deux
  pages : un balayage seul rend 2 730 a 2 742 ids sur 2 747.

  Mesure faite sur quatre ordres de tri :

  | balayage | ids |
  |---|---|
  | `cashAsc` seul | 2 730 |
  | union `cashAsc` + `cashDesc` | **2 747** |
  | + `mileageAsc`, `yearDesc` | +0 |

  Inverser l'ordre place les ex aequo a d'autres positions : l'union des deux
  atteint exactement le total annonce. Le mode full balaie donc dans les deux
  sens et s'arrete des que l'union atteint `totalResultCount`.

  Le mode quick garde un seul balayage (la vitesse prime) ; comme aucun id
  n'est jamais retire de l'etat, un vehicule manque a un passage est rattrape
  au suivant sans etre signale « nouveau » a tort.

- **Disparitions** : un vehicule doit manquer a **deux** balayages complets
  consecutifs avant d'etre declare retire (compteur `missed`). Sans ce delai,
  la derive marquait a tort une douzaine d'annonces comme disparues.

## Fichiers

```
scripts/watch.py       recuperation, diff, notifications, generation
scripts/template.html  gabarit de l'interface
data/state.json        etat, historique de prix et donnees d'affichage
data/cars.csv          export courant trie par prix
docs/index.html        coquille de l'interface (~12 Ko)
docs/cars.json         donnees affichees, rechargees sans cache
```
