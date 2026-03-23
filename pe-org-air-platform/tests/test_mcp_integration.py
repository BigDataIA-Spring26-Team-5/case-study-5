"""CS5 MCP integration tests — verify tools call real CS1-CS4 services.

T.1: test_calculate_org_air_calls_cs3 — proves MCP tool calls CS3Client
T.2: test_no_hardcoded_data — proves tools don't return hardcoded fallbacks
"""

import asyncio
from unittest.mock import patch, MagicMock

import pytest

# Eagerly import the module so patch.object works reliably
# (avoids pkgutil.resolve_name issues with string-based patch targets).
import app.mcp.server as _mcp_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_mock_assessment(ticker: str = "NVDA"):
    """Build a minimal mock CompanyAssessment."""
    from app.services.integration.cs3_client import (
        CompanyAssessment, DimensionScore, score_to_level,
    )
    level, level_name = score_to_level(85.0)
    dim_score = DimensionScore(
        dimension="data_infrastructure", score=85.0,
        level=level, level_name=level_name,
    )
    return CompanyAssessment(
        company_id=ticker,
        ticker=ticker,
        dimension_scores={"data_infrastructure": dim_score},
        org_air_score=84.94,
        valuation_risk=78.0,
        human_capital_risk=72.0,
        synergy=0.12,
    )


# ---------------------------------------------------------------------------
# T.1 — Verify CS3 client is actually invoked
# ---------------------------------------------------------------------------

def test_calculate_org_air_calls_cs3():
    """Patches CS3Client.get_assessment and verifies the MCP tool calls it."""
    mock_assessment = _make_mock_assessment("NVDA")

    with patch.object(_mcp_server, "_cs3") as mock_cs3_factory:
        mock_client = MagicMock()
        mock_client.get_assessment.return_value = mock_assessment
        mock_cs3_factory.return_value = mock_client

        result = _run(_mcp_server._calculate_org_air_score({"company_id": "NVDA"}))

        mock_client.get_assessment.assert_called_once_with("NVDA")
        assert result["company_id"] == "NVDA"
        assert result["org_air"] == 84.94


# ---------------------------------------------------------------------------
# T.2 — Verify tools do NOT return hardcoded data when CS3 is down
# ---------------------------------------------------------------------------

def test_no_hardcoded_data():
    """When CS3Client raises ConnectionError, the tool must propagate the error
    rather than returning hardcoded/fallback scores."""

    with patch.object(_mcp_server, "_cs3") as mock_cs3_factory:
        mock_client = MagicMock()
        mock_client.get_assessment.side_effect = ConnectionError("CS3 not running")
        mock_cs3_factory.return_value = mock_client

        with pytest.raises(ConnectionError):
            _run(_mcp_server._calculate_org_air_score({"company_id": "NVDA"}))
