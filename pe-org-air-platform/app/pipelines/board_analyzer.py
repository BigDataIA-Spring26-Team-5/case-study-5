"""
Task 5.0d: Board Composition Analyzer (Case Study 3)
"""
from __future__ import annotations

import argparse
import json
import structlog
from multiprocessing import context
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.s3_storage import S3StorageService, get_s3_service
from app.repositories.document_repository import DocumentRepository
from app.config.company_mappings import CompanyRegistry
from app.pipelines.board_io import ProxyData, load_proxy_data

logger = structlog.get_logger()

D = Decimal

# ── Data Models ──────────────────────────────────────────────

@dataclass
class BoardMember:
    name: str
    title: str
    committees: List[str]
    bio: str
    is_independent: bool
    tenure_years: int

@dataclass
class GovernanceSignal:
    company_id: str
    ticker: str
    has_tech_committee: bool
    has_ai_expertise: bool
    has_data_officer: bool
    has_risk_tech_oversight: bool
    has_ai_in_strategy: bool
    tech_expertise_count: int
    independent_ratio: Decimal
    governance_score: Decimal
    confidence: Decimal
    ai_experts: List[str] = field(default_factory=list)
    relevant_committees: List[str] = field(default_factory=list)
    board_members: List[dict] = field(default_factory=list)

# ── Text Helpers ─────────────────────────────────────────────

_BAD_NAME_TOKENS = {
    "item", "form", "proxy", "statement", "required", "business",
    "annual", "meeting", "proposal", "section", "table", "schedule",
    "total", "fiscal", "year", "held", "date", "stock", "shares",
    "vote", "voting", "record", "notice", "appendix", "part", "page",
    "corporation", "company", "group", "inc", "llc", "ltd",
    "committee", "management", "international", "capital", "partners",
    "foundation", "university", "institute", "national", "services",
}
_MONTHS = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}
_CHECKMARK = "\u00fc"
_ZWSP = "\u200b"

_BAD_NAME_PHRASES = {
    "morgan stanley", "american airlines", "denver broncos", "goldman sachs",
    "bank of america", "wells fargo", "general electric", "dollar general",
    "jpmorgan", "walmart", "target corp", "united health", "caterpillar",
    "sandler selects", "blackrock", "vanguard", "fidelity", "berkshire",
    "best buy", "recteq", "insulet", "accenture", "jump plus",
    "la grenade", "ernst young",
}

_COMMITTEE_PAT = re.compile(
    r"\b("
    r"(?:Audit|Compensation|Nominating|Governance|Corporate Governance|Risk|Technology|Digital|Innovation|IT|"
    r"Cybersecurity|Science|Sustainability|Public Responsibility|ESG|Data|AI|Artificial Intelligence|"
    r"E[- ]?Commerce|Omnichannel|Supply Chain)"
    r"(?:\s*(?:and|&|,)\s*(?:Audit|Compensation|Nominating|Governance|Corporate Governance|Risk|Technology|Digital|"
    r"Innovation|IT|Cybersecurity|Sustainability|Public Responsibility|ESG|Data|AI|E[- ]?Commerce|Omnichannel|"
    r"Supply Chain))*"
    r"\s+Committee"
    r")\b",
    re.IGNORECASE,
)
_AGE_PAT = re.compile(r'Age\s*:?\s*(\d{2})', re.I)
_SINCE_PAT = re.compile(r'(?:Director\s+[Ss]ince|Joined\s+the\s+Board)\s*:?\s*(\d{4})', re.I)

def _strip_zwsp(text: str) -> str:
    return text.replace(_ZWSP, "").replace("\u00a0", " ").strip()

def _clean_cell(cell) -> str:
    if not cell:
        return ""
    return _strip_zwsp(str(cell))

def _table_text(table: dict, max_rows: int = 8) -> str:
    parts = [str(h) for h in table.get("headers", [])]
    for row in table.get("rows", [])[:max_rows]:
        if isinstance(row, list):
            parts.extend(str(c) for c in row)
        else:
            parts.append(str(row))
    return " ".join(parts)

def _looks_like_org_name(name: str) -> bool:
    nl = name.lower().strip()
    for phrase in _BAD_NAME_PHRASES:
        if phrase in nl:
            return True
    for suffix in ["inc", "corp", "llc", "ltd", "co.", "group", "partners",
                   "airlines", "broncos", "stanley", "sachs", "selects",
                   "stores", "grills", "corporation"]:
        if nl.endswith(suffix) or (" " + suffix + " ") in nl:
            return True
    return False

def _is_plausible_person_name(name: str) -> bool:
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) < 5 or len(name) > 60:
        return False
    parts = name.split()
    if len(parts) < 2 or len(parts) > 5:
        return False
    for p in parts:
        pc = p.replace(".", "").replace(",", "").replace("'", "").replace("\u2019", "")
        if not pc:
            continue
        if not pc[0].isalpha() or not pc[0].isupper():
            return False
        if pc.lower() in _MONTHS or pc.lower() in _BAD_NAME_TOKENS:
            return False
        if sum(ch.isdigit() for ch in pc) > 0:
            return False
    return True

def extract_committees(text: str) -> List[str]:
    seen, comms = set(), []
    for m in _COMMITTEE_PAT.finditer(text):
        name = " ".join(m.group(1).split())
        key = name.lower()
        if key not in seen:
            seen.add(key)
            comms.append(name)
    return comms

def _parse_bio_details(bio: str) -> Tuple[str, bool, int, List[str]]:
    bio_lower = bio.lower()
    title = "Director"
    for kws, t in [
        (["president and ceo", "chief executive"], "President and CEO"),
        (["chief financial", " cfo "], "CFO"),
        (["chief technology", " cto "], "CTO"),
        (["chief information", " cio "], "CIO"),
        (["chief data officer", " cdo "], "Chief Data Officer"),
        (["chief ai officer", " caio "], "Chief AI Officer"),
        (["chief analytics", "cao", "chief digital"], "Chief Digital/Analytics Officer"),
        (["lead independent"], "Lead Independent Director"),
        (["chairman"], "Chairman"),
    ]:
        found = False
        for kw in kws:
            idx = bio_lower.find(kw)
            if idx != -1:
                prefix = bio_lower[max(0, idx - 40):idx]
                if "retired" in prefix or "former" in prefix:
                    continue
                title = t
                found = True
                break
        if found:
            break
    is_indep = "independent" in bio_lower and "not independent" not in bio_lower
    tenure = 0
    since = re.search(r'\bdirector\s+since\s*:?\s*(\d{4})\b', bio_lower)
    if since:
        try:
            tenure = max(0, 2026 - int(since.group(1)))
        except ValueError:
            pass
    comms = []
    for c in ["audit", "compensation", "nominating", "governance", "risk", "technology",
              "digital", "innovation", "cybersecurity", "sustainability", "esg", "data", "ai",
              "e-commerce", "omnichannel", "supply chain"]:
        if c in bio_lower:
            comms.append(c.title() + " Committee")
    return title, is_indep, tenure, comms

