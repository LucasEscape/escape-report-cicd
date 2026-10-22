# Escape scan report (Markdown · GitHub Actions)

`escape_scan_report_mermaid.py` starts an Escape scan (optional), waits for it, and builds a recap of the scan as Markdown and publishes it in the **GitHub Actions job summary**, so it shows on the workflow run page. It's the Markdown version of `../escape_scan_report.py`: same data, same CI gate.

Charts are [Mermaid](https://mermaid.js.org) diagrams written as text in the Markdown. GitHub renders them natively, **private repositories included**, with nothing to host. The script is self-contained: pure Python 3.8+ standard library, nothing to install.

The report contains:

1. **Header**: OWASP logo top-left, Escape logo top-right, scan name, links to the scan in Escape and to the PDF report, profile, target, status, date, CI gate result
2. **Security posture**: score and grade, open findings by severity, score trend over the last 20 finished scans
3. **Overview**: findings per category, scan configuration and its errors or warnings
4. **Compliance: OWASP Top 10**: dedicated table of the [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/), with pass/fail, open findings and highest severity per category, plus the findings per failed category
5. **Coverage**: endpoints discovered and reached, responses breakdown, coverage per scanner user
6. **Findings**: open findings with severity, description and remediation, each linking to Escape

Severity colours: 🟣 **Critical** purple · 🔴 **High** red · 🟡 **Medium** yellow · 🔵 **Low** blue · ⚪ **Info** light blue in charts, white circle in tables.

Every chart is paired with a table, so the report still reads fine where Mermaid isn't rendered.

### Logos

The logos are loaded from public URLs set in `LOGOS` at the top of the script (owasp.org for OWASP, the Escape blog for Escape). GitHub only shows images in a job summary from public URLs, so files committed to a private repo wouldn't display. If a URL stops working, replace it in `LOGOS`.

---

## Run locally

Credentials are read from a `.env` file next to the script (gitignored, never commit it):

```dotenv
ESCAPE_API_KEY=<YOUR_API_KEY>
ESCAPE_PROFILE_ID=<PROFILE_ID>
```

`.env` values override variables already exported in your shell.

```bash
python3 escape_scan_report_mermaid.py                       # report on the last scan → escape-report.md + .pdf
python3 escape_scan_report_mermaid.py --start-scan --wait  # start a scan, wait for it, then report
```

To preview it like GitHub does, open the file in VS Code with a Mermaid preview extension, or push it to a branch and open it on GitHub.

| Option | Description |
|---|---|
| `--profile <uuid>` | Profile to scan / report on (default: `ESCAPE_PROFILE_ID`) |
| `--scan <uuid>` | Report on this scan instead of the profile's last scan |
| `--start-scan` | Start a new scan of the profile first (tagged with the CI commit), then report on it |
| `--wait` | Wait until the scan is `FINISHED`, `FAILED` or `CANCELED` before building the report |
| `--wait-timeout <s>` | Stop waiting after this many seconds and exit `3` without a report (default: no limit) |
| `--fail-on <severity>` | CI gate: exit `1` if an open finding is at or above `critical`, `high`, `medium`, `low` or `info` |
| `--full` | Don't truncate descriptions and remediations |
| `--generate-pdf` / `--no-generate-pdf` | Have Escape generate the PDF report, or skip it (faster). Default: generate, or `ESCAPE_GENERATE_PDF` (`true` / `false`). `--no-report` still works |
| `--pdf <path>` | Where to save the Escape PDF report (default: next to the Markdown file, same name with `.pdf`) |
| `--pdf-timeout <s>` | How long to wait for Escape to generate the PDF, default `600` |
| `--output <path>` | Markdown file to write (default: next to the script) |
| `--stdout` | Also print the Markdown to stdout |
| `--no-step-summary` | Don't append to `$GITHUB_STEP_SUMMARY` (the workflow publishes the summary itself) |

Exit codes: `0` OK · `1` `--fail-on` threshold reached · `2` API or usage error · `3` `--wait-timeout` reached, scan still running · `4` scan `FAILED` or `CANCELED` (the report is still built).

---

## GitHub Actions: scan staging after each deployment

```
push / merge on main
   └─► Deploy to staging            (.github/workflows/deploy-staging.yml, your deployment)
          └─► Escape scan           (.github/workflows/escape-scan.yml, separate workflow)
                 1. start an Escape scan of the profile, tagged with the deployed commit
                 2. wait for it (15 min to 6 h)
                 3. build the report: job summary + escape-report.pdf artifact
```

The scan runs in its **own workflow**, started by `workflow_run` once the deployment succeeded: the deployment finishes right away and is never blocked or slowed down by the scan. A failed deployment doesn't start a scan.

`deploy-staging.yml` is a placeholder: replace its *Deploy* step with your real deployment and keep the workflow name `Deploy to staging`, or rename it in the `workflows:` list at the top of `escape-scan.yml`.

### Report latency

The scan duration can't be predicted, so the job doesn't sleep a fixed time: it asks Escape for the scan status **every 30 s**. Once the scan ends:

| Step | Time |
|---|---|
| Notice the end of the scan | ≤ 30 s |
| Fetch results (findings, OWASP mapping, coverage) while Escape renders the PDF | ~1-2 min |
| Escape PDF rendering | usually < 1 min, up to ~9 min seen; capped at 8 min (`PDF_TIMEOUT`) |
| Uploads + job summary | < 30 s |

So the report is out **~2 min** after the scan in the usual case, **≤ ~10 min** in the worst case. If the PDF isn't ready after 8 min, the report is published anyway with "PDF report n/a".

### Scans longer than 6 hours (8 h, 12 h…)

A GitHub-hosted job can't run more than 6 h. The Escape job waits up to **5 h 20 min**; if the scan is still running, it starts a new *Escape scan* run with the scan id (`workflow_dispatch`, input `scan_id`), which resumes the wait, as many times as needed: an 8 h scan takes 2 runs, a 12 h scan 3 runs. The last one publishes the report, with the same ≤ ~10 min latency. Earlier runs say in their summary that the wait continues in the next run.

Limitations:

- **One runner busy for the whole scan.** Free on a public repository; on a private one, an 8 h scan uses ~8 h of Actions minutes.
- **A few seconds without polling at each handover**, while the new run starts. If the scan ends then, the new run sees it on its first check.
- **The chain depends on GitHub.** If the handover fails (Actions outage, workflow disabled, `actions: write` not allowed), the scan keeps running in Escape but no report is published. Recover with *Run workflow* and the scan id.
- **Several runs per scan** in the Actions list. The report is in the last one.
- **No limit on the total duration.** GitHub's other limits (35 days per run, 24 h per token) apply to each run, and each handover starts a fresh one.

To avoid the handover entirely, run the job on a **self-hosted runner**, where `timeout-minutes` can go up to 5 days.

### Setup

In **Settings → Secrets and variables → Actions → Repository** (not *Environment*):

| Type | Name | Value |
|---|---|---|
| Secret | `ESCAPE_API_KEY` | Escape API key (**required**) |
| Variable | `ESCAPE_PROFILE_ID` | Profile to scan (**required**) |
| Variable | `ESCAPE_GENERATE_PDF` | `true` (default when unset) or `false`: have Escape generate the PDF report on automatic runs, or skip it |
| Variable | `ESCAPE_FAIL_ON` | e.g. `high`: the *Escape scan* run turns red when open findings reach it. Empty = report only |

Permissions used by the workflow: `contents: read`, and `actions: write` to re-dispatch itself and delete old report artifacts. If your organization restricts workflow permissions, allow *Read and write* in *Settings → Actions → General*.

### Manual runs

*Actions → Escape scan → Run workflow*:

| Input | Use |
|---|---|
| `scan_id` | Wait for / report on this scan (e.g. a scan started from the Escape UI) |
| `start_new_scan` | Checked (default): start a new scan. Unchecked: report on the profile's last scan |
| `generate_pdf` | Checked (default): Escape generates the PDF report. Unchecked: skip it, the report has no PDF link |
| `profile_id`, `fail_on`, `full` | Override the repository variables for this run |

### What you get

- The report in the **job summary** of the *Escape scan* run.
- The gate result, and a failed scan (`FAILED` / `CANCELED`), as **annotations** on the run.
- Two **artifacts**: `escape-report.pdf`, the Escape PDF report as-is (not zipped), downloaded directly by the header's **PDF report** link, and `escape-report-markdown`.
- Only the **latest** report per branch: each run deletes the report artifacts of the previous runs on the same branch. Artifacts are kept 90 days if no new run replaces them.

If Escape doesn't produce the PDF in time or the download fails, the header says "PDF report n/a" with the reason, and the run continues. When PDF generation is turned off, there's no PDF link and no `escape-report.pdf` artifact, and the report is out in seconds.

> ℹ️ Escape locks a profile while one of its scans is running. If a deployment happens meanwhile, Escape may refuse to start the new scan: that run fails with exit `2` and the Escape error message, and the running scan's report is still published by its own run.

> ℹ️ GitHub limits a job summary to 1 MiB. Above that, the summary is truncated with a note pointing to the artifact.

---

## Notes

- The OWASP table maps each open finding through its compliance tags (one API call per open finding, run 8 at a time).
- Transient API errors (429, 5xx, timeouts) are retried with exponential backoff; while waiting for a scan, API errors never stop the wait.
- Escape can take several minutes to render the PDF (up to ~10 min seen), so the script waits up to 10 minutes for it.
- Both logos are dark on a transparent background, so they're hard to see in GitHub's dark theme.
