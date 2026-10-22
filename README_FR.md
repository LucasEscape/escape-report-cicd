# Rapport de scan Escape pour la CI/CD

Scannez votre environnement de staging avec Escape après chaque déploiement et obtenez un rapport de sécurité lisible dans votre pipeline, sans ralentir vos déploiements.

[Read in English](README_EN.md)

---

## À quoi ça sert

`escape_scan_report_mermaid.py` relie votre CI/CD à Escape :

1. **Il lance un scan Escape** de votre application une fois déployée en staging, rattaché au commit déployé.
2. **Il attend la fin du scan**, quelle que soit sa durée. Un scan dure d'environ 15 minutes à plus de 8 heures, et sa durée n'est pas prévisible : le script interroge Escape toutes les 30 secondes au lieu d'attendre un temps fixe.
3. **Il publie le rapport** environ une minute après la fin du scan : un récapitulatif Markdown sur la page du run, et le rapport PDF généré par Escape.

Le scan tourne dans **son propre workflow**, lancé une fois le déploiement réussi. Votre déploiement se termine normalement et n'attend jamais le scan.

```
push / merge sur main
  └─► votre workflow de déploiement (ex. « Deploy to staging »)
        └─► workflow Escape scan   ← séparé, ne bloque jamais le déploiement
              1. lance un scan Escape du profil
              2. attend sa fin (15 min à 8 h et plus)
              3. publie le rapport : résumé du run + PDF Escape
```

Le script tient en un seul fichier Python, uniquement avec la bibliothèque standard (Python 3.8+). Rien à installer.

### Contenu du rapport

| Section | Contenu |
|---|---|
| En-tête | Nom du scan, liens vers le scan dans Escape et vers le rapport PDF, profil, cible, statut, date, durée, résultat du contrôle CI |
| 1. Security posture | Score et note (de A à E), findings ouverts par sévérité, évolution du score sur les 20 derniers scans terminés |
| 2. Overview | Findings par catégorie, configuration du scan (mode, durée, authentification), erreurs et avertissements de configuration |
| 3. Compliance: OWASP Top 10 | Tableau de l'[OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/) : conforme ou non, findings ouverts et sévérité maximale par catégorie, et les findings de chaque catégorie en échec |
| 4. Coverage | Endpoints découverts et atteints, répartition des réponses, couverture par utilisateur du scanner |
| 5. Findings | Chaque finding ouvert avec sa sévérité, sa description et sa remédiation, lié à sa page dans Escape |

Le rapport lui-même est rédigé en anglais.

