# tests/test_signals.py

"""
Signal Tests - Tests for signal models and API endpoints
"""

import pytest
from uuid import uuid4, UUID
from datetime import datetime, timezone
from pydantic import ValidationError
from fastapi import status

from app.models.signal import (
    SignalCategory,
    SignalSource,
    ExternalSignal,
    CompanySignalSummary,
    JobPosting,
    Patent,
    JobScoreBreakdown,
    JobAnalysisResult,
    JobAnalysisResponse,
    TechScoreBreakdown,
    TechAnalysisResult,
    TechAnalysisResponse,
    PatentScoreBreakdown,
    PatentAnalysisResult,
    PatentAnalysisResponse,
    LeadershipScoreBreakdown,
    LeadershipAnalysisResult,
    LeadershipAnalysisResponse,
    CombinedAnalysisResponse,
)


# SIGNAL CATEGORY ENUM TESTS


class TestSignalCategoryEnum:
    """Tests for SignalCategory enumeration."""

    def test_all_categories_exist(self):
        """Test that all signal categories are defined."""
        expected = [
            "technology_hiring",
            "innovation_activity",
            "digital_presence",
            "leadership_signals",
            "glassdoor_culture",
            "board_governance",
        ]
        actual = [c.value for c in SignalCategory]
        assert actual == expected

    def test_category_count(self):
        """Test that exactly 6 signal categories exist."""
        assert len(SignalCategory) == 6


class TestSignalSourceEnum:
    """Tests for SignalSource enumeration."""

    def test_all_sources_exist(self):
        """Test that all signal sources are defined."""
        expected = [
            "linkedin", "indeed", "glassdoor",
            "uspto",
            "builtwith",
            "press_release", "company_website",
            "sec_filing",
            "wappalyzer", "builtwith_wappalyzer", "board_proxy",
        ]
        actual = [s.value for s in SignalSource]
        assert actual == expected

    def test_source_count(self):
        """Test that exactly 11 signal sources exist."""
        assert len(SignalSource) == 11



# EXTERNAL SIGNAL MODEL TESTS


class TestExternalSignal:
    """Tests for ExternalSignal model validation."""

    def test_valid_external_signal(self, sample_company_id):
        """Test creating a valid external signal."""
        signal = ExternalSignal(
            company_id=sample_company_id,
            category=SignalCategory.TECHNOLOGY_HIRING,
            source=SignalSource.LINKEDIN,
            signal_date=datetime.now(timezone.utc),
            raw_value="Senior ML Engineer role posted",
            normalized_score=75.0,
            confidence=0.85
        )
        assert signal.normalized_score == 75.0
        assert signal.confidence == 0.85
        assert signal.category == SignalCategory.TECHNOLOGY_HIRING

    def test_signal_default_values(self, sample_company_id):
        """Test that default values are applied."""
        signal = ExternalSignal(
            company_id=sample_company_id,
            category=SignalCategory.INNOVATION_ACTIVITY,
            source=SignalSource.USPTO,
            signal_date=datetime.now(timezone.utc),
            raw_value="AI Patent filed",
            normalized_score=50.0
        )
        assert signal.confidence == 0.8  # Default
        assert signal.metadata == {}  # Default empty dict
        assert signal.id is not None  # Auto-generated

    def test_invalid_score_too_high(self, sample_company_id):
        """Test that normalized_score > 100 raises validation error."""
        with pytest.raises(ValidationError):
            ExternalSignal(
                company_id=sample_company_id,
                category=SignalCategory.DIGITAL_PRESENCE,
                source=SignalSource.BUILTWITH,
                signal_date=datetime.now(timezone.utc),
                raw_value="Tech stack analysis",
                normalized_score=150.0
            )

    def test_invalid_score_negative(self, sample_company_id):
        """Test that normalized_score < 0 raises validation error."""
        with pytest.raises(ValidationError):
            ExternalSignal(
                company_id=sample_company_id,
                category=SignalCategory.DIGITAL_PRESENCE,
                source=SignalSource.BUILTWITH,
                signal_date=datetime.now(timezone.utc),
                raw_value="Tech stack analysis",
                normalized_score=-10.0
            )

    def test_invalid_confidence_too_high(self, sample_company_id):
        """Test that confidence > 1 raises validation error."""
        with pytest.raises(ValidationError):
            ExternalSignal(
                company_id=sample_company_id,
                category=SignalCategory.LEADERSHIP_SIGNALS,
                source=SignalSource.SEC_FILING,
                signal_date=datetime.now(timezone.utc),
                raw_value="CEO tech background",
                normalized_score=80.0,
                confidence=1.5
            )

    def test_score_boundary_zero(self, sample_company_id):
        """Test that normalized_score = 0 is valid."""
        signal = ExternalSignal(
            company_id=sample_company_id,
            category=SignalCategory.TECHNOLOGY_HIRING,
            source=SignalSource.INDEED,
            signal_date=datetime.now(timezone.utc),
            raw_value="No AI roles",
            normalized_score=0.0
        )
        assert signal.normalized_score == 0.0

    def test_score_boundary_hundred(self, sample_company_id):
        """Test that normalized_score = 100 is valid."""
        signal = ExternalSignal(
            company_id=sample_company_id,
            category=SignalCategory.TECHNOLOGY_HIRING,
            source=SignalSource.LINKEDIN,
            signal_date=datetime.now(timezone.utc),
            raw_value="Strong AI hiring",
            normalized_score=100.0
        )
        assert signal.normalized_score == 100.0



