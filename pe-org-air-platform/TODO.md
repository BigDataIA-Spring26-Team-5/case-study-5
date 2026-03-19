# Structural Cleanup — Remaining Tasks

This file is a continuation prompt. A previous session began executing the
structural cleanup plan for the `refactor` branch. Pick up exactly where it
left off. All context needed to continue is below.

---

## What Was Already Completed

### ✅ Item 1 — Merge cache singletons (DONE)
- Removed `get_redis_cache()` and `from functools import lru_cache` from
  `app/services/redis_cache.py`.
- `app/services/cache.py` is already the canonical singleton entry point.
- `app/services/__init__.py` already did not export `get_redis_cache` — no
  change needed there.

### ✅ Item 2 (partial) — schemas/evidence.py created (DONE)
- `app/schemas/evidence.py` was created with these types moved out of
  `app/models/evidence.py`:
  `CompanyEvidenceResponse`, `BackfillStatus`, `BackfillResponse`,
  `BackfillProgress`, `CompanyBackfillResult`, `BackfillTaskStatus`,
  `CompanyDocumentStat`, `CompanySignalStat`, `SignalCategoryBreakdown`,
  `EvidenceStatsResponse`

---

## What Remains — Do These In Order

### 🔲 Item 2 (finish) — Complete the evidence type migration

**Step 1** — Edit `app/models/evidence.py`:
Remove the 10 types that were moved to `app/schemas/evidence.py`. Keep only:
`DocumentSummary`, `SignalEvidence`, `GlassdoorReview`, `CultureSignal`,
`BoardMember`, `GovernanceSignal`.
The `from app.models.signal import CompanySignalSummary` import can also be
removed since `CompanyEvidenceResponse` (which needed it) has moved.

**Step 2** — Edit `app/routers/evidence.py`:
Change the import block from:
```python
from app.models.evidence import (
    DocumentSummary,
    CompanyEvidenceResponse,
    SignalEvidence,
)
```
To:
```python
from app.models.evidence import DocumentSummary, SignalEvidence
from app.schemas.evidence import CompanyEvidenceResponse
```

**Step 3** — Edit `app/schemas/__init__.py`:
The file is currently empty (1 line). Replace with:
```python
from app.schemas.evidence import (
    CompanyEvidenceResponse,
    BackfillStatus,
    BackfillResponse,
    BackfillProgress,
    CompanyBackfillResult,
    BackfillTaskStatus,
    CompanyDocumentStat,
    CompanySignalStat,
    SignalCategoryBreakdown,
    EvidenceStatsResponse,
)

__all__ = [
    "CompanyEvidenceResponse",
    "BackfillStatus",
    "BackfillResponse",
    "BackfillProgress",
    "CompanyBackfillResult",
    "BackfillTaskStatus",
    "CompanyDocumentStat",
    "CompanySignalStat",
    "SignalCategoryBreakdown",
    "EvidenceStatsResponse",
]
```

**Step 4** — Verify: `app/services/evidence_service.py` imports only
`DocumentSummary` from `app.models.evidence` — that type stays, so no change
needed there.

---

### 🔲 Item 3 — Merge `core/errors.py` + `core/exceptions.py`

**Step 1** — Edit `app/repositories/base.py`:
- Remove the import block at lines 19–24:
  ```python
  from app.core.exceptions import (
      DatabaseConnectionException,
      DuplicateEntityException,
      ForeignKeyViolationException,
      RepositoryException,
  )
  ```
- Add the four exception classes directly above the `get_snowflake_connection`
  function:
  ```python
  class RepositoryException(Exception):
      """Base exception for repository-layer errors."""

  class DatabaseConnectionException(RepositoryException):
      """Raised when a Snowflake connection cannot be established."""

  class DuplicateEntityException(RepositoryException):
      """Raised on UNIQUE constraint violation."""

  class ForeignKeyViolationException(RepositoryException):
      """Raised on FOREIGN KEY constraint violation."""
  ```

