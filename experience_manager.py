"""
Experience accumulation module — records every analysis and trade result
Core mechanism for the system "getting smarter over time"
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional
from config import EXPERIENCE_LOG_FILE

logger = logging.getLogger(__name__)


def load_experience() -> List[Dict]:
    """Load historical experience records"""
    if not os.path.exists(EXPERIENCE_LOG_FILE):
        return []
    try:
        with open(EXPERIENCE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load experience records: {e}")
        return []


def save_experience(records: List[Dict]) -> None:
    """Save experience records"""
    os.makedirs(os.path.dirname(EXPERIENCE_LOG_FILE), exist_ok=True)
    try:
        with open(EXPERIENCE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save experience records: {e}")


def add_analysis_record(
    market_question: str,
    market_yes_price: float,
    claude_probability: float,
    claude_reasoning: str,
    recommended_action: str,
    action_taken: str,
    bet_amount_usd: float = 0,
    market_id: str = "",
) -> Dict:
    """Record an analysis result. Result updated later via update_trade_result or auto_review."""
    records = load_experience()
    record = {
        "id": len(records) + 1,
        "timestamp": datetime.now().isoformat(),
        "market_id": market_id,
        "market_question": market_question,
        "market_yes_price": market_yes_price,
        "claude_probability": claude_probability,
        "probability_edge": claude_probability - market_yes_price,
        "claude_reasoning": claude_reasoning[:500],
        "recommended_action": recommended_action,
        "action_taken": action_taken,
        "bet_amount_usd": bet_amount_usd,
        "result": None,
        "pnl_usd": None,
        "lesson": None,
    }
    records.append(record)
    save_experience(records)
    logger.info(f"Analysis recorded #{record['id']}: {market_question[:50]}")
    return record


def update_trade_result(record_id: int, result: str, pnl_usd: float, lesson: str = "") -> None:
    """Update the outcome of a past trade. result: 'WIN' / 'LOSS' / 'PUSH'"""
    records = load_experience()
    for r in records:
        if r["id"] == record_id:
            r["result"] = result
            r["pnl_usd"] = pnl_usd
            r["lesson"] = lesson
            break
    save_experience(records)
    logger.info(f"Record #{record_id} updated: {result} ${pnl_usd:+.2f}")


def format_experience_for_claude(max_records: int = 20) -> str:
    """Format historical experience as text for the AI prompt. Only includes completed records."""
    records = load_experience()
    completed = [r for r in records if r.get("result") is not None]
    recent = completed[-max_records:]

    if not recent:
        return "No historical trade records yet. This is the first run."

    wins = sum(1 for r in recent if r["result"] == "WIN")
    losses = sum(1 for r in recent if r["result"] == "LOSS")
    total_pnl = sum(r.get("pnl_usd", 0) or 0 for r in recent)

    summary = f"Historical record (last {len(recent)} trades): {wins}W {losses}L, Total PnL ${total_pnl:+.2f}\n\n"
    summary += "Representative cases:\n"

    for r in recent[-5:]:
        summary += (
            f"- {r['timestamp'][:10]} | {r['market_question'][:60]}\n"
            f"  Market:{r['market_yes_price']*100:.0f}% vs AI:{r['claude_probability']*100:.0f}%"
            f"  → {r['action_taken']} → {r['result']} ${r.get('pnl_usd', 0):+.1f}\n"
        )
        if r.get("lesson"):
            summary += f"  Lesson: {r['lesson']}\n"

    return summary


def get_recent_market_ids(cooldown_hours: int = 6) -> set:
    """Get market IDs analyzed in the last N hours for dedup"""
    records = load_experience()
    if not records:
        return set()

    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)

    recent_ids = set()
    for r in records:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            if ts > cutoff:
                q = r.get("market_question", "")
                recent_ids.add(q[:80])
        except (ValueError, KeyError):
            continue
    return recent_ids


def get_stats() -> Dict:
    """Get overall performance statistics"""
    records = load_experience()
    completed = [r for r in records if r.get("result") is not None]
    if not completed:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0}

    wins = sum(1 for r in completed if r["result"] == "WIN")
    losses = sum(1 for r in completed if r["result"] == "LOSS")
    total_pnl = sum(r.get("pnl_usd", 0) or 0 for r in completed)

    return {
        "total": len(completed),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(completed) if completed else 0,
        "total_pnl": total_pnl,
    }
