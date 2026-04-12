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


# ── Schengen location filter ──────────────────────────────────────────────────
#
# Two modes:
#   strict=False (company scrapes)  — only rejects known non-Schengen strings.
#                                     Empty / "Remote" / unknown → kept.
#   strict=True  (discovery APIs)   — requires a positive Schengen match.
#                                     Empty / "Remote" alone / "Worldwide" → rejected.

_SCHENGEN_PAT = re.compile(
    r'\b('
    r'austria|belgium|czech(?:\s+republic)?|czechia|denmark|estonia|'
    r'finland|france|germany|greece|hungary|iceland|italy|latvia|'
    r'liechtenstein|lithuania|luxembourg|malta|netherlands|norway|'
    r'poland|portugal|slovakia|slovenia|spain|sweden|switzerland|croatia|'
    r'ireland|'
    r'berlin|munich|m[uü]nchen|hamburg|frankfurt|cologne|k[oö]ln|'
    r'd[uü]sseldorf|stuttgart|freiburg|heidelberg|darmstadt|karlsruhe|'
    r'dresden|leipzig|n[uü]rnberg|mannheim|bonn|aachen|ulm|'
    r'erlangen|regensburg|augsburg|'
    r'amsterdam|rotterdam|eindhoven|delft|utrecht|the\s+hague|den\s+haag|'
    r'groningen|leiden|nijmegen|'
    r'paris|lyon|marseille|toulouse|bordeaux|grenoble|nice|rennes|'
    r'sophia\s+antipolis|saclay|'
    r'zurich|z[uü]rich|geneva|gen[eè]ve|bern|basel|lausanne|'
    r'stockholm|gothenburg|g[oö]teborg|malm[oö]|oslo|bergen|trondheim|'
    r'copenhagen|k[oø]benhavn|aarhus|helsinki|tampere|'
    r'vienna|wien|graz|linz|salzburg|'
    r'brussels|bruxelles|antwerp|ghent|leuven|li[eè]ge|luxembourg\s+city|'
    r'milan|milano|rome|roma|turin|torino|bologna|florence|'
    r'madrid|barcelona|seville|valencia|bilbao|'
    r'lisbon|lisboa|porto|'
    r'warsaw|krakow|wroc[lł]aw|prague|brno|'
    r'dublin|cork|'
    r'europe|european|eu\b'
    r')\b',
    re.IGNORECASE,
)

_NON_SCHENGEN_PAT = re.compile(
    r'\b('
    r'china|beijing|shanghai|shenzhen|guangzhou|hong\s+kong|'
    r'india|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai|'
    r'united\s+states|usa\b|u\.s\.a\.?|'
    r'new\s+york|san\s+francisco|seattle|austin|boston|chicago|'
    r'los\s+angeles|silicon\s+valley|bay\s+area|'
    r'canada|toronto|vancouver|montreal|calgary|'
    r'australia|sydney|melbourne|brisbane|'
    r'japan|tokyo|osaka|'
    r'south\s+korea|korea|seoul|'
    r'singapore|taiwan|taipei|'
    r'brazil|mexico|russia|ukraine|turkey|'
    r'uae|dubai|abu\s+dhabi|'
    r'united\s+kingdom|england|scotland|wales|'
    r'london|manchester|edinburgh|birmingham|'
    r'\buk\b|britain|british'
    r')\b',
    re.IGNORECASE,
)

_REMOTE_ANYWHERE = re.compile(
    r'\b(worldwide|anywhere|global(?!\s+delivery)|fully\s+remote(?!\s+europe)|'
    r'remote\s*[-]\s*(?:us|usa|canada|india|apac|latam))\b',
    re.IGNORECASE,
)


def is_schengen(job: dict, strict: bool = False) -> bool:
    location = (job.get("location") or "").strip()

    if not location:
        return not strict

    if _NON_SCHENGEN_PAT.search(location):
        logger.debug(f"[LOCATION-REJECT] '{location}' — {job.get('title','')}")
        return False

    if _SCHENGEN_PAT.search(location):
        return True

    if re.search(r'\bremote\b', location, re.IGNORECASE):
        if strict:
            return bool(re.search(r'\b(europe|european|eu\b|schengen)', location, re.IGNORECASE))
        return True

    if _REMOTE_ANYWHERE.search(location):
        return not strict

    return not strict


# ── ML / Embedded relevance filter (discovery sources only) ──────────────────