**Step 2** — Edit `app/core/errors.py`:
Append the entire validation handler section from `exceptions.py` at the
bottom (after `ERROR_STATUS_MAP`). Add the necessary imports at the top of
`errors.py`:
```python
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request, status
```
Then append:
```python
# ---------------------------------------------------------------------------
# Validation exception handler (registered in main.py)
# ---------------------------------------------------------------------------

FIELD_MESSAGES = {
    "name": {
        "missing": "Company name is required",
        "string_too_short": "Company name cannot be empty",
        "string_too_long": "Company name must not exceed 255 characters",
        "string_type": "Company name must be a string",
    },
    "ticker": {
        "string_too_long": "Ticker symbol must not exceed 10 characters",
        "string_pattern_mismatch": "Ticker symbol must contain only uppercase letters (A-Z)",
        "string_type": "Ticker symbol must be a string",
    },
    "industry_id": {
        "missing": "Industry ID is required",
        "uuid_parsing": "Industry ID must be a valid UUID format",
        "uuid_type": "Industry ID must be a valid UUID",
    },
    "position_factor": {
        "less_than_equal": "Position factor must be between -1.0 and 1.0",
        "greater_than_equal": "Position factor must be between -1.0 and 1.0",
        "float_type": "Position factor must be a number",
        "float_parsing": "Position factor must be a valid number",
    },
}

DEFAULT_MESSAGES = {
    "missing": "Field '{field}' is required",
    "string_too_short": "Field '{field}' is too short",
    "string_too_long": "Field '{field}' is too long",
    "string_pattern_mismatch": "Field '{field}' has invalid format",
    "less_than_equal": "Field '{field}' exceeds maximum allowed value",
    "greater_than_equal": "Field '{field}' is below minimum allowed value",
    "uuid_parsing": "Field '{field}' must be a valid UUID",
    "uuid_type": "Field '{field}' must be a valid UUID",
    "string_type": "Field '{field}' must be a string",
    "float_type": "Field '{field}' must be a number",
    "float_parsing": "Field '{field}' must be a valid number",
    "int_type": "Field '{field}' must be an integer",
    "int_parsing": "Field '{field}' must be a valid integer",
    "json_invalid": "Malformed JSON request body",
    "extra_forbidden": "Unknown field '{field}' is not allowed",
}


def get_validation_message(field: str, error_type: str) -> str:
    if field in FIELD_MESSAGES:
        for key in FIELD_MESSAGES[field]:
            if key in error_type:
                return FIELD_MESSAGES[field][key]
    for key, template in DEFAULT_MESSAGES.items():
        if key in error_type:
            return template.format(field=field)
    return f"Invalid value for field '{field}'"


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if not errors:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    err = errors[0]
    error_type = err.get("type", "")
    loc = err.get("loc", [])
    if "json_invalid" in error_type:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error_code": "INVALID_REQUEST",
                "message": "Malformed JSON request body",
                "details": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    field = ".".join(str(l) for l in loc if l != "body")
    message = get_validation_message(field, error_type)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "VALIDATION_ERROR",
            "message": message,
            "details": {"field": field, "type": error_type} if field else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
```
Note: `datetime` and `timezone` are already imported in `errors.py`.

**Step 3** — Edit `app/main.py` line 29:
Change:
```python
from app.core.exceptions import validation_exception_handler
```
To:
```python
from app.core.errors import validation_exception_handler
```

**Step 4** — Delete `app/core/exceptions.py`.

---

### 🔲 Item 4 — Document the pipelines/services layering contract

**Create `app/pipelines/__init__.py`** (file does not exist yet):
```python
"""
Pipelines — PE Org-AI-R Platform
app/pipelines/

Pipeline modules contain pure business logic: external API integrations,
data extraction, and scoring algorithms. They have no knowledge of HTTP,
FastAPI, or the request/response cycle.

Calling convention:
  Routers → Services → Pipelines
  (Pipelines are never called directly by routers)

Each pipeline exposes a stable public interface (usually a single class
or async runner function). Services own the lifecycle (singleton setup,
S3 key construction, Snowflake writes after pipeline output).
"""
```