# COMPANY SIGNAL SUMMARY MODEL TESTS


class TestCompanySignalSummary:
    """Tests for CompanySignalSummary model validation."""

    def test_valid_summary_all_scores(self, sample_company_id):
        """Test creating a valid summary with all scores."""
        summary = CompanySignalSummary(
            company_id=sample_company_id,
            ticker="TEST",
            technology_hiring_score=80.0,
            innovation_activity_score=70.0,
            digital_presence_score=60.0,
            leadership_signals_score=50.0,
            signal_count=10
        )
        assert summary.technology_hiring_score == 80.0
        # Composite should be calculated: 0.30*80 + 0.25*70 + 0.25*60 + 0.20*50 = 66.5
        assert summary.composite_score == pytest.approx(66.5, rel=0.01)

    def test_summary_partial_scores_no_composite(self, sample_company_id):
        """Test that composite is None when not all scores present."""
        summary = CompanySignalSummary(
            company_id=sample_company_id,
            ticker="TEST",
            technology_hiring_score=80.0,
            innovation_activity_score=70.0,
            signal_count=5
        )
        assert summary.composite_score is None

    def test_summary_default_values(self, sample_company_id):
        """Test that default values are applied."""
        summary = CompanySignalSummary(
            company_id=sample_company_id,
            ticker="TEST"
        )
        assert summary.signal_count == 0
        assert summary.technology_hiring_score is None
        assert summary.composite_score is None

    def test_invalid_score_too_high(self, sample_company_id):
        """Test that score > 100 raises validation error."""
        with pytest.raises(ValidationError):
            CompanySignalSummary(
                company_id=sample_company_id,
                ticker="TEST",
                technology_hiring_score=150.0
            )

    def test_invalid_score_negative(self, sample_company_id):
        """Test that score < 0 raises validation error."""
        with pytest.raises(ValidationError):
            CompanySignalSummary(
                company_id=sample_company_id,
                ticker="TEST",
                innovation_activity_score=-10.0
            )



# JOB POSTING MODEL TESTS


class TestJobPosting:
    """Tests for JobPosting model validation."""

    def test_valid_job_posting(self, sample_company_id):
        """Test creating a valid job posting."""
        posting = JobPosting(
            company_id=str(sample_company_id),
            company_name="Test Company",
            title="Machine Learning Engineer",
            description="Build ML models for production",
            location="Remote",
            source="linkedin",
            ai_keywords_found=["machine learning", "neural networks"],
            is_ai_role=True,
            ai_score=85.0
        )
        assert posting.title == "Machine Learning Engineer"
        assert posting.is_ai_role is True
        assert len(posting.ai_keywords_found) == 2

    def test_job_posting_defaults(self, sample_company_id):
        """Test that default values are applied."""
        posting = JobPosting(
            company_id=str(sample_company_id),
            company_name="Test Company",
            title="Software Engineer",
            description="Build software"
        )
        assert posting.source == "unknown"
        assert posting.ai_keywords_found == []
        assert posting.is_ai_role is False
        assert posting.ai_score == 0.0

    def test_invalid_ai_score_too_high(self, sample_company_id):
        """Test that ai_score > 100 raises validation error."""
        with pytest.raises(ValidationError):
            JobPosting(
                company_id=str(sample_company_id),
                company_name="Test Company",
                title="Engineer",
                description="Description",
                ai_score=150.0
            )



