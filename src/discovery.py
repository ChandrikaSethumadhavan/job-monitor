import time
import random
import os
import requests
import logging

logger = logging.getLogger(__name__)

_UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
]

def _headers():
    return {"User-Agent": random.choice(_UA)}

# ── Arbeitnow (completely free, EU focused, no API key) ───────────────────────

def fetch_arbeitnow(keywords: list = None) -> list:
    jobs = []
    for page in range(1, 4):
        try:
            r = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                headers=_headers(),
                timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("data", [])
            if not items:
                break
            for j in items:
                if keywords:
                    text = f"{j.get('title','')} {' '.join(j.get('tags',[]))}".lower()
                    if not any(kw.lower() in text for kw in keywords):
                        continue
                jobs.append({
                    "id": j.get("slug", ""),
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("location", ""),
                    "url": j.get("url", ""),
                    "remote": j.get("remote", False),
                    "source": "arbeitnow",
                    "tags": j.get("tags", []),
                })
            time.sleep(random.uniform(2.0, 4.0))
        except Exception as e:
            logger.error(f"Arbeitnow page {page}: {e}")
            break
    logger.info(f"Arbeitnow: {len(jobs)} jobs")
    return jobs

# ── Remotive (free, remote jobs globally) ────────────────────────────────────

def fetch_remotive(keywords: list = None, category: str = None) -> list:
    params = {}
    if keywords:
        params["search"] = " ".join(keywords)
    if category:
        params["category"] = category
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs",
            params=params,
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        jobs = [{
            "id": str(j["id"]),
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": j.get("candidate_required_location", "Remote"),
            "url": j.get("url", ""),
            "remote": True,
            "source": "remotive",
            "salary": j.get("salary", ""),
        } for j in r.json().get("jobs", [])[:100]]
        logger.info(f"Remotive: {len(jobs)} jobs")
        return jobs
    except Exception as e:
        logger.error(f"Remotive: {e}")
        return []

# ── Adzuna (free API key from developer.adzuna.com, 250 calls/day) ────────────
# Country codes: gb de nl fr se no dk fi at be ch pl es it pt ie

def fetch_adzuna(country: str, keywords: str, pages: int = 2) -> list:
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    api_key = os.environ.get("ADZUNA_API_KEY", "")
    if not app_id or not api_key:
        logger.warning("ADZUNA_APP_ID / ADZUNA_API_KEY not set, skipping")
        return []

    jobs = []
    for page in range(1, pages + 1):
        try:
            r = requests.get(
                f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
                params={
                    "app_id": app_id,
                    "app_key": api_key,
                    "results_per_page": 50,
                    "what": keywords,
                    "content-type": "application/json",
                },
                timeout=15,
            )
            r.raise_for_status()
            for j in r.json().get("results", []):
                jobs.append({
                    "id": str(j.get("id", "")),
                    "title": j.get("title", ""),
                    "company": j.get("company", {}).get("display_name", ""),
                    "location": j.get("location", {}).get("display_name", ""),
                    "url": j.get("redirect_url", ""),
                    "source": f"adzuna:{country}",
                    "salary_min": j.get("salary_min"),
                    "salary_max": j.get("salary_max"),
                })
            time.sleep(1.0)
        except Exception as e:
            logger.error(f"Adzuna {country} p{page}: {e}")
            break

    logger.info(f"Adzuna {country}: {len(jobs)} jobs")
    return jobs