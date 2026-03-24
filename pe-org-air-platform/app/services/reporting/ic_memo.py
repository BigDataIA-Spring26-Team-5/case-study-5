"""IC Memo Generator — produces Investment Committee memo as a PDF document.

Requires:  weasyprint>=60.0
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

IC_MEMO_CSS = """\
body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    margin: 40px 50px;
    font-size: 11pt;
    color: #333;
    line-height: 1.5;
}
h1 {
    font-size: 20pt;
    color: #1a365d;
    border-bottom: 2px solid #1a365d;
    padding-bottom: 8px;
    margin-bottom: 4px;
}
h2 {
    font-size: 14pt;
    color: #2c5282;
    margin-top: 28px;
    margin-bottom: 8px;
}
h3 {
    font-size: 12pt;
    color: #2c5282;
    margin-top: 20px;
    margin-bottom: 6px;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
}
th {
    background-color: #1a365d;
    color: white;
    padding: 8px 12px;
    text-align: left;
    font-size: 10pt;
}
td {
    padding: 8px 12px;
    border: 1px solid #ccc;
    font-size: 10pt;
}
tr:nth-child(even) td {
    background-color: #f7fafc;
}
.meta {
    color: #666;
    font-size: 10pt;
    margin: 2px 0;
}
.confidential {
    color: #c53030;
    font-weight: bold;
    font-style: italic;
    border: 1px solid #c53030;
    padding: 6px 12px;
    display: inline-block;
    margin-top: 8px;
}
.recommendation {
    font-weight: bold;
    border-left: 4px solid;
    padding: 8px 14px;
    margin-top: 8px;
}
.rec-proceed {
    color: #276749;
    border-color: #276749;
    background-color: #f0fff4;
}
.rec-monitor {
    color: #975a16;
    border-color: #975a16;
    background-color: #fffff0;
}
.rec-conditional {
    color: #c53030;
    border-color: #c53030;
    background-color: #fff5f5;
}
ul.gaps {
    padding-left: 20px;
}
ul.gaps li {
    margin: 6px 0;
}
"""


class ICMemoGenerator:
    """Generates IC memo PDF document from DD results.

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
        company_id: str,
        scoring_result: Dict[str, Any],
        gap_analysis: Dict[str, Any],
        ebitda_projection: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> str:
        """Generate IC memo and save as .pdf (or .txt fallback).

        Returns:
            Path to the generated file.
        """
        org_air = scoring_result.get("org_air", 0.0)
        vr = scoring_result.get("vr_score", 0.0)
        hr = scoring_result.get("hr_score", 0.0)
        date_str = datetime.utcnow().strftime("%B %d, %Y")

        HTML = self._try_weasyprint()
        if HTML is None:
            logger.warning("weasyprint not installed — saving as .txt")
            return self._generate_text(
                company_id, org_air, vr, hr, gap_analysis, ebitda_projection, output_path, date_str
            )

        try:
            cid = html.escape(str(company_id))

            # Executive summary
            readiness = (
                "Strong AI readiness positions for value creation."
                if org_air >= 70
                else "Improvement opportunities identified across key dimensions."
            )

            # Scoring table rows
            scoring_rows = ""
            for metric, score, bench in [
                ("Org-AI-R", org_air, 65.0),
                ("V^R (Valuation Readiness)", vr, 60.0),
                ("H^R (Human Capital Risk)", hr, 60.0),
            ]:
                scoring_rows += (
                    f"<tr><td>{html.escape(metric)}</td>"
                    f"<td>{float(score):.1f}</td>"
                    f"<td>{float(bench):.1f}</td></tr>\n"
                )

            # Dimension scores table
            dim_scores = scoring_result.get("dimension_scores", {})
            dim_html = ""
            if dim_scores:
                dim_rows = ""
                for dim, score in dim_scores.items():
                    dim_name = html.escape(str(dim).replace("_", " ").title())
                    dim_rows += f"<tr><td>{dim_name}</td><td>{float(score or 0.0):.1f}</td></tr>\n"
                dim_html = f"""
                <h3>Dimension Scores</h3>
                <table>
                    <thead><tr><th>Dimension</th><th>Score</th></tr></thead>
                    <tbody>{dim_rows}</tbody>
                </table>"""

            # Gap analysis
            gaps = gap_analysis.get("dimension_gaps", [])
            if gaps:
                gap_items = ""
                for gap in gaps[:5]:
                    dim_name = html.escape(str(gap.get("dimension", "")).replace("_", " ").title())
                    current = float(gap.get("current_score", 0) or 0)
                    target = float(gap.get("target_score", 0) or 0)
                    priority = html.escape(str(gap.get("priority", "N/A")))
                    gap_items += (
                        f"<li>{dim_name}: current {current:.1f} &rarr; "
                        f"target {target:.1f} [Priority: {priority}]</li>\n"
                    )
                gaps_html = f'<ul class="gaps">{gap_items}</ul>'
            else:
                gaps_html = "<p>No gap analysis data available.</p>"

            # EBITDA projection
            scenarios = ebitda_projection.get("scenarios", {})
            base_val = html.escape(str(scenarios.get("base", "N/A")))
            risk_val = html.escape(str(ebitda_projection.get("risk_adjusted", "N/A")))
            ebitda_bullets = ""
            if scenarios:
                for scenario, value in scenarios.items():
                    ebitda_bullets += f"<li>{html.escape(str(scenario).title())}: {html.escape(str(value))}</li>\n"
                ebitda_bullets = f"<ul>{ebitda_bullets}</ul>"

            # IC recommendation
            if org_air >= 75:
                rec_text = "PROCEED — strong AI readiness supports full capital deployment"
                rec_class = "rec-proceed"
            elif org_air >= 60:
                rec_text = "PROCEED WITH MONITORING — address top 2 dimension gaps within 90 days"
                rec_class = "rec-monitor"
            else:
                rec_text = "CONDITIONAL — address critical gaps before next capital deployment"
                rec_class = "rec-conditional"

            html_str = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{IC_MEMO_CSS}</style></head>
