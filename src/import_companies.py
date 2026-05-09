"""
Merge companies from two Excel spreadsheets into config/companies.yaml.

Inputs (drop into data/imports/):
  - companies_to_monitor.xlsx  : multi-sheet, each sheet has a "links" column.
  - already_applied.xlsx       : single sheet with "Stellenanzeige" + "Unternehmen".
                                  Falls back to "Unternehmen" when Stellenanzeige is empty.

Output:
  Replaces the block between the BEGIN/END markers in config/companies.yaml.
  Existing hand-curated entries and the discovery block are preserved.

Run:
  python src/import_companies.py
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yaml
from openpyxl import load_workbook

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import_companies")

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "companies.yaml"
IMPORTS = ROOT / "data" / "imports"
FILE_MONITOR = IMPORTS / "companies_to_monitor.xlsx"
FILE_APPLIED = IMPORTS / "already_applied.xlsx"

BEGIN = "  # ── BEGIN imported (managed by import_companies.py) ──"
END   = "  # ── END imported ──"


# ── helpers ──────────────────────────────────────────────────────────────────

def _header_map(ws) -> dict[str, int]:
    """Lower-cased header → 1-based column index for the first row."""
    out: dict[str, int] = {}
    for col_idx, cell in enumerate(next(ws.iter_rows(min_row=1, max_row=1)), start=1):
        if cell.value is None:
            continue
        out[str(cell.value).strip().lower()] = col_idx
    return out


def _cell(row, col_idx: int | None) -> str:
    if col_idx is None or col_idx - 1 >= len(row):
        return ""
    v = row[col_idx - 1].value
    return "" if v is None else str(v).strip()


def _normalize_url(raw: str) -> str | None:
    """Return canonical URL, or None if not URL-like."""
    s = (raw or "").strip()
    if not s or s.lower() in {"nan", "none", "-"}:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        if "." not in s or " " in s:
            return None
        s = "https://" + s
    try:
        p = urlparse(s)
        if not p.netloc:
            return None
    except Exception:
        return None
    host = p.netloc.lower()
    path = p.path.rstrip("/")
    return f"{p.scheme.lower()}://{host}{path}"


def _name_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^(www|jobs|careers|career|join|apply|boards|hire|hiring)\.", "", host)
    core = host.split(".")[0] if host else url
    return core.replace("-", " ").replace("_", " ").title() or url


def _entries_from_monitor_workbook(path: Path) -> list[dict]:
    if not path.exists():
        log.warning("not found: %s — skipping", path)
        return []
    wb = load_workbook(path, read_only=True, data_only=True)
    out: list[dict] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        headers = _header_map(ws)
        link_col = headers.get("links") or headers.get("link") or headers.get("url")
        if not link_col:
            log.info("sheet %r in %s has no links/url column — skipping",
                     sheet_name, path.name)
            continue
        for row in ws.iter_rows(min_row=2):
            url = _normalize_url(_cell(row, link_col))
            if not url:
                continue
            out.append({"url": url, "name": _name_from_url(url),
                        "_src": f"{path.name}:{sheet_name}"})
    return out


def _entries_from_applied_workbook(path: Path) -> list[dict]:
    if not path.exists():
        log.warning("not found: %s — skipping", path)
        return []
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    headers = _header_map(ws)
    link_col = headers.get("stellenanzeige")
    name_col = headers.get("unternehmen")
    if link_col is None and name_col is None:
        log.warning("%s has neither Stellenanzeige nor Unternehmen header", path.name)
        return []
    out: list[dict] = []
    for row in ws.iter_rows(min_row=2):
        url = _normalize_url(_cell(row, link_col)) if link_col else None
        company = _cell(row, name_col) if name_col else ""
        if url:
            out.append({"url": url,
                        "name": company or _name_from_url(url),
                        "_src": f"{path.name}:applied"})
        elif company:
            # No URL — keep as a name-only marker. Bot won't scrape it (no URL),
            # but it's recorded in the YAML for visibility.
            out.append({"url": "", "name": company, "_src": f"{path.name}:applied"})
    return out


# ── existing-yaml awareness ──────────────────────────────────────────────────

def _existing_urls(text: str) -> set[str]:
    """Parse the YAML and collect every existing company URL (normalized)."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        log.error("companies.yaml is not valid YAML: %s", e)
        sys.exit(1)
    urls: set[str] = set()
    for entry in data.get("companies", []) or []:
        if isinstance(entry, dict) and entry.get("url"):
            n = _normalize_url(entry["url"])
            if n:
                urls.add(n)
        elif isinstance(entry, str):
            n = _normalize_url(entry)
            if n:
                urls.add(n)
    return urls


