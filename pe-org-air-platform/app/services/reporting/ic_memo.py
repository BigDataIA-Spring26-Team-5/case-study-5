"""IC Memo Generator — produces Investment Committee memo as a Word document.

Requires:  python-docx>=1.1.0  (add to requirements.txt)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ICMemoGenerator:
    """Generates IC memo Word document from DD results.

    Falls back to plain-text if python-docx is not installed.
    """

    def _try_docx(self) -> Optional[Any]:
        try:
            from docx import Document  # type: ignore[import]
            return Document
        except ImportError:
            return None

    def generate(
        self,
        company_id: str,
        scoring_result: Dict[str, Any],
        gap_analysis: Dict[str, Any],
        ebitda_projection: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> str:
        """Generate IC memo and save as .docx (or .txt fallback).

        Returns:
            Path to the generated file.
        """
        org_air = scoring_result.get("org_air", 0.0)
        vr = scoring_result.get("vr_score", 0.0)
        hr = scoring_result.get("hr_score", 0.0)
        date_str = datetime.utcnow().strftime("%B %d, %Y")

        Document = self._try_docx()
        if Document is None:
            logger.warning("python-docx not installed — saving as .txt")
            return self._generate_text(
                company_id, org_air, vr, hr, gap_analysis, ebitda_projection, output_path, date_str
            )

        doc = Document()

        # Title
        doc.add_heading(f"Investment Committee Memo: {company_id}", 0)
        doc.add_paragraph(f"Date: {date_str}")
        doc.add_paragraph("Prepared by: PE Org-AI-R Agentic Platform")
        doc.add_paragraph("CONFIDENTIAL", style="Intense Quote")

        # Executive Summary
        doc.add_heading("Executive Summary", level=1)
        readiness = "Strong AI readiness positions for value creation." if org_air >= 70 else \
                    "Improvement opportunities identified across key dimensions."
        doc.add_paragraph(
            f"{company_id} has an Org-AI-R score of {org_air:.1f}, "
            f"reflecting V^R of {vr:.1f} and H^R of {hr:.1f}. {readiness}"
        )

        # Scoring Table
        doc.add_heading("AI Readiness Assessment", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "Metric", "Score", "Benchmark"
        for metric, score, bench in [
            ("Org-AI-R", org_air, 65.0),
            ("V^R (Valuation Readiness)", vr, 60.0),
            ("H^R (Human Capital Risk)", hr, 60.0),
        ]:
            row = table.add_row().cells
            row[0].text = metric
            row[1].text = f"{score:.1f}"
            row[2].text = f"{bench:.1f}"

        # Dimension Scores
        dim_scores = scoring_result.get("dimension_scores", {})
        if dim_scores:
            doc.add_heading("Dimension Scores", level=2)
            dim_table = doc.add_table(rows=1, cols=2)
            dim_table.style = "Table Grid"
            dim_hdr = dim_table.rows[0].cells
            dim_hdr[0].text, dim_hdr[1].text = "Dimension", "Score"
            for dim, score in dim_scores.items():
                row = dim_table.add_row().cells
                row[0].text = dim.replace("_", " ").title()
                row[1].text = f"{score:.1f}"

        # Gap Analysis
        doc.add_heading("Gap Analysis & 100-Day Plan", level=1)
        gaps = gap_analysis.get("dimension_gaps", [])
        if gaps:
            for gap in gaps[:5]:
                doc.add_paragraph(
                    f"{gap.get('dimension', '').replace('_', ' ').title()}: "
                    f"current {gap.get('current_score', 0):.1f} → "
                    f"target {gap.get('target_score', 0):.1f}  "
                    f"[Priority: {gap.get('priority', 'N/A')}]",
                    style="List Bullet",
                )
        else:
            doc.add_paragraph("No gap analysis data available.")

        # EBITDA Projection
        doc.add_heading("EBITDA Impact Projection", level=1)
        scenarios = ebitda_projection.get("scenarios", {})
        doc.add_paragraph(
            f"Base case EBITDA improvement: {scenarios.get('base', 'N/A')} | "
            f"Risk-adjusted: {ebitda_projection.get('risk_adjusted', 'N/A')}"
        )
        if scenarios:
            for scenario, value in scenarios.items():
                doc.add_paragraph(f"{scenario.title()}: {value}", style="List Bullet")

        # IC Recommendation
        doc.add_heading("IC Recommendation", level=1)
        if org_air >= 75:
            rec = "PROCEED — strong AI readiness supports full capital deployment"
        elif org_air >= 60:
            rec = "PROCEED WITH MONITORING — address top 2 dimension gaps within 90 days"
        else:
            rec = "CONDITIONAL — address critical gaps before next capital deployment"
        doc.add_paragraph(f"Recommendation: {rec}", style="Intense Quote")

        path = output_path or f"ic_memo_{company_id}_{datetime.now().strftime('%Y%m%d')}.docx"
        doc.save(path)
        logger.info("IC memo saved to %s", path)
        return path

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
        with open(path, "w") as f:
            f.write(f"INVESTMENT COMMITTEE MEMO: {company_id}\n")
            f.write(f"Date: {date_str} | CONFIDENTIAL\n\n")
            f.write(f"Org-AI-R: {org_air:.1f} | V^R: {vr:.1f} | H^R: {hr:.1f}\n")
            f.write(f"EBITDA Base: {scenarios.get('base', 'N/A')} | "
                    f"Risk-adj: {ebitda_projection.get('risk_adjusted', 'N/A')}\n")
        return path


# Module-level singleton
ic_memo_generator = ICMemoGenerator()
