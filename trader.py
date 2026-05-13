"""
Trade execution module — execute decisions from AI analysis
Default: simulation mode (paper trade). Real trading requires:
  POLYMARKET_PRIVATE_KEY and AUTO_TRADE_ENABLED=True
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional
from config import (
    AUTO_TRADE_ENABLED,
    MAX_BET_PER_TRADE_USD,
    PROBABILITY_EDGE_THRESHOLD,
    TRADE_LOG_FILE,
    DAILY_BET_LIMIT_USD,
    DAILY_LOSS_LIMIT_USD,
)
from experience_manager import add_analysis_record

logger = logging.getLogger(__name__)


def _today_total_bet() -> float:
    """Calculate total bet amount placed today"""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    records = __import__("experience_manager").load_experience()
    return sum(
        r.get("bet_amount_usd", 0) or 0
        for r in records
        if r.get("timestamp", "").startswith(today)
    )


def _today_total_pnl() -> float:
    """Calculate realized PnL today"""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    records = __import__("experience_manager").load_experience()
    return sum(
        r.get("pnl_usd", 0) or 0
        for r in records
        if r.get("timestamp", "").startswith(today) and r.get("result") is not None
    )


def execute_trade_decision(analysis: Dict) -> Dict:
    """Entry point for trade execution. Real or paper based on config."""
    action = analysis.get("action", "SKIP")
    edge = abs(analysis.get("probability_edge", 0))
    confidence = analysis.get("confidence", "LOW")

    # Safety checks
    if action == "SKIP":
        logger.info(f"Skip (SKIP): {analysis['market_question'][:50]}")
        return {"status": "SKIPPED", "reason": "AI recommends skip"}

    if edge < PROBABILITY_EDGE_THRESHOLD:
        logger.info(f"Skip (edge insufficient): edge={edge*100:.1f}% < threshold={PROBABILITY_EDGE_THRESHOLD*100:.0f}%")
        return {"status": "SKIPPED", "reason": f"Edge insufficient {edge*100:.1f}%"}

    if confidence != "HIGH":
        logger.info(f"Skip (confidence insufficient): {confidence}")
        return {"status": "SKIPPED", "reason": f"Confidence {confidence}, requires HIGH"}

    # Daily risk limits
    today_bet = _today_total_bet()
    today_pnl = _today_total_pnl()
    if today_bet >= DAILY_BET_LIMIT_USD:
        logger.info(f"Skip (daily bet limit ${DAILY_BET_LIMIT_USD} reached)")
        return {"status": "SKIPPED", "reason": f"Daily bet limit ${DAILY_BET_LIMIT_USD}"}
    if today_pnl <= -DAILY_LOSS_LIMIT_USD:
        logger.info(f"Skip (daily loss limit ${DAILY_LOSS_LIMIT_USD} reached)")
        return {"status": "SKIPPED", "reason": f"Daily loss: ${today_pnl:+.2f}"}

    # Calculate bet size (simplified Kelly: proportional to edge)
    bet_amount = min(MAX_BET_PER_TRADE_USD, MAX_BET_PER_TRADE_USD * (edge / 0.20))
    bet_amount = min(bet_amount, DAILY_BET_LIMIT_USD - today_bet)
    bet_amount = round(bet_amount, 2)

    # Log to experience (regardless of real vs paper)
    record = add_analysis_record(
        market_question=analysis["market_question"],
        market_yes_price=analysis["market_yes_price"],
        claude_probability=analysis["probability"],
        claude_reasoning=analysis.get("reasoning", ""),
        recommended_action=action,
        action_taken=f"{'Live order' if AUTO_TRADE_ENABLED else 'Paper trade'}: {action} ${bet_amount}",
        bet_amount_usd=bet_amount if AUTO_TRADE_ENABLED else 0,
        market_id=analysis.get("market_id", ""),
    )

    if AUTO_TRADE_ENABLED and os.getenv("POLYMARKET_PRIVATE_KEY"):
        return _execute_real_trade(analysis, bet_amount, record["id"])
    else:
        return _execute_paper_trade(analysis, bet_amount, record["id"])


def _execute_paper_trade(analysis: Dict, bet_amount: float, record_id: int) -> Dict:
    """Paper trade — log only, no real order"""
    result = {
        "status": "PAPER_TRADE",
        "record_id": record_id,
        "action": analysis["action"],
        "market": analysis["market_question"][:80],
        "bet_amount_usd": bet_amount,
        "market_price": analysis["market_yes_price"],
        "claude_probability": analysis["probability"],
        "edge": analysis["probability_edge"],
        "timestamp": datetime.now().isoformat(),
    }

    _append_trade_log(result)
    logger.info(
        f"[Paper] Trade record #{record_id}: {analysis['action']} ${bet_amount} | "
        f"Edge {analysis['probability_edge']*100:+.1f}% | "
        f"{analysis['market_question'][:50]}"
    )
    return result


def _execute_real_trade(analysis: Dict, bet_amount: float, record_id: int) -> Dict:
    """
    Real trade execution
    Requires: pip install py-clob-client
    Requires: POLYMARKET_PRIVATE_KEY in .env
    WARNING: Real funds at risk
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType

        private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137
        )

        is_buy_yes = analysis["action"] == "BUY_YES"
        token_id = analysis.get("yes_token_id") if is_buy_yes else analysis.get("no_token_id")

        if not token_id:
            logger.error("Missing token_id, cannot place order")
            return {"status": "FAILED", "reason": "Missing token_id"}

        price = analysis["market_yes_price"] if is_buy_yes else (1 - analysis["market_yes_price"])
        size = bet_amount / price

        order = clob_client.create_and_post_order(OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side="BUY",
            order_type=OrderType.GTC,
        ))

        result = {
            "status": "ORDER_PLACED",
            "record_id": record_id,
            "order_id": order.get("orderID", "unknown"),
            "action": analysis["action"],
            "bet_amount_usd": bet_amount,
            "timestamp": datetime.now().isoformat(),
        }
        _append_trade_log(result)
        logger.info(f"[OK] Live order placed #{record_id}: order ID={result['order_id']}")
        return result

    except ImportError:
        logger.error("py-clob-client not installed. Run: pip install py-clob-client")
        return {"status": "FAILED", "reason": "py-clob-client not installed"}
    except Exception as e:
        logger.error(f"Live order failed: {e}")
        return {"status": "FAILED", "reason": str(e)}


def _append_trade_log(entry: Dict) -> None:
    """Append entry to trade log file"""
    os.makedirs(os.path.dirname(TRADE_LOG_FILE), exist_ok=True)
    logs = []
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE) as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append(entry)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)
