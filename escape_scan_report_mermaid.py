#!/usr/bin/env python3
"""
Escape — last scan recap as Markdown, with charts drawn as Mermaid diagrams.

Made for the GitHub Actions job summary: GitHub renders Mermaid natively (job summaries, files,
PRs), so the charts show on the workflow run page even for a private repository. The report is
a single self-contained .md file: charts are plain text, nothing to host.

Usage:
    python3 escape_scan_report_mermaid.py                     # reads ESCAPE_API_KEY / ESCAPE_PROFILE_ID from .env
    python3 escape_scan_report_mermaid.py --profile <uuid>    # another profile
    python3 escape_scan_report_mermaid.py --fail-on high      # CI gate: exit 1 if open HIGH/CRITICAL findings
    python3 escape_scan_report_mermaid.py --output out/report.md
    python3 escape_scan_report_mermaid.py --start-scan --wait  # start a scan, wait for it, then report
    python3 escape_scan_report_mermaid.py --scan <uuid> --wait # wait for a running scan, then report

Environment variables (or .env next to the script):
    ESCAPE_API_KEY        (required) Escape public API key
    ESCAPE_PROFILE_ID     (required) profile to report on, overridden by --profile
    ESCAPE_API_URL        (optional) defaults to https://public.escape.tech/v3
    ESCAPE_PDF_PATH       (optional) same as --pdf: where to save the Escape PDF report
    ESCAPE_PDF_TIMEOUT    (optional) same as --pdf-timeout (seconds, default 600)
    GITHUB_STEP_SUMMARY   set by GitHub Actions; the report is appended to the job summary

Exit codes: 0 = OK, 1 = --fail-on threshold reached, 2 = API / usage error,
            3 = --wait-timeout reached, scan still running (no report), 4 = scan FAILED or CANCELED.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

APP_URL = "https://app.escape.tech"
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
# Colour policy: critical purple · high red · medium yellow · low blue · info light blue
SEV_COLOR = {"CRITICAL": "#7c3aed", "HIGH": "#dc2626", "MEDIUM": "#eab308", "LOW": "#2563eb", "INFO": "#7dd3fc"}
SEV_EMOJI = {"CRITICAL": "🟣", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪"}

# (file, alt text, height in px, side); the first existing file of each tuple is used
# Header logos (url, alt text, height in px, side). Public URLs on purpose: GitHub job summaries only
# display images from absolute public URLs, never from a private repository or a relative path.
LOGOS = [
    ("https://policy.owasp.org/assets/images/logo.png", "OWASP", 44, "left"),
    ("https://escape.tech/blog/content/images/2026/03/Dark-Blue---MAIN-LOGO---DARK-Backgrounds--1-.png",
     "Escape", 36, "right"),
]

# OWASP API Security Top 10 (2023): the "OWASP" framework of the Escape API
OWASP_TOP10 = [
    ("API1", "Broken Object Level Authorization"),
    ("API2", "Broken Authentication"),
    ("API3", "Broken Object Property Level Authorization"),
    ("API4", "Unrestricted Resource Consumption"),
    ("API5", "Broken Function Level Authorization"),
    ("API6", "Unrestricted Access to Sensitive Business Flows"),
    ("API7", "Server Side Request Forgery"),
    ("API8", "Security Misconfiguration"),
    ("API9", "Improper Inventory Management"),
    ("API10", "Unsafe Consumption of APIs"),
]
OWASP_DOC = "https://owasp.org/API-Security/editions/2023/en/0x11-t10/"

GITHUB_SUMMARY_LIMIT = 1024 * 1024  # GitHub rejects step summaries above 1 MiB


# ─── .env ─────────────────────────────────────────────────────────────────────
def load_dotenv(path):
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes.
    Values in the file override the environment; in CI, where .env is not committed, the
    variables come from the pipeline secrets instead."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = re.sub(r"^export\s+", "", key.strip())
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ[key] = value


def in_github_actions():
    return os.environ.get("GITHUB_ACTIONS") == "true"


# ─── API client ───────────────────────────────────────────────────────────────
class APIError(Exception):
    pass


class EscapeAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base = os.environ.get("ESCAPE_API_URL", "https://public.escape.tech/v3").rstrip("/")

    def get(self, path, params=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        return self._send(urllib.request.Request(url, headers=self._headers()), "GET", path)

    def post(self, path, body):
        req = urllib.request.Request(f"{self.base}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers=dict(self._headers(), **{"Content-Type": "application/json"}))
        return self._send(req, "POST", path)

    def _headers(self):
        return {"X-ESCAPE-API-KEY": self.api_key, "Accept": "application/json",
                "User-Agent": "escape-scan-report-md/2.0"}

    def _send(self, req, method, path):
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                body = e.read().decode(errors="replace")[:300]
                raise APIError(f"API error {e.code} on {method} {path}: {body}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                # Network errors, including a read timeout in the middle of a response
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise APIError(f"Cannot reach {self.base}: {getattr(e, 'reason', e)}")

    def paginate(self, path, params=None):
        params = dict(params or {}, size=100)
        while True:
            page = self.get(path, params)
            yield from page.get("data", [])
            cursor = page.get("nextCursor")
            if not cursor or not page.get("data"):
                return
            params["cursor"] = cursor

    def total(self, path, params=None):
        return self.get(path, dict(params or {}, size=1)).get("totalCount", 0)


def fail(msg):
    if in_github_actions():
        print(f"::error title=Escape scan report::{msg}", file=sys.stderr)
    print(f"✖ {msg}", file=sys.stderr)
    return 2


# ─── PDF report (Escape export job) ───────────────────────────────────────────
REPORT_BLOCKS = ["SCAN_SUMMARY", "ISSUE_SUMMARY", "ISSUE_LIST", "ISSUE_DETAIL"]


def start_report(api, scan_id):
    try:
        job = api.post("/jobs", {"blocks": [{"kind": k} for k in REPORT_BLOCKS],
                                 "scanId": scan_id, "notify": False})
        return job["id"], None
    except (APIError, KeyError):
        return None, "report job could not be started"


def wait_report(api, job_id, timeout=600):
    deadline = time.time() + timeout
    while True:
        try:
            job = api.get(f"/jobs/{job_id}")
        except APIError:
            return None, f"report status unavailable (job {job_id})"
        status = job.get("status")
        if status == "COMPLETED":
            urls = [a["signedUrl"] for a in job.get("artefacts") or [] if a.get("signedUrl")]
            return (urls[0], None) if urls else (None, f"report finished without a file (job {job_id})")
        if status == "FAILED":
            return None, f"report generation failed (job {job_id})"
        if time.time() > deadline:
            return None, f"report still {str(status).lower()} after {timeout}s (job {job_id})"
        time.sleep(3)


def download_pdf(url, path):
    """Save the PDF behind the signed URL (no API key: the URL carries its own signature)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "escape-scan-report-md/2.0"}),
                                    timeout=120) as resp, open(path, "wb") as f:
            f.write(resp.read())
        return path, None
    except (urllib.error.URLError, OSError) as e:
        return None, f"PDF download failed: {getattr(e, 'reason', e)}"