def extract_strategy_text(text: str) -> str:
    t = text.lower()
    for anchor in ["strategic priorities", "our strategy", "business strategy", "strategy"]:
        idx = t.find(anchor)
        if idx != -1:
            return text[idx:idx + 6000]
    for anchor in ["artificial intelligence", "machine learning", "generative ai", "ai ",
                    "accelerated computing", "chief technology officer", "gpu", "data center"]:
        idx = t.find(anchor)
        if idx != -1:
            start = max(0, idx - 1500)
            return text[start:start + 6000]
    return ""

def _split_name_from_title(raw: str) -> Tuple[str, str]:
    lines = raw.replace('\n', '|').split('|')
    if len(lines) >= 2:
        cand = lines[0].strip()
        cand = re.sub(r'\(.*?\)', '', cand).strip()
        for sfx in ["Lead Independent Director", "Independent Director", "Director"]:
            if cand.endswith(sfx):
                cand = cand[:cand.rfind(sfx)].strip()
        if _is_plausible_person_name(cand):
            return cand, " ".join(lines[1:]).strip()
    m = re.search(
        r'([a-z.])([A-Z](?:Retired|Chairman|President|CEO|Chief|Executive|Senior|Former|Partner|Member|Lead|Director|EVP|SVP))',
        raw,
    )
    if m:
        np = raw[:m.start() + 1].strip()
        tp = raw[m.start() + 1:].strip()
        np = re.sub(r'\(.*?\)', '', np).strip()
        if _is_plausible_person_name(np):
            return np, tp
    am = re.search(r'\bAge\s*:?\s*\d', raw, re.I)
    if am:
        before = raw[:am.start()].strip()
        before = re.sub(r'Director\s+Since\s*:\s*\d{4}', '', before, flags=re.I).strip()
        before = before.replace('\n', ' ')
        before = re.sub(r'([a-z])([A-Z])', r'\1 \2', before)
        cand = before.title() if before == before.upper() else before
        cand = re.sub(r'\s+', ' ', cand).strip().rstrip(',.')
        if _is_plausible_person_name(cand):
            return cand, raw[am.start():].strip()
    cleaned = re.sub(r'\(.*?\)', '', raw).strip()
    for sfx in ["Lead Independent Director", "Independent Director", "Director"]:
        if cleaned.endswith(sfx):
            cleaned = cleaned[:cleaned.rfind(sfx)].strip()
    return cleaned, ""

# ── Generic Table Detection ──────────────────────────────────

def _is_director_summary_table(table: dict) -> bool:
    headers = [str(h).lower().strip() for h in table.get("headers", [])]
    rows = table.get("rows", [])
    header_text = " ".join(headers)
    row0_text = ""
    if rows:
        row0 = rows[0] if isinstance(rows[0], list) else [rows[0]]
        row0_text = " ".join(_strip_zwsp(str(c)).lower() for c in row0)
    combined = header_text + " " + row0_text
    has_name = any(w in combined for w in ["name", "nominee"])
    has_age_label = "age" in combined
    has_enough_rows = table.get("row_count", len(rows)) >= 5
    if not (has_name and has_age_label and has_enough_rows):
        return False
    age_count = 0
    for row in rows[:15]:
        if not isinstance(row, list):
            continue
        for cell in row:
            c = _strip_zwsp(str(cell))
            if c.isdigit() and 30 <= int(c) <= 99:
                age_count += 1
                break
    return age_count >= 3

def _is_bio_table(table: dict) -> bool:
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers and not rows:
        return False
    all_text = _table_text(table, max_rows=6)
    has_age = bool(re.search(r'\bAge\s*:?\s*\d{2}', all_text, re.I))
    if not has_age:
        return False
    has_director = bool(re.search(
        r'(?:Director\s+[Ss]ince|Joined\s+the\s+Board|Independent|Biography|Committee|Birthplace)',
        all_text, re.I,
    ))
    row_count = table.get("row_count", len(rows))
    return has_age and (has_director or row_count <= 8)

# ── Generic Summary Table Extraction ─────────────────────────

def _find_column_indices(header_cells: list) -> dict:
    indices = {"name": -1, "age": -1, "since": -1, "indep": -1, "occ": -1}
    for i, h in enumerate(header_cells):
        hl = str(h).lower().strip()
        if any(w in hl for w in ["name", "nominee", "director"]) and indices["name"] == -1:
            indices["name"] = i
        elif "age" in hl and indices["age"] == -1:
            indices["age"] = i
        elif "since" in hl and indices["since"] == -1:
            indices["since"] = i
        elif "independent" in hl and indices["indep"] == -1:
            indices["indep"] = i
        elif any(w in hl for w in ["occupation", "principal"]) and indices["occ"] == -1:
            indices["occ"] = i
    if indices["name"] == -1:
        indices["name"] = 0
    return indices

