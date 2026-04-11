import json
from pathlib import Path

STATE_FILE = Path("data/seen_jobs.json")

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"jobs": {}}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def is_new_job(state: dict, source: str, job_id: str) -> bool:
    return str(job_id) not in state["jobs"].get(source, {})

def mark_job_seen(state: dict, source: str, job_id: str, meta: dict):
    state["jobs"].setdefault(source, {})[str(job_id)] = meta