# ─── Scan: start and wait ─────────────────────────────────────────────────────
SCAN_DONE = {"FINISHED", "FAILED", "CANCELED"}


def set_output(name, value):
    """Expose a value to the next GitHub Actions steps (steps.<id>.outputs.<name>)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def start_scan(api, profile_id):
    """POST /scans — start a scan of the profile, tagged with the commit that triggered the CI."""
    env = os.environ.get
    sha = env("ESCAPE_COMMIT_HASH") or env("GITHUB_SHA")
    body = {"profileId": profile_id, "initiator": "CI"}
    commit = {
        "commitHash": sha,
        "commitBranch": env("ESCAPE_COMMIT_BRANCH") or env("GITHUB_REF_NAME"),
        "commitAuthor": env("ESCAPE_COMMIT_AUTHOR") or env("GITHUB_ACTOR"),
        "commitLink": (f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{env('GITHUB_REPOSITORY')}/commit/{sha}"
                       if sha and env("GITHUB_REPOSITORY") else None),
    }
    body.update({k: v for k, v in commit.items() if v})
    scan = api.post("/scans", body)
    if not scan.get("id"):
        raise APIError(f"Escape did not return a scan id: {json.dumps(scan)[:300]}")
    return scan


def wait_scan(api, scan_id, timeout, interval=30):
    """Poll GET /scans/{id} until the scan is FINISHED, FAILED or CANCELED.
    Returns the final status, or None when `timeout` seconds elapsed first (0 = no limit).
    Polling every 30 s: the end of the scan is detected within 30 s, so the report follows quickly."""
    start = time.time()
    last_log = 0
    while True:
        try:
            scan = api.get(f"/scans/{scan_id}")
        except APIError as e:  # a transient outage must not lose hours of waiting
            print(f"⚠ {e} — retrying", file=sys.stderr)
            scan = {}
        status = scan.get("status")
        elapsed = time.time() - start
        if status in SCAN_DONE:
            print(f"✔ Scan {scan_id} {status} after {fmt_duration(elapsed * 1000)} of waiting", file=sys.stderr)
            return status
        if time.time() - last_log >= 300 or not last_log:
            progress = scan.get("progressRatio")
            pct = f" · {round(progress * 100)}%" if isinstance(progress, (int, float)) else ""
            print(f"… scan {scan_id} {status or 'unknown'}{pct} · waiting for {fmt_duration(elapsed * 1000)}",
                  file=sys.stderr, flush=True)
            last_log = time.time()
        if timeout and elapsed + interval > timeout:
            return None
        time.sleep(interval)


# ─── Helpers ──────────────────────────────────────────────────────────────────
def fmt_date(iso):
    return iso.replace("T", " ")[:16] + " UTC" if iso else "—"


def fmt_duration(ms):
    if not ms:
        return "—"
    s = int(ms / 1000)
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s % 60:02d}s"


def md_section(md, title):
    """Extract a '### Title' section from an issue's markdown context."""
    if not md:
        return ""
    m = re.search(rf"^#+\s*{title}\s*$(.*?)(?=^#+\s|\Z)", md, re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def md_to_text(md, keep_code=False):
    code = (lambda m: " `" + " ".join(m.group(1).split()).replace("`", "'") + "` ") if keep_code \
        else (lambda m: " [code example in Escape] ")
    md = re.sub(r"```[a-zA-Z]*\n?(.*?)```", code, md, flags=re.S)
    md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    md = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "• ", md, flags=re.M)
    if not keep_code:
        md = re.sub(r"[*`]{1,3}", "", md)
    return " ".join(md.split())