# PATENT MODEL TESTS


class TestPatent:
    """Tests for Patent model validation."""

    def test_valid_patent(self, sample_company_id):
        """Test creating a valid patent."""
        patent = Patent(
            company_id=str(sample_company_id),
            company_name="Test Company",
            patent_id="PAT-001",
            patent_number="US10000001",
            title="AI-based prediction system",
            abstract="A system for predicting outcomes using ML",
            patent_type="utility",
            ai_keywords_found=["artificial intelligence", "prediction"],
            is_ai_patent=True,
            ai_score=90.0
        )
        assert patent.title == "AI-based prediction system"
        assert patent.is_ai_patent is True
        assert patent.ai_score == 90.0

    def test_patent_defaults(self, sample_company_id):
        """Test that default values are applied."""
        patent = Patent(
            company_id=str(sample_company_id),
            company_name="Test Company",
            patent_id="PAT-002",
            patent_number="US10000002",
            title="Method and system"
        )
        assert patent.abstract == ""
        assert patent.patent_type == ""
        assert patent.ai_keywords_found == []
        assert patent.is_ai_patent is False
        assert patent.ai_score == 0.0

    def test_invalid_ai_score_negative(self, sample_company_id):
        """Test that ai_score < 0 raises validation error."""
        with pytest.raises(ValidationError):
            Patent(
                company_id=str(sample_company_id),
                company_name="Test Company",
                patent_id="PAT-003",
                patent_number="US10000003",
                title="Patent",
                ai_score=-5.0
            )



# JOB SCORE BREAKDOWN MODEL TESTS


class TestJobScoreBreakdown:
    """Tests for JobScoreBreakdown model validation."""

    def test_valid_breakdown(self):
        """Test creating a valid job score breakdown."""
        breakdown = JobScoreBreakdown(
            ratio_score=30.0,
            volume_bonus=20.0,
            diversity_score=25.0,
            total_score=75.0
        )
        assert breakdown.ratio_score == 30.0
        assert breakdown.total_score == 75.0

    def test_invalid_ratio_score_too_high(self):
        """Test that ratio_score > 40 raises validation error."""
        with pytest.raises(ValidationError):
            JobScoreBreakdown(
                ratio_score=50.0,  # Max is 40
                volume_bonus=20.0,
                diversity_score=25.0,
                total_score=95.0
            )

    def test_invalid_volume_bonus_too_high(self):
        """Test that volume_bonus > 30 raises validation error."""
        with pytest.raises(ValidationError):
            JobScoreBreakdown(
                ratio_score=30.0,
                volume_bonus=40.0,  # Max is 30
                diversity_score=25.0,
                total_score=95.0
            )

    def test_boundary_values(self):
        """Test boundary values for breakdown scores."""
        breakdown = JobScoreBreakdown(
            ratio_score=40.0,  # Max
            volume_bonus=30.0,  # Max
            diversity_score=30.0,  # Max
            total_score=100.0  # Max
        )
        assert breakdown.total_score == 100.0



# JOB ANALYSIS RESULT MODEL TESTS


