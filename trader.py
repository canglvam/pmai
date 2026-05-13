"""
交易执行模块 - 根据分析结果下注
默认安全模式：只记录推荐，不实际下单
开启自动交易需要配置 POLYMARKET_PRIVATE_KEY 并设置 AUTO_TRADE_ENABLED=True
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
    """计算今日已下注总额"""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    records = __import__("experience_manager").load_experience()
    return sum(
        r.get("bet_amount_usd", 0) or 0
        for r in records
        if r.get("timestamp", "").startswith(today)
    )


def _today_total_pnl() -> float:
    """计算今日已实现盈亏"""
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    records = __import__("experience_manager").load_experience()
    return sum(
        r.get("pnl_usd", 0) or 0
        for r in records
        if r.get("timestamp", "").startswith(today) and r.get("result") is not None
    )


def execute_trade_decision(analysis: Dict) -> Dict:
    """
    执行交易决策的入口函数
    根据 AUTO_TRADE_ENABLED 决定是真实下单还是仅记录
    """
    action = analysis.get("action", "SKIP")
    edge = abs(analysis.get("probability_edge", 0))
    confidence = analysis.get("confidence", "LOW")

    # 安全检查：不满足条件则跳过
    if action == "SKIP":
        logger.info(f"跳过（SKIP）: {analysis['market_question'][:50]}")
        return {"status": "SKIPPED", "reason": "Claude建议跳过"}

    if edge < PROBABILITY_EDGE_THRESHOLD:
        logger.info(f"跳过（边际不足）: 边际={edge*100:.1f}% < 阈值={PROBABILITY_EDGE_THRESHOLD*100:.0f}%")
        return {"status": "SKIPPED", "reason": f"边际不足 {edge*100:.1f}%"}

    if confidence != "HIGH":
        logger.info(f"跳过（置信度不足）: {confidence}")
        return {"status": "SKIPPED", "reason": f"置信度 {confidence}，要求 HIGH"}

    # 单日风控检查
    today_bet = _today_total_bet()
    today_pnl = _today_total_pnl()
    if today_bet >= DAILY_BET_LIMIT_USD:
        logger.info(f"跳过（单日下注已达上限 ${DAILY_BET_LIMIT_USD}）")
        return {"status": "SKIPPED", "reason": f"单日下注上限 ${DAILY_BET_LIMIT_USD}"}
    if today_pnl <= -DAILY_LOSS_LIMIT_USD:
        logger.info(f"跳过（单日亏损已达上限 ${DAILY_LOSS_LIMIT_USD}）")
        return {"status": "SKIPPED", "reason": f"单日风控：亏损 ${today_pnl:+.2f}"}

    # 计算下注金额（Kelly准则简化版：按边际比例）
    bet_amount = min(MAX_BET_PER_TRADE_USD, MAX_BET_PER_TRADE_USD * (edge / 0.20))
    bet_amount = min(bet_amount, DAILY_BET_LIMIT_USD - today_bet)  # 不超过单日剩余额度
    bet_amount = round(bet_amount, 2)

    # 记录到经验日志（无论是否真实下单）
    record = add_analysis_record(
        market_question=analysis["market_question"],
        market_yes_price=analysis["market_yes_price"],
        claude_probability=analysis["probability"],
        claude_reasoning=analysis.get("reasoning", ""),
        recommended_action=action,
        action_taken=f"{'真实下单' if AUTO_TRADE_ENABLED else '模拟记录'}: {action} ${bet_amount}",
        bet_amount_usd=bet_amount if AUTO_TRADE_ENABLED else 0,
        market_id=analysis.get("market_id", ""),
    )

    if AUTO_TRADE_ENABLED and os.getenv("POLYMARKET_PRIVATE_KEY"):
        return _execute_real_trade(analysis, bet_amount, record["id"])
    else:
        return _execute_paper_trade(analysis, bet_amount, record["id"])


def _execute_paper_trade(analysis: Dict, bet_amount: float, record_id: int) -> Dict:
    """
    模拟交易（安全模式）- 只记录，不实际操作
    """
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
        f"[Paper] 模拟交易记录 #{record_id}: {analysis['action']} ${bet_amount} | "
        f"边际 {analysis['probability_edge']*100:+.1f}% | "
        f"{analysis['market_question'][:50]}"
    )
    return result


def _execute_real_trade(analysis: Dict, bet_amount: float, record_id: int) -> Dict:
    """
    真实交易执行
    需要安装: pip install py-clob-client
    需要配置: POLYMARKET_PRIVATE_KEY（以太坊私钥）
    ⚠ 警告：真实资金操作，请谨慎
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType

        private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137  # Polygon 主网
        )

        # 确定购买的 token
        is_buy_yes = analysis["action"] == "BUY_YES"
        token_id = analysis.get("yes_token_id") if is_buy_yes else analysis.get("no_token_id")

        if not token_id:
            logger.error("缺少 token_id，无法下单")
            return {"status": "FAILED", "reason": "缺少 token_id"}

        # 下限价单
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
        logger.info(f"[OK] 真实下单成功 #{record_id}: 订单ID={result['order_id']}")
        return result

    except ImportError:
        logger.error("py-clob-client 未安装，请运行: pip install py-clob-client")
        return {"status": "FAILED", "reason": "py-clob-client 未安装"}
    except Exception as e:
        logger.error(f"真实下单失败: {e}")
        return {"status": "FAILED", "reason": str(e)}


def _append_trade_log(entry: Dict) -> None:
    """追加到交易日志文件"""
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
