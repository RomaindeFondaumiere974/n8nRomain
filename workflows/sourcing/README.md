# Sourcing de profils (n8n)

Tu remplis un formulaire avec tes critères. n8n cherche des profils sur LinkedIn, Malt, GitHub ou un autre site, Claude note chaque profil par rapport à ton besoin, et tu reçois les meilleurs par email. Tout est aussi enregistré dans un Google Sheet.

## Comment ça marche

```
Formulaire
  -> Claude écrit 2 à 4 requêtes par plateforme
  -> SOURCING_01 (Google X-ray via Serper) : LinkedIn, Malt, autre site
  -> SOURCING_02 (API GitHub)
  -> dédoublonnage
  -> Claude note chaque profil (score, points forts, vigilance, accroche)
  -> Google Sheet + email récapitulatif
```

| Fichier | Rôle |
|---|---|
| `SOURCING_00_Formulaire_Recherche_Profils.json` | Formulaire, orchestration, notation, Sheet, email |
| `SOURCING_01_Source_XRay_Google.json` | Recherche Google `site:linkedin.com/in ...` via Serper |
| `SOURCING_02_Source_GitHub.json` | Recherche d'utilisateurs GitHub (API officielle) |

Les JSON sont générés par `scripts/build_sourcing_workflows.py`. Pour modifier un workflow, modifie le script puis relance `python3 scripts/build_sourcing_workflows.py`. Si tu modifies directement dans n8n, réexporte le JSON ici pour garder les deux synchronisés.

## Installation (15 minutes)

### 1. Comptes et clés

| Service | Pour quoi | Où |
|---|---|---|
| Serper.dev | Recherche Google (X-ray) | serper.dev, 2 500 requêtes gratuites à l'inscription |
| Anthropic | Requêtes + notation | console.anthropic.com |
| GitHub | Recherche de développeurs | Un token "fine-grained" sans aucune permission suffit (données publiques) |
| Google Sheets + Gmail | Stockage + email | Credentials OAuth Google déjà présents sur ton instance |

### 2. Credentials à créer dans n8n

- **Header Auth** nommé `Serper` : Name = `X-API-KEY`, Value = ta clé Serper.
- **Anthropic** : ta clé API.
- **GitHub API** : ton token.
- **Basic Auth** nommé `Formulaire sourcing` : identifiant et mot de passe de ton choix. Sans ça, n'importe qui ayant l'URL du formulaire peut consommer tes crédits.

### 3. Import

1. Importer `SOURCING_01_Source_XRay_Google.json`. Sur le nœud **Serper**, choisir le credential `Serper`. Enregistrer. Copier l'ID du workflow (dans l'URL : `/workflow/<ID>`).
2. Importer `SOURCING_02_Source_GitHub.json`. Choisir le credential GitHub sur les 2 nœuds HTTP. Enregistrer. Copier l'ID.
3. Importer `SOURCING_00_Formulaire_Recherche_Profils.json`, puis :
   - nœud **⚙️ Config** : coller les 2 IDs dans `SUBWF_XRAY_ID` et `SUBWF_GITHUB_ID` ;
   - choisir les credentials sur le formulaire (Basic Auth), les 2 nœuds Claude (Anthropic), Google Sheets et les 2 nœuds Gmail.
4. Ouvrir le Google Sheet [Sourcing - Profils](https://docs.google.com/spreadsheets/d/1XED45FG8yd6Bkn7AcGq0FBshIU8rS-QANRqumEQK6ck/edit) et **renommer l'onglet `Untitled` en `Profils`**. Le Sheet a été créé sur `romain.defondaumiere.ai@gmail.com` : si ton credential Google Sheets n8n utilise un autre compte, partage-lui le fichier.
5. Activer `SOURCING_00`. Les deux sous-workflows n'ont pas besoin d'être activés.
6. Ouvrir l'URL de **production** du formulaire (nœud Formulaire, onglet "Production URL").

### 4. Premier test

Fais une recherche à 20 profils, LinkedIn seul. Puis vérifie dans les exécutions :
- `🗂️ Préparer les tâches` : les requêtes sont-elles celles que tu aurais écrites ?
- `🔎 Lancer les recherches` : chaque source renvoie des profils, pas une erreur ?
- l'email et le Sheet sont remplis.

## Réglages (nœud ⚙️ Config)

| Clé | Défaut | Effet |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5-5` | Modèle pour les requêtes et la notation |
| `MIN_SCORE_EMAIL` | 60 | Score minimum pour apparaître dans l'email (tout va dans le Sheet) |
| `SCORING_BATCH_SIZE` | 15 | Profils notés par appel à Claude |
| `GSHEET_TAB` | `Profils` | Nom de l'onglet |

Les appels à Claude activent le repli automatique sur un autre modèle en cas de refus (`fallbacks: "default"`). Pour le désactiver, retire `fallbacks` et l'en-tête `anthropic-beta` des 2 nœuds Claude.

## Coût indicatif par recherche de 100 profils

- Serper : 10 à 30 requêtes, couvertes par le crédit gratuit au début, puis environ 1 $ les 1 000.
- Claude : 1 appel pour les requêtes + 7 lots de notation, de l'ordre de 0,50 à 1 $. Estimation, à vérifier sur ta console Anthropic après les premières recherches.

## Limites à connaître

- **LinkedIn n'est pas scrapé directement.** On lit les résultats Google des profils publics : nom, titre, entreprise, parfois localisation. Pas l'historique complet. La note de Claude repose donc sur un extrait : c'est un tri, pas une évaluation. Il faut ouvrir le profil avant de contacter.
- **Couverture partielle.** Google n'indexe pas tous les profils, et ceux qui ont masqué leur profil public n'apparaissent pas.
- **GitHub** : la recherche porte sur la localisation, les langages et les abonnés, pas sur la bio. Utile pour des profils techniques, inutile ailleurs.
- **Malt** : seuls les profils indexés par Google sur `malt.fr/profile` remontent.

## RGPD

Tu constitues un fichier de données personnelles sur des personnes qui ne te les ont pas données. C'est possible au titre de l'intérêt légitime, à condition de :
- ne garder que ce qui sert au recrutement (le Sheet ne stocke que des infos professionnelles publiques) ;
- informer la personne au plus tard au premier contact, et dans un délai d'un mois maximum (article 14 RGPD) : finalité, source des données, durée de conservation, droits ;
- supprimer les lignes à échéance : la colonne `date_suppression_prevue` est fixée à 24 mois. Rien n'est supprimé automatiquement, c'est à toi de faire la purge.

Référence : [guide du recrutement de la CNIL](https://www.cnil.fr/le-guide-du-recrutement).
