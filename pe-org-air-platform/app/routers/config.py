"""Configuration endpoints — serves scoring parameters for MCP resources."""
from fastapi import APIRouter
from app.core.settings import get_settings
from app.config.company_mappings import CS3_PORTFOLIO
from app.config.retrieval_settings import RETRIEVAL_SETTINGS

router = APIRouter(prefix="/api/v1/config", tags=["Configuration"])


@router.get("/scoring-parameters")
async def get_scoring_parameters():
    s = get_settings()
    return {
        "version": s.PARAM_VERSION,
        "alpha": s.ALPHA_VR_WEIGHT,
        "beta": s.BETA_SYNERGY_WEIGHT,
        "lambda_penalty": s.LAMBDA_PENALTY,
        "delta_position": s.DELTA_POSITION,
    }


@router.get("/dimension-weights")
async def get_dimension_weights():
    s = get_settings()
    weights = {
        "data_infrastructure": s.W_DATA_INFRA,
        "ai_governance": s.W_AI_GOVERNANCE,
        "technology_stack": s.W_TECH_STACK,
        "talent_skills": s.W_TALENT,
        "leadership_vision": s.W_LEADERSHIP,
        "use_case_portfolio": s.W_USE_CASES,
        "culture_change": s.W_CULTURE,
    }
    total = sum(weights.values())
    return {"weights": weights, "total": round(total, 6), "is_valid": abs(total - 1.0) <= 0.001}


@router.get("/sector-baselines")
async def get_sector_baselines():
    s = get_settings()
    return {
        "baselines": {
            "technology": s.SECTOR_HR_BASELINE_TECHNOLOGY,
            "financial_services": s.SECTOR_HR_BASELINE_FINANCIAL_SERVICES,
            "healthcare": s.SECTOR_HR_BASELINE_HEALTHCARE,
            "manufacturing": s.SECTOR_HR_BASELINE_MANUFACTURING,
            "retail": s.SECTOR_HR_BASELINE_RETAIL,
            "energy": s.SECTOR_HR_BASELINE_ENERGY,
            "business_services": s.SECTOR_HR_BASELINE_BUSINESS_SERVICES,
            "consumer": s.SECTOR_HR_BASELINE_CONSUMER,
        }
    }


@router.get("/portfolio")
async def get_portfolio_config():
    return {
        "cs3_portfolio_tickers": CS3_PORTFOLIO,
        "retrieval": {
            "max_context_chars": RETRIEVAL_SETTINGS.max_context_chars,
            "default_top_k": 10,
            "dense_weight": RETRIEVAL_SETTINGS.dense_weight,
            "sparse_weight": RETRIEVAL_SETTINGS.sparse_weight,
            "rrf_k": RETRIEVAL_SETTINGS.rrf_k,
        },
    }