_ML_INCLUDE = re.compile(
    r'\b(machine.?learning|ml\s+engineer|deep.?learning|neural.?network|'
    r'embedded.?ml|embedded.?ai|tinyml|tiny.?ml|edge.?ai|edge.?ml|'
    r'computer.?vision|ai.?engineer|mlops|model.?deploy|inference|'
    r'quantiz|onnx|tensorflow|pytorch|yolo|'
    r'embedded.?system|firmware|microcontroller|fpga|sensor.?fusion|'
    r'iot.?engineer|signal.?processing|image.?processing|object.?detect|'
    r'data\s+scientist|ml\s+researcher|ai\s+researcher|research\s+engineer)\b',
    re.IGNORECASE,
)

_NON_ML_ROLES = re.compile(
    r'\b(marketing.?manager|sales.?(manager|rep\b|representative)|'
    r'accountant|financial.?analyst|legal.?counsel|legal.?advisor|'
    r'talent.?acqui|customer.?success|office.?manager|'
    r'content.?writer|copywriter|graphic.?design|ux.?designer|'
    r'product.?design(?!er))\b',
    re.IGNORECASE,
)

_GERMAN_TITLE = re.compile(
    r'\b(Entwickler|Softwareentwickler|Ingenieur|Wissenschaftler|'
    r'Mitarbeiter|Projektleiter|Berater|Fachinformatiker|'
    r'Systemingenieur|Datenanalyst|Praktikant|Werkstudent|'
    r'Absolvent|Konstrukteur|Vertrieb|Forscher)\b',
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


# ── Seniority filter ──────────────────────────────────────────────────────────
# Keeps: ML Engineer, Senior Engineer, Research Scientist, Data Scientist, Associate
# Rejects: Staff Engineer, Principal, Director, VP, Head of, Manager, Fellow

_TOO_SENIOR = re.compile(
    r'\b('
    r'staff\s+(?:engineer|scientist|ml|ai|researcher)|'
    r'principal\s+(?:engineer|scientist|researcher|ml|ai|data)|'
    r'distinguished\s+engineer|'
    r'senior\s+(?:staff|principal)\b|'
    r'\bdirector\b|'
    r'\bvp\b|vice\s+president|'
    r'head\s+of\b|'
    r'chief\s+(?:ai|ml|science|scientist|data|technology|officer)\b|'
    r'\bfellow\b|'
    r'engineering\s+manager|research\s+manager|ml\s+manager|'
    r'ai\s+manager|data\s+manager|team\s+lead(?:er)?\s+(?:ml|ai|data|embedded)'
    r')\b',
    re.IGNORECASE,
)


def is_too_senior(job: dict) -> bool:
    return bool(_TOO_SENIOR.search(job.get("title", "")))


# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        logger.warning("config/companies.yaml not found — using defaults")
        return {"companies": [], "discovery": {}}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def _register(state, new_jobs, jobs, apply_ml_filter=False, strict_location=False):
    for job in jobs:
        if not is_schengen(job, strict=strict_location):
            continue
        if is_too_senior(job):
            logger.debug(f"[SENIOR-REJECT] {job.get('title','')}")
            continue
        if apply_ml_filter and not is_relevant(job):
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

    # Company-specific pages — lenient location, no ML filter (already curated)
    companies = config.get("companies", [])
    logger.info(f"Monitoring {len(companies)} company page(s)")
    for entry in companies:
        if isinstance(entry, str):
            entry = {"url": entry, "name": entry}
        _register(state, new_jobs,
                  scrape_company(entry, session),
                  apply_ml_filter=False,
                  strict_location=False)

    # Discovery sources — strict location + ML relevance filter
    disc = config.get("discovery", {})

    if disc.get("arbeitnow", {}).get("enabled", True):
        kw = disc["arbeitnow"].get("keywords") if isinstance(disc.get("arbeitnow"), dict) else None
        _register(state, new_jobs,
                  fetch_arbeitnow(keywords=kw),
                  apply_ml_filter=True,
                  strict_location=True)

    if disc.get("remotive", {}).get("enabled", False):
        kw  = disc["remotive"].get("keywords")
        cat = disc["remotive"].get("category")
        _register(state, new_jobs,
                  fetch_remotive(keywords=kw, category=cat),
                  apply_ml_filter=True,
                  strict_location=True)

    if disc.get("adzuna", {}).get("enabled", False):
        kw        = disc["adzuna"].get("keywords", "machine learning embedded")
        countries = disc["adzuna"].get("countries", ["de", "nl"])
        for cc in countries:
            _register(state, new_jobs,
                      fetch_adzuna(cc, kw),
                      apply_ml_filter=True,
                      strict_location=True)

    logger.info(f"New jobs this run: {len(new_jobs)}")
    save_state(state)
    send_email(new_jobs)


if __name__ == "__main__":
    main()