def _extract_from_summary_table(table: dict) -> List[BoardMember]:
    headers = [str(h) for h in table.get("headers", [])]
    rows = table.get("rows", [])
    header_text_clean = _strip_zwsp(" ".join(headers))
    data_rows = rows
    if len(header_text_clean) < 10 and rows:
        first_row = rows[0] if isinstance(rows[0], list) else [rows[0]]
        first_row_text = _strip_zwsp(" ".join(str(c) for c in first_row))
        if "name" in first_row_text.lower() or "age" in first_row_text.lower():
            headers = [_strip_zwsp(str(c)) for c in first_row]
            data_rows = rows[1:]
    idx = _find_column_indices([h.lower() for h in headers])
    members = []
    for row in data_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        clean_row = [_clean_cell(c) for c in row]
        raw = clean_row[idx["name"]] if idx["name"] < len(clean_row) else ""
        if not raw or len(raw) < 3:
            for ci in range(len(clean_row)):
                c = clean_row[ci]
                if c and len(c) > 3 and not c.isdigit():
                    raw = c
                    break
        if not raw or len(raw) < 3:
            continue
        cell_tenure = 0
        cs = re.search(r'(?:Director\s+since|since)\s+(\d{4})', raw, re.I)
        if cs:
            cell_tenure = max(0, 2026 - int(cs.group(1)))
        name_cleaned, title_from_cell = _split_name_from_title(raw)
        if any(s in name_cleaned.lower() for s in ["(1)", "(2)", "(3)", "footnote", "qualified", "previously"]):
            continue
        if _looks_like_org_name(name_cleaned):
            continue
        if not _is_plausible_person_name(name_cleaned):
            continue
        age = 0
        if idx["age"] >= 0 and idx["age"] < len(clean_row):
            a = clean_row[idx["age"]]
            if a.isdigit() and 30 <= int(a) <= 99:
                age = int(a)
        if age == 0:
            for cell in clean_row:
                if cell.isdigit() and 30 <= int(cell) <= 99:
                    age = int(cell)
                    break
        tenure = cell_tenure
        if tenure == 0 and idx["since"] >= 0 and idx["since"] < len(clean_row):
            s = clean_row[idx["since"]]
            if s.isdigit() and len(s) == 4:
                tenure = max(0, 2026 - int(s))
        if tenure == 0:
            for cell in clean_row:
                if cell.isdigit() and len(cell) == 4 and 1980 <= int(cell) <= 2026:
                    tenure = max(0, 2026 - int(cell))
                    break
        is_indep = False
        if idx["indep"] >= 0 and idx["indep"] < len(clean_row):
            v = clean_row[idx["indep"]]
            is_indep = _CHECKMARK in v or v.lower() in ("yes", "true", "x")
        if not is_indep:
            for ci in range(min(5, len(clean_row)), min(10, len(clean_row))):
                if ci < len(clean_row) and _CHECKMARK in str(row[ci]):
                    is_indep = True
                    break
        if not is_indep and "independent" in raw.lower():
            is_indep = True
        title = "Director"
        if idx["occ"] >= 0 and idx["occ"] < len(clean_row):
            occ = clean_row[idx["occ"]]
            if occ and len(occ) > 5 and occ != _CHECKMARK:
                title = occ
        elif title_from_cell:
            title = title_from_cell[:100]
        members.append(BoardMember(
            name=name_cleaned, title=title, committees=[],
            bio="", is_independent=is_indep, tenure_years=tenure,
        ))
    return members

# ── Generic Bio Table Extraction ─────────────────────────────

def _extract_name_from_bio_table(table: dict) -> Optional[str]:
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    for h in headers:
        hs = re.sub(r'\s+', ' ', _strip_zwsp(str(h)))
        if len(hs) < 4:
            continue
        age_match = re.search(r'\bAge\s*:?\s*\d{2}', hs, re.I)
        if age_match:
            before = hs[:age_match.start()].strip()
            before = re.sub(r'(?:INDEPENDENT\s+)?DIRECTOR.*', '', before, flags=re.I).strip()
            before = re.sub(r'Biography.*', '', before, flags=re.I).strip()
            before = re.sub(r'Birthplace.*', '', before, flags=re.I).strip()
            before = re.sub(r'Director\s+Since\s*:\s*\d{4}', '', before, flags=re.I).strip()
            before = re.sub(r'([a-z])([A-Z])', r'\1 \2', before)
            before = re.sub(r'([A-Z]{2,})([A-Z][a-z])', r'\1 \2', before)
            cand = before.title() if before == before.upper() else before
            cand = re.sub(r'\s+', ' ', cand).strip().rstrip(',.')
            if _is_plausible_person_name(cand):
                return cand
        indep_match = re.search(r'(INDEPENDENT\s+DIRECTOR|Lead\s+Independent)', hs, re.I)
        if indep_match:
            before = hs[:indep_match.start()].strip()
            before = re.sub(r'([a-z])([A-Z])', r'\1 \2', before).strip()
            cand = before.title() if before == before.upper() else before
            cand = re.sub(r'\s+', ' ', cand).strip()
            if _is_plausible_person_name(cand):
                return cand
        cand = re.sub(r'\(.*?\)', '', hs).strip()
        cand = cand.title() if cand == cand.upper() else cand
        if _is_plausible_person_name(cand) and len(cand) < 40:
            return cand
    for row in rows[:3]:
        if not isinstance(row, list):
            continue
        for cell in row:
            cs = _strip_zwsp(str(cell))
            if len(cs) < 4 or len(cs) > 60:
                continue
            if re.search(r'\bAge\s*:', cs, re.I):
                continue
            if len(cs) > 50 or '\n' in cs:
                continue
            cand = cs.title() if cs == cs.upper() else cs
            cand = re.sub(r'\s+', ' ', cand).strip()
            if _is_plausible_person_name(cand):
                return cand
    return None

def _extract_from_bio_tables(tables: List[dict]) -> List[BoardMember]:
    members = []
    seen_names = set()
    for table in tables:
        if not _is_bio_table(table):
            continue
        name = _extract_name_from_bio_table(table)
        if not name:
            continue
        name_key = name.lower().strip()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        all_text = _table_text(table, max_rows=20)
        title, is_indep, tenure, comms = _parse_bio_details(all_text[:2000])
        if "independent" in all_text.lower()[:500]:
            is_indep = True
        lower_text = all_text.lower()
        if any(w in lower_text for w in [
            "chairman and chief executive officer of jpmorgan",
            "chairman and ceo, ge aerospace",
            "ceo,dollar general", "ceo, dollar general",
        ]):
            is_indep = False
        if tenure == 0:
            sm = _SINCE_PAT.search(all_text)
            if sm:
                try:
                    tenure = max(0, 2026 - int(sm.group(1)))
                except ValueError:
                    pass
        members.append(BoardMember(
            name=name, title=title, committees=comms,
            bio=all_text[:800], is_independent=is_indep, tenure_years=tenure,
        ))
    return members

# ── Regex Fallback ───────────────────────────────────────────

_BIO_PAT = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2})\b"
    r"(?:\s*,)?\s*(?:Age|age)\s*(\d{2})\b"
    r"(.*?)(?=\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}\b(?:\s*,)?\s*(?:Age|age)\s*\d{2}\b|$)",
    re.DOTALL,
)
_HONORIFIC_PAT = re.compile(
    r"\b(?:Mr\.|Ms\.|Mrs\.|Dr\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
)

