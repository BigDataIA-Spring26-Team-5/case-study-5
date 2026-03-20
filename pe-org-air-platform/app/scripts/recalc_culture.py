"""
Recalculate culture scores from existing raw reviews in S3.
No scraping needed — uses already-collected review data.

Usage:
    python -m app.scripts.recalc_culture --dry-run          # show impact without uploading
    python -m app.scripts.recalc_culture                     # recalculate and upload
    python -m app.scripts.recalc_culture NVDA --dry-run      # single company
"""

import json
import sys
import logging
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)


def recalc_company(ticker: str, dry_run: bool = False):
    """Recalculate culture signal for a company from existing S3 raw reviews."""
    from app.services.s3_storage import get_s3_service
    from app.core.settings import settings
    from app.pipelines.glassdoor_collector import CultureCollector, CultureReview, _normalize_date

    ticker = ticker.upper()

    s3_service = get_s3_service()
    s3 = s3_service.s3_client
    bucket = settings.S3_BUCKET

    # Find latest raw reviews file
    prefix = f"glassdoor_signals/raw/{ticker}/"
    all_keys = s3_service.list_files(prefix)
    keys = [k for k in all_keys if k.endswith("_raw.json")]

    if not keys:
        logger.error(f"[{ticker}] No raw review files found in S3 under {prefix}")
        return None

    latest_key = sorted(keys)[-1]
    logger.info(f"[{ticker}] Loading raw reviews from: {latest_key}")

    raw_data = json.loads(s3.get_object(Bucket=bucket, Key=latest_key)["Body"].read())
    raw_reviews = raw_data.get("reviews", [])
    logger.info(f"[{ticker}] Loaded {len(raw_reviews)} raw reviews")

    # Convert to CultureReview objects
    reviews = []
    for r in raw_reviews:
        rd = None
        if r.get("review_date"):
            rd = _normalize_date(r["review_date"])
        reviews.append(CultureReview(
            review_id=r.get("review_id", "unknown"),
            rating=float(r.get("rating", 3.0)),
            title=r.get("title", ""),
            pros=r.get("pros", ""),
            cons=r.get("cons", ""),
            advice_to_management=r.get("advice_to_management"),
            is_current_employee=r.get("is_current_employee", True),
            job_title=r.get("job_title", ""),
            review_date=rd,
            source=r.get("source", "glassdoor"),
        ))

    # Run analysis with current collector settings
    collector = CultureCollector()
    signal = collector.analyze_reviews(ticker, ticker, reviews)

    logger.info(f"\n[{ticker}] RECALCULATED SCORES:")
    logger.info(f"  Overall:          {signal.overall_score}")
    logger.info(f"  Innovation:       {signal.innovation_score}")
    logger.info(f"  Data-Driven:      {signal.data_driven_score}")
    logger.info(f"  AI Awareness:     {signal.ai_awareness_score}")
    logger.info(f"  Change Readiness: {signal.change_readiness_score}")
    logger.info(f"  Reviews:          {signal.review_count}")
    logger.info(f"  Avg Rating:       {signal.avg_rating}")
    logger.info(f"  Confidence:       {signal.confidence}")

    if dry_run:
        logger.info(f"  🏜️  DRY RUN — not uploading to S3")
        return signal

    # Upload new output to S3
    from dataclasses import asdict
    output_data = asdict(signal)
    # Convert Decimals to float for JSON
    def dec_to_float(obj):
        if isinstance(obj, dict):
            return {k: dec_to_float(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [dec_to_float(v) for v in obj]
        elif isinstance(obj, Decimal):
            return float(obj)
        return obj

    output_data = dec_to_float(output_data)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_data["run_timestamp"] = ts
    output_data["recalculated"] = True

    output_key = f"glassdoor_signals/output/{ticker}/{ts}_culture.json"
    s3.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=json.dumps(output_data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(f"  ✅ Uploaded to S3: {output_key}")
    return signal


def main():
    ap = argparse.ArgumentParser(description="Recalculate culture scores from existing raw reviews")
    ap.add_argument("tickers", nargs="*", help="Ticker symbols (default: all 5)")
    ap.add_argument("--dry-run", action="store_true", help="Show scores without uploading")
    args = ap.parse_args()

    all_tickers = ["NVDA", "JPM", "WMT", "GE", "DG"]
    tickers = [t.upper() for t in args.tickers] if args.tickers else all_tickers

    results = {}
    for ticker in tickers:
        try:
            signal = recalc_company(ticker, dry_run=args.dry_run)
            if signal:
                results[ticker] = signal
        except Exception as e:
            logger.error(f"[{ticker}] FAILED: {e}", exc_info=True)

    if results:
        logger.info(f"\n{'='*70}")
        logger.info(f"  {'Ticker':<6} {'Overall':>8} {'Innov':>7} {'Data':>7} {'AI':>7} {'Change':>7} {'#Rev':>5}")
        logger.info(f"  {'-'*6} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*5}")
        for t, s in sorted(results.items()):
            logger.info(f"  {t:<6} {s.overall_score:>8} {s.innovation_score:>7} {s.data_driven_score:>7} {s.ai_awareness_score:>7} {s.change_readiness_score:>7} {s.review_count:>5}")
        logger.info(f"{'='*70}")


if __name__ == "__main__":
    main()