def shorten(text, limit):
    if limit and len(text) > limit:
        return text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def cell(text):
    """Escape a value for a Markdown table cell."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("|", "&#124;").replace("\n", " "))


def score_emoji(pct):
    return "🟢" if pct >= 80 else "🟡" if pct >= 50 else "🔴"


def score_grade(pct):
    for limit, grade in ((90, "A"), (75, "B"), (60, "C"), (40, "D")):
        if pct >= limit:
            return grade
    return "E"


def title_case(s):
    return s.replace("_", " ").title()


# ─── Data collection ──────────────────────────────────────────────────────────
def collect_coverage(api, scan_id):
    overall, by_user, total = Counter(), {}, 0
    for t in api.paginate(f"/scans/{scan_id}/targets", {"types": "API_ROUTE"}):
        route = t.get("apiRoute") or {}
        if route.get("blacklisted"):
            continue
        total += 1
        overall[route.get("coverage") or "NOT_TESTED"] += 1
        for u in route.get("coverageByUser") or []:
            by_user.setdefault(u.get("name") or "?", Counter())[u.get("coverage") or "NOT_TESTED"] += 1
    return {
        "routes_total": total,
        "overall": overall,
        "by_user": by_user,
        "pages": api.total(f"/scans/{scan_id}/targets", {"types": "WEB_PAGE"}),
        "crawled": api.total(f"/scans/{scan_id}/targets", {"types": "WEB_CRAWLED_URL"}),
    }


def collect_owasp(api, stats, open_issues):
    """Map open findings to the OWASP API Top 10 (2023) through each issue's compliance tags."""
    def owasp_items(issue):
        try:
            detail = api.get(f"/issues/{issue['id']}")
        except APIError:
            return issue, None
        return issue, [c.get("item", "") for c in detail.get("compliances") or [] if c.get("framework") == "OWASP"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        mapped = list(pool.map(owasp_items, open_issues))

    framework = next((x for x in stats.get("issue", {}).get("compliance", []) if x.get("compliance") == "OWASP"), {})
    failed = set(framework.get("failedItems") or [])
    rows = []
    for ref, name in OWASP_TOP10:
        item = f"OWASP_{ref}_2023"
        issues = [i for i, items in mapped if items and item in items]
        sevs = [i["severity"] for i in issues if i["severity"] in SEVERITIES]
        rows.append({"ref": f"{ref}:2023", "name": name, "issues": issues,
                     "worst": min(sevs, key=SEVERITIES.index) if sevs else None,
                     "failed": bool(issues) or item in failed})
    return {"enabled": framework.get("enabled", False), "rows": rows,
            "unmapped": sum(1 for _, items in mapped if items is None)}


def collect(api, profile_id, scan_id=None, want_report=True, report_timeout=600):
    profile = api.get(f"/profiles/{profile_id}")
    scan_ref = {"id": scan_id} if scan_id else (profile.get("lastResourceScan") or profile.get("lastScan"))
    if not scan_ref:
        raise APIError(f"Profile {profile_id} has no scan yet.")
    scan_id = scan_ref["id"]

    # Start the PDF export first so it renders while the rest of the data is fetched
    report_job = start_report(api, scan_id) if want_report else None

    scan = api.get(f"/scans/{scan_id}")
    stats = api.get(f"/scans/{scan_id}/statistics")
    config = api.get(f"/scans/{scan_id}/configuration").get("configuration") or {}
    history = list(api.get("/scans", {"profileIds": profile_id, "size": 100,
                                      "sortType": "createdAt", "sortDirection": "desc"}).get("data", []))
    all_issues = list(api.paginate("/issues", {"scanIds": scan_id}))
    open_issues = sorted((i for i in all_issues if i.get("status") == "OPEN"),
                         key=lambda i: (SEVERITIES.index(i["severity"]) if i["severity"] in SEVERITIES else 9,
                                        i.get("name", "")))
    coverage = collect_coverage(api, scan_id)
    owasp = collect_owasp(api, stats, open_issues)

    report = None
    if report_job:
        job_id, err = report_job
        report = wait_report(api, job_id, report_timeout) if job_id else (None, err)

    trend = sorted((s for s in history if s.get("status") == "FINISHED" and s.get("score") is not None),
                   key=lambda s: s["createdAt"])[-20:]
    score = scan.get("score")
    return {
        "profile": profile, "scan": scan, "stats": stats, "config": config,
        "all_issues": all_issues, "open_issues": open_issues, "coverage": coverage, "owasp": owasp,
        "report": report,
        "sev_counts": Counter(i["severity"] for i in open_issues),
        "score_pct": round(score * 100) if score is not None else None,
        "trend": [(s["createdAt"][:10], round(s["score"] * 100)) for s in trend],
        "categories": [(title_case(x["category"]), x["count"])
                       for x in stats.get("issue", {}).get("categories", []) if x.get("count")],
    }


# ─── Markdown sections ────────────────────────────────────────────────────────
def md_header(d, gate):
    profile, scan = d["profile"], d["scan"]
    logos = [f'<img align="{side}" src="{url}" alt="{alt}" height="{height}">' for url, alt, height, side in LOGOS]
    # Floated left / right, then cleared so the title starts below both logos
    out = ["<p>", *logos, "</p>", '<br clear="all">', ""]
    # Scans have no name of their own in Escape: they're identified by their profile's name
    out += ['<h1 align="center">Security scan recap</h1>',
            f'<h3 align="center">{html.escape(profile.get("name") or "—")}</h3>', ""]

    # Plain HTML: Markdown is not parsed inside an HTML block such as <p align="center">
    links = [f'🔗 <a href="{APP_URL}/scans/{scan["id"]}/issues/"><b>Open the scan in Escape</b></a>']
    # Never Escape's temporary download link: the PDF is downloaded and linked where it's stored.
    # In GitHub Actions it points to the run's artifacts; the workflow then swaps in the artifact's own URL.
    if d.get("pdf_href"):
        links.append(f'📄 <a href="{html.escape(d["pdf_href"])}"><b>PDF report</b></a>')
    elif d.get("pdf_error"):
        links.append(f"📄 PDF report n/a — {html.escape(d['pdf_error'])}")
    out += ['<p align="center">', " · ".join(links), "</p>", ""]

    status = scan.get("status", "—")
    st = {"FINISHED": "✅", "FAILED": "❌", "CANCELED": "⚠️"}.get(status, "⏳")
    asset = (profile.get("asset") or {}).get("name") or "—"
    out += ["| | |", "|---|---|",
            f"| **Profile** | {cell(profile.get('name'))} _({profile.get('scannerKind', '')})_ |",
            f"| **Target** | {cell(asset)} |",
            f"| **Scan** | `{scan['id']}` · {st} **{status}** · {scan.get('initiator', '').lower()} |",
            f"| **Date** | {fmt_date(scan.get('createdAt'))} · duration {fmt_duration(scan.get('duration'))} |",
            ""]
    if gate:
        out += [f"> {gate}", ""]
    return out


def md_posture(d, charts):
    out = ["## 1. Security posture", ""]
    pct = d["score_pct"]
    if pct is None:
        out += ["**Security score** n/a (scan did not finish)", ""]
    else:
        out += [f"**Security score** &nbsp; {score_emoji(pct)} **{pct}/100 · grade {score_grade(pct)}**", "",
                charts.score(d)]

    out += ["### Open findings by severity", "", charts.severities(d),
            "| Severity | Open |", "|---|--:|"]
    out += [f"| {SEV_EMOJI[s]} **{s}** | {d['sev_counts'].get(s, 0)} |" for s in SEVERITIES]
    out.append("")

    if len(d["trend"]) >= 2:
        vals = [v for _, v in d["trend"]]
        delta = vals[-1] - vals[-2]
        arrow = f"▲ +{delta}" if delta > 0 else f"▼ {delta}" if delta < 0 else "= 0"
        out += [f"### Score trend _(last {len(vals)} finished scans)_", "", charts.trend(d),
                f"vs previous scan: **{arrow}**", ""]
    return out


def md_overview(d, charts):
    out = ["## 2. Overview", ""]
    sc = d["sev_counts"]
    chips = " · ".join(f"{SEV_EMOJI[s]} {sc.get(s, 0)} {s}" for s in SEVERITIES)
    out += [f"**{len(d['open_issues'])} open findings** &nbsp; {chips}", ""]

    status_counts = Counter(i["status"] for i in d["all_issues"])
    others = [f"{n} {s.replace('_', ' ').lower()}" for s, n in status_counts.items() if s != "OPEN"]
    if others:
        out.append(f"- **Also on this scan:** {', '.join(others)}")
    if d["categories"]:
        out.append("- **Categories:** " + ", ".join(f"{k} ({n})" for k, n in d["categories"]))
    out.append("")
    if d["categories"]:
        out += [charts.categories(d)]

    config, scan = d["config"], d["scan"]
    mode = config.get("mode", "—")
    max_dur = config.get("max_duration")
    problems = scan.get("problems") or []
    errors = [p for p in problems if p.get("severity") == "ERROR"]
    warnings = [p for p in problems if p.get("severity") != "ERROR"]
    health = ("✅ no configuration error or warning" if not problems else
              f"{'❌' if errors else '✅'} **{len(errors)} error(s)**, "
              f"{'⚠️' if warnings else '✅'} **{len(warnings)} warning(s)**")
    out += ["### Configuration", "", "| | |", "|---|---|",
            f"| **Mode** | {'⚠️' if mode == 'read_write' else '✅'} `{mode}` |",
            f"| **Max duration** | {f'{max_dur} min' if max_dur else '—'} |",
            f"| **Authentication** | {'✅ configured' if config.get('authentication') else '⚠️ none (unauthenticated scan)'} |",
            f"| **Health** | {health} |", ""]
    for p in errors + warnings:
        icon = "❌" if p.get("severity") == "ERROR" else "⚠️"
        out.append(f"- {icon} **`{p.get('code', '')}`** {cell(p.get('message', ''))}")
    if problems:
        out.append("")
    return out


def md_compliance(d):
    owasp = d["owasp"]
    rows = owasp["rows"]
    failed = sum(1 for r in rows if r["failed"])
    out = ["## 3. Compliance: OWASP Top 10", "",
           f"[OWASP API Security Top 10 (2023)]({OWASP_DOC}) · "
           f"**{10 - failed}/10 categories passed** · {'❌' if failed else '✅'} **{failed} failed**", ""]
    if not owasp["enabled"]:
        out += ["> ⚠️ The OWASP framework is not enabled on this organization; the mapping below may be partial.", ""]
    out += ["| Ref | Category | Status | Open findings | Highest severity |", "|---|---|:-:|--:|---|"]
    for r in rows:
        status = "❌ **Fail**" if r["failed"] else "✅ Pass"
        worst = f"{SEV_EMOJI[r['worst']]} **{r['worst']}**" if r["worst"] else "—"
        out.append(f"| `{r['ref']}` | {r['name']} | {status} | {len(r['issues']) or '—'} | {worst} |")
    out.append("")

    failing = [r for r in rows if r["issues"]]
    if failing:
        out += ["<details><summary><b>Findings per failed OWASP category</b></summary>", ""]
        for r in failing:
            out.append(f"- **`{r['ref']}` {r['name']}**")
            for i in r["issues"]:
                link = f"{APP_URL}/all-risks/issues/{i['id']}/overview/"
                out.append(f"  - {SEV_EMOJI.get(i['severity'], '')} [{cell(i.get('name', ''))}]({link})")
        out += ["", "</details>", ""]
    if owasp["unmapped"]:
        out += [f"_{owasp['unmapped']} finding(s) could not be checked against OWASP (API error)._", ""]
    return out


def md_coverage(d, charts):
    cov = d["coverage"]
    total, ok = cov["routes_total"], cov["overall"].get("OK", 0)
    pct = round(100 * ok / total) if total else 0
    extra = f" _(+ {cov['pages']} web pages, {cov['crawled']} crawled URLs)_" if cov["pages"] or cov["crawled"] else ""
    out = ["## 4. Coverage", "", "| | |", "|---|---|",
           f"| **Endpoints discovered** | **{total}**{extra} |",
           f"| **Endpoints reached (2xx)** | 🟢 **{ok}** ({pct}%) |"]
    others = [f"{s.replace('_', ' ').lower()} {n}" for s, n in cov["overall"].most_common() if s != "OK"]
    if others:
        out.append(f"| **Other responses** | {', '.join(others)} |")
    out += ["", charts.coverage(d)]

    if cov["by_user"]:
        out += ["### Tested with users", "", charts.coverage_by_user(d),
                "| User | Reached | Tested |", "|---|--:|--:|"]
        for user, cnt in sorted(cov["by_user"].items()):
            u_total = sum(cnt.values())
            out.append(f"| 👤 **{cell(user)}** | {cnt.get('OK', 0)}/{u_total} | {u_total} |")
        out.append("")
    return out


def md_findings(d, full):
    issues = d["open_issues"]
    out = [f"## 5. Findings ({len(issues)})", ""]
    if not issues:
        return out + ["✅ **No open finding on this scan.**", ""]
    out += ["| Severity | Finding / Description | Remediation |", "|---|---|---|"]
    for issue in issues:
        sev = issue["severity"]
        ctx = issue.get("context") or ""
        desc = md_to_text(md_section(ctx, "Summary") or md_section(ctx, "Problem")
                          or md_section(ctx, "Description") or "")
        rem = md_to_text(md_section(ctx, "Remediation") or md_section(ctx, "Fix") or "", keep_code=full) \
            or "See issue details in Escape."
        link = f"{APP_URL}/all-risks/issues/{issue['id']}/overview/"
        name = cell(issue.get("name", "")).replace("[", "\\[").replace("]", "\\]")
        out.append(f"| {SEV_EMOJI.get(sev, '')} **{sev}** | **[{name}]({link})**<br>"
                   f"{cell(shorten(desc, None if full else 260))} | {cell(shorten(rem, None if full else 300))} |")
    out.append("")
    return out


def run_url():
    """URL of the current GitHub Actions run, None outside Actions."""
    if not (in_github_actions() and os.environ.get("GITHUB_RUN_ID")):
        return None
    return (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}"
            f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")


def md_footer(d):
    url = run_url()
    run = f" · [workflow run]({url})" if url else ""
    return ["---", "", f"<sub>Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} "
            f"from the Escape public API · profile `{d['profile'].get('id', '')}`{run}</sub>", ""]


def build_markdown(d, charts, gate, full):
    lines = (md_header(d, gate) + md_posture(d, charts) + md_overview(d, charts) + md_compliance(d)
             + md_coverage(d, charts) + md_findings(d, full) + md_footer(d))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


# ─── Entry point ──────────────────────────────────────────────────────────────
def run(charts_cls, default_output, description):
    load_dotenv(os.path.join(HERE, ".env"))
    load_dotenv(os.path.join(os.getcwd(), ".env"))

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--profile", default=os.environ.get("ESCAPE_PROFILE_ID"),
                        help="Escape profile id (default: ESCAPE_PROFILE_ID from env / .env)")
    parser.add_argument("--scan", help="Report on this scan id instead of the profile's last scan")
    parser.add_argument("--start-scan", action="store_true",
                        help="Start a new scan of the profile first, then report on it (use with --wait)")
    parser.add_argument("--wait", action="store_true",
                        help="Wait for the scan to be FINISHED / FAILED / CANCELED before building the report")
    parser.add_argument("--wait-timeout", type=int, metavar="SECONDS", default=0,
                        help="Stop waiting after this many seconds and exit 3 without report (default: no limit)")
    parser.add_argument("--fail-on", choices=[s.lower() for s in SEVERITIES],
                        help="Exit 1 if an open finding at or above this severity exists (CI gate)")
    parser.add_argument("--full", action="store_true", help="Do not truncate descriptions / remediations")
    parser.add_argument("--no-report", action="store_true", help="Do not generate the PDF report")
    parser.add_argument("--pdf", metavar="PATH", default=os.environ.get("ESCAPE_PDF_PATH"),
                        help="Where to save the PDF report (default: next to the Markdown, same name .pdf)")
    parser.add_argument("--pdf-timeout", type=int, metavar="SECONDS",
                        default=int(os.environ.get("ESCAPE_PDF_TIMEOUT") or 600),
                        help="How long to wait for Escape to generate the PDF (default: 600)")
    parser.add_argument("--output", default=os.path.join(HERE, default_output),
                        help=f"Markdown file to write (default: {default_output} next to the script)")
    parser.add_argument("--stdout", action="store_true", help="Also print the Markdown to stdout")
    parser.add_argument("--no-step-summary", action="store_true", help="Don't append to $GITHUB_STEP_SUMMARY")
    args = parser.parse_args()

    api_key = os.environ.get("ESCAPE_API_KEY")
    if not api_key:
        return fail("ESCAPE_API_KEY is not set (environment, CI secret or .env file).")
    if not args.profile:
        return fail("No profile id: set ESCAPE_PROFILE_ID (environment, CI variable or .env) or pass --profile.")

    api = EscapeAPI(api_key)
    scan_status = None
    try:
        if args.start_scan:
            if args.scan:
                return fail("--start-scan and --scan are mutually exclusive.")
            scan = start_scan(api, args.profile)
            args.scan = scan["id"]
            set_output("scan_id", args.scan)
            print(f"✔ Scan started: {args.scan} ({APP_URL}/scans/{args.scan}/)", file=sys.stderr)
        if args.wait:
            if not args.scan:
                profile = api.get(f"/profiles/{args.profile}")
                ref = profile.get("lastResourceScan") or profile.get("lastScan")
                if not ref:
                    return fail(f"Profile {args.profile} has no scan to wait for.")
                args.scan = ref["id"]
            set_output("scan_id", args.scan)
            scan_status = wait_scan(api, args.scan, args.wait_timeout)
            set_output("scan_status", scan_status or "RUNNING")
            if scan_status is None:
                print(f"⏳ Scan {args.scan} still running after {fmt_duration(args.wait_timeout * 1000)}: "
                      "no report yet (exit 3)", file=sys.stderr)
                return 3
        d = collect(api, args.profile, args.scan, not args.no_report, args.pdf_timeout)
    except APIError as e:
        return fail(str(e))

    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)

    # The PDF is downloaded, never linked from Escape (its signed URL expires after 6h)
    if d["report"]:
        url, err = d["report"]
        pdf_path = os.path.abspath(args.pdf or os.path.splitext(output)[0] + ".pdf")
        if url:
            os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
            saved, err = download_pdf(url, pdf_path)
        if url and saved:
            print(f"✔ PDF report saved to {pdf_path}", file=sys.stderr)
            # In GitHub Actions the PDF is uploaded as an artifact of the run (see the workflow)
            d["pdf_href"] = (f"{run_url()}#artifacts" if run_url()
                             else os.path.relpath(pdf_path, os.path.dirname(output)).replace(os.sep, "/"))
        else:
            d["pdf_error"] = err
            print(f"⚠ PDF report not available: {err}", file=sys.stderr)

    gate, code = None, 0
    if args.fail_on:
        threshold = SEVERITIES.index(args.fail_on.upper())
        blocking = sum(n for s, n in d["sev_counts"].items()
                       if s in SEVERITIES and SEVERITIES.index(s) <= threshold)
        if blocking:
            gate, code = f"❌ **CI gate failed:** {blocking} open finding(s) at or above **{args.fail_on.upper()}**", 1
        else:
            gate = f"✅ **CI gate passed:** no open finding at or above **{args.fail_on.upper()}**"

    charts = charts_cls()
    markdown = build_markdown(d, charts, gate, args.full)

    with open(output, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"✔ Report written to {output}", file=sys.stderr)
    if args.stdout:
        print(markdown)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and not args.no_step_summary:
        body = markdown
        if len(body.encode()) > GITHUB_SUMMARY_LIMIT and args.full:
            body = build_markdown(d, charts, gate, False)  # fall back to truncated texts
        if len(body.encode()) > GITHUB_SUMMARY_LIMIT:
            body = body.encode()[: GITHUB_SUMMARY_LIMIT - 200].decode(errors="ignore") + \
                "\n\n> ⚠️ Report truncated to fit GitHub's 1 MiB job summary limit. Full report in the artifact.\n"
        with open(summary, "a", encoding="utf-8") as f:
            f.write(body + "\n")

    if gate:
        plain = re.sub(r"\*\*", "", gate)
        if in_github_actions():
            print(f"::{'error' if code else 'notice'} title=Escape CI gate::{plain[2:]}", file=sys.stderr)
        print(plain, file=sys.stderr)
    if scan_status in ("FAILED", "CANCELED"):
        # The report is still published (partial results), but the run must show the scan didn't complete
        msg = f"Escape scan {args.scan} {scan_status}: the report only covers what was tested before it stopped"
        print(f"::error title=Escape scan {scan_status.lower()}::{msg}" if in_github_actions() else f"✖ {msg}",
              file=sys.stderr)
        return 4
    return code