def _extract_members_regex_fallback(text: str) -> List[BoardMember]:
    members, seen = [], set()
    for m in _BIO_PAT.finditer(text):
        name = " ".join(m.group(1).split())
        if not _is_plausible_person_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        bio = (m.group(3) or "").strip()
        if any(bad in bio.lower()[:200] for bad in ["table of contents", "proposal", "item 1", "item 7"]):
            continue
        seen.add(key)
        title, indep, tenure, comms = _parse_bio_details(bio[:1200])
        members.append(BoardMember(name=name, title=title, committees=comms, bio=bio[:600], is_independent=indep, tenure_years=tenure))
        if len(members) >= 40:
            break
    if len(members) < 3:
        for m in _HONORIFIC_PAT.finditer(text):
            name = " ".join(m.group(1).split())
            if not _is_plausible_person_name(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            start = max(0, m.start() - 120)
            ctx = text[start:m.end() + 600]
            title, indep, tenure, comms = _parse_bio_details(ctx)
            members.append(BoardMember(name=name, title=title, committees=comms, bio=ctx[:600], is_independent=indep, tenure_years=tenure))
            if len(members) >= 25:
                break
    return members

def _enrich_with_bios(members: List[BoardMember], text: str) -> List[BoardMember]:
    if not text:
        return members
    text_lower = text.lower()
    for m in members:
        parts = m.name.split()
        last = parts[-1].lower() if parts else ""
        if len(last) < 3:
            continue
        idx = text_lower.find(last)
        if idx == -1:
            continue
        start = max(0, idx - 200)
        end = min(len(text), idx + 2000)
        bio_window = text[start:end]
        if len(bio_window) > len(m.bio):
            m.bio = bio_window[:1200]
        if m.title == "Director":
            t, _, _, _ = _parse_bio_details(m.bio)
            if t != "Director":
                m.title = t
    return members

# ── Company-Specific Extractors ──────────────────────────────
def _extract_jpm_directors(tables: List[dict]) -> List[BoardMember]:
    """
    Extract JPM directors from the nominee table.

    JPM Table structure (table_index 21):
    - Headers (5): [Nominee..., Age, Principal Occupation, Other Directorships, Committee Membership]
    - Rows (6 cells each): ["", "NameTitleDirector since YYYY", "age", "occupation", "count", "committees"]
    - Note: rows have 6 cells but headers have 5 — rows have a leading empty cell offset
    """
    summary = None
    for t in tables:
        ht = " ".join(str(h).lower() for h in t.get("headers", []))
        if "nominee" in ht and t.get("row_count", 0) >= 10:
            summary = t
            break

    if not summary:
        for t in tables:
            headers = [str(h).lower() for h in t.get("headers", [])]
            rows = t.get("rows", [])
            if t.get("row_count", len(rows)) >= 10:
                has_age = any("age" in h for h in headers)
                director_count = sum(
                    1 for r in rows if isinstance(r, list) and
                    any("director since" in str(c).lower() for c in r)
                )
                if has_age and director_count >= 5:
                    summary = t
                    break

    if not summary:
        return []

    rows = summary.get("rows", [])
    members = []

    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        clean = [_clean_cell(c) for c in row]

        # Find the name cell (contains "Director since")
        name_cell = ""
        name_idx = -1
        for ci, c in enumerate(clean):
            if "director since" in c.lower() and len(c) > 10:
                name_cell = c
                name_idx = ci
                break

        if not name_cell:
            continue

        # Extract name: everything before "Director since" (with optional "Lead Independent" prefix)
        ds_match = re.search(r'(?:Lead\s+Independent\s+)?Director\s+since', name_cell, re.I)
        if ds_match:
            name = name_cell[:ds_match.start()].strip()
        else:
            name = name_cell

        # Fix camelCase boundary where name runs into title word
        camel = re.search(r'([a-z])([A-Z])', name)
        if camel:
            after = name[camel.start() + 1:]
            title_words = ["lead", "director", "chairman", "president", "retired",
                           "chief", "senior", "co", "executive", "former"]
            if any(after.lower().startswith(tw) for tw in title_words):
                name = name[:camel.start() + 1]

        name = name.strip().rstrip(',.')
        if not _is_plausible_person_name(name):
            continue

        # Age: cell right after name cell
        age = 0
        age_idx = name_idx + 1
        if age_idx < len(clean) and clean[age_idx].isdigit():
            a = int(clean[age_idx])
            if 30 <= a <= 99:
                age = a

        # Occupation: cell after age
        occupation = ""
        occ_idx = name_idx + 2
        if occ_idx < len(clean) and len(clean[occ_idx]) > 5 and not clean[occ_idx].isdigit():
            occupation = clean[occ_idx]

        # Committees: cell after directorships count (name_idx + 4)
        comms = []
        comm_idx = name_idx + 4
        if comm_idx < len(clean) and len(clean[comm_idx]) > 2:
            comm_text = clean[comm_idx]
            comms = [c.strip() for c in comm_text.split(";") if c.strip()]

        # Tenure from "Director since YYYY"
        tenure = 0
        sm = re.search(r'Director\s+since\s*:?\s*(\d{4})', name_cell, re.I)
        if sm:
            tenure = max(0, 2026 - int(sm.group(1)))

        # Independence: only JPMorgan's own CEO is non-independent
        is_indep = True
        occ_lower = occupation.lower()
        if "jpmorgan" in occ_lower and ("chief executive" in occ_lower or "ceo" in occ_lower):
            is_indep = False

        # Title
        title = "Director"
        if "lead independent" in name_cell.lower():
            title = "Lead Independent Director"
        elif not is_indep and "chairman" in occ_lower and "chief executive" in occ_lower:
            title = "Chairman and CEO"
        elif occupation:
            title = occupation[:100]

        members.append(BoardMember(
            name=name,
            title=title,
            committees=comms,
            bio=occupation,
            is_independent=is_indep,
            tenure_years=tenure,
        ))

    cnt = len(members)
    logger.info("[JPM] JPM-specific extractor: %d directors", cnt)
    return members

def _extract_ge_directors(tables: List[dict]) -> List[BoardMember]:
    members = []
    seen = set()
    for table in tables:
        for h in table.get("headers", []):
            hs = re.sub(r'\s+', ' ', _strip_zwsp(str(h)))
            if len(hs) < 20:
                continue
            age_m = re.search(r'Age\s*:\s*(\d{2})', hs, re.I)
            since_m = re.search(r'Director\s+Since\s*:\s*(\d{4})', hs, re.I)
            if not age_m:
                continue
            cut = since_m.start() if since_m else age_m.start()
            name_raw = hs[:cut].strip()
            name_raw = re.sub(r'INDEPENDENT\s*$', '', name_raw, flags=re.I).strip()
            name_raw = re.sub(r'([a-z])([A-Z])', r'\1 \2', name_raw)
            name_raw = re.sub(r'\s+', ' ', name_raw).strip().rstrip(',.')
            candidate = name_raw.title() if name_raw == name_raw.upper() else name_raw
            if not _is_plausible_person_name(candidate):
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            tenure = 0
            if since_m:
                tenure = max(0, 2026 - int(since_m.group(1)))
            is_indep = "independent" in hs.lower()
            rows = table.get("rows", [])
            all_text = " ".join(
                str(c) for row in rows for c in (row if isinstance(row, list) else [row])
            )
            title, _, _, comms = _parse_bio_details(all_text[:2000])
            if "chairman" in hs.lower() and "ceo" in hs.lower() and "independent" not in hs.lower():
                is_indep = False
            members.append(BoardMember(
                name=candidate, title=title, committees=comms,
                bio=all_text[:800], is_independent=is_indep, tenure_years=tenure,
            ))
    cnt = len(members)
    logger.info("[GE] GE-specific extractor: %d directors", cnt)
    return members

def _extract_dg_directors(tables: List[dict]) -> List[BoardMember]:
    """
    Extract DG directors from tables.
    
    DG tables have pervasive ZWSP characters. The summary table (table_index 9) has:
    - Headers: mostly ZWSP, with "DirectorSince(CalendarYear)" and "CommitteeMemberships"
    - Row 0: real headers ["Name and Principal Occupation", "Independent", "Age", ...]
    - Data rows: director data at detected cell positions:
      - Name+Title concatenated in one cell
      - Age as a 2-digit number
      - Director Since as a 4-digit year
    """
    # Strategy 1: Find and parse the summary table
    summary = _find_dg_summary_table(tables)
    if summary:
        members = _parse_dg_summary(summary)
        if len(members) >= 7:
            logger.info("[DG] DG summary extractor: %d directors", len(members))
            return members

    # Strategy 2: Extract from bio tables
    members = _extract_dg_from_bio_tables(tables)
    if len(members) >= 5:
        return members

    return members
def _find_dg_summary_table(tables: List[dict]) -> Optional[dict]:
    """Find the DG director summary table despite ZWSP-heavy format."""
    for t in tables:
        rows = t.get("rows", [])
        if len(rows) < 8:
            continue

        # Combine headers and row 0 text (after stripping ZWSP)
        all_headers = " ".join(_clean_cell(h) for h in t.get("headers", []))
        row0_text = ""
        if rows:
            row0 = rows[0] if isinstance(rows[0], list) else [rows[0]]
            row0_text = " ".join(_clean_cell(c) for c in row0)

        combined = (all_headers + " " + row0_text).lower()

        has_name = "name" in combined and ("principal" in combined or "occupation" in combined)
        has_age = "age" in combined
        has_since = "directorsince" in combined.replace(" ", "") or "director since" in combined
        has_committee = "committee" in combined

        if has_name and has_age and (has_since or has_committee):
            # Verify data rows have ages
            age_count = 0
            for row in rows[1:]:
                if not isinstance(row, list):
                    continue
                for cell in row:
                    c = _clean_cell(cell)
                    if c.isdigit() and 30 <= int(c) <= 99:
                        age_count += 1
                        break
            if age_count >= 5:
                logger.info("[DG] Found DG summary table (%d data rows, %d with ages)", len(rows) - 1, age_count)
                return t

    return None

def _parse_dg_summary(table: dict) -> List[BoardMember]:
    """Parse DG summary table with ZWSP-heavy format using dynamic column detection."""
    rows = table.get("rows", [])
    if not rows:
        return []

    # Detect column positions from row 0 (header row) and first data row
    name_col = -1
    age_col = -1
    since_col = -1

    # Check row 0 for column labels
    row0 = rows[0] if isinstance(rows[0], list) else [rows[0]]
    for i, cell in enumerate(row0):
        c = _clean_cell(cell).lower()
        if "name" in c and ("principal" in c or "occupation" in c):
            name_col = i
        elif c == "age":
            age_col = i

    # Detect since_col from data rows (4-digit year)
    for row in rows[1:3]:
        if not isinstance(row, list):
            continue
        clean = [_clean_cell(c) for c in row]
        for i, c in enumerate(clean):
            if c.isdigit() and len(c) == 4 and 2000 <= int(c) <= 2026 and i != age_col:
                since_col = i
                break
        if since_col >= 0:
            break

    # Fallback: detect name_col from data rows (longest non-empty cell)
    if name_col == -1:
        for row in rows[1:3]:
            if not isinstance(row, list):
                continue
            clean = [_clean_cell(c) for c in row]
            best_len = 0
            for i, c in enumerate(clean):
                if len(c) > best_len and not c.isdigit():
                    best_len = len(c)
                    name_col = i

    members = []

    for row in rows[1:]:  # Skip header row
        if not isinstance(row, list):
            continue
        clean = [_clean_cell(c) for c in row]

        # Get the name cell
        name_cell = clean[name_col] if name_col >= 0 and name_col < len(clean) else ""
        if not name_cell or len(name_cell) < 3:
            continue

        # Skip continuation rows like "CEO,Dollar General Corporation"
        if name_cell.startswith("CEO,") or name_cell.startswith("Chief "):
            continue

        # Extract name from concatenated cell
        name, title_part = _extract_dg_name_from_cell(name_cell)
        if not name:
            continue

        # Get age
        age = 0
        if age_col >= 0 and age_col < len(clean):
            a = clean[age_col]
            if a.isdigit() and 30 <= int(a) <= 99:
                age = int(a)

        # Get tenure
        tenure = 0
        if since_col >= 0 and since_col < len(clean):
            s = clean[since_col]
            if s.isdigit() and len(s) == 4:
                tenure = max(0, 2026 - int(s))

        # Independence: all DG directors are independent except the CEO (Vasos)
        is_indep = True
        if "vasos" in name.lower():
            is_indep = False
        if title_part:
            tp_lower = title_part.lower()
            if "dollar general" in tp_lower and ("ceo" in tp_lower or "chief executive" in tp_lower):
                is_indep = False

        # Title
        title = "Director"
        if "chairman" in (title_part or "").lower() and "dollar general" in (title_part or "").lower():
            title = "Chairman"
        elif title_part:
            title = title_part.split(",")[0].strip()[:80]

        members.append(BoardMember(
            name=name,
            title=title,
            committees=[],
            bio=title_part or "",
            is_independent=is_indep,
            tenure_years=tenure,
        ))

    return members

def _extract_dg_name_from_cell(cell_text: str) -> Tuple[str, str]:
    """
    Extract person name from DG concatenated cell like:
    "Warren F. BryantRetired Chairman, President & CEO,Longs Drug Stores Corporation"
    "Todd J. Vasos"
    """
    text = cell_text.strip()
    if not text:
        return "", ""

    # If it's just a clean name, return it
    if len(text) < 30 and _is_plausible_person_name(text):
        return text, ""

    # Find camelCase boundary where name ends and title begins
    title_starts = [
        "Retired", "Chairman", "President", "CEO,", "Chief", "Executive",
        "Senior", "EVP,", "SVP,", "CFO", "COO", "CIO", "Former",
        "Co-Chief", "Managing",
    ]

    best_cut = len(text)
    for ts in title_starts:
        idx = text.find(ts)
        if idx > 3 and idx < best_cut:
            if idx > 0 and text[idx - 1].islower():
                best_cut = idx

    if best_cut < len(text):
        name = text[:best_cut].strip()
        title_part = text[best_cut:].strip()
    else:
        m = re.search(r'([a-z])([A-Z][a-z])', text)
        if m and m.start() > 5:
            name = text[:m.start() + 1].strip()
            title_part = text[m.start() + 1:].strip()
        else:
            name = text
            title_part = ""

    name = name.strip().rstrip(',.')

    if not _is_plausible_person_name(name):
        return "", title_part
    if _looks_like_org_name(name):
        return "", title_part

    return name, title_part


def _extract_dg_from_bio_tables(tables: List[dict]) -> List[BoardMember]:
    """DG bio tables: header like 'WARRENF. BRYANTAge:79Director Since:2009'."""
    members = []
    seen = set()
    for table in tables:
        for h in table.get("headers", []):
            hs = re.sub(r'\s+', ' ', _strip_zwsp(str(h)))
            if len(hs) < 15:
                continue
            age_m = re.search(r'Age\s*:\s*(\d{2})', hs, re.I)
            since_m = re.search(r'Director\s+Since\s*:\s*(\d{4})', hs, re.I)
            if not age_m:
                continue
            # Cut at Age (not Director Since) to get clean name
            cut = age_m.start()
            name_raw = hs[:cut].strip()
            # Fix concatenated names: "WARRENF." → "WARREN F."
            name_raw = re.sub(r'([A-Z])([A-Z]\.)', r'\1 \2', name_raw)
            name_raw = re.sub(r'([a-z])([A-Z])', r'\1 \2', name_raw)
            name_raw = re.sub(r'\s+', ' ', name_raw).strip().rstrip(',.')
            candidate = name_raw.title() if name_raw == name_raw.upper() else name_raw
            if not _is_plausible_person_name(candidate):
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            tenure = 0
            if since_m:
                tenure = max(0, 2026 - int(since_m.group(1)))
            is_indep = True
            if "vasos" in candidate.lower():
                is_indep = False
            rows = table.get("rows", [])
            all_text = " ".join(
                _clean_cell(c) for row in rows for c in (row if isinstance(row, list) else [row])
            )
            title, _, _, comms = _parse_bio_details(all_text[:2000])
            members.append(BoardMember(
                name=candidate, title=title, committees=comms,
                bio=all_text[:800], is_independent=is_indep, tenure_years=tenure,
            ))
    cnt = len(members)
    logger.info("[DG] DG bio table extractor: %d directors", cnt)
    return members

# ── Main Orchestrator ────────────────────────────────────────

def extract_board_from_proxy_data(proxy: ProxyData) -> Tuple[List[BoardMember], List[str]]:
    ticker = proxy.ticker
    members: List[BoardMember] = []

    if ticker == "JPM" and proxy.tables:
        members = _extract_jpm_directors(proxy.tables)
    elif ticker == "GE" and proxy.tables:
        members = _extract_ge_directors(proxy.tables)
    elif ticker == "DG" and proxy.tables:
        members = _extract_dg_directors(proxy.tables)

    if len(members) < 3:
        for table in proxy.tables:
            if _is_director_summary_table(table):
                logger.info("[%s] Found director summary table (%s rows)", ticker, table.get('row_count', '?'))
                extracted = _extract_from_summary_table(table)
                if len(extracted) >= 3:
                    members = extracted
                    logger.info("[%s] Extracted %d directors from summary table", ticker, len(members))
                    break

    if len(members) < 3 and proxy.tables:
        bio_members = _extract_from_bio_tables(proxy.tables)
        if len(bio_members) > len(members):
            logger.info("[%s] Extracted %d directors from bio tables", ticker, len(bio_members))
            members = bio_members

    if len(members) < 3:
        logger.info("[%s] Table extraction insufficient, using regex fallback", ticker)
        members = _extract_members_regex_fallback(proxy.text_content)

    members = _enrich_with_bios(members, proxy.text_content)
    logger.info("[%s] Final board: %d members", ticker, len(members))

    committees = extract_committees(proxy.text_content)
    for table in proxy.tables:
        for h in table.get("headers", []):
            if "committee" in str(h).lower() and len(str(h).strip()) > 5:
                name = str(h).strip()
                if name.lower() not in [c.lower() for c in committees]:
                    committees.append(name)
    return members, committees

# ── Analyzer ─────────────────────────────────────────────────

class BoardCompositionAnalyzer:
    AI_EXPERTISE_KEYWORDS = [
        "artificial intelligence", "machine learning",
        "chief data officer", "cdo", "caio", "chief ai",
        "chief technology", "cto", "chief digital",
        "data science", "analytics", "digital transformation",
        "generative ai", "genai", "deep learning",
        "computer vision", "natural language processing",
        "supply chain", "logistics", "warehouse automation",
        "automation", "robotics", "predictive maintenance",
        "advanced analytics", "mlops", "responsible ai",
         # Semiconductor / GPU domain
        "semiconductor", "chip design", "gpu",
        "accelerated computing", "high performance computing",
        "parallel computing", "graphics processing",
        "cuda", "tensor", "inference",
        "software engineering", "systems engineering",
        "cloud infrastructure", "platform engineering",
    ]
    TECH_COMMITTEE_NAMES = [
        "technology committee", "digital committee",
        "innovation committee", "it committee",
        "technology and cybersecurity", "technology & cybersecurity",
        "cybersecurity committee", "data committee",
        "ai committee", "e-commerce committee",
        "ecommerce committee", "supply chain committee",
        "technology and ecommerce",
    ]
    DATA_OFFICER_TITLES = [
        "chief data officer", "cdo", "chief ai officer", "caio",
        "chief analytics officer", "cao", "chief digital officer",
        "chief technology officer", "cto",
         # Senior tech leadership (equivalent at tech companies)
        "svp of software", "svp software engineering",
        "vp of deep learning", "vp deep learning",
        "vp of ai", "svp of ai",
        "vp of engineering", "svp of engineering",
        "evp of engineering",
        "svp of gpu", "vp of gpu",
        "head of engineering", "head of software",
        "svp of research", "vp of research",
    ]
    RISK_TECH_WORDS = [
        "technology", "cyber", "digital", "information security",
        "security", "data", "privacy", "ai",
    ]
    AI_STRATEGY_KEYWORDS = [
        "artificial intelligence", "machine learning",
        "generative ai", "genai", "ai strategy", "ai-driven",
        "automation", "advanced analytics", "data science",
        "computer vision", "optimization",
        # Domain-specific (NVDA proxy uses these instead of "AI")
        "accelerated computing", "deep learning",
        "gpu", "inference", "data center",
        "ai platform", "ai infrastructure",
        "neural network", "large language model",
        "foundation model", "full-stack", "cuda",
    ]
  
    def __init__(self, s3: Optional[S3StorageService] = None, doc_repo: Optional[DocumentRepository] = None):
        self.s3 = s3
        self.doc_repo = doc_repo
        self._last_evidence_trail: Dict[str, dict] = {}
        self._extraction_context = None

    def set_extraction_context(self, context: Dict[str, Any]) -> None:
            """Set LLM-extracted governance context."""
            self._extraction_context = context
            logger.info(f"  ✅ Set extraction context: {len(context.get('directors', []))} directors")
        
    def get_extraction_context(self) -> Optional[Dict[str, Any]]:
            """Return current extraction context if set."""
            return self._extraction_context
        
    def clear_extraction_context(self) -> None:
            """Clear extraction context after analysis."""
            self._extraction_context = None

    def analyze_board(self, company_id: str, ticker: str, members: List[BoardMember], committees: List[str], strategy_text: str = "", full_proxy_text: str = "") -> GovernanceSignal:
        score = D("20")
        relevant_comms: List[str] = []
        trail: Dict[str, dict] = {}

        matched_tc = [c for c in committees if any(tc in c.lower() for tc in self.TECH_COMMITTEE_NAMES)]
        has_tech = len(matched_tc) > 0
        if has_tech:
            score += D("15")
        relevant_comms.extend(matched_tc)
        trail["tech_committee"] = {"points": 15 if has_tech else 0, "max_points": 15, "triggered": has_tech, "matched_committees": matched_tc}

        ai_experts = []
        for m in members:
            combined = (m.title + " " + m.bio).lower()
            if any(kw in combined for kw in self.AI_EXPERTISE_KEYWORDS):
                ai_experts.append(m.name)
        has_ai = len(ai_experts) > 0
        if has_ai:
            score += D("20")
        trail["ai_expertise"] = {"points": 20 if has_ai else 0, "max_points": 20, "triggered": has_ai, "expert_count": len(ai_experts)}

        has_officer = False
        for m in members:
            combined = (m.title + " " + m.bio).lower()
            if any(dt in combined for dt in self.DATA_OFFICER_TITLES):
                if any(w in combined for w in ["chief", "officer", "vp", "svp", "head", "president"]):
                    has_officer = True
                    break
        # Also search full proxy text for exec officer titles
        # (CTO, CDO etc. are executive officers, not always board members)
        search_text = full_proxy_text or strategy_text
        if not has_officer and search_text:
            proxy_lower = search_text.lower()
            for dt in self.DATA_OFFICER_TITLES:
                if dt in proxy_lower:
                    idx = proxy_lower.find(dt)
                    context = proxy_lower[max(0, idx - 60):idx + 80]
                    if "former" not in context and "retired" not in context and "prior" not in context:
                        has_officer = True
                        break
        if has_officer:
            score += D("15")
        trail["data_officer"] = {"points": 15 if has_officer else 0, "max_points": 15, "triggered": has_officer}

       # NEW CODE - Uses LLM context if available:
        indep_ratio = D("0")
        indep_names, non_indep = [], []

        # Check if LLM extracted better independence data
        llm_context = self.get_extraction_context()
        if llm_context and llm_context.get("board_size", 0) > 0:
            # Use LLM-extracted independence data
            llm_indep_count = llm_context.get("independent_count", 0)
            llm_board_size = llm_context.get("board_size", 0)
            llm_indep_ratio = D(str(llm_indep_count)) / D(str(llm_board_size))
            
            logger.info(f"[{ticker}]   🤖 LLM independence: {llm_indep_count}/{llm_board_size} = {llm_indep_ratio:.2%}")
            
            # If LLM found directors, prefer its independence data
            if llm_board_size >= len(members):
                indep_ratio = llm_indep_ratio.quantize(D("0.0001"), rounding=ROUND_HALF_UP)
                logger.info(f"[{ticker}]   ✅ Using LLM independence ratio")
            else:
                # LLM found fewer directors than regex, use regex
                if members:
                    for m in members:
                        (indep_names if m.is_independent else non_indep).append(m.name)
                    indep_ratio = (D(str(len(indep_names))) / D(str(len(members)))).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
        else:
            # No LLM context, use regex extraction
            if members:
                for m in members:
                    (indep_names if m.is_independent else non_indep).append(m.name)
                indep_ratio = (D(str(len(indep_names))) / D(str(len(members)))).quantize(D("0.0001"), rounding=ROUND_HALF_UP)

        ratio_pass = indep_ratio > D("0.5")
        if ratio_pass:
            score += D("10")
        trail["independent_ratio"] = {
            "points": 10 if ratio_pass else 0,
            "max_points": 10,
            "triggered": ratio_pass,
            "ratio": float(indep_ratio),
            "independent_count": llm_context.get("independent_count", len(indep_names)) if llm_context else len(indep_names),
            "total_directors": llm_context.get("board_size", len(members)) if llm_context else len(members),
        }
        has_risk_tech = False
        for c in committees:
            cl = c.lower()
            if "risk" in cl and any(w in cl for w in self.RISK_TECH_WORDS):
                has_risk_tech = True
                relevant_comms.append(c)
                break
        if not has_risk_tech:
            for m in members:
                for mc in m.committees:
                    if "risk" in mc.lower() and any(w in mc.lower() for w in self.RISK_TECH_WORDS):
                        has_risk_tech = True
                        break
                if has_risk_tech:
                    break
        if not has_risk_tech and full_proxy_text:
            pt_lower = full_proxy_text.lower()
            if ("oversight" in pt_lower or "oversee" in pt_lower) and any(w in pt_lower for w in ["technology risk", "cybersecurity risk", "information security risk"]):
                has_risk_tech = True
        if has_risk_tech:
            score += D("10")
        trail["risk_tech_oversight"] = {"points": 10 if has_risk_tech else 0, "max_points": 10, "triggered": has_risk_tech}

        has_ai_strat = False
        strat_matches = []
        ai_search_text = full_proxy_text or strategy_text
        if ai_search_text:
            strat_matches = [kw for kw in self.AI_STRATEGY_KEYWORDS if kw in ai_search_text.lower()]
            has_ai_strat = len(strat_matches) > 0
        if has_ai_strat:
            score += D("10")
        trail["ai_in_strategy"] = {"points": 10 if has_ai_strat else 0, "max_points": 10, "triggered": has_ai_strat, "matched_keywords": strat_matches}

        score = min(score, D("100"))
        confidence = min(D("0.50") + D(str(len(members))) / D("20"), D("0.95")).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
        self._last_evidence_trail = trail
        relevant_comms = list(dict.fromkeys(relevant_comms))

        return GovernanceSignal(
            company_id=company_id, ticker=ticker.upper(),
            has_tech_committee=has_tech, has_ai_expertise=has_ai,
            has_data_officer=has_officer, has_risk_tech_oversight=has_risk_tech,
            has_ai_in_strategy=has_ai_strat, tech_expertise_count=len(ai_experts),
            independent_ratio=indep_ratio, governance_score=score, confidence=confidence,
            ai_experts=ai_experts[:25], relevant_committees=relevant_comms[:25],
            board_members=[
                {"name": m.name, "title": m.title, "is_independent": m.is_independent,
                 "tenure_years": m.tenure_years, "committees": m.committees[:6]}
                for m in members[:15]
            ],
        )

    def scrape_and_analyze(self, ticker: str, company_id: Optional[str] = None, use_s3: bool = True) -> GovernanceSignal:
        ticker = ticker.upper()
        info = CompanyRegistry.get(ticker)
        cid = company_id or ticker
        logger.info("=== Board analysis: %s (%s) ===", ticker, info['name'])
        proxy = load_proxy_data(ticker, s3=self.s3, doc_repo=self.doc_repo, use_s3=use_s3)
        logger.info("[%s] Proxy: %s chars, %d tables", ticker, f"{len(proxy.text_content):,}", len(proxy.tables))
        members, committees = extract_board_from_proxy_data(proxy)
        logger.info("[%s] Committees: %s", ticker, committees[:10])
        for m in members[:3]:
            logger.info("[%s]   %s | %s | indep=%s | tenure=%dy", ticker, m.name, m.title, m.is_independent, m.tenure_years)
        # strategy_text = extract_strategy_text(proxy.text_content)
        # logger.info("[%s] Strategy window: %s chars", ticker, f"{len(strategy_text):,}")
        # signal = self.analyze_board(cid, ticker, members, committees, strategy_text)

        strategy_text = extract_strategy_text(proxy.text_content)
        logger.info("[%s] Strategy window: %s chars", ticker, f"{len(strategy_text):,}")
        signal = self.analyze_board(cid, ticker, members, committees, strategy_text, full_proxy_text=proxy.text_content)
        logger.info("[%s] GOVERNANCE SCORE: %s/100  (confidence: %s)", ticker, signal.governance_score, signal.confidence)
        logger.info("[%s]   Base:                  20", ticker)
        logger.info("[%s]   Tech Committee:        %s", ticker, "YES (+15)" if signal.has_tech_committee else "NO  (+0)")
        logger.info("[%s]   AI Expertise:          %s  [%d expert(s)]", ticker, "YES (+20)" if signal.has_ai_expertise else "NO  (+0)", signal.tech_expertise_count)
        logger.info("[%s]   CAIO/CDO/CTO:          %s", ticker, "YES (+15)" if signal.has_data_officer else "NO  (+0)")
        logger.info("[%s]   Independent > 50%%:     %s  [ratio=%s]", ticker, "YES (+10)" if signal.independent_ratio > D('0.5') else "NO  (+0)", signal.independent_ratio)
        logger.info("[%s]   Risk+Tech Oversight:   %s", ticker, "YES (+10)" if signal.has_risk_tech_oversight else "NO  (+0)")
        logger.info("[%s]   AI in Strategy:        %s", ticker, "YES (+10)" if signal.has_ai_in_strategy else "NO  (+0)")
        return signal

    def get_last_evidence_trail(self) -> Dict[str, dict]:
        return self._last_evidence_trail

    def analyze_multiple(self, tickers: List[str], use_s3: bool = True, delay: float = 1.0) -> Dict[str, GovernanceSignal]:
        results: Dict[str, GovernanceSignal] = {}
        for i, t in enumerate(tickers):
            ticker = t.upper()
            try:
                results[ticker] = self.scrape_and_analyze(ticker, use_s3=use_s3)
            except Exception as e:
                logger.error("[%s] FAILED: %s", ticker, e)
            if i < len(tickers) - 1:
                time.sleep(delay)
        return results

# ── Output Helpers ───────────────────────────────────────────

def _signal_to_dict(signal: GovernanceSignal) -> dict:
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in signal.__dict__.items()}

