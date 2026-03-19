# PE Org-AI-R Platform — API Documentation

**Version:** 1.0.0
**Base URL:** `http://localhost:8000`
**Swagger UI:** `/docs` · **ReDoc:** `/redoc`

---

## Table of Contents

1. [Root](#root)
2. [Health](#health)
3. [Companies (CS1)](#companies-cs1)
4. [Dimension Scores](#dimension-scores)
5. [Documents — Collection (CS2)](#documents--collection-cs2)
6. [Documents — Parsing (CS2)](#documents--parsing-cs2)
7. [Documents — Chunking (CS2)](#documents--chunking-cs2)
8. [Signals (CS2)](#signals-cs2)
9. [Evidence (CS2)](#evidence-cs2)
10. [CS3 Dimensions Scoring](#cs3-dimensions-scoring)
11. [RAG (CS4)](#rag-cs4)
12. [CS3 TC + V^R Scoring](#cs3-tc--vr-scoring)
13. [CS3 Position Factor](#cs3-position-factor)
14. [CS3 H^R (Human Readiness)](#cs3-hr-human-readiness)
15. [CS3 Org-AI-R](#cs3-org-ai-r)
16. [Analyst Notes (CS4)](#analyst-notes-cs4)

---

## Root

### `GET /`

**Summary:** Root endpoint — service identity and documentation links.

**Parameters:** None

**Response `200`:**
```json
{
  "service": "PE Org-AI-R Platform Foundation API",
  "version": "1.0.0",
  "docs": {
    "swagger": "/docs",
    "redoc": "/redoc"
  },
  "status": "running"
}
```

---

## Health

### `GET /healthz`

**Summary:** Lightweight health check (always 200, for Render uptime monitor).

**Parameters:** None

**Response `200`:**
```json
{
  "status": "ok",
  "timestamp": "2026-03-18T12:00:00+00:00"
}
```

**Core logic:**
1. Returns immediately without touching any external dependencies.

---

### `GET /health`

**Summary:** Deep dependency health check — Snowflake, Redis, S3.

**Parameters:** None

**Response model:** `HealthResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"healthy"` or `"degraded"` |
| `timestamp` | `datetime` | UTC timestamp |
| `version` | `string` | API version string |
| `dependencies` | `Dict[str, str]` | Per-dependency status strings |

**Responses:** `200` (all healthy) · `503` (one or more degraded)

**Core logic:**
1. Runs three async checks concurrently: `check_snowflake()`, `check_redis()`, `check_s3()`.
2. Snowflake: calls `HealthRepository.ping()` — returns user + role on success.
3. Redis: creates a transient client via `REDIS_URL` env var, calls `PING`.
4. S3: calls `s3_client.head_bucket(Bucket=<bucket>)`.
5. If all `dependencies` values start with `"healthy"` → `200`; otherwise `503`.

---

## Companies (CS1)

> **Prefix:** `/api/v1`
> **Caching:** Redis, TTL = 5 minutes (`TTL_COMPANY`)

### `POST /api/v1/companies`

**Summary:** Create a new company.

**Request body:** `CompanyCreate`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` (1–255 chars) | Yes | Company display name |
| `ticker` | `string` (max 10 chars) | No | Stock ticker; auto-uppercased |
| `industry_id` | `UUID` | Yes | Must reference an existing industry |
| `position_factor` | `float` [−1.0, 1.0] | No (default `0.0`) | CS3 position factor override |

**Response `201`:** `CompanyResponse`

| Field | Type | Notes |
|-------|------|-------|
| `id` | `UUID` | DB-assigned UUID |
| `name` | `string` | |
| `ticker` | `string \| null` | |
| `industry_id` | `UUID` | |
| `position_factor` | `float` | |
| `sector` | `string \| null` | Groq-enriched |
| `sub_sector` | `string \| null` | Groq-enriched |
| `market_cap_percentile` | `float \| null` | Groq-enriched |
| `revenue_millions` | `float \| null` | Groq-enriched |
| `employee_count` | `int \| null` | Groq-enriched |
| `fiscal_year_end` | `string \| null` | Groq-enriched |
| `created_at` / `updated_at` | `datetime` | |
| `cache` | `CacheInfo \| null` | Cache metadata |

**Error codes:** `404 INDUSTRY_NOT_FOUND` · `409 DUPLICATE_COMPANY`

**Core logic:**
1. Validates `industry_id` exists via `IndustryRepository.exists()`.
2. Checks for duplicate `(name, industry_id)` via `CompanyRepository.check_duplicate()`.
3. Inserts company row into Snowflake.
4. If `ticker` is provided, schedules a background task (`_enrich_company_in_background`):
   - Calls Groq to fill `sub_sector`, `market_cap_percentile`, `revenue_millions`, `employee_count`, `fiscal_year_end`.
   - Calls `enrich_portfolio_metadata()` and creates a portfolio entry linked to the company.
5. Invalidates all company Redis cache keys.
6. Returns the new company row immediately (enrichment completes asynchronously).

---

### `GET /api/v1/companies/all`

**Summary:** Get all companies (no pagination).

**Parameters:** None

**Response `200`:** `CompanyListResponse`

| Field | Type | Description |
|-------|------|-------------|
| `items` | `List[CompanyResponse]` | All companies |
| `total` | `int` | Count of items |
| `cache` | `CacheInfo \| null` | Hit/miss + latency metadata |

**Core logic:**
1. Checks Redis for cache key `companies:all`.
2. On miss, calls `CompanyRepository.get_all()` and wraps into `CompanyListResponse`.
3. Writes result to Redis with `TTL_COMPANY`.

---

### `GET /api/v1/companies`

**Summary:** List companies (paginated, filterable).

**Query parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `page` | `int` (≥1) | `1` | Page number |
| `page_size` | `int` (1–100) | `20` | Items per page |
| `industry_id` | `UUID` | None | Filter by industry |
| `min_revenue` | `float` | None | Minimum revenue in USD millions |

**Response `200`:** `PaginatedCompanyResponse`

| Field | Type |
|-------|------|
| `items` | `List[CompanyResponse]` |
| `total` | `int` |
| `page` | `int` |
| `page_size` | `int` |
| `total_pages` | `int` |
| `cache` | `CacheInfo \| null` |

**Core logic:**
1. Builds cache key from all four query params.
2. On miss: fetches all companies (filtered by `industry_id` if provided), applies `min_revenue` filter, slices for pagination.
3. Caches result.

---

### `GET /api/v1/companies/{ticker}`

**Summary:** Get a single company by UUID or ticker symbol.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | UUID string **or** ticker (case-insensitive) |

**Response `200`:** `CompanyResponse`

**Error:** `404 COMPANY_NOT_FOUND`

**Core logic:**
1. Tries to parse `ticker` as a UUID → `get_by_id()`; on `ValueError` → `get_by_ticker()`.
2. Checks Redis cache for `company:<uuid>`.
3. On miss, re-fetches from Snowflake and caches.

---

### `GET /api/v1/companies/{ticker}/dimension-keywords`

**Summary:** Get Groq-expanded rubric keywords for a company × dimension pair.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker (auto-uppercased) |

**Query parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `dimension` | `string` | Yes | One of the 7 V^R dimensions (e.g. `data_infrastructure`) |

**Response `200`:** `DimensionKeywordsResponse`

| Field | Type |
|-------|------|
| `ticker` | `string` |
| `dimension` | `string` |
| `keywords` | `List[str]` |

**Core logic:**
1. Looks up base keywords for the given dimension from `_BASE_DIMENSION_KEYWORDS`.
2. If the company is not in the DB, returns base keywords immediately.
3. Calls `get_dimension_keywords()` (Groq) to expand base keywords with company-specific synonyms.

---

## Dimension Scores

### `GET /api/v1/dimensions/weights`

**Summary:** Get the current dimension weights configuration.

**Parameters:** None

**Response `200`:** `DimensionWeightsResponse`

| Field | Type | Description |
|-------|------|-------------|
| `weights` | `Dict[str, float]` | Dimension name → weight (e.g. `{"data_infrastructure": 0.15, ...}`) |
| `total` | `float` | Sum of all weights (must be ≈ 1.0) |
| `is_valid` | `bool` | True if `0.999 ≤ total ≤ 1.001` |
| `timestamp` | `string` | UTC ISO timestamp |

**Error:** `500 WEIGHTS_MISCONFIGURED` (if weights do not sum to 1.0)

**Core logic:**
1. Validates `DIMENSION_WEIGHTS` at module load time (raises `ValueError` if misconfigured).
2. Checks Redis for key `dimension:weights` with `TTL_DIMENSION_WEIGHTS`.
3. On miss, builds `DimensionWeightsResponse` from the in-memory `DIMENSION_WEIGHTS` dict.

---

## Documents — Collection (CS2)

> **Prefix:** `/api/v1/documents`

### `POST /api/v1/documents/collect`

**Summary:** Collect SEC filings for a single company.

**Request body:** `DocumentCollectionRequest`

| Field | Type | Notes |
|-------|------|-------|
| `ticker` | `string` | Company ticker |
| `filing_types` | `List[FilingType]` | `10-K`, `10-Q`, `8-K`, `DEF 14A` |
| `years_back` | `int` | How many years of filings to retrieve |

**Response `200`:** `DocumentCollectionResponse` (counts by filing type, status, S3 keys)

**Core logic:**
1. Downloads filings from SEC EDGAR (with rate limiting).
2. Deduplicates by content hash.
3. Uploads raw files to S3 at `sec/raw/{ticker}/...`.
4. Saves metadata (accession number, filing date, type, S3 key, hash) to Snowflake.

---

### `POST /api/v1/documents/collect/all`

**Summary:** Collect SEC filings for all 10 target companies.

**Query parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `filing_types` | `List[FilingType]` | `[10-K, 10-Q, 8-K, DEF 14A]` | Filing types to collect |
| `years_back` | `int` (1–10) | `3` | Look-back window |

**Response `200`:** `List[DocumentCollectionResponse]`

**Core logic:** Iterates over all 10 target companies; calls `collect_for_company()` for each.

---

## Documents — Parsing (CS2)

### `POST /api/v1/documents/parse/{ticker}`

**Summary:** Parse all collected SEC filings for a company.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |

**Response `200`:** `ParseByTickerResponse` (per-document parse results, section counts)

**Core logic:**
1. Downloads raw files from S3 (`sec/raw/{ticker}/...`).
2. Extracts plain text and tables from HTML and PDF sources.
3. Identifies key SEC sections: Risk Factors (Item 1A), MD&A (Item 7), Business (Item 1).
4. Uploads parsed JSON to S3 at `sec/parsed/{ticker}/...`.
5. Updates `word_count` in Snowflake document metadata.

---

### `POST /api/v1/documents/parse`

**Summary:** Parse SEC filings for all companies (batch).

**Parameters:** None

**Response `200`:** `ParseAllResponse`

**Core logic:** Calls `parse_by_ticker()` for each of the 10 target companies.

---

## Documents — Chunking (CS2)

### `POST /api/v1/documents/chunk/{ticker}`

**Summary:** Chunk all parsed documents for a company.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |

**Query parameters:**

| Name | Type | Default | Constraints | Description |
|------|------|---------|-------------|-------------|
| `chunk_size` | `int` | `750` | 100–2000 | Target words per chunk |
| `chunk_overlap` | `int` | `50` | 0–200 | Overlap in words between consecutive chunks |

**Response `200`:** Chunk summary (counts, S3 keys, Snowflake IDs)

**Core logic:**
1. Downloads parsed JSON from S3 (`sec/parsed/{ticker}/...`).
2. Splits text into overlapping windows of `chunk_size` words with `chunk_overlap` words of shared context.
3. Uploads chunk files to S3 at `sec/chunks/{ticker}/...`.
4. Saves chunk metadata to Snowflake `document_chunks` table.

---

### `POST /api/v1/documents/chunk`

**Summary:** Chunk parsed documents for all companies (batch).

**Query parameters:** Same as single-company endpoint (`chunk_size`, `chunk_overlap`).

**Response `200`:** Batch chunk summary.

---

## Signals (CS2)

> **Prefix:** `/api/v1`

### `POST /api/v1/signals/collect`

**Summary:** Trigger signal collection for a company (async background task).

**Request body:** `CollectionRequest`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `company_id` | `string` | — | Company UUID or ticker |
| `categories` | `List[str]` | all 4 | `technology_hiring`, `innovation_activity`, `digital_presence`, `leadership_signals` |
| `years_back` | `int` (1–10) | `5` | Look-back for patent search |
| `force_refresh` | `bool` | `false` | Bypass cached signal data |

**Response `200`:** `CollectionResponse`

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `string` | UUID for the background task |
| `status` | `string` | `"queued"` |
| `message` | `string` | Confirmation message |

**Core logic:**
1. Creates a task record in `_task_store` (in-memory dict) with `status="queued"`.
2. Schedules `run_signal_collection()` as a FastAPI background task.
3. Returns `task_id` immediately.
4. Background task resolves the company by ticker or UUID, then calls signal services per category:
   - `technology_hiring` → `JobSignalService.analyze_company()` (JobSpy)
   - `innovation_activity` → `PatentSignalService.analyze_company()` (PatentsView/USPTO)
   - `digital_presence` → `TechSignalService.analyze_company()` (BuiltWith/Wappalyzer)
   - `leadership_signals` → `LeadershipService.analyze_company()` (SEC DEF-14A)
5. Task status transitions to `"completed"` or `"completed_with_errors"`.

---

### `GET /api/v1/signals/detailed`

**Summary:** List signals with optional filters.

**Query parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `category` | `string` | None | One of the 4 signal categories |
| `ticker` | `string` | None | Filter to a single company |
| `min_score` | `float` [0–100] | None | Minimum normalized score |
| `limit` | `int` (1–1000) | `100` | Maximum results to return |

**Response `200`:**
```json
{
  "total": 42,
  "filters": { "category": "...", "ticker": "...", "min_score": null },
  "signals": [ { ...signal rows... } ]
}
```

**Core logic:**
1. If `ticker` provided: resolves company → fetches signals by category or all.
2. If no `ticker`: iterates all companies, collecting signals per company.
3. Applies `min_score` filter in memory.
4. Slices to `limit`.

---

## Evidence (CS2)

### `GET /api/v1/companies/{ticker}/evidence`

**Summary:** Get aggregated evidence summary for a company.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker (auto-uppercased) |

**Response `200`:** `CompanyEvidenceResponse`

| Field | Type | Description |
|-------|------|-------------|
| `company_id` | `string` | UUID |
| `company_name` | `string` | |
| `ticker` | `string` | |
| `document_summary` | `DocumentSummary` | Aggregate SEC filing stats (counts by type/status, word totals, chunk totals, date range, freshness) |
| `signals` | `List[SignalEvidence]` | Individual signal records (category, source, score, confidence, metadata) |
| `signal_count` | `int` | |
| `signal_summary` | `CompanySignalSummary \| null` | Per-category scores + composite score |

**Core logic:**
1. Resolves `ticker` → company row (404 if not found).
2. Fetches all documents via `DocumentRepository.get_by_ticker()`.
3. Calls `build_document_summary()` to aggregate filing stats.
4. Fetches all signals via `SignalRepository.get_signals_by_ticker()`.
5. Fetches signal summary via `SignalRepository.get_summary_by_ticker()`.

---

## CS3 Dimensions Scoring

> **Prefix:** `/api/v1`

### `POST /api/v1/scoring/all`

**Summary:** Score all companies that have CS2 signal data.

**Parameters:** None

**Response `200`:** `AllScoringResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"completed"` |
| `companies_scored` | `int` | |
| `companies_failed` | `int` | |
| `results` | `List[ScoringResponse]` | Per-company results |
| `duration_seconds` | `float` | |

**Core logic:** Calls `ScoringService.score_all_companies()` — iterates every company with an entry in `company_signal_summaries` and runs the full pipeline (see below).

---

### `POST /api/v1/scoring/{ticker}`

**Summary:** Run the full CS3 scoring pipeline for a single company.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Auto-uppercased |

**Response `200`:** `ScoringResponse`

| Field | Type | Description |
|-------|------|-------------|
| `ticker` | `string` | |
| `company_id` | `string \| null` | |
| `status` | `string` | `"success"` or `"failed"` |
| `scored_at` | `string \| null` | ISO timestamp |
| `dimension_scores` | `List[Dict]` | 7 scored dimensions |
| `mapping_matrix` | `List[Dict] \| null` | Evidence → dimension weight matrix |
| `coverage` | `Dict \| null` | Evidence source coverage stats |
| `evidence_sources` | `Dict \| null` | Source breakdown |
| `persisted` | `bool` | Whether written to Snowflake |
| `duration_seconds` | `float \| null` | |
| `error` | `string \| null` | Present on failure |

**Core logic:**
1. Reads CS2 signal scores from `company_signal_summaries` (hiring, innovation, digital, leadership).
2. Reads SEC document chunks from `document_chunks` + S3 for Items 1, 1A, and 7.
3. Runs rubric scoring: evaluates SEC text against 7-dimension rubrics (Task 5.0b).
4. Maps evidence to 7 dimensions using the weighted Table 1 matrix (Task 5.0a).
5. Persists mapping matrix and dimension scores to Snowflake.

**Prerequisite:** Company must have CS2 signal data (`POST /api/v1/signals/collect` first).

---

### `GET /api/v1/scoring/{ticker}/dimensions`

**Summary:** View the 7 dimension scores for a company from Snowflake.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Auto-uppercased |

**Response `200`:** `DimensionScoresResponse`

| Field | Type |
|-------|------|
| `ticker` | `string` |
| `scores` | `List[Dict]` |
| `score_count` | `int` |

**Error:** `404 SCORES_NOT_FOUND` (if `POST /api/v1/scoring/{ticker}` has not been run)

**Core logic:** Queries `evidence_dimension_scores WHERE ticker = '{ticker}'` via `ScoringRepository.get_dimension_scores()`.

---

## RAG (CS4)

> **Prefix:** `/api/v1/rag`

### `POST /api/v1/rag/index/{ticker}`

**Summary:** Fetch CS2 evidence for a company and index into ChromaDB.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |

**Query parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `source_types` | `string` | None | Comma-separated source type filter (e.g. `sec_10k_item_1,board_proxy_def14a`) |
| `signal_categories` | `string` | None | Comma-separated signal category filter |
| `min_confidence` | `float` | `0.0` | Minimum evidence confidence threshold |
| `force` | `bool` | `false` | Delete existing docs for this ticker before re-indexing |

**Response `200`:** `IndexResponse`

| Field | Type |
|-------|------|
| `indexed_count` | `int` |
| `ticker` | `string` |
| `source_counts` | `Dict[str, int]` |

**Core logic:**
1. If `force=true`, deletes existing ChromaDB documents matching ticker (and `source_types` if provided).
2. Calls `CS2Client.get_evidence()` to retrieve structured evidence from Snowflake/S3.
3. Calls `VectorStore.index_cs2_evidence()` with `DimensionMapper` to assign dimension labels.
4. Marks evidence as indexed in Snowflake via `CS2Client.mark_indexed()`.
5. Refreshes the BM25 sparse index via `HybridRetriever.refresh_sparse_index()`.
6. Seeds BM25 with retrieved evidence via `HybridRetriever.seed_from_evidence()`.

---

### `POST /api/v1/rag/index`

**Summary:** Bulk index CS2 evidence for multiple tickers.

**Request body:** `BulkIndexRequest`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tickers` | `List[str]` | — | Required list of tickers |
| `source_types` | `List[str] \| null` | None | Source type filter |
| `signal_categories` | `List[str] \| null` | None | Signal category filter |
| `min_confidence` | `float` | `0.0` | Evidence confidence threshold |
| `force` | `bool` | `false` | Delete existing docs before re-indexing |

**Response `200`:** `BulkIndexResponse`

| Field | Type |
|-------|------|
| `results` | `Dict[str, IndexResponse]` |
| `total_indexed` | `int` |
| `failed` | `Dict[str, str]` |

---

### `DELETE /api/v1/rag/index`

**Summary:** Delete documents from the ChromaDB index.

**Query parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string \| null` | If provided, deletes only that ticker's docs; otherwise wipes all |

**Response `200`:**
```json
{ "wiped_count": 42, "scope": "NVDA" }
```

---

### `POST /api/v1/rag/search`

**Summary:** Hybrid dense + sparse search with optional HyDE.

**Request body:** `SearchRequest`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | `string` | — | Search query |
| `ticker` | `string \| null` | None | Restrict search to one company |
| `source_types` | `List[str] \| null` | None | Source type filter |
| `dimension` | `string \| null` | None | One of the 7 V^R dimensions |
| `top_k` | `int` | `10` | Number of results to return |
| `use_hyde` | `bool` | `false` | Use Hypothetical Document Embeddings |

**Response `200`:** `List[SearchResult]`

| Field | Type |
|-------|------|
| `doc_id` | `string` |
| `content` | `string` (first 500 chars) |
| `metadata` | `Dict` |
| `score` | `float` |
| `retrieval_method` | `string` |

**Core logic:**
1. Normalises `dimension` via `DIMENSION_ALIAS_MAP`.
2. If `use_hyde=true` and dimension set: uses `HyDERetriever` (generates a hypothetical document then searches).
3. If `ticker` set (no HyDE): uses `_retrieve_with_fallback()` with dimension-aware source affinity and graceful fallback.
4. Otherwise: calls `HybridRetriever.retrieve()` with filter metadata.

---

### `GET /api/v1/rag/justify/{ticker}/{dimension}`

**Summary:** Generate IC-ready justification for a dimension score with cited evidence.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |
| `dimension` | `string` | One of the 7 V^R dimensions |

**Response `200`:** `JustifyResponse`

| Field | Type | Description |
|-------|------|-------------|
| `ticker` | `string` | |
| `dimension` | `string` | |
| `score` | `float` | Numeric score (0–100) |
| `level` | `int` | Rubric level (1–4) |
| `level_name` | `string` | e.g. `"Emerging"`, `"Scaling"` |
| `generated_summary` | `string` | LLM-generated narrative |
| `evidence_strength` | `string` | `"strong"` / `"moderate"` / `"weak"` |
| `supporting_evidence` | `List[Dict]` | Up to 5 cited evidence items |
| `gaps_identified` | `List[str]` | Identified gaps for this dimension |

**Core logic:** Delegates to `JustificationGenerator.generate_justification()` in a thread pool.

---

### `GET /api/v1/rag/ic-prep/{ticker}`

**Summary:** Generate a full 7-dimension IC meeting package.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |

**Query parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `dimensions` | `string` | None | Comma-separated subset of dimensions to include |

**Response `200`:** `ICPrepResponse`

| Field | Type | Description |
|-------|------|-------------|
| `company_id` | `string` | UUID |
| `ticker` | `string` | |
| `executive_summary` | `string` | LLM-generated paragraph |
| `recommendation` | `string` | Investment recommendation |
| `key_strengths` | `List[str]` | |
| `key_gaps` | `List[str]` | |
| `risk_factors` | `List[str]` | |
| `dimension_scores` | `Dict[str, float]` | Dimension → score |
| `total_evidence_count` | `int` | |
| `generated_at` | `string` | ISO timestamp |

**Core logic:** Calls `ICPrepWorkflow.prepare_meeting()` which runs `JustificationGenerator` for each dimension and synthesises into an IC package.

---

### `GET /api/v1/rag/diagnostics`

**Summary:** Full ChromaDB diagnostic — document counts by company, source type, and dimension.

**Parameters:** None

**Response `200`:**
```json
{
  "total_documents": 1234,
  "by_company":      { "NVDA": 210, ... },
  "by_source_type":  { "sec_10k_item_1": 180, ... },
  "by_dimension":    { "technology_stack": 150, ... }
}
```

**Core logic:** Calls `VectorStore.get_all_metadata()` and aggregates using `collections.Counter`.

---

### `GET /api/v1/rag/chatbot/{ticker}`

**Summary:** Answer a free-form question about a company using RAG + LLM.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |

**Query parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `question` | `string` | Yes | Free-form natural language question |
| `dimension` | `string` | No | Override dimension detection |
| `use_hyde` | `bool` | No (default `false`) | Enable HyDE retrieval |

**Response `200`:**
```json
{
  "answer": "...",
  "evidence": [ { "source_type": "...", "dimension": "...", "fiscal_year": "...", "content": "...", "score": 0.92 } ],
  "sources_used": 8,
  "dimension_detected": "technology_stack",
  "dim_confidence": 0.75,
  "ticker": "NVDA"
}
```

**Core logic:**
1. Input guardrails: `validate_ticker()`, `validate_question()`, `validate_dimension()`.
2. Dimension detection:
   - If `dimension` not supplied: runs `_detect_dimension_scored()` (keyword-weighted detector).
   - If confidence < 0.12 and not intentionally broad: falls back to `_detect_dimension_with_llm()` (Groq).
3. Retrieves top-8 docs via `_retrieve_with_fallback()` (dimension-specific source affinity, graceful fallback).
4. Context enrichment: appends structured dimension scores from Snowflake and signal summary.
5. Calls `ModelRouter.complete("chat_response", ...)` (routes to Claude Haiku).
6. Output guardrails: `check_no_refusal()`, `check_answer_grounded()`, `check_answer_length()`.

---

## CS3 TC + V^R Scoring

> **Prefix:** `/api/v1/scoring`

### `POST /api/v1/scoring/tc-vr/portfolio`

**Summary:** Compute Talent Concentration (TC) + V^R score for all 5 CS3 portfolio companies.

**Parameters:** None

**Response `200`:** `PortfolioTCVRResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"success"` or `"partial"` |
| `companies_scored` | `int` | |
| `companies_failed` | `int` | |
| `results` | `List[TCVRResponse]` | Per-company TC, V^R, job analysis, validation |
| `summary_table` | `List[Dict]` | Compact comparison table (TC, talent_risk_adj, weighted_dim_score, V^R, validation flags) |
| `duration_seconds` | `float` | |

**Portfolio:** `NVDA`, `JPM`, `WMT`, `GE`, `DG`

**Core logic:**
1. For each ticker calls `CompositeScoringService.compute_tc_vr()` (Task 5.0e + 5.2).
2. TC = talent concentration ratio derived from AI job posting analysis.
3. V^R = `talent_risk_adj × weighted_dim_score`; validates against CS3 Table 5 expected ranges.
4. Logs summary table to application logger.

---

## CS3 Position Factor

> **Prefix:** `/api/v1/scoring`

### `POST /api/v1/scoring/pf/portfolio`

**Summary:** Calculate Position Factor (PF) for all 5 CS3 portfolio companies.

**Parameters:** None

**Response `200`:** `PortfolioPFResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | |
| `companies_scored` | `int` | |
| `companies_failed` | `int` | |
| `results` | `List[PFResponse]` | Per-company VR score, market cap percentile, PF breakdown, validation |
| `summary_table` | `List[Dict]` | Compact comparison table |
| `duration_seconds` | `float` | |

**Core logic:**
1. For each ticker calls `CompositeScoringService.compute_pf()` (Task 6.0a).
2. `PF = 0.6 × VR_component + 0.4 × MCap_component`.
3. `VR_component` = normalised V^R relative to sector average; `MCap_component` = normalised market cap percentile.
4. Validates PF against CS3 Table 5 expected ranges.

---

## CS3 H^R (Human Readiness)

> **Prefix:** `/api/v1/scoring`

### `POST /api/v1/scoring/hr/portfolio`

**Summary:** Calculate H^R (Human Readiness) for all 5 CS3 portfolio companies.

**Parameters:** None

**Response `200`:** `PortfolioHRResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | |
| `companies_scored` | `int` | |
| `companies_failed` | `int` | |
| `results` | `List[HRResponse]` | Per-company sector, HR_base, PF, position_adjustment, H^R, validation |
| `summary_table` | `List[Dict]` | |
| `duration_seconds` | `float` | |

**Core logic:**
1. For each ticker calls `CompositeScoringService.compute_hr()` (Task 6.1).
2. `H^R = HR_base × (1 + 0.15 × PF)`.
3. `HR_base` is the sector-specific baseline human readiness score.
4. Validates H^R against expected ranges.

---

## CS3 Org-AI-R

> **Prefix:** `/api/v1/scoring`

### `POST /api/v1/scoring/orgair/results`

**Summary:** Generate result JSON files for all 5 companies (CS3 submission artefacts).

**Parameters:** None

**Response `200`:** `ResultsGenerationResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | |
| `files_generated` | `int` | |
| `local_files` | `List[str]` | Paths under `results/` |
| `s3_files` | `List[str]` | S3 keys under `scoring/results/` |
| `summary` | `List[Dict]` | Brief per-company summary |
| `duration_seconds` | `float` | |

**Core logic:**
1. Runs the full Org-AI-R pipeline (`compute_full_pipeline()`) for all 5 companies.
2. Generates `{ticker}.json` files containing: final Org-AI-R, V^R, H^R, synergy, 7 dimension scores, TC, PF, confidence intervals, job analysis, and validation flags.
3. Saves files both locally to `results/` and to S3 under `scoring/results/`.

---

### `POST /api/v1/scoring/orgair/portfolio`

**Summary:** Calculate Org-AI-R score for all 5 CS3 portfolio companies.

**Parameters:** None

**Response `200`:** `PortfolioOrgAIRResponse`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | |
| `companies_scored` | `int` | |
| `companies_failed` | `int` | |
| `results` | `List[OrgAIRResponse]` | Per-company V^R, H^R, synergy, Org-AI-R, breakdown, validation |
| `summary_table` | `List[Dict]` | |
| `duration_seconds` | `float` | |

**Core logic:**
1. For each ticker calls `CompositeScoringService.compute_orgair()` (Task 6.4).
2. `Org-AI-R = weighted_base + synergy_contribution`.
3. `weighted_base = 0.5 × V^R + 0.3 × H^R + 0.2 × PF`.
4. `synergy_contribution` reflects cross-dimension reinforcement effects.
5. Validates Org-AI-R against CS3 Table 5 expected ranges.

---

## Analyst Notes (CS4)

> **Prefix:** `/api/v1/analyst-notes`
> All endpoints resolve `{ticker}` to a company UUID or raise `404 COMPANY_NOT_FOUND`.

### `POST /api/v1/analyst-notes/{ticker}/interview`

**Summary:** Submit an interview transcript.

**Path parameters:** `ticker` — company ticker

**Request body:** `SubmitInterviewRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `interviewee` | `string` | Yes | Name of person interviewed |
| `interviewee_title` | `string` | Yes | Job title |
| `transcript` | `string` | Yes | Full interview transcript text |
| `assessor` | `string` | Yes | Analyst name |
| `dimensions_discussed` | `List[str] \| null` | No | V^R dimensions covered |

**Response `201`:** `NoteSubmittedResponse`

| Field | Type |
|-------|------|
| `note_id` | `string` |
| `company_id` | `string` |
| `note_type` | `string` (`"interview_transcript"`) |
| `dimension` | `string` |
| `assessor` | `string` |
| `created_at` | `string` |
| `s3_key` | `string \| null` |

**Core logic:** Calls `AnalystNotesCollector.submit_interview()` which indexes the transcript into ChromaDB, persists metadata to Snowflake, and uploads the raw text to S3.

---

### `POST /api/v1/analyst-notes/{ticker}/dd-finding`

**Summary:** Submit a due diligence finding.

**Path parameters:** `ticker`

**Request body:** `SubmitDDFindingRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | `string` | Yes | Short title |
| `finding` | `string` | Yes | Full finding text |
| `dimension` | `string` | Yes | Relevant V^R dimension |
| `severity` | `string` | Yes | One of: `critical`, `high`, `medium`, `low` |
| `assessor` | `string` | Yes | Analyst name |

**Response `201`:** `NoteSubmittedResponse` (note_type = `"dd_finding"`)

**Core logic:** `AnalystNotesCollector.submit_dd_finding()` → ChromaDB + Snowflake + S3.

---

### `POST /api/v1/analyst-notes/{ticker}/data-room`

**Summary:** Submit a data room document summary.

**Path parameters:** `ticker`

**Request body:** `SubmitDataRoomRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `document_name` | `string` | Yes | Source document filename |
| `summary` | `string` | Yes | Summary text |
| `dimension` | `string` | Yes | Relevant V^R dimension |
| `assessor` | `string` | Yes | Analyst name |

**Response `201`:** `NoteSubmittedResponse` (note_type = `"data_room_summary"`)

**Core logic:** `AnalystNotesCollector.submit_data_room_summary()` → ChromaDB + Snowflake + S3.

---

### `GET /api/v1/analyst-notes/{ticker}`

**Summary:** List all analyst notes for a company.

**Path parameters:** `ticker`

**Response `200`:** `ListNotesResponse`

| Field | Type |
|-------|------|
| `company_id` | `string` |
| `count` | `int` |
| `notes` | `List[AnalystNoteOut]` |

`AnalystNoteOut` fields: `note_id`, `company_id`, `note_type`, `content`, `dimension`, `assessor`, `confidence`, `metadata`, `created_at`, `s3_key`.

**Core logic:** `AnalystNotesCollector.list_notes(company_id)` — reads from in-memory cache with Snowflake fallback.

---

### `GET /api/v1/analyst-notes/{ticker}/{note_id}`

**Summary:** Get a single analyst note by ID.

**Path parameters:**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | `string` | Company ticker |
| `note_id` | `string` | Note UUID |

**Response `200`:** `AnalystNoteOut`

**Error:** `404 NOTE_NOT_FOUND`

**Core logic:** `AnalystNotesCollector.get_note(note_id)` — in-memory cache first, then Snowflake fallback.

---

### `POST /api/v1/analyst-notes/{ticker}/load`

**Summary:** Restore in-memory cache from Snowflake (after a server restart).

**Path parameters:** `ticker`

**Response `200`:** `ListNotesResponse`

**Core logic:**
1. Calls `AnalystNotesCollector.load_from_snowflake(company_id)`.
2. Re-fetches all notes from Snowflake and S3.
3. Re-indexes them into ChromaDB.
4. Repopulates the in-memory cache.

---

*Generated 2026-03-18*
