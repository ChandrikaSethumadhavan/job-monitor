import os
import sys
import re
import yaml
import logging
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from state     import load_state, save_state, is_new_job, mark_job_seen
from scraper   import scrape_company, get_session
from discovery import fetch_arbeitnow, fetch_remotive, fetch_adzuna
from notifier  import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("main")

CONFIG_FILE = Path("config/companies.yaml")

# ── ML / Embedded relevance filter ───────────────────────────────────────────
# Applied only to broad discovery sources (Arbeitnow, Remotive, Adzuna).
# Company-specific scrapes are already curated, so no filter is applied there.

_ML_INCLUDE = re.compile(
    r'\b(machine.?learning|ml engineer|deep.?learning|neural.?network|'
    r'embedded.?ml|embedded.?ai|tinyml|tiny.?ml|edge.?ai|edge.?ml|'
    r'computer.?vision|ai.?engineer|mlops|model.?deploy|inference|'
    r'quantiz|onnx|tensorflow|pytorch|yolo|'
    r'embedded.?system|firmware|microcontroller|fpga|sensor.?fusion|'
    r'iot.?engineer|signal.?processing|image.?processing|object.?detect)\b',
    re.IGNORECASE,
)

_NON_ML_ROLES = re.compile(
    r'\b(marketing.?manager|sales.?(manager|representative|engineer)|'
    r'accountant|financial.?analyst|legal.?counsel|'
    r'talent.?acqui|customer.?success|office.?manager|'
    r'content.?writer|copywriter|graphic.?design)\b',
    re.IGNORECASE,
)

# Reject postings whose title is in German only — we want English-posted roles.
# German-speaking countries (DE/AT/CH) post many roles in German.
_GERMAN_TITLE = re.compile(
    r'\b(Entwickler|Softwareentwickler|Ingenieur|Wissenschaftler|'
    r'Mitarbeiter|Projektleiter|Berater|Fachinformatiker|'
    r'Systemingenieur|Datenanalyst|Praktikant|Werkstudent|'
    r'Absolvent|Konstrukteur|Vertrieb)\b',
)


def is_relevant(job: dict) -> bool:
    title = job.get("title", "")
    tags  = " ".join(job.get("tags", []))
    text  = f"{title} {tags}"

    if _GERMAN_TITLE.search(title):
        return False
    if _NON_ML_ROLES.search(title):
        return False
    return bool(_ML_INCLUDE.search(text))


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        logger.warning("config/companies.yaml not found — using defaults")
        return {"companies": [], "discovery": {}}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def _register(state, new_jobs, jobs, apply_filter=False):
    for job in jobs:
        if apply_filter and not is_relevant(job):
            continue
        source = job.get("source", "unknown")
        job_id = str(job.get("id") or hashlib.md5(
            f"{job.get('title','')}{job.get('url','')}".encode()).hexdigest()[:12])
        if is_new_job(state, source, job_id):
            new_jobs.append(job)
            mark_job_seen(state, source, job_id, {
                "title": job.get("title"), "url": job.get("url")})


def main():
    config  = load_config()
    state   = load_state()
    session = get_session()
    new_jobs: list = []

    # ── Company-specific career pages (curated, no ML filter needed) ──────
    companies = config.get("companies", [])
    logger.info(f"Monitoring {len(companies)} company page(s)")
    for entry in companies:
        if isinstance(entry, str):
            entry = {"url": entry, "name": entry}
        _register(state, new_jobs, scrape_company(entry, session), apply_filter=False)

    # ── Broad discovery sources (ML relevance filter applied) ─────────────
    disc = config.get("discovery", {})

    if disc.get("arbeitnow", {}).get("enabled", True):
        kw = disc["arbeitnow"].get("keywords") if isinstance(disc.get("arbeitnow"), dict) else None
        _register(state, new_jobs, fetch_arbeitnow(keywords=kw), apply_filter=True)

    if disc.get("remotive", {}).get("enabled", False):
        kw  = disc["remotive"].get("keywords")
        cat = disc["remotive"].get("category")
        _register(state, new_jobs, fetch_remotive(keywords=kw, category=cat), apply_filter=True)

    if disc.get("adzuna", {}).get("enabled", False):
        kw        = disc["adzuna"].get("keywords", "machine learning embedded")
        countries = disc["adzuna"].get("countries", ["de", "nl"])
        for cc in countries:
            _register(state, new_jobs, fetch_adzuna(cc, kw), apply_filter=True)

    logger.info(f"Total new jobs this run: {len(new_jobs)}")
    save_state(state)
    send_email(new_jobs)


if __name__ == "__main__":
    main()
