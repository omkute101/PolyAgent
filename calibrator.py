"""
Calibration engine — tracks classification accuracy over time.
Determines if the system's classifications actually predict market movements.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

import config
import logger

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class CalibrationReport:
    total: int
    accuracy: float
    by_source: dict[str, float]
    by_classification: dict[str, float]
    recommendation: str


def check_resolutions():
    """
    Check if any open trades have resolved. Update calibration table.
    Queries Gamma API for market resolution status.
    """
    trades = logger.get_recent_trades(limit=100)
    unresolved = [
        t for t in trades
        if t.get("classification") and t.get("status") in ("dry_run", "executed")
    ]

    if not unresolved:
        return 0

    resolved_count = 0
    for trade in unresolved:
        market_id = trade["market_id"]

        try:
            resp = httpx.get(
                f"{GAMMA_API}/markets",
                params={"condition_id": market_id},
                timeout=10,
            )
            data = resp.json()
            items = data if isinstance(data, list) else data.get("data", [])

            if not items:
                continue

            market_data = items[0]
            if not market_data.get("closed", False):
                continue

            # Market resolved — determine direction
            outcome_prices = market_data.get("outcomePrices", "")
            if isinstance(outcome_prices, str):
                import json
                try:
                    prices = json.loads(outcome_prices)
                except Exception:
                    continue
            else:
                prices = outcome_prices

            if not prices or len(prices) < 2:
                continue

            exit_price = float(prices[0])
            entry_price = trade["market_price"]

            if exit_price > entry_price:
                actual_direction = "bullish"
            elif exit_price < entry_price:
                actual_direction = "bearish"
            else:
                actual_direction = "neutral"

            classification = trade.get("classification", "neutral")
            correct = classification == actual_direction

            logger.log_calibration(
                trade_id=trade["id"],
                classification=classification,
                materiality=trade.get("materiality", 0),
                entry_price=entry_price,
                exit_price=exit_price,
                actual_direction=actual_direction,
                correct=correct,
            )
            resolved_count += 1

        except Exception as e:
            log.debug(f"[calibrator] Error checking {market_id}: {e}")
            continue

    if resolved_count:
        log.info(f"[calibrator] Resolved {resolved_count} trades")
    return resolved_count


def get_report() -> CalibrationReport:
    """Generate a calibration report from stored data."""
    stats = logger.get_calibration_stats()

    if stats["total"] == 0:
        return CalibrationReport(
            total=0,
            accuracy=0.0,
            by_source={},
            by_classification={},
            recommendation="Not enough data — need at least 20 resolved trades for meaningful calibration.",
        )

    accuracy = stats["accuracy"]

    if accuracy >= 65:
        rec = f"Strong signal. {accuracy:.1f}% accuracy suggests real edge. Consider increasing bet sizes cautiously."
    elif accuracy >= 55:
        rec = f"Moderate signal. {accuracy:.1f}% accuracy is above chance but thin. Keep current sizing."
    elif accuracy >= 45:
        rec = f"Weak signal. {accuracy:.1f}% accuracy is near random. Review classification prompt and news sources."
    else:
        rec = f"Negative signal. {accuracy:.1f}% accuracy is below chance. PAUSE trading and investigate."

    return CalibrationReport(
        total=stats["total"],
        accuracy=accuracy,
        by_source=stats["by_source"],
        by_classification=stats["by_classification"],
        recommendation=rec,
    )


if __name__ == "__main__":
    print("Checking resolutions...")
    count = check_resolutions()
    print(f"Resolved: {count}")

    report = get_report()
    print(f"\nCalibration Report:")
    print(f"  Total: {report.total}")
    print(f"  Accuracy: {report.accuracy:.1f}%")
    print(f"  By source: {report.by_source}")
    print(f"  By classification: {report.by_classification}")
    print(f"  Recommendation: {report.recommendation}")