def print_signal(signal: GovernanceSignal):
    info = CompanyRegistry.get(signal.ticker)
    logger.info("=" * 60)
    logger.info("  BOARD GOVERNANCE - %s (%s)", signal.ticker, info['name'])
    logger.info("=" * 60)
    logger.info("  Score: %s/100  Confidence: %s", signal.governance_score, signal.confidence)

def save_signal(signal: GovernanceSignal, out_dir: str = "results") -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{signal.ticker.lower()}_governance.json"
    path.write_text(json.dumps(_signal_to_dict(signal), indent=2, default=str), encoding="utf-8")
    logger.info("[%s] Saved locally -> %s", signal.ticker, path)
    return path

def save_signal_to_s3(signal: GovernanceSignal, evidence_trail: Optional[Dict[str, dict]] = None, s3: Optional[S3StorageService] = None) -> str:
    if s3 is None:
        s3 = get_s3_service()
    data = _signal_to_dict(signal)
    from datetime import datetime, timezone
    data["_meta"] = {
        "signal_type": "board_composition",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "CS3 Task 5.0d",
        "score_breakdown": evidence_trail or {},
    }
    s3_key = s3.store_signal_data(signal_type="board_composition", ticker=signal.ticker.upper(), data=data)
    logger.info("[%s] Saved to S3 -> %s", signal.ticker, s3_key)
    return s3_key