**Also update the docstring in `app/services/__init__.py`** (currently just
`"""Services module for the PE OrgAIR Platform."""`). Replace it with:
```python
"""
Services — PE Org-AI-R Platform
app/services/

Subdirectory layout rule:
  Top-level flat  — single-class services with no domain siblings.
  Subdirectory    — services that share a domain, have utilities, or need
                    their own namespace (e.g. signals/, retrieval/, llm/).
"""
```

---

### 🔲 Item 5 — Move signal services to `app/services/signals/`

**Files to move** (from `app/services/` to `app/services/signals/`):
- `base_signal_service.py`
- `job_data_service.py`
- `job_signal_service.py`
- `patent_signal_service.py`
- `tech_signal_service.py`
- `leadership_service.py`
- `board_composition_service.py`
- `culture_signal_service.py`
- `evidence_service.py`

**Approach:** Write new files in `app/services/signals/` with updated
internal imports, then replace the old flat files with backward-compatible
shims that re-export from the new location (so any import site not listed
below continues to work).

**Internal import changes needed in the new signal files:**

`signals/job_signal_service.py`:
```python
# Change:
from app.services.base_signal_service import BaseSignalService
from app.services.job_data_service import get_job_data_service
# To:
from app.services.signals.base_signal_service import BaseSignalService
from app.services.signals.job_data_service import get_job_data_service
```

`signals/patent_signal_service.py`:
```python
# Change:
from app.services.base_signal_service import BaseSignalService
# To:
from app.services.signals.base_signal_service import BaseSignalService
```

`signals/tech_signal_service.py`:
```python
# Change:
from app.services.base_signal_service import BaseSignalService
# To:
from app.services.signals.base_signal_service import BaseSignalService
```

All other moved files (`base_signal_service.py`, `job_data_service.py`,
`leadership_service.py`, `board_composition_service.py`,
`culture_signal_service.py`, `evidence_service.py`) have no internal
cross-references to other signal services — copy them verbatim.

**Create `app/services/signals/__init__.py`** with re-exports:
```python
"""Signal services domain package."""
from app.services.signals.base_signal_service import BaseSignalService
from app.services.signals.job_data_service import JobDataService, get_job_data_service
from app.services.signals.job_signal_service import JobSignalService, get_job_signal_service
from app.services.signals.patent_signal_service import PatentSignalService, get_patent_signal_service
from app.services.signals.tech_signal_service import TechSignalService, get_tech_signal_service
from app.services.signals.leadership_service import LeadershipSignalService, get_leadership_service
from app.services.signals.board_composition_service import BoardCompositionService
from app.services.signals.culture_signal_service import CultureSignalService, get_culture_signal_service
from app.services.signals.evidence_service import build_document_summary
```

**Replace old flat files with shims**, e.g. `app/services/job_signal_service.py`:
```python
# Moved to app/services/signals/job_signal_service.py
from app.services.signals.job_signal_service import JobSignalService, get_job_signal_service
__all__ = ["JobSignalService", "get_job_signal_service"]
```
(Repeat for each of the 9 moved files.)

**Update `app/core/lifespan.py`** — inside `_create_singletons()`, update the
6 domain-service imports (lines 96–101). Change:
```python
from app.services.job_signal_service import JobSignalService
from app.services.patent_signal_service import PatentSignalService
from app.services.tech_signal_service import TechSignalService
from app.services.leadership_service import LeadershipSignalService
from app.services.board_composition_service import BoardCompositionService
from app.services.culture_signal_service import CultureSignalService
```
To:
```python
from app.services.signals.job_signal_service import JobSignalService
from app.services.signals.patent_signal_service import PatentSignalService
from app.services.signals.tech_signal_service import TechSignalService
from app.services.signals.leadership_service import LeadershipSignalService
from app.services.signals.board_composition_service import BoardCompositionService
from app.services.signals.culture_signal_service import CultureSignalService
```