class TestJobAnalysisResult:
    """Tests for JobAnalysisResult model validation."""

    def test_valid_result(self, sample_company_id):
        """Test creating a valid job analysis result."""
        breakdown = JobScoreBreakdown(
            ratio_score=25.0,
            volume_bonus=15.0,
            diversity_score=20.0,
            total_score=60.0
        )
        result = JobAnalysisResult(
            ticker="TEST",
            company_id=str(sample_company_id),
            total_jobs=50,
            ai_jobs=15,
            normalized_score=60.0,
            confidence=0.85,
            breakdown=breakdown,
            ai_keywords_found=["machine learning", "AI"],
            sources=["linkedin", "indeed"],
            job_postings_analyzed=50
        )
        assert result.total_jobs == 50
        assert result.ai_jobs == 15
        assert result.normalized_score == 60.0

    def test_invalid_negative_jobs(self, sample_company_id):
        """Test that negative job count raises validation error."""
        breakdown = JobScoreBreakdown(
            ratio_score=10.0, volume_bonus=10.0,
            diversity_score=10.0, total_score=30.0
        )
        with pytest.raises(ValidationError):
            JobAnalysisResult(
                ticker="TEST",
                company_id=str(sample_company_id),
                total_jobs=-5,  # Invalid
                ai_jobs=0,
                normalized_score=30.0,
                confidence=0.8,
                breakdown=breakdown,
                ai_keywords_found=[],
                sources=[],
                job_postings_analyzed=0
            )



# TECH SCORE BREAKDOWN MODEL TESTS


class TestTechScoreBreakdown:
    """Tests for TechScoreBreakdown model validation."""

    def test_valid_breakdown(self):
        """Test creating a valid tech score breakdown."""
        breakdown = TechScoreBreakdown(
            base_score=40.0,
            volume_bonus=25.0,
            top_tools_bonus=15.0,
            total_score=80.0
        )
        assert breakdown.base_score == 40.0
        assert breakdown.total_score == 80.0

    def test_invalid_base_score_too_high(self):
        """Test that base_score > 50 raises validation error."""
        with pytest.raises(ValidationError):
            TechScoreBreakdown(
                base_score=60.0,  # Max is 50
                volume_bonus=25.0,
                top_tools_bonus=15.0,
                total_score=100.0
            )



# PATENT SCORE BREAKDOWN MODEL TESTS


class TestPatentScoreBreakdown:
    """Tests for PatentScoreBreakdown model validation."""

    def test_valid_breakdown(self):
        """Test creating a valid patent score breakdown."""
        breakdown = PatentScoreBreakdown(
            ratio_score=35.0,
            volume_bonus=25.0,
            recency_score=15.0,
            diversity_score=8.0,
            total_score=83.0
        )
        assert breakdown.ratio_score == 35.0
        assert breakdown.total_score == 83.0

    def test_invalid_recency_score_too_high(self):
        """Test that recency_score > 20 raises validation error."""
        with pytest.raises(ValidationError):
            PatentScoreBreakdown(
                ratio_score=35.0,
                volume_bonus=25.0,
                recency_score=25.0,  # Max is 20
                diversity_score=8.0,
                total_score=93.0
            )



# LEADERSHIP SCORE BREAKDOWN MODEL TESTS


class TestLeadershipScoreBreakdown:
    """Tests for LeadershipScoreBreakdown model validation."""

    def test_valid_breakdown(self):
        """Test creating a valid leadership score breakdown."""
        breakdown = LeadershipScoreBreakdown(
            tech_exec_score=25.0,
            keyword_score=20.0,
            performance_metric_score=18.0,
            board_tech_score=12.0,
            total_score=75.0
        )
        assert breakdown.tech_exec_score == 25.0
        assert breakdown.total_score == 75.0

    def test_invalid_tech_exec_score_too_high(self):
        """Test that tech_exec_score > 30 raises validation error."""
        with pytest.raises(ValidationError):
            LeadershipScoreBreakdown(
                tech_exec_score=35.0,  # Max is 30
                keyword_score=20.0,
                performance_metric_score=18.0,
                board_tech_score=12.0,
                total_score=85.0
            )

    def test_invalid_board_tech_score_too_high(self):
        """Test that board_tech_score > 15 raises validation error."""
        with pytest.raises(ValidationError):
            LeadershipScoreBreakdown(
                tech_exec_score=25.0,
                keyword_score=20.0,
                performance_metric_score=18.0,
                board_tech_score=20.0,  # Max is 15
                total_score=83.0
            )




# COMBINED ANALYSIS RESPONSE TESTS


class TestCombinedAnalysisResponse:
    """Tests for CombinedAnalysisResponse model."""

    def test_valid_response(self):
        """Test creating a valid combined response."""
        response = CombinedAnalysisResponse(
            ticker="TEST",
            status="success",
            composite_score=75.0,
            summary_updated=True
        )
        assert response.ticker == "TEST"
        assert response.composite_score == 75.0

    def test_partial_response(self):
        """Test response with optional fields as None."""
        response = CombinedAnalysisResponse(
            ticker="TEST",
            status="partial",
            summary_updated=False
        )
        assert response.job_analysis is None
        assert response.tech_analysis is None
        assert response.patent_analysis is None
        assert response.leadership_analysis is None