def _strip_managed_block(text: str) -> str:
    pat = re.compile(
        re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?",
        re.DOTALL,
    )
    return pat.sub("", text)


def _render_block(entries: list[dict]) -> str:
    lines = [BEGIN]
    for e in entries:
        name = (e["name"] or "").replace('"', "'")
        src  = e.get("_src", "")
        if e["url"]:
            lines.append(f'  - url: {e["url"]}')
            lines.append(f'    name: "{name}"  # from {src}')
        else:
            lines.append(f'  # (no URL) {name}  # from {src}')
    lines.append(END)
    return "\n".join(lines) + "\n"


def _insert_block(text: str, block: str) -> str:
    """Insert block at the end of the `companies:` list, before the discovery section."""
    text = _strip_managed_block(text)

    # Anchor: the `discovery:` top-level key. Walk back over any immediately
    # preceding comment / blank lines so the imported block sits cleanly
    # inside the companies list, not glued to the divider above discovery.
    m = re.search(r"^discovery:", text, re.MULTILINE)
    if m:
        idx = m.start()
        # Rewind past contiguous comment lines and blank lines above `discovery:`.
        while idx > 0:
            prev_nl = text.rfind("\n", 0, idx - 1)
            line_start = prev_nl + 1 if prev_nl != -1 else 0
            line = text[line_start:idx].rstrip("\r\n")
            if line.strip() == "" or line.lstrip().startswith("#"):
                idx = line_start
                if prev_nl == -1:
                    break
            else:
                break
        return text[:idx] + block + "\n" + text[idx:]

    # Fallback: append at file end.
    if not text.endswith("\n"):
        text += "\n"
    return text + block


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    if not CONFIG.exists():
        log.error("missing %s", CONFIG)
        return 1

    raw_entries = (
        _entries_from_monitor_workbook(FILE_MONITOR)
        + _entries_from_applied_workbook(FILE_APPLIED)
    )
    log.info("rows read from spreadsheets: %d", len(raw_entries))

    yaml_text = CONFIG.read_text(encoding="utf-8")
    existing = _existing_urls(_strip_managed_block(yaml_text))

    seen: set[str] = set()
    deduped: list[dict] = []
    skipped_existing = 0
    for e in raw_entries:
        key = e["url"] or f"name::{e['name'].lower()}"
        if key in seen:
            continue
        seen.add(key)
        if e["url"] and e["url"] in existing:
            skipped_existing += 1
            continue
        deduped.append(e)

    log.info("after dedup: %d new, %d already in companies.yaml",
             len(deduped), skipped_existing)

    block = _render_block(deduped)
    new_text = _insert_block(yaml_text, block)

    # Sanity-check the result is still valid YAML before writing.
    try:
        yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        log.error("refusing to write — generated YAML is invalid: %s", e)
        return 1

    # Atomic write: encode to a temp file in the same dir, then replace.
    # Avoids truncating CONFIG if encoding/IO fails mid-write.
    fd, tmp_path = tempfile.mkstemp(
        dir=str(CONFIG.parent), prefix=".companies.", suffix=".yaml.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        os.replace(tmp_path, CONFIG)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    log.info("updated %s", CONFIG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