**Update `app/services/__init__.py`** — update the lazy import paths:
```python
# Change (in each lazy wrapper):
from app.services.leadership_service import ...  →  from app.services.signals.leadership_service import ...
from app.services.job_data_service import ...    →  from app.services.signals.job_data_service import ...
from app.services.job_signal_service import ...  →  from app.services.signals.job_signal_service import ...
from app.services.tech_signal_service import ... →  from app.services.signals.tech_signal_service import ...
from app.services.patent_signal_service import ...→ from app.services.signals.patent_signal_service import ...
```

**Update `app/scripts/collect_evidence.py`** lines 30–33:
```python
# Change:
from app.services.job_signal_service import get_job_signal_service
from app.services.patent_signal_service import get_patent_signal_service
from app.services.tech_signal_service import get_tech_signal_service
from app.services.leadership_service import get_leadership_service
# To:
from app.services.signals.job_signal_service import get_job_signal_service
from app.services.signals.patent_signal_service import get_patent_signal_service
from app.services.signals.tech_signal_service import get_tech_signal_service
from app.services.signals.leadership_service import get_leadership_service
```

**Update `app/routers/rag.py`** line 971 (inside a try block):
```python
# Change:
from app.services.culture_signal_service import get_culture_signal_service
# To:
from app.services.signals.culture_signal_service import get_culture_signal_service
```

---

### 🔲 Item 6 — Decompose `board_analyzer.py` (1,495 lines)

The full file has been read. Here is the decomposition plan:

**Step 1 — Add `CompanyRegistry` to `app/config/company_mappings.py`**

Append this class at the end of the file (before the helper functions or
after them — after is fine):

```python
# =============================================================================
# COMPANY REGISTRY — CIK lookups for SEC EDGAR board proxy analysis
# =============================================================================

class CompanyRegistry:
    """Maps CS3 portfolio tickers to SEC CIK numbers for DEF 14A retrieval."""
    COMPANIES: Dict[str, Dict] = {
        "NVDA": {"cik": "0001045810", "name": "NVIDIA Corporation",          "sector": "technology"},
        "JPM":  {"cik": "0000019617", "name": "JPMorgan Chase & Co.",        "sector": "financial_services"},
        "WMT":  {"cik": "0000104169", "name": "Walmart Inc.",                "sector": "retail"},
        "GE":   {"cik": "0000040545", "name": "GE Aerospace",                "sector": "manufacturing"},
        "DG":   {"cik": "0000029534", "name": "Dollar General Corporation",  "sector": "retail"},
    }

    @classmethod
    def get(cls, ticker: str) -> Dict:
        t = ticker.upper()
        if t in cls.COMPANIES:
            return cls.COMPANIES[t]
        raise ValueError(f"Unknown ticker '{ticker}'.")

    @classmethod
    def register(cls, ticker: str, cik: str, name: str, sector: str = "unknown"):
        cls.COMPANIES[ticker.upper()] = {"cik": cik, "name": name, "sector": sector}

    @classmethod
    def all_tickers(cls) -> List[str]:
        return list(cls.COMPANIES.keys())
```

**Step 2 — Create `app/pipelines/board_io.py`**

Extract the following from `board_analyzer.py` into a new file
`app/pipelines/board_io.py`:
- `SEC_HEADERS` dict
- `EDGAR_DELAY` constant
- `ProxyData` dataclass
- `strip_html()` function
- `_load_s3_json()` function
- `_find_s3_parsed_keys()` function
- `_load_proxy_from_s3()` function
- `_fetch_from_edgar()` function
- `load_proxy_data()` function

`board_io.py` should import `CompanyRegistry` from
`app.config.company_mappings` (not from `board_analyzer`).

Imports needed in `board_io.py`:
```python
import json, re, time
import httpx
import structlog
from dataclasses import dataclass
from typing import Dict, List, Optional
from app.config.company_mappings import CompanyRegistry
from app.repositories.document_repository import DocumentRepository
from app.services.s3_storage import S3StorageService, get_s3_service
```

