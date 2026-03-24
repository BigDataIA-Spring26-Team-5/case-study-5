"""LP Letter Generator — produces LP update letter summarizing Fund-AI-R metrics.

Requires:  weasyprint>=60.0
"""
from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LP_LETTER_TEMPLATE = """\
Dear Limited Partners,

We are pleased to share the quarterly AI Readiness update for {fund_id}.

FUND PERFORMANCE SUMMARY
-------------------------
Fund-AI-R Score:     {fund_air:.1f} / 100
Portfolio Companies: {company_count}
AI Leaders (≥70):    {leaders}
AI Laggards (<50):   {laggards}

PORTFOLIO HIGHLIGHTS
--------------------
{highlights}

VALUE CREATION PIPELINE
------------------------
{value_creation}

Our agentic due diligence platform continues to surface actionable insights
across the portfolio. The Org-AI-R framework identifies specific improvement
vectors for each company, enabling precise capital deployment decisions.

We remain committed to driving AI-readiness across the portfolio and look
forward to sharing further progress in our next update.

Sincerely,
The Investment Team
{fund_id}
{date}
"""

LP_LETTER_CSS = """\
body {
    font-family: Georgia, 'Times New Roman', serif;
    margin: 50px 60px;
    font-size: 11pt;
    color: #333;
    line-height: 1.6;
}
h1 {
    font-size: 18pt;
    color: #1a365d;
    border-bottom: 1px solid #1a365d;
    padding-bottom: 6px;
    margin-bottom: 20px;
}
h2 {
    font-size: 13pt;
    color: #2c5282;
    margin-top: 24px;
    margin-bottom: 8px;
}
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 8px 0;
}
ul {
    padding-left: 20px;
}
li {
    margin: 4px 0;
}
p {
    margin: 4px 0;
}
"""


class LPLetterGenerator:
    """Generates LP update letters from Fund-AI-R metrics.

    Falls back to plain-text if weasyprint is not installed.
    """

    def _try_weasyprint(self) -> Optional[Any]:
        try:
            from weasyprint import HTML  # type: ignore[import]
            return HTML
        except Exception:
            return None

    def generate(
        self,
        fund_id: str,
        fund_metrics: Dict[str, Any],
        company_scores: List[Dict[str, Any]],
        output_path: Optional[str] = None,
    ) -> str:
        """Generate LP update letter and save as .pdf (or .txt fallback).

        Args:
            fund_id: Fund identifier
            fund_metrics: Dict with fund_air, company_count, etc.
            company_scores: List of dicts with ticker, org_air, sector
            output_path: Override default file path

        Returns:
            Path to the generated file.
        """
        leaders = sum(1 for c in company_scores if c.get("org_air", 0) >= 70)
        laggards = sum(1 for c in company_scores if c.get("org_air", 0) < 50)
        date_str = datetime.utcnow().strftime("%B %d, %Y")

        sorted_companies = sorted(
            company_scores, key=lambda x: x.get("org_air", 0), reverse=True
        )
        highlights = "\n".join([
            f"  • {c.get('ticker', '?')}: Org-AI-R {c.get('org_air', 0):.1f} "
            f"({'Leader' if c.get('org_air', 0) >= 70 else 'Developing'})"
            for c in sorted_companies
        ])

        top = sorted_companies[0] if sorted_companies else {}
        value_creation = (
            f"  • {top.get('ticker', 'N/A')} leads the portfolio at "
            f"{top.get('org_air', 0):.1f} Org-AI-R. "
            f"Gap analysis identifies data infrastructure and talent as primary value levers."
        )

        # Get fund_air from the right field name (handles both CS5 and legacy)
        fund_air = fund_metrics.get("fund_air", fund_metrics.get("fund_air_score", 0.0))

        letter = LP_LETTER_TEMPLATE.format(
            fund_id=fund_id,
            fund_air=fund_air,
            company_count=len(company_scores),
            leaders=leaders,
            laggards=laggards,
            highlights=highlights,
            value_creation=value_creation,
            date=date_str,
        )

        HTML = self._try_weasyprint()
        path = output_path or f"lp_letter_{fund_id}_{datetime.now().strftime('%Y%m%d')}.pdf"

        if HTML is None:
            logger.warning("weasyprint not installed — saving as .txt")
            txt_path = path.replace(".pdf", ".txt")
            with open(txt_path, "w") as f:
                f.write(letter)
            logger.info("LP letter saved to %s", txt_path)
            return txt_path

        try:
            body_parts = [f"<h1>LP Update &mdash; {html_mod.escape(fund_id)}</h1>"]
            in_list = False

            for line in letter.strip().split("\n"):
                stripped = line.strip()
                is_bullet = stripped.startswith("\u2022") or stripped.startswith("  \u2022")

                if is_bullet:
                    if not in_list:
                        body_parts.append("<ul>")
                        in_list = True
                    text = stripped.lstrip(" \u2022").strip()
                    body_parts.append(f"<li>{html_mod.escape(text)}</li>")
                else:
                    if in_list:
                        body_parts.append("</ul>")
                        in_list = False

                    if stripped.startswith("---"):
                        body_parts.append("<hr>")
                    elif stripped.isupper() and len(stripped) > 5:
                        body_parts.append(f"<h2>{html_mod.escape(stripped.title())}</h2>")
                    elif stripped == "":
                        continue
                    else:
                        body_parts.append(f"<p>{html_mod.escape(stripped)}</p>")

            if in_list:
                body_parts.append("</ul>")

            body_html = "\n".join(body_parts)
            html_str = (
                f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f"<style>{LP_LETTER_CSS}</style></head>"
                f"<body>{body_html}</body></html>"
            )

            HTML(string=html_str).write_pdf(path)
            logger.info("LP letter saved to %s", path)
            return path
        except Exception as exc:
            logger.exception("LP letter PDF generation failed; falling back to text: %s", exc)
            txt_path = path.replace(".pdf", ".txt")
            with open(txt_path, "w") as f:
                f.write(letter)
            return txt_path


# Module-level singleton
lp_letter_generator = LPLetterGenerator()
