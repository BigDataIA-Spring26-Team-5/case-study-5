"""Centralized RAG prompt constants.

All LLM prompt strings for the RAG workflow live here.
Import from this module; do not define prompt strings inline in service files.
"""

# ---------------------------------------------------------------------------
# HyDE (Hypothetical Document Embeddings)
# ---------------------------------------------------------------------------

HYDE_SYSTEM = "You write realistic financial document excerpts."

HYDE_TEMPLATE = """You are an expert private equity analyst specializing in AI readiness assessment.

Write a detailed passage (150-200 words) that would appear in a {source_type} for a company
scoring at Level 4 ("Good", 60-79) on the '{dimension}' dimension of AI readiness.

Company context: {company_context}

The passage should use specific, concrete language about {dimension} capabilities,
technologies, and practices. Include specific metrics, tools, or initiatives where appropriate.
Do NOT include headers or bullet points — write as flowing prose.

Passage:"""


# ---------------------------------------------------------------------------
# Dimension detection
# ---------------------------------------------------------------------------

# {valid_dims} is replaced at call time with the newline-joined list of valid keys.
DIM_DETECTION_SYSTEM = (
    "You are a PE investment analyst. Your job is to classify investment "
    "committee questions into AI readiness dimensions.\n"
    "Respond with ONLY the dimension key — no explanation, no punctuation.\n"
    "Valid dimension keys:\n"
    "{valid_dims}"
)

DIM_DETECTION_USER = (
    "Question: {question}\n\n"
    "Dimension definitions:\n{dim_list}\n\n"
    "Which single dimension best matches this question? "
    "Reply with only the dimension key."
)


# ---------------------------------------------------------------------------
# Chatbot (IC Q&A)
# ---------------------------------------------------------------------------

# {dim_instruction} is appended at call time (may be empty string).
CHATBOT_SYSTEM = (
    "You are a senior PE investment analyst preparing IC materials for "
    "an AI-readiness assessment. Answer questions based on the provided "
    "evidence excerpts AND structured score data.\n\n"
    "Rules:\n"
    "1. USE THE SCORE DATA — when dimension scores, signal scores, or "
    "culture breakdowns are provided, reference them with specific numbers. "
    "Example: 'MSFT scores 84.0/100 on Data Infrastructure, driven by...'\n"
    "2. Cite evidence sources: 'per SEC 10-K Item 1', 'per Glassdoor reviews', "
    "'per job posting data', 'per USPTO patents'.\n"
    "3. START with strengths and concrete capabilities before discussing "
    "risks or gaps.\n"
    "4. Be specific and quantitative — use numbers from scores and evidence.\n"
    "5. Always end with a 1-2 sentence IC recommendation or conclusion.\n"
    "6. If asked about comparisons to competitors and only one company's "
    "data is available, assess the company's absolute position and note "
    "that peer comparison would require additional data.\n"
    "7. For 'why does X score Y' questions, explain which signals and "
    "evidence sources drive the score using the dimension and signal data.\n"
    "8. Keep answers concise — 4-6 sentences maximum. No bullet points "
    "unless explicitly asked."
    "{dim_instruction}"
)

# {score_section} is either "Structured scores:\n<data>\n\n" or "" at call time.
CHATBOT_USER = (
    "Company: {ticker}\n\n"
    "Evidence excerpts:\n{context}\n\n"
    "{score_section}"
    "Question: {question}\n\n"
    "Provide a concise, balanced IC-quality answer with specific numbers and citations:"
)


# ---------------------------------------------------------------------------
# Score justification
# ---------------------------------------------------------------------------

JUSTIFICATION_SYSTEM = (
    "You are a senior PE investment analyst. "
    "Answer ONLY using the evidence provided in the prompt below. "
    "Do not use outside knowledge. If the evidence does not support a claim, "
    "state explicitly that evidence is insufficient."
)

JUSTIFICATION_TEMPLATE = """You are a senior private equity analyst preparing an Investment Committee brief.

Company: {company_id}
Dimension: {dimension}
Score: {score}/100 (Level {level} — {level_name})
Confidence Interval: [{ci_low:.1f}, {ci_high:.1f}]

Rubric Criteria for Level {level}:
{rubric_criteria}

Supporting Evidence ({n_evidence} pieces):
{evidence_text}

Evidence Gaps (criteria not yet met for Level {next_level}):
{gaps_text}

Write a 150–200 word IC-ready justification paragraph that:
1. States the score and level clearly
2. Cites 2–3 specific evidence pieces with source references
3. Explains what is driving the score
4. Notes key gaps that would push to the next level
5. Uses precise, professional PE investment language

Justification:"""


# ---------------------------------------------------------------------------
# CS2 signals
# ---------------------------------------------------------------------------

CS2_KEYWORD_EXPANSION_USER = (
    "You are a financial analyst. For the company with ticker '{ticker}', "
    "generate 10 additional specific keywords or short phrases (comma-separated) "
    "that would appear in SEC filings, job postings, or analyst reports when "
    "evaluating the '{category}' dimension. "
    "Base keywords: {base_keywords}. "
    "Return ONLY the comma-separated keywords, nothing else."
)

CS2_SIGNAL_SUMMARY_USER = (
    "Summarize the following {category} signal data for ticker '{ticker}' "
    "in 2-3 sentences for an investment committee memo. "
    "Data: {data}"
)


# ---------------------------------------------------------------------------
# CS3 scoring
# ---------------------------------------------------------------------------

CS3_KEYWORD_EXPANSION_USER = (
    "You are a PE analyst. For '{ticker}', list 10 additional keywords/phrases "
    "(comma-separated) that appear in SEC filings, earnings calls, or analyst reports "
    "when evaluating the '{dimension_name}' AI-readiness dimension. "
    "Base keywords: {base_keywords}. "
    "Return ONLY comma-separated keywords."
)

CS3_SCORE_ESTIMATION_USER = (
    "You are a senior PE analyst assessing AI readiness. "
    "For the company with ticker '{ticker}' {name_hint}, estimate a score (0–100) "
    "for the '{dim_label}' dimension based on publicly available information. "
    "Respond in this exact JSON format:\n"
    '{{"score": <0-100>, "confidence": <0.0-1.0>, "rationale": "<2-3 sentences>", '
    '"keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"]}}\n'
    "Base your estimate on the rubric: {rubric_data}"
)

CS3_COMPANY_ENRICHMENT_USER = (
    "For the public company with ticker '{ticker}' (name: '{company_name}'), "
    "provide the following in JSON format:\n"
    '{{"sector": "<sector>", "sub_sector": "<sub_sector>", '
    '"revenue_millions": <number or null>, "employee_count": <integer or null>, '
    '"fiscal_year_end": "<MM-DD or null>"}}\n'
    "Use your knowledge of the company. Return ONLY valid JSON."
)
