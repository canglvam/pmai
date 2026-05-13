"""
Auto-review module — checks settled markets and auto-updates trade results
Replaces manual --update-result, zero human intervention
"""

import logging
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from experience_manager import load_experience, save_experience

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def _retry_session(total: int = 3, backoff: float = 1.0) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=total, backoff_factor=backoff, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def check_market_resolution(market_id: str) -> Optional[Dict]:
    """Query settlement status of a single market. Returns resolution info or None."""
    if not market_id:
        return None

    try:
        resp = _retry_session().get(
            f"{GAMMA_API}/markets/{market_id}",
            timeout=10,
        )
        resp.raise_for_status()
        m = resp.json()
    except requests.RequestException as e:
        logger.warning(f"Failed to query market #{market_id}: {e}")
        return None

    if not m.get("closed"):
        return None

    prices = m.get("outcomePrices")
    if not prices:
        return None

    try:
        prices = [float(p) for p in prices]
    except (ValueError, TypeError):
        return None

    outcomes = m.get("outcomes", ["Yes", "No"])

    if len(prices) >= 2:
        if prices[0] >= 0.99:
            return {"resolved": True, "winner": outcomes[0], "tie": False}
        elif prices[1] >= 0.99:
            return {"resolved": True, "winner": outcomes[1], "tie": False}
        elif abs(prices[0] - prices[1]) < 0.02:
            return {"resolved": True, "winner": "TIE", "tie": True}

    return None


def determine_result(action: str, winner: str, outcomes: list) -> str:
    """Determine WIN / LOSS / PUSH based on our position and market winner"""
    yes_label = outcomes[0] if outcomes else "Yes"

    if winner == "TIE":
        return "PUSH"

    is_yes_won = (winner == yes_label)

    if action == "BUY_YES" and is_yes_won:
        return "WIN"
    elif action == "BUY_YES" and not is_yes_won:
        return "LOSS"
    elif action == "BUY_NO" and not is_yes_won:
        return "WIN"
    elif action == "BUY_NO" and is_yes_won:
        return "LOSS"
    return "PUSH"


def calculate_pnl(action: str, result: str, market_price: float, bet_amount: float) -> float:
    """Calculate PnL for a trade"""
    if result == "PUSH" or bet_amount == 0:
        return 0.0
    if result == "LOSS":
        return -bet_amount
    if result == "WIN":
        p = market_price
        if action == "BUY_YES":
            return bet_amount * (1 - p) / p if p > 0 else bet_amount
        elif action == "BUY_NO":
            return bet_amount * p / (1 - p) if p < 1 else bet_amount
    return 0.0


def run_auto_review() -> int:
    """Iterate all pending records, check if resolved, auto-update. Returns count updated."""
    records = load_experience()
    pending = [r for r in records if r.get("result") is None and r.get("market_id")]

    if not pending:
        return 0

    updated = 0
    for record in pending:
        market_id = record["market_id"]
        resolution = check_market_resolution(market_id)

        if not resolution:
            continue

        action = record.get("recommended_action", "SKIP")
        winner = resolution["winner"]
        outcomes_hint = ["Yes", "No"]

        result = determine_result(action, winner, outcomes_hint)
        pnl = calculate_pnl(
            action, result,
            record["market_yes_price"],
            record.get("bet_amount_usd", 0) or _estimate_bet(record.get("action_taken", "")),
        )

        record["result"] = result
        record["pnl_usd"] = round(pnl, 2)

        if result == "LOSS":
            edge = abs(record.get("probability_edge", 0))
            record["lesson"] = (
                f"Auto-review: {action} loss. Edge was {edge*100:.0f}%. "
                f"Market winner: {winner}"
            )
        elif result == "WIN":
            record["lesson"] = f"Auto-review: {action} win."

        logger.info(
            f"Auto-review #{record['id']}: {result} ${record['pnl_usd']:+.2f} | "
            f"{record['market_question'][:50]}"
        )
        updated += 1

    if updated:
        save_experience(records)

    return updated


def _estimate_bet(action_taken: str) -> float:
    """Extract bet amount from action_taken string"""
    import re
    match = re.search(r'\$([\d.]+)', action_taken)
    return float(match.group(1)) if match else 0.0