Les graphiques sont des diagrammes [Mermaid](https://mermaid.js.org), écrits sous forme de texte dans le Markdown. GitHub les affiche nativement, dans les dépôts publics comme privés, sans rien héberger. Chaque graphique a son tableau équivalent : le rapport reste lisible là où Mermaid n'est pas affiché.

Couleurs des sévérités dans les graphiques : critique violet, haute rouge, moyenne jaune, basse bleu, info bleu clair.

### Délais mesurés

Sur un vrai run (scan de 1 h 17, PDF activé), le rapport et le PDF ont été publiés **56 secondes** après la fin du scan.

| Étape après la fin du scan | Durée |
|---|---|
| Détecter la fin du scan | jusqu'à 30 s |
| Récupérer findings, mapping OWASP et couverture, pendant qu'Escape génère le PDF | environ 1 à 2 min |
| Génération du PDF par Escape | en général moins d'1 min, jusqu'à environ 9 min observées, plafonnée à 8 min |
| Uploads et résumé du run | moins de 30 s |

Le rapport sort en environ 2 minutes dans le cas habituel, et en 10 minutes environ au pire. Si le PDF n'est pas prêt au bout de 8 minutes, le rapport est publié quand même et indique « PDF report n/a ».

---

## Prérequis

- Un compte Escape avec un **profil** pour votre application de staging. L'ID du profil est l'UUID dans son URL : `https://app.escape.tech/profiles/<PROFILE_ID>/…`
- Une **clé API** Escape : *Escape → User settings → API key*
- Pour le workflow prêt à l'emploi : **GitHub Actions**, avec des runners hébergés par GitHub ou auto-hébergés. Pour les autres CI, voir [Autres systèmes de CI/CD](#autres-systèmes-de-cicd).

---

## L'ajouter à un pipeline GitHub Actions existant

### 1. Copier deux fichiers dans votre dépôt

```
votre-depot/
├── escape_scan_report_mermaid.py
└── .github/workflows/escape-scan.yml
```

Le fichier `deploy-staging.yml` de ce dépôt n'est qu'un exemple pour la démo : ne le copiez pas, vous gardez votre propre workflow de déploiement.

Si vous placez le script dans un sous-dossier, modifiez les deux lignes `python3 escape_scan_report_mermaid.py` de `escape-scan.yml` (étapes *Start the Escape scan* et *Wait for the scan, build the report*).

### 2. Le relier à votre workflow de déploiement

En haut de `escape-scan.yml`, indiquez le `name:` de votre workflow de déploiement et les branches à scanner :

```yaml
on:
  workflow_run:
    workflows: ["Deploy to staging"]   # ← le name: de votre workflow de déploiement
    types: [completed]
    branches: [main]                   # ← branches dont les déploiements sont scannés
```

Le scan ne démarre que si ce workflow **réussit**. Un déploiement en échec ne lance pas de scan.

GitHub ne déclenche un workflow `workflow_run` que si son fichier est sur la **branche par défaut** : mergez d'abord `escape-scan.yml` sur cette branche.

### 3. Ajouter le secret et les variables

Dans *Settings → Secrets and variables → Actions*, utilisez les sections **Repository**, pas *Environment* : le workflow ne déclare pas d'environnement, il ne peut donc pas lire les secrets d'environnement.

| Type | Nom | Obligatoire | Valeur |
|---|---|---|---|
| Secret | `ESCAPE_API_KEY` | oui | Votre clé API Escape |
| Variable | `ESCAPE_PROFILE_ID` | oui | Le profil à scanner |
| Variable | `ESCAPE_ON_RUNNING_SCAN` | non | `keep` (défaut), `replace` ou `replace_and_report`. Voir [Quand un scan tourne déjà](#quand-un-scan-tourne-déjà) |
| Variable | `ESCAPE_GENERATE_PDF` | non | `true` (défaut) ou `false`. Voir [Rapport PDF](#rapport-pdf) |
| Variable | `ESCAPE_FAIL_ON` | non | `critical`, `high`, `medium`, `low` ou `info`. Voir [Contrôle de sécurité](#contrôle-de-sécurité) |

### 4. Vérifier les permissions

Le workflow demande `contents: read` et `actions: write`. `actions: write` lui permet de :

- relancer un nouveau run de lui-même quand un scan dépasse la limite de 6 heures par job,
- annuler le run qui attendait un scan qu'il remplace,
- supprimer les artefacts des rapports précédents.

Si votre organisation restreint les permissions des workflows, autorisez *Read and write permissions* dans *Settings → Actions → General → Workflow permissions*.

### 5. Déployer et consulter le résultat

Poussez ou mergez sur votre branche de déploiement. Une fois le déploiement réussi, ouvrez *Actions → Escape scan → le dernier run* :

- le **résumé du run** affiche le rapport complet,
- le lien **PDF report** en haut télécharge directement le PDF Escape,
- la section **Artifacts** liste `escape-report.pdf` et `escape-report-markdown`.

### Déploiement hors de GitHub Actions

Si votre déploiement ne passe pas par GitHub Actions (Argo CD, Jenkins, une console cloud…), supprimez le bloc `workflow_run` et déclenchez le workflow Escape depuis votre outil de déploiement, via l'API GitHub :

```bash
curl -X POST \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<owner>/<repo>/actions/workflows/escape-scan.yml/dispatches \
  -d '{"ref": "main"}'
```

Le token doit avoir la permission *Actions: write* sur le dépôt. Le même appel accepte les paramètres d'un lancement manuel, par exemple `{"ref": "main", "inputs": {"on_running_scan": "replace"}}`.

---

## Configuration

### Lancement manuel

*Actions → Escape scan → Run workflow* permet de modifier la configuration pour un run :

| Paramètre | Effet |
|---|---|
| `scan_id` | Attendre ce scan et produire son rapport, au lieu d'en lancer un nouveau. Pratique pour un scan lancé depuis l'interface Escape |
| `start_new_scan` | Coché (défaut) : lancer un nouveau scan. Décoché : rapport sur le dernier scan du profil |
| `on_running_scan` | Remplace `ESCAPE_ON_RUNNING_SCAN` |
| `generate_pdf` | Coché (défaut) : Escape génère le rapport PDF. Décoché : pas de PDF, rapport plus rapide |
| `profile_id` | Remplace `ESCAPE_PROFILE_ID` |
| `fail_on` | Remplace `ESCAPE_FAIL_ON`. `none` désactive le contrôle |
| `full` | Ne pas tronquer les descriptions et remédiations du tableau des findings |

### Quand un scan tourne déjà

Escape ne lance qu'un scan à la fois par profil. Quand un nouveau déploiement arrive alors qu'un scan du même profil tourne encore, `ESCAPE_ON_RUNNING_SCAN` décide de la suite :

| Mode | Scan en cours | Nouveau scan | Rapports publiés |
|---|---|---|---|
| `keep` (défaut) | continue | pas lancé | Celui du scan en cours, par le run qui l'attend. Le nouveau run indique seulement « scan non lancé » |
| `replace` | arrêté | lancé | Seulement celui du nouveau scan. Le run qui attendait le scan arrêté est annulé et ne publie rien |
| `replace_and_report` | arrêté | lancé | D'abord le rapport **partiel** du scan arrêté, avec un bandeau « Scan stopped and replaced » et son propre PDF (`escape-report-stopped.pdf`), puis celui du nouveau scan, dans le même run |

Choisissez `keep` quand chaque scan doit aller au bout, par exemple pour des scans longs sur une branche qui déploie souvent. Choisissez `replace` quand seul le dernier déploiement compte. Choisissez `replace_and_report` pour garder une trace de ce que le scan arrêté avait déjà trouvé.

Dans les deux modes `replace`, le workflow arrête le scan dans Escape, attend qu'il soit annulé, puis lance le nouveau, en réessayant pendant environ une minute si Escape n'a pas encore libéré le profil. Il retrouve le run qui attendait l'ancien scan grâce à son nom, qui contient l'ID du profil : `Escape scan · profile <id>`.

Un scan lancé depuis l'interface Escape ou par une planification Escape n'a pas de run GitHub qui l'attend. En `replace`, il est arrêté sans rapport. En `replace_and_report`, le nouveau run publie son rapport partiel.

### Rapport PDF

Avec `ESCAPE_GENERATE_PDF` à `true` (défaut), le workflow demande à Escape le même PDF que *Export report* dans l'interface Escape, le télécharge et l'attache au run :

- uploadé **tel quel, sans zip**, comme artefact `escape-report.pdf`,
- lié depuis l'en-tête du rapport : un clic télécharge le PDF,
- le rapport ne contient jamais le lien de téléchargement temporaire d'Escape (il expire après 6 heures).

Passez-la à `false` pour sauter le PDF. Le rapport est alors publié en quelques secondes, sans lien PDF ni artefact `escape-report.pdf`.

**Seul le dernier rapport est conservé.** Chaque run supprime les artefacts de rapport des runs précédents **sur la même branche** : une branche de pull request ne supprime pas le rapport de `main`. Les artefacts sont gardés 90 jours si aucun nouveau run ne les remplace.

### Contrôle de sécurité

Avec `ESCAPE_FAIL_ON` défini, par exemple à `high`, le run *Escape scan* passe en rouge s'il reste des findings ouverts de cette sévérité ou au-dessus. Le résultat apparaît en haut du rapport et en annotation sur le run. Le déploiement n'est jamais bloqué : seul le run Escape est marqué en échec.

Un scan terminé en `FAILED` ou `CANCELED` marque aussi le run en échec. Son rapport partiel est quand même publié.

### Scans de plus de 6 heures

Un job sur runner hébergé par GitHub ne peut pas dépasser 6 heures. Le job Escape attend au maximum **5 h 20**, puis lance un nouveau run *Escape scan* avec l'ID du scan, qui reprend l'attente, autant de fois que nécessaire :

| Durée du scan | Runs |
|---|---|
| jusqu'à 5 h 20 | 1 |
| 8 h | 2 |
| 12 h | 3 |

Le dernier run publie le rapport, avec les mêmes délais que ci-dessus. Les runs précédents indiquent dans leur résumé que l'attente continue dans le run suivant.

Limites :

- Un runner reste occupé pendant toute la durée du scan. Les runners hébergés par GitHub sont gratuits sur les dépôts publics ; sur un dépôt privé, un scan de 8 heures consomme environ 8 heures de minutes Actions.
- Si le relais échoue (panne de GitHub Actions, workflow désactivé, `actions: write` refusé), le scan continue dans Escape mais aucun rapport n'est publié. Rattrapez-le avec *Run workflow* et le paramètre `scan_id`.
- Sur un **runner auto-hébergé**, un job peut durer jusqu'à 5 jours : augmentez `timeout-minutes` et `WAIT_TIMEOUT` dans `escape-scan.yml`, il n'y a alors plus de relais.

### Valeurs réglables dans `escape-scan.yml`

| Valeur | Défaut | Signification |
|---|---|---|
| `WAIT_TIMEOUT` | `19200` (5 h 20) | Secondes d'attente d'un run avant le relais vers un nouveau run |
| `PDF_TIMEOUT` | `480` (8 min) | Secondes d'attente de la génération du PDF par Escape |
| `timeout-minutes` | `360` | Limite GitHub d'un job sur runner hébergé |
| `retention-days` | `90` | Durée de conservation des artefacts de rapport |

### Logos

L'en-tête du rapport affiche deux logos, définis dans `LOGOS` en haut du script (OWASP à gauche, Escape à droite). Pour afficher votre logo, remplacez une URL. L'image doit être accessible par une **URL publique** : GitHub n'affiche dans le résumé d'un run que des images publiques, pas des fichiers d'un dépôt privé.

---

## Référence du script

Le workflow appelle le script, mais vous pouvez aussi le lancer seul, en local ou dans une autre CI.

### En local

Créez un fichier `.env` à côté du script. Ne le commitez jamais : il est listé dans `.gitignore`.

```dotenv
ESCAPE_API_KEY=<VOTRE_CLE_API>
ESCAPE_PROFILE_ID=<PROFILE_ID>
```

Les valeurs du `.env` remplacent les variables déjà exportées dans votre terminal.

```bash
python3 escape_scan_report_mermaid.py                          # rapport sur le dernier scan du profil
python3 escape_scan_report_mermaid.py --start-scan --wait      # lance un scan, l'attend, puis fait le rapport
python3 escape_scan_report_mermaid.py --scan <uuid> --wait     # attend un scan donné, puis fait le rapport
```

Le rapport est écrit dans `escape-report.md`, et le PDF dans `escape-report.pdf`.

### Options

| Option | Description |
|---|---|
| `--profile <uuid>` | Profil à scanner ou sur lequel faire le rapport. Défaut : `ESCAPE_PROFILE_ID` |
| `--scan <uuid>` | Rapport sur ce scan au lieu du dernier scan du profil |
| `--start-scan` | Lance d'abord un nouveau scan du profil, rattaché au commit de la CI |
| `--start-only` | Avec `--start-scan` : lance le scan et s'arrête, sans attendre ni faire de rapport |
| `--on-running-scan <mode>` | `keep`, `replace` ou `replace_and_report`. Défaut : `ESCAPE_ON_RUNNING_SCAN`, sinon `keep` |
| `--stopped-output <chemin>` | Fichier du rapport du scan arrêté avec `replace_and_report`. Défaut : `escape-report-stopped.md` |
| `--wait` | Attend que le scan soit `FINISHED`, `FAILED` ou `CANCELED` avant de faire le rapport |
| `--wait-timeout <s>` | Arrête l'attente après ce nombre de secondes et sort avec le code `3`. Défaut : pas de limite |
| `--fail-on <sévérité>` | Sort avec le code `1` s'il reste un finding ouvert de sévérité `critical`, `high`, `medium`, `low` ou `info` ou au-dessus |
| `--full` | Ne tronque pas les descriptions et remédiations |
| `--generate-pdf` / `--no-generate-pdf` | Génère le rapport PDF Escape ou le saute. Défaut : `ESCAPE_GENERATE_PDF`, sinon génération |
| `--pdf <chemin>` | Où enregistrer le PDF. Défaut : à côté du fichier Markdown, même nom |
| `--pdf-timeout <s>` | Temps d'attente maximal de la génération du PDF. Défaut : `600` |
| `--output <chemin>` | Fichier Markdown à écrire. Défaut : `escape-report.md` à côté du script |
| `--stdout` | Affiche aussi le Markdown sur la sortie standard |
| `--no-step-summary` | N'ajoute pas le rapport à `$GITHUB_STEP_SUMMARY` |

### Variables d'environnement

| Variable | Description |
|---|---|
| `ESCAPE_API_KEY` | Clé API Escape (obligatoire) |
| `ESCAPE_PROFILE_ID` | ID du profil (obligatoire sauf si `--profile` est donné) |
| `ESCAPE_ON_RUNNING_SCAN` | Équivalent de `--on-running-scan` |
| `ESCAPE_GENERATE_PDF` | `true` ou `false`, équivalent de `--generate-pdf` / `--no-generate-pdf` |
| `ESCAPE_PDF_PATH`, `ESCAPE_PDF_TIMEOUT` | Équivalents de `--pdf`, `--pdf-timeout` |
| `ESCAPE_COMMIT_HASH`, `ESCAPE_COMMIT_BRANCH`, `ESCAPE_COMMIT_AUTHOR` | Commit rattaché au scan dans Escape. Défaut : le commit de la CI (`GITHUB_SHA`, `GITHUB_REF_NAME`, `GITHUB_ACTOR`) |
| `ESCAPE_API_URL` | URL de base de l'API. Défaut : `https://public.escape.tech/v3` |

### Codes de sortie

| Code | Signification |
|---|---|
| `0` | Rapport publié |
| `1` | Seuil `--fail-on` atteint |
| `2` | Erreur d'API ou d'utilisation : clé absente, profil inconnu, réseau… |
| `3` | `--wait-timeout` atteint, le scan tourne encore, pas encore de rapport |
| `4` | Le scan s'est terminé en `FAILED` ou `CANCELED`. Le rapport partiel est publié |
| `5` | Aucun scan lancé : un scan tourne déjà et le mode est `keep` |

Les erreurs temporaires de l'API (HTTP 429, 5xx, délais dépassés) sont retentées avec un délai croissant. Pendant l'attente d'un scan, une erreur d'API n'interrompt jamais l'attente.

---

## Autres systèmes de CI/CD

Le workflow prêt à l'emploi est fait pour GitHub Actions, et c'est l'intégration que nous avons fait tourner de bout en bout. Sur les autres systèmes, appelez directement le script : les exemples ci-dessous montrent le principe, adaptez-les à vos runners.

Deux points à prévoir :

- **Ne pas bloquer le déploiement.** Lancez le scan dans un job qui vient après le déploiement et ne peut pas le faire échouer, ou dans un pipeline séparé.
- **Durée maximale du job.** Le job attend tant que le scan tourne. Réglez la durée maximale des jobs de votre CI au-dessus de votre plus long scan, ou utilisez `--wait-timeout` et relancez le job avec `--scan <id>` quand il sort avec le code `3`.

### GitLab CI

```yaml
escape-scan:
  stage: post-deploy          # après votre étape de déploiement
  needs: [deploy-staging]
  allow_failure: true         # le scan ne fait jamais échouer le déploiement
  timeout: 8h                 # doit rester sous la durée maximale de vos runners
  image: python:3.12-slim
  script:
    - python3 escape_scan_report_mermaid.py --start-scan --wait --output escape-report.md
  artifacts:
    when: always
    paths: [escape-report.md, escape-report.pdf]
    expire_in: 90 days
```

Définissez `ESCAPE_API_KEY` (masquée) et `ESCAPE_PROFILE_ID` dans *Settings → CI/CD → Variables*. GitLab affiche Mermaid dans les fichiers Markdown : ouvrez `escape-report.md` depuis les artefacts du job.

### Jenkins

```groovy
stage('Escape scan') {
  environment {
    ESCAPE_API_KEY    = credentials('escape-api-key')
    ESCAPE_PROFILE_ID = '<PROFILE_ID>'
  }
  options { timeout(time: 8, unit: 'HOURS') }
  steps {
    catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE') {
      sh 'python3 escape_scan_report_mermaid.py --start-scan --wait --output escape-report.md'
    }
  }
  post {
    always { archiveArtifacts artifacts: 'escape-report.*', allowEmptyArchive: true }
  }
}
```

Placez cette étape après le déploiement, ou dans un job séparé déclenché par celui-ci, pour que le déploiement n'attende pas le scan.

---

## Sécurité

- **Visibilité du dépôt.** Sur un dépôt public, tout le monde peut lire le résumé du run, et toute personne connectée à GitHub peut télécharger l'artefact PDF : findings, endpoints vulnérables et remédiations compris. Gardez le dépôt privé si ce n'est pas voulu.
- **Clé API.** Stockez-la comme secret de la CI, jamais dans le code. Le fichier `.env` sert uniquement en local et est exclu de Git.
- **Mode du scan.** Le scan teste réellement votre application de staging. En mode `read_write`, Escape envoie aussi des requêtes qui modifient des données : scannez le staging, pas la production.

---

## Dépannage

| Symptôme | Cause et solution |
|---|---|
| `ESCAPE_API_KEY is not set`, code de sortie `2` | Le secret manque, ou il a été créé dans *Environment* au lieu de *Repository* |
| Le workflow Escape ne démarre jamais | Le nom dans `workflows:` ne correspond pas au `name:` de votre workflow de déploiement, la branche n'est pas listée, ou `escape-scan.yml` n'est pas sur la branche par défaut |
| « Escape scan not started » dans le résumé | Un scan du profil tournait déjà et le mode est `keep`. Son rapport vient du run qui l'attend |
| *Delete previous reports* échoue | Votre organisation limite les permissions des workflows : autorisez *Read and write permissions* |
| « PDF report n/a » | Escape n'a pas généré le PDF dans le délai `PDF_TIMEOUT`, ou le téléchargement a échoué. Le reste du rapport est complet |
| Un graphique s'affiche en code | L'outil d'affichage ne gère pas Mermaid. Le tableau sous chaque graphique contient les mêmes données |

Une question ou un retour : contactez votre interlocuteur Escape.