<body>
    <h1>Investment Committee Memo: {cid}</h1>
    <p class="meta">Date: {html.escape(date_str)}</p>
    <p class="meta">Prepared by: PE Org-AI-R Agentic Platform</p>
    <p class="confidential">CONFIDENTIAL</p>

    <h2>Executive Summary</h2>
    <p>{cid} has an Org-AI-R score of {org_air:.1f}, reflecting V^R of {vr:.1f}
       and H^R of {hr:.1f}. {html.escape(readiness)}</p>

    <h2>AI Readiness Assessment</h2>
    <table>
        <thead><tr><th>Metric</th><th>Score</th><th>Benchmark</th></tr></thead>
        <tbody>{scoring_rows}</tbody>
    </table>
    {dim_html}

    <h2>Gap Analysis &amp; 100-Day Plan</h2>
    {gaps_html}

    <h2>EBITDA Impact Projection</h2>
    <p>Base case EBITDA improvement: {base_val} | Risk-adjusted: {risk_val}</p>
    {ebitda_bullets}

    <h2>IC Recommendation</h2>
    <p class="recommendation {rec_class}">Recommendation: {html.escape(rec_text)}</p>
</body>
</html>"""

            path = output_path or f"ic_memo_{company_id}_{datetime.now().strftime('%Y%m%d')}.pdf"
            HTML(string=html_str).write_pdf(path)
            logger.info("IC memo saved to %s", path)
            return path
        except Exception as exc:
            logger.exception("IC memo PDF generation failed; falling back to text: %s", exc)
            return self._generate_text(
                company_id, org_air, vr, hr, gap_analysis, ebitda_projection, output_path, date_str
            )

    def _generate_text(
        self,
        company_id: str,
        org_air: float,
        vr: float,
        hr: float,
        gap_analysis: Dict[str, Any],
        ebitda_projection: Dict[str, Any],
        output_path: Optional[str],
        date_str: str,
    ) -> str:
        path = output_path or f"ic_memo_{company_id}_{datetime.now().strftime('%Y%m%d')}.txt"
        scenarios = ebitda_projection.get("scenarios", {})
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"INVESTMENT COMMITTEE MEMO: {company_id}\n")
            f.write(f"Date: {date_str} | CONFIDENTIAL\n\n")
            f.write(f"Org-AI-R: {org_air:.1f} | V^R: {vr:.1f} | H^R: {hr:.1f}\n")
            f.write(f"EBITDA Base: {scenarios.get('base', 'N/A')} | "
                    f"Risk-adj: {ebitda_projection.get('risk_adjusted', 'N/A')}\n")
        return path


# Module-level singleton
ic_memo_generator = ICMemoGenerator()