# ─── Mermaid charts ───────────────────────────────────────────────────────────
COVERAGE_COLOR = {"OK": "#16a34a", "NOT_FOUND": "#94a3b8", "UNAUTHORIZED": "#f97316", "FORBIDDEN": "#ea580c",
                  "SERVER_ERROR": "#dc2626", "VALIDATION_ERROR": "#7c3aed", "TIMEOUT": "#0891b2",
                  "NOT_TESTED": "#cbd5e1"}
FALLBACK = ["#2563eb", "#0d9488", "#db2777", "#65a30d", "#9333ea", "#0369a1"]


def label(s):
    return str(s).replace('"', "'")


def mermaid(body, theme_vars):
    init = json.dumps({"theme": "base", "themeVariables": theme_vars})
    return f"```mermaid\n%%{{init: {init}}}%%\n{body}\n```\n"


def xychart(title, x_labels, y_title, y_max, series, palette, horizontal=False):
    """series: [('bar'|'line', [values])], drawn in order (later on top)."""
    xs = ", ".join(f'"{label(x)}"' for x in x_labels)
    body = [f"xychart-beta{' horizontal' if horizontal else ''}", f'    title "{label(title)}"',
            f"    x-axis [{xs}]", f'    y-axis "{label(y_title)}" 0 --> {max(1, y_max)}']
    body += [f"    {kind} [{', '.join(str(v) for v in values)}]" for kind, values in series]
    return mermaid("\n".join(body), {"xyChart": {"plotColorPalette": ", ".join(palette)}})


