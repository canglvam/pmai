"""
经验积累模块 - 记录每次分析和交易结果，供下次分析参考
这是系统"越用越聪明"的核心机制
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional
from config import EXPERIENCE_LOG_FILE

logger = logging.getLogger(__name__)


def load_experience() -> List[Dict]:
    """加载历史经验记录"""
    if not os.path.exists(EXPERIENCE_LOG_FILE):
        return []
    try:
        with open(EXPERIENCE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载经验记录失败: {e}")
        return []


def save_experience(records: List[Dict]) -> None:
    """保存经验记录"""
    os.makedirs(os.path.dirname(EXPERIENCE_LOG_FILE), exist_ok=True)
    try:
        with open(EXPERIENCE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存经验记录失败: {e}")


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
    """
    记录一次分析结果（下注结果稍后通过 update_trade_result 或自动复盘更新）
    """
    records = load_experience()
    record = {
        "id": len(records) + 1,
        "timestamp": datetime.now().isoformat(),
        "market_id": market_id,                     # 用于自动复盘时查询结算状态
        "market_question": market_question,
        "market_yes_price": market_yes_price,       # 市场定价的YES概率
        "claude_probability": claude_probability,   # Claude 判断的真实概率
        "probability_edge": claude_probability - market_yes_price,
        "claude_reasoning": claude_reasoning[:500], # 截断，节省token
        "recommended_action": recommended_action,
        "action_taken": action_taken,
        "bet_amount_usd": bet_amount_usd,
        "result": None,       # 待更新：WIN / LOSS / PUSH
        "pnl_usd": None,      # 待更新：盈亏金额
        "lesson": None,       # 待更新：事后复盘
    }
    records.append(record)
    save_experience(records)
    logger.info(f"已记录分析 #{record['id']}: {market_question[:50]}")
    return record


def update_trade_result(record_id: int, result: str, pnl_usd: float, lesson: str = "") -> None:
    """
    事后更新某条记录的交易结果
    result: 'WIN' / 'LOSS' / 'PUSH'
    """
    records = load_experience()
    for r in records:
        if r["id"] == record_id:
            r["result"] = result
            r["pnl_usd"] = pnl_usd
            r["lesson"] = lesson
            break
    save_experience(records)
    logger.info(f"已更新记录 #{record_id} 结果: {result} ${pnl_usd:+.2f}")


def format_experience_for_claude(max_records: int = 20) -> str:
    """
    把历史经验格式化成文本，喂给 Claude API 参考
    只取最近 N 条有结果的记录
    """
    records = load_experience()
    completed = [r for r in records if r.get("result") is not None]
    recent = completed[-max_records:]

    if not recent:
        return "暂无历史交易记录，这是系统首次运行。"

    wins = sum(1 for r in recent if r["result"] == "WIN")
    losses = sum(1 for r in recent if r["result"] == "LOSS")
    total_pnl = sum(r.get("pnl_usd", 0) or 0 for r in recent)

    summary = f"历史战绩（最近{len(recent)}笔）：{wins}胜 {losses}负，总盈亏 ${total_pnl:+.2f}\n\n"
    summary += "代表性案例：\n"

    for r in recent[-5:]:  # 只给最近5条详情，避免token过多
        summary += (
            f"- {r['timestamp'][:10]} | {r['market_question'][:60]}\n"
            f"  市场定价:{r['market_yes_price']*100:.0f}% vs Claude判断:{r['claude_probability']*100:.0f}%"
            f"  → {r['action_taken']} → {r['result']} ${r.get('pnl_usd', 0):+.1f}\n"
        )
        if r.get("lesson"):
            summary += f"  教训：{r['lesson']}\n"

    return summary


def get_recent_market_ids(cooldown_hours: int = 6) -> set:
    """
    获取最近 N 小时内已分析过的市场 ID 集合
    用于去重，避免同一扫描周期内重复分析同一市场浪费 token
    """
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
                # 从 market_question 提取关键词作为去重标识
                # 直接用 question 的前 80 个字符做 fuzzy key
                q = r.get("market_question", "")
                recent_ids.add(q[:80])
        except (ValueError, KeyError):
            continue
    return recent_ids


def get_stats() -> Dict:
    """获取整体统计数据"""
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
