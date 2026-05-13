"""
自动复盘模块 — 检查已结算市场，自动更新交易结果
替代手动 --update-result，零人工干预
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
    """
    查询单个市场的结算状态
    返回: {"resolved": True, "winner": "YES"/"NO", "tie": False} 或 None
    """
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
        logger.warning(f"查询市场 #{market_id} 失败: {e}")
        return None

    if not m.get("closed"):
        return None  # 还没结算，继续等

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
    """
    根据我们的仓位和市场胜出方，判断 WIN / LOSS / PUSH
    outcomes 是市场定义的输出，如 ["Yes", "No"] 或 ["TeamA", "TeamB"]
    """
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
    """计算盈亏金额"""
    if result == "PUSH" or bet_amount == 0:
        return 0.0
    if result == "LOSS":
        return -bet_amount
    if result == "WIN":
        # BUY_YES: buy at price p, win → return bet/p, profit = bet*(1-p)/p
        # BUY_NO: buy at 1-p, win → return bet/(1-p), profit = bet*p/(1-p)
        p = market_price
        if action == "BUY_YES":
            return bet_amount * (1 - p) / p if p > 0 else bet_amount
        elif action == "BUY_NO":
            return bet_amount * p / (1 - p) if p < 1 else bet_amount
    return 0.0


def run_auto_review() -> int:
    """
    遍历所有待结算记录，检查是否已出结果，自动更新
    返回: 本次更新的记录数
    """
    records = load_experience()
    pending = [r for r in records if r.get("result") is None and r.get("market_id")]

    if not pending:
        return 0

    updated = 0
    for record in pending:
        market_id = record["market_id"]
        resolution = check_market_resolution(market_id)

        if not resolution:
            continue  # 还没结算或查询失败

        action = record.get("recommended_action", "SKIP")
        winner = resolution["winner"]
        outcomes_hint = ["Yes", "No"]  # 大多数市场用 Yes/No

        result = determine_result(action, winner, outcomes_hint)
        pnl = calculate_pnl(
            action, result,
            record["market_yes_price"],
            record.get("bet_amount_usd", 0) or _estimate_bet(record.get("action_taken", "")),
        )

        record["result"] = result
        record["pnl_usd"] = round(pnl, 2)

        # 简单教训生成
        if result == "LOSS":
            edge = abs(record.get("probability_edge", 0))
            record["lesson"] = (
                f"自动复盘：{action} 亏损。边缘{edge*100:.0f}%。"
                f"市场胜出方: {winner}"
            )
        elif result == "WIN":
            record["lesson"] = f"自动复盘：{action} 盈利。"

        logger.info(
            f"自动复盘 #{record['id']}: {result} ${record['pnl_usd']:+.2f} | "
            f"{record['market_question'][:50]}"
        )
        updated += 1

    if updated:
        save_experience(records)

    return updated


def _estimate_bet(action_taken: str) -> float:
    """从 action_taken 字符串中提取下注金额"""
    import re
    match = re.search(r'\$([\d.]+)', action_taken)
    return float(match.group(1)) if match else 0.0