def colored_bars(title, labels, values, colors, y_title, horizontal=False):
    """Bar chart with one colour per bar. xychart colours a whole series, not a single bar: one series
    per bar, holding its value at its own position and 0 elsewhere, gives each bar its own colour.
    Mermaid draws 0 as an 8px stub: a last all-zero grey series covers the stubs as a neutral baseline."""
    series = [("bar", [v if j == i else 0 for j, v in enumerate(values)]) for i in range(len(values))]
    series.append(("bar", [0] * len(values)))
    return xychart(title, labels, y_title, max(values), series, list(colors) + ["#e5e7eb"], horizontal)


class MermaidCharts:
    """Each method returns a Markdown fragment ('' when there is nothing to draw)."""

    def score(self, d):
        # Mermaid has no gauge: a 20-cell bar keeps the score readable at a glance
        pct = d["score_pct"]
        filled = round(20 * pct / 100)
        square = {"🟢": "🟩", "🟡": "🟨", "🔴": "🟥"}[score_emoji(pct)]
        return square * filled + "⬜" * (20 - filled) + "\n"

    def severities(self, d):
        return colored_bars("Open findings by severity", SEVERITIES,
                            [d["sev_counts"].get(s, 0) for s in SEVERITIES],
                            [SEV_COLOR[s] for s in SEVERITIES], "Findings")

    def trend(self, d):
        dates = [day[5:] for day, _ in d["trend"]]
        values = [v for _, v in d["trend"]]
        return xychart("Security score per finished scan", dates, "Score", 100,
                       [("bar", values), ("line", values)], ["#c7d2fe", "#4338ca"])

    def categories(self, d):
        cats = sorted(d["categories"], key=lambda c: -c[1])
        return xychart("Open findings per category", [k for k, _ in cats], "Findings", max(n for _, n in cats),
                       [("bar", [n for _, n in cats])], ["#4338ca"], horizontal=True)

    def coverage(self, d):
        overall = d["coverage"]["overall"]
        if not overall:
            return ""
        # Bars rather than a pie: small slices of a pie get overlapping percentage labels
        extra = iter(FALLBACK)
        rows = overall.most_common()
        return colored_bars("API endpoints by response", [st.replace("_", " ").lower() for st, _ in rows],
                            [n for _, n in rows],
                            [COVERAGE_COLOR.get(st) or next(extra, "#64748b") for st, _ in rows],
                            "Endpoints", horizontal=True)

    def coverage_by_user(self, d):
        users = sorted(d["coverage"]["by_user"].items())
        tested = [sum(c.values()) for _, c in users]
        reached = [c.get("OK", 0) for _, c in users]
        # Reached bar drawn on top of the tested bar → reads as "x reached out of y tested"
        return xychart("Endpoints reached (green) out of tested (grey) per user", [u for u, _ in users],
                       "Endpoints", max(tested), [("bar", tested), ("bar", reached)], ["#cbd5e1", "#16a34a"])


if __name__ == "__main__":
    try:
        sys.exit(run(MermaidCharts, "escape-report.md",
                     "Escape — recap of the last scan of a profile, as Markdown with Mermaid charts"))
    except KeyboardInterrupt:
        sys.exit(130)