# SIGNAL API ENDPOINT TESTS


class TestSignalsCollectEndpoint:
    """Tests for POST /api/v1/signals/collect endpoint."""

    def test_collect_signals_success(self, client, sample_company_id):
        """Test successful signal collection request."""
        request_data = {
            "company_id": sample_company_id,
            "categories": ["technology_hiring"],
            "years_back": 5,
            "force_refresh": False
        }

        response = client.post("/api/v1/signals/collect", json=request_data)

        # Should return 200 with task_id (async task queued)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "queued"

    def test_collect_signals_invalid_years(self, client, sample_company_id):
        """Test that invalid years_back returns 422."""
        request_data = {
            "company_id": sample_company_id,
            "categories": ["technology_hiring"],
            "years_back": 15  # Max is 10
        }

        response = client.post("/api/v1/signals/collect", json=request_data)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestSignalsTaskStatusEndpoint:
    """Tests for GET /api/v1/signals/tasks/{task_id} endpoint."""

    def test_task_status_not_found(self, client):
        """Test that non-existent task returns 404."""
        fake_task_id = str(uuid4())

        response = client.get(f"/api/v1/signals/tasks/{fake_task_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestListSignalsEndpoint:
    """Tests for GET /api/v1/signals endpoint."""

    def test_list_signals_success(self, client):
        """Test listing signals."""
        response = client.get("/api/v1/signals")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "total" in data
        assert "signals" in data
        assert "filters" in data

    def test_list_signals_with_category_filter(self, client):
        """Test filtering signals by category."""
        response = client.get("/api/v1/signals?category=technology_hiring")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["filters"]["category"] == "technology_hiring"

    def test_list_signals_with_min_score_filter(self, client):
        """Test filtering signals by minimum score."""
        response = client.get("/api/v1/signals?min_score=50")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["filters"]["min_score"] == 50.0

    def test_list_signals_with_limit(self, client):
        """Test limiting signal results."""
        response = client.get("/api/v1/signals?limit=10")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["signals"]) <= 10

    def test_list_signals_invalid_min_score(self, client):
        """Test that invalid min_score returns 422."""
        response = client.get("/api/v1/signals?min_score=150")
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_list_signals_company_not_found(self, client):
        """Test that non-existent ticker returns 404."""
        response = client.get("/api/v1/signals?ticker=NOTEXIST")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCompanySignalsEndpoint:
    """Tests for GET /api/v1/companies/{id}/signals endpoint."""

    def test_company_signals_not_found(self, client):
        """Test that non-existent company returns 404."""
        fake_id = str(uuid4())

        response = client.get(f"/api/v1/companies/{fake_id}/signals")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_company_signals_by_ticker_not_found(self, client):
        """Test that non-existent ticker returns 404."""
        response = client.get("/api/v1/companies/NOTEXIST/signals")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCompanySignalsByCategoryEndpoint:
    """Tests for GET /api/v1/companies/{id}/signals/{category} endpoint."""

    def test_signals_invalid_category(self, client, sample_company_id):
        """Test that invalid category returns 400."""
        response = client.get(
            f"/api/v1/companies/{sample_company_id}/signals/invalid_category"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_signals_company_not_found(self, client):
        """Test that non-existent company returns 404."""
        fake_id = str(uuid4())

        response = client.get(
            f"/api/v1/companies/{fake_id}/signals/technology_hiring"
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("category", [
        "technology_hiring",
        "innovation_activity",
        "digital_presence",
        "leadership_signals"
    ])
    def test_valid_categories_accepted(self, client, sample_company_id, category):
        """Test that all valid categories are accepted."""
        response = client.get(
            f"/api/v1/companies/{sample_company_id}/signals/{category}"
        )
        # Should return either 200 (found) or 404 (company not in signals DB)
        # but NOT 400 (invalid category)
        assert response.status_code != status.HTTP_400_BAD_REQUEST