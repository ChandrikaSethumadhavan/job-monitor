import re
import time
import random
import hashlib
import requests
from bs4 import BeautifulSoup
from typing import Optional
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:122.0) Gecko/20100101 Firefox/122.0",
]


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def detect_ats(url: str, html: str) -> Optional[tuple]:
    """Detect which ATS platform is used and return (platform, slug)."""
    combined = url + " " + html

    m = re.search(r'boards\.greenhouse\.io/([A-Za-z0-9_\-]+)', combined)
    if m:
        return ("greenhouse", m.group(1).split("/")[0])

    m = re.search(r'jobs\.lever\.co/([A-Za-z0-9_\-]+)', combined)
    if m:
        return ("lever", m.group(1).split("/")[0])

    m = re.search(r'jobs\.ashbyhq\.com/([A-Za-z0-9_\-]+)', combined)
    if m:
        return ("ashby", m.group(1).split("/")[0])

    m = re.search(r'apply\.workable\.com/([A-Za-z0-9_\-]+)', combined)
    if m:
        return ("workable", m.group(1).split("/")[0])

    # SmartRecruiters — used by Bosch, Continental, Siemens, etc.
    m = re.search(r'jobs\.smartrecruiters\.com/([A-Za-z0-9_\-]+)', combined)
    if m:
        return ("smartrecruiters", m.group(1).split("/")[0])

    return None


# ── ATS-specific JSON / API fetchers ─────────────────────────────────────────

def fetch_greenhouse(slug: str, session: requests.Session) -> list:
    url = f"https://boards.greenhouse.io/{slug}/jobs.json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        return [{
            "id": str(j["id"]),
            "title": j["title"],
            "company": slug,
            "location": j.get("location", {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "source": f"greenhouse:{slug}",
        } for j in r.json().get("jobs", [])]
    except Exception as e:
        logger.error(f"Greenhouse {slug}: {e}")
        return []


def fetch_lever(slug: str, session: requests.Session) -> list:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        return [{
            "id": j["id"],
            "title": j["text"],
            "company": slug,
            "location": j.get("categories", {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "source": f"lever:{slug}",
        } for j in r.json()]
    except Exception as e:
        logger.error(f"Lever {slug}: {e}")
        return []


def fetch_ashby(slug: str, session: requests.Session) -> list:
    api = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams"
    query = """
    query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
      jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
        jobPostings {
          id title locationName isRemote externalLink
          jobLocation { locationStr }
        }
      }
    }"""
    try:
        r = session.post(api, json={
            "operationName": "ApiJobBoardWithTeams",
            "variables": {"organizationHostedJobsPageName": slug},
            "query": query,
        }, timeout=15)
        r.raise_for_status()
        postings = r.json().get("data", {}).get("jobBoard", {}).get("jobPostings", [])
        return [{
            "id": j["id"],
            "title": j["title"],
            "company": slug,
            "location": j.get("locationName") or j.get("jobLocation", {}).get("locationStr", ""),
            "url": j.get("externalLink") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}",
            "source": f"ashby:{slug}",
        } for j in postings]
    except Exception as e:
        logger.error(f"Ashby {slug}: {e}")
        return []


def fetch_workable(slug: str, session: requests.Session) -> list:
    url = f"https://apply.workable.com/{slug}/"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        jobs = []
        for item in soup.select("[data-ui='job']"):
            title_el = item.select_one("h3, [data-ui='job-title']")
            link_el  = item.find("a", href=True)
            loc_el   = item.select_one("[data-ui='job-location'], span")
            if title_el and link_el:
                href = link_el["href"]
                if not href.startswith("http"):
                    href = f"https://apply.workable.com{href}"
                jobs.append({
                    "id": hashlib.md5(href.encode()).hexdigest()[:12],
                    "title": title_el.get_text(strip=True),
                    "company": slug,
                    "location": loc_el.get_text(strip=True) if loc_el else "",
                    "url": href,
                    "source": f"workable:{slug}",
                })
        return jobs
    except Exception as e:
        logger.error(f"Workable {slug}: {e}")
        return []


def fetch_smartrecruiters(slug: str, session: requests.Session) -> list:
    """
    SmartRecruiters public API — used by Bosch (BoschGroup), Continental,
    Siemens, and many other large industrial companies.
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    try:
        r = session.get(url, params={"limit": 100}, timeout=15)
        r.raise_for_status()
        jobs = []
        for j in r.json().get("content", []):
            loc = j.get("location", {})
            city    = loc.get("city", "")
            country = loc.get("country", "")
            location = f"{city}, {country}".strip(", ")
            jobs.append({
                "id": j["id"],
                "title": j["name"],
                "company": j.get("company", {}).get("name", slug),
                "location": location,
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                "source": f"smartrecruiters:{slug}",
            })
        return jobs
    except Exception as e:
        logger.error(f"SmartRecruiters {slug}: {e}")
        return []


# ── Generic HTML scraper (fallback for unknown portals) ──────────────────────

def fetch_generic(url: str, session: requests.Session) -> list:
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        job_re = re.compile(
            r'\b(job|career|position|opening|role|vacancy|vacature|stelle)\b', re.I
        )
        base = urlparse(url)
        seen, jobs = set(), []

        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            if not text or not (5 < len(text) < 120):
                continue
            if not job_re.search(href + " " + text):
                continue
            if href.startswith("/"):
                href = f"{base.scheme}://{base.netloc}{href}"
            elif not href.startswith("http"):
                continue
            uid = hashlib.md5(text.lower().encode()).hexdigest()[:12]
            if uid not in seen:
                seen.add(uid)
                jobs.append({
                    "id": uid,
                    "title": text,
                    "company": base.netloc,
                    "location": "",
                    "url": href,
                    "source": f"generic:{base.netloc}",
                })

        return jobs[:60]
    except Exception as e:
        logger.error(f"Generic scrape {url}: {e}")
        return []


# ── Main entry point ──────────────────────────────────────────────────────────

_ATS_FETCHERS = {
    "greenhouse":      fetch_greenhouse,
    "lever":           fetch_lever,
    "ashby":           fetch_ashby,
    "workable":        fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
}


def scrape_company(entry: dict, session: requests.Session) -> list:
    url  = entry["url"]
    name = entry.get("name", url)
    time.sleep(random.uniform(2.0, 5.0))   # polite delay between company checks
    logger.info(f"Scraping: {name}")

    # 1. Does the URL itself point directly to a known ATS?
    ats = detect_ats(url, "")
    if ats:
        return _ATS_FETCHERS.get(ats[0], lambda *_: [])(ats[1], session)

    # 2. Fetch the page and check for embedded ATS iframes / scripts
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        ats = detect_ats("", r.text)
        if ats:
            time.sleep(random.uniform(1.0, 3.0))
            return _ATS_FETCHERS.get(ats[0], lambda *_: [])(ats[1], session)
        # 3. Last resort: generic link scraper
        return fetch_generic(url, session)
    except Exception as e:
        logger.error(f"Could not load {url}: {e}")
        return []