**Step 3 — Edit `app/pipelines/board_analyzer.py`**

- Remove the `CompanyRegistry` class definition (lines ~62–84).
- Remove `SEC_HEADERS`, `EDGAR_DELAY`, `ProxyData` dataclass, `strip_html`,
  `_load_s3_json`, `_find_s3_parsed_keys`, `_load_proxy_from_s3`,
  `_fetch_from_edgar`, `load_proxy_data` (roughly lines ~86–178).
- Add imports at the top:
  ```python
  from app.config.company_mappings import CompanyRegistry
  from app.pipelines.board_io import ProxyData, load_proxy_data, strip_html
  ```
  (`strip_html` is referenced in `extract_strategy_text` indirectly — check
  if it's actually needed in board_analyzer; if not, omit it.)
- Remove the old top-of-file import `from app.services.s3_storage import
  S3StorageService, get_s3_service` if `S3StorageService` is no longer used
  directly (it moves to `board_io`). It IS still needed for the
  `BoardCompositionAnalyzer.__init__` signature and `save_signal_to_s3`.
  Keep it.

**Step 4 — Edit `app/services/signals/board_composition_service.py`**

Change:
```python
from app.pipelines.board_analyzer import (
    BoardCompositionAnalyzer,
    CompanyRegistry,
    save_signal_to_s3,
)
```
To:
```python
from app.pipelines.board_analyzer import BoardCompositionAnalyzer, save_signal_to_s3
from app.config.company_mappings import CompanyRegistry
```

If Item 5 shims are in place, the file is at
`app/services/signals/board_composition_service.py`. If the shim approach
was used, update the new canonical file, not the shim.

---

## Verification Checklist

Run after each item completes:
```bash
cd /home/aq/work/NEU/SPRING_26/Big\ Data/CS5/pe-org-air-platform
python -m py_compile app/services/redis_cache.py app/services/cache.py
python -m py_compile app/schemas/evidence.py app/models/evidence.py app/routers/evidence.py
python -m py_compile app/core/errors.py app/repositories/base.py app/main.py
python -m py_compile app/pipelines/__init__.py
# After Item 5:
python -m py_compile app/services/signals/*.py app/core/lifespan.py
# Final full check:
python -c "from app.main import app; print('Import OK')"
```

---

## Key File Locations (for reference)

All files are under:
`/home/aq/work/NEU/SPRING_26/Big Data/CS5/pe-org-air-platform/`

- `app/services/redis_cache.py` — Item 1 done
- `app/schemas/evidence.py` — Item 2 created (needs models/evidence.py cleanup)
- `app/models/evidence.py` — remove 10 API response types (Item 2 finish)
- `app/routers/evidence.py` — update import (Item 2 finish)
- `app/schemas/__init__.py` — add re-exports (Item 2 finish)
- `app/core/errors.py` — append validation handler (Item 3)
- `app/core/exceptions.py` — DELETE after migration (Item 3)
- `app/repositories/base.py` — add 4 exception classes locally (Item 3)
- `app/main.py` — update 1 import line (Item 3)
- `app/pipelines/__init__.py` — CREATE with docstring (Item 4)
- `app/services/__init__.py` — update docstring + lazy import paths (Items 4+5)
- `app/services/signals/` — CREATE directory with 9 files (Item 5)
- `app/core/lifespan.py` — update 6 imports (Item 5)
- `app/scripts/collect_evidence.py` — update 4 imports (Item 5)
- `app/routers/rag.py` — update 1 inline import at line 971 (Item 5)
- `app/config/company_mappings.py` — append CompanyRegistry class (Item 6)
- `app/pipelines/board_io.py` — CREATE with I/O helpers (Item 6)
- `app/pipelines/board_analyzer.py` — remove CompanyRegistry + I/O (Item 6)
- `app/services/signals/board_composition_service.py` — update import (Item 6)