def print_summary_table(results: Dict[str, GovernanceSignal]):
    if not results:
        return
    logger.info("=" * 60)
    for t, s in sorted(results.items(), key=lambda x: x[1].governance_score, reverse=True):
        logger.info("  %-8s %5s  %5.1f%%  %5s   %5s", t, s.governance_score, float(s.independent_ratio)*100, s.tech_expertise_count, s.confidence)
    logger.info("=" * 60)

# ── CLI ──────────────────────────────────────────────────────

DEFAULT_5 = ["NVDA", "JPM", "WMT", "GE", "DG"]

def main():
    parser = argparse.ArgumentParser(description="CS3 Board Composition Analyzer")
    parser.add_argument("tickers", nargs="*", help="Tickers to analyze")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    tickers = CompanyRegistry.all_tickers() if args.all else ([t.upper() for t in args.tickers] if args.tickers else DEFAULT_5)
    analyzer = BoardCompositionAnalyzer()
    results = analyzer.analyze_multiple(tickers, use_s3=not args.no_cache, delay=args.delay)
    for _, signal in results.items():
        print_signal(signal)
        save_signal(signal)
        try:
            save_signal_to_s3(signal, evidence_trail=analyzer.get_last_evidence_trail())
        except Exception as e:
            logger.warning("[%s] S3 save failed: %s", signal.ticker, e)
    if len(results) > 1:
        print_summary_table(results)

if __name__ == "__main__":
    main()