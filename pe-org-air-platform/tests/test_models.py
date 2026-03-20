# tests/test_models.py

"""
Model Validation Tests - Tests for all Pydantic model validations
"""

import pytest
from uuid import uuid4, UUID
from datetime import date, datetime
from pydantic import ValidationError

from app.models.enumerations import Dimension, AssessmentType, AssessmentStatus
from app.models.dimension import (
    DimensionScoreBase,
    DimensionScoreCreate,
    DimensionScoreUpdate,
    DimensionScoreResponse,
    DIMENSION_WEIGHTS
)



# ENUMERATION TESTS


class TestDimensionEnum:
    """Tests for Dimension enumeration."""
    
    def test_all_dimensions_exist(self):
        """Test that all 7 dimensions are defined."""
        expected = [
            "data_infrastructure", "ai_governance", "technology_stack",
            "talent_skills", "leadership_vision", "use_case_portfolio", "culture_change"
        ]
        actual = [d.value for d in Dimension]
        assert actual == expected
    
    def test_dimension_count(self):
        """Test that exactly 7 dimensions exist."""
        assert len(Dimension) == 7


class TestAssessmentTypeEnum:
    """Tests for AssessmentType enumeration."""
    
    def test_all_assessment_types_exist(self):
        """Test that all assessment types are defined."""
        expected = ["screening", "due_diligence", "quarterly", "exit_prep"]
        actual = [t.value for t in AssessmentType]
        assert actual == expected
    
    def test_assessment_type_count(self):
        """Test that exactly 4 assessment types exist."""
        assert len(AssessmentType) == 4


class TestAssessmentStatusEnum:
    """Tests for AssessmentStatus enumeration."""
    
    def test_all_statuses_exist(self):
        """Test that all statuses are defined."""
        expected = ["draft", "in_progress", "submitted", "approved", "superseded"]
        actual = [s.value for s in AssessmentStatus]
        assert actual == expected
    
    def test_status_count(self):
        """Test that exactly 5 statuses exist."""
        assert len(AssessmentStatus) == 5



# DIMENSION WEIGHTS TESTS


class TestDimensionWeights:
    """Tests for dimension weights configuration."""
    
    def test_weights_sum_to_one(self):
        """Test that all dimension weights sum to 1.0."""
        total = sum(DIMENSION_WEIGHTS.values())
        assert 0.999 <= total <= 1.001
    
    def test_all_dimensions_have_weights(self):
        """Test that every dimension has a weight assigned."""
        for dimension in Dimension:
            assert dimension in DIMENSION_WEIGHTS
    
    def test_weights_are_valid_range(self):
        """Test that all weights are between 0 and 1."""
        for dimension, weight in DIMENSION_WEIGHTS.items():
            assert 0 <= weight <= 1
    
    def test_expected_weight_values(self, expected_dimension_weights):
        """Test that weights match expected values."""
        for dimension in Dimension:
            expected = expected_dimension_weights[dimension.value]
            actual = DIMENSION_WEIGHTS[dimension]
            assert actual == expected



# DIMENSION SCORE MODEL TESTS


class TestDimensionScoreCreate:
    """Tests for DimensionScoreCreate model validation."""
    
    def test_valid_dimension_score(self, valid_dimension_score_data):
        """Test creating a valid dimension score."""
        score = DimensionScoreCreate(**valid_dimension_score_data)
        assert score.score == 85.5
        assert score.dimension == Dimension.DATA_INFRASTRUCTURE
        assert score.confidence == 0.92
    
    def test_valid_minimal_dimension_score(self, valid_dimension_score_minimal):
        """Test creating dimension score with only required fields."""
        score = DimensionScoreCreate(**valid_dimension_score_minimal)
        assert score.score == 72.0
        assert score.confidence == 0.8  # Default
        assert score.evidence_count == 0  # Default
    
    def test_auto_weight_assignment(self, sample_assessment_id):
        """Test that weight is auto-assigned based on dimension."""
        data = {"assessment_id": sample_assessment_id, "dimension": "data_infrastructure", "score": 80.0}
        score = DimensionScoreCreate(**data)
        assert score.weight == 0.25
    
    def test_invalid_score_too_high(self, invalid_dimension_score_high_score):
        """Test that score > 100 raises validation error."""
        with pytest.raises(ValidationError):
            DimensionScoreCreate(**invalid_dimension_score_high_score)
    
    def test_invalid_score_negative(self, invalid_dimension_score_negative_score):
        """Test that score < 0 raises validation error."""
        with pytest.raises(ValidationError):
            DimensionScoreCreate(**invalid_dimension_score_negative_score)
    
    def test_invalid_weight_too_high(self, invalid_dimension_score_bad_weight):
        """Test that weight > 1 raises validation error."""
        with pytest.raises(ValidationError):
            DimensionScoreCreate(**invalid_dimension_score_bad_weight)
    
    def test_invalid_confidence_too_high(self, invalid_dimension_score_bad_confidence):
        """Test that confidence > 1 raises validation error."""
        with pytest.raises(ValidationError):
            DimensionScoreCreate(**invalid_dimension_score_bad_confidence)
    
    def test_invalid_dimension_value(self, invalid_dimension_score_bad_dimension):
        """Test that invalid dimension raises validation error."""
        with pytest.raises(ValidationError):
            DimensionScoreCreate(**invalid_dimension_score_bad_dimension)
    
    def test_score_boundary_zero(self, sample_assessment_id):
        """Test that score = 0 is valid."""
        data = {"assessment_id": sample_assessment_id, "dimension": "data_infrastructure", "score": 0.0}
        score = DimensionScoreCreate(**data)
        assert score.score == 0.0
    
    def test_score_boundary_hundred(self, sample_assessment_id):
        """Test that score = 100 is valid."""
        data = {"assessment_id": sample_assessment_id, "dimension": "data_infrastructure", "score": 100.0}
        score = DimensionScoreCreate(**data)
        assert score.score == 100.0


class TestDimensionScoreUpdate:
    """Tests for DimensionScoreUpdate model validation."""
    
    def test_valid_partial_update(self):
        """Test valid partial update."""
        update = DimensionScoreUpdate(score=90.0)
        assert update.score == 90.0
        assert update.dimension is None
    
    def test_invalid_update_score_too_high(self):
        """Test that score > 100 raises validation error."""
        with pytest.raises(ValidationError):
            DimensionScoreUpdate(score=150.0)
    
    def test_empty_update_allowed(self):
        """Test that empty update is allowed."""
        update = DimensionScoreUpdate()
        assert update.score is None


class TestAllDimensionTypes:
    """Test dimension score creation for all dimension types."""
    
    @pytest.mark.parametrize("dimension,expected_weight", [
        ("data_infrastructure", 0.25),
        ("ai_governance", 0.20),
        ("technology_stack", 0.15),
        ("talent_skills", 0.15),
        ("leadership_vision", 0.10),
        ("use_case_portfolio", 0.10),
        ("culture_change", 0.05),
    ])
    def test_each_dimension_auto_weight(self, sample_assessment_id, dimension, expected_weight):
        """Test auto-weight assignment for each dimension type."""
        data = {"assessment_id": sample_assessment_id, "dimension": dimension, "score": 75.0}
        score = DimensionScoreCreate(**data)
        assert score.weight == expected_weight

