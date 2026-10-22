# Escape scan report for CI/CD

Scan your staging environment with Escape after every deployment, wait for the scan however long it takes, and publish a security report with the Escape PDF in your pipeline, without blocking your deployments.

Scannez votre staging avec Escape après chaque déploiement, attendez la fin du scan quelle que soit sa durée, et publiez un rapport de sécurité avec le PDF Escape dans votre pipeline, sans bloquer vos déploiements.

- **English:** [README_EN.md](README_EN.md)
- **Français :** [README_FR.md](README_FR.md)

| File | Role |
|---|---|
| `escape_scan_report_mermaid.py` | The script: starts the scan, waits for it, builds the report |
| `.github/workflows/escape-scan.yml` | Ready-made GitHub Actions workflow, triggered after your deployment |
| `.github/workflows/deploy-staging.yml` | Demo placeholder for a deployment workflow |
