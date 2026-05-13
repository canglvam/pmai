"""
AI 分析模块 - 系统的"大脑"
支持 DeepSeek V4（便宜，先跑通） 和 Claude Opus/Sonnet（更强，后续升级）

切换模型只需修改 config.py 里的 ACTIVE_MODEL 一行：
  "deepseek-v4-pro"   → DeepSeek V4 Pro（推荐先用这个跑通）
  "deepseek-v4-flash" → DeepSeek V4 Flash（最便宜）
  "claude-opus"       → Claude Opus 4.6（最强，跑通后升级）
  "claude-sonnet"     → Claude Sonnet 4.6（均衡）
"""

import json
import logging
import re
import time
from typing import Dict, Optional

from config import (
    ACTIVE_MODEL,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL_MAP,
    ANTHROPIC_API_KEY, CLAUDE_MODEL_MAP,
    AI_MAX_TOKENS, PROBABILITY_EDGE_THRESHOLD,
)
from experience_manager import format_experience_for_claude
from polymarket_client import format_market_for_analysis, format_rules_for_review
from news_searcher import format_news_for_analysis

logger = logging.getLogger(__name__)

# ============================================================
# 初始化 AI 客户端
# ============================================================
def _init_client():
    if ACTIVE_MODEL in DEEPSEEK_MODEL_MAP:
        from openai import OpenAI
        model_name = DEEPSEEK_MODEL_MAP[ACTIVE_MODEL]
        logger.info(f"AI 大脑：DeepSeek {model_name}")
        return "deepseek", model_name, OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
            timeout=60.0,   # DeepSeek 有时响应慢，加长超时
            max_retries=2,  # 减少无效重试，失败快速报错
        )
    elif ACTIVE_MODEL in CLAUDE_MODEL_MAP:
        import anthropic
        model_name = CLAUDE_MODEL_MAP[ACTIVE_MODEL]
        logger.info(f"AI 大脑：Claude {model_name}")
        return "claude", model_name, anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    else:
        raise ValueError(
            f"未知模型 '{ACTIVE_MODEL}'，请在 config.py 里选择：\n"
            f"  deepseek-v4-pro / deepseek-v4-flash / claude-opus / claude-sonnet"
        )

CLIENT_TYPE, MODEL_NAME, ai_client = _init_client()

# ============================================================
# 分析提示词
# ============================================================
SYSTEM_PROMPT = """你是一个专业的预测市场分析师，专注于 Polymarket 平台。

你的任务：
1. 分析给定的预测市场事件
2. 综合相关新闻和历史经验，判断事件发生的真实概率
3. 与市场当前定价对比，找出定价偏差（alpha）
4. 给出明确操作建议

输出格式（必须严格输出 JSON，不要有任何其他文字）：
{
  "title_cn": "事件中文名称",
  "probability": 0.XX,
  "confidence": "HIGH/MEDIUM/LOW",
  "reasoning": "详细分析理由（中文）",
  "key_factors": ["关键因素1", "关键因素2"],
  "action": "BUY_YES/BUY_NO/SKIP",
  "action_reason": "操作理由（中文）",
  "risk_warning": "风险提示（中文）"
}

规则：
- 只有判断与市场定价差距明显（>8%）且置信度 HIGH 时才建议操作
- 政治、经济类事件通常比娱乐类更可预测
- 对不确定信息保持保守态度
"""

# ============================================================
# 核心调用函数
# ============================================================
def _call_ai(user_message: str, system_prompt: str = SYSTEM_PROMPT) -> Optional[str]:
    """统一的 AI 调用接口，屏蔽 DeepSeek 和 Claude 的差异"""
    try:
        if CLIENT_TYPE == "deepseek":
            response = ai_client.chat.completions.create(
                model=MODEL_NAME,
                max_tokens=AI_MAX_TOKENS,
                temperature=0.3,  # 低温度保证分析一致性
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content

        elif CLIENT_TYPE == "claude":
            response = ai_client.messages.create(
                model=MODEL_NAME,
                max_tokens=AI_MAX_TOKENS,
                temperature=0.3,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text

    except Exception as e:
        logger.error(f"AI 调用失败 ({CLIENT_TYPE}/{MODEL_NAME}): {e}")
        return None


# ============================================================
# 市场分析
# ============================================================
def analyze_market(market: Dict, news_items: list) -> Optional[Dict]:
    """核心分析：综合市场数据、新闻、历史经验，输出分析结论"""
    market_text = format_market_for_analysis(market)
    news_text = format_news_for_analysis(news_items)
    experience_text = format_experience_for_claude(max_records=20)

    user_message = f"""
请分析以下预测市场事件：

=== 市场信息 ===
{market_text}

=== 相关新闻（最新）===
{news_text}

=== 历史经验参考 ===
{experience_text}

请综合以上信息给出 JSON 分析。只有概率差 >{PROBABILITY_EDGE_THRESHOLD*100:.0f}% 且置信度 HIGH 时才建议操作。
""".strip()

    raw_text = _call_ai(user_message)
    if not raw_text:
        return None

    analysis = _parse_json_response(raw_text)
    if not analysis:
        logger.error(f"无法解析 AI 的 JSON 输出: {raw_text[:200]}")
        return None

    market_price = market.get("yes_price", 0.5) or 0.5
    analysis["market_yes_price"] = market_price
    analysis["probability_edge"] = analysis["probability"] - market_price
    analysis["market_question"] = market["question"]
    analysis["market_id"] = market["id"]
    analysis["market_url"] = market["url"]
    analysis["model_used"] = f"{CLIENT_TYPE}/{MODEL_NAME}"
    analysis["yes_token_id"] = market.get("yes_token_id", "")
    analysis["no_token_id"] = market.get("no_token_id", "")

    title_cn = analysis.get("title_cn", "")
    display_name = f"{title_cn} | {market['question'][:40]}" if title_cn else market['question'][:50]
    logger.info(
        f"分析完成 [{CLIENT_TYPE}]: '{display_name}' | "
        f"市场:{market_price*100:.0f}% → AI:{analysis['probability']*100:.0f}% | "
        f"建议:{analysis['action']}"
    )
    return analysis


def batch_analyze_markets(markets: list, news_map: dict) -> list:
    """批量分析多个市场，按机会大小排序"""
    results = []
    for i, market in enumerate(markets):
        news = news_map.get(market["id"], [])
        analysis = analyze_market(market, news)
        if analysis:
            results.append(analysis)
        if i < len(markets) - 1:
            time.sleep(1)
    results.sort(key=lambda x: abs(x.get("probability_edge", 0)), reverse=True)
    return results


def generate_summary_report(analyses: list) -> str:
    """生成本次扫描汇总报告"""
    if not analyses:
        return "本次扫描未发现显著机会。"

    opportunities = [
        a for a in analyses
        if abs(a.get("probability_edge", 0)) >= PROBABILITY_EDGE_THRESHOLD
        and a.get("confidence") == "HIGH"
        and a.get("action") != "SKIP"
    ]

    lines = [
        f"[Report] Polymarket 扫描报告 [{CLIENT_TYPE}/{MODEL_NAME}]",
        f"共分析 {len(analyses)} 个市场，发现 {len(opportunities)} 个机会\n",
    ]

    if opportunities:
        lines.append("[>>] 推荐操作：")
        for a in opportunities:
            edge = a['probability_edge'] * 100
            direction = "[BUY YES]" if a['action'] == "BUY_YES" else "[BUY NO]"
            title_cn = a.get('title_cn', '')
            title_display = f"{title_cn} | {a['market_question'][:80]}" if title_cn else a['market_question'][:80]
            lines.append(
                f"\n{direction} | 边际: {edge:+.1f}%"
                f"{' [复审: ' + a['review'].get('verdict', '?') + ']' if a.get('review') else ''}\n"
                f"事件: {title_display}\n"
                f"市场:{a['market_yes_price']*100:.0f}% → AI:{a['probability']*100:.0f}%\n"
                f"理由: {a['action_reason']}\n"
                f"链接: {a['market_url']}"
            )
    else:
        lines.append("当前无高置信度机会，建议观望。")

    return "\n".join(lines)


def _parse_json_response(text: str) -> Optional[Dict]:
    """从 AI 回复中提取 JSON"""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ============================================================
# 二审复核：重新阅读规则，确认决策无误
# ============================================================
REVIEW_SYSTEM_PROMPT = """你是一个严格的 Polymarket 规则审核员。

你的任务是对一份已做出的投资分析进行"二审复核"：
1. 逐条阅读市场的完整清算规则
2. 检查初次分析的判断是否与规则一致
3. 特别关注：补充信息、规则澄清、边界情况、排除条款
4. 如果发现初次分析忽略了关键规则，必须纠正

输出格式（严格 JSON）：
{
  "verdict": "CONFIRM/OVERRIDE/ADJUST",
  "reviewed_probability": 0.XX,
  "reviewed_action": "BUY_YES/BUY_NO/SKIP",
  "reviewed_confidence": "HIGH/MEDIUM/LOW",
  "review_reasoning": "审核理由：逐条分析规则后得出的结论",
  "rule_conflicts": ["发现的规则冲突点", "..."],
  "final_recommendation": "综合结论"
}

规则：
- 如果清算规则明确与初次判断冲突，必须设 verdict=OVERRIDE 并给出正确结论
- 如果规则有模糊空间，降低置信度，设 verdict=ADJUST
- 如果规则完全支持初次判断，设 verdict=CONFIRM
- 特别注意规则中的排除条款（什么不算），这经常是陷阱"""


def review_decision(analysis: Dict, market: Dict) -> Optional[Dict]:
    """
    二审复核：把完整清算规则发给 AI，让它重新审核初次判断
    只对 action != SKIP 的高置信度分析进行复审
    """
    rules_text = format_rules_for_review(market)
    experience_text = format_experience_for_claude(max_records=10)

    review_message = f"""
请审核以下初次分析是否与市场清算规则一致：

=== 市场完整清算规则 ===
{rules_text}

=== 初次分析结论 ===
- 事件中文名：{analysis.get('title_cn', 'N/A')}
- 判断概率：{analysis['probability']*100:.1f}%
- 市场定价：{analysis['market_yes_price']*100:.1f}%
- 概率差：{analysis['probability_edge']*100:+.1f}%
- 操作建议：{analysis['action']}
- 初次理由：{analysis.get('reasoning', '无')}
- 操作理由：{analysis.get('action_reason', '无')}
- 风险提示：{analysis.get('risk_warning', '无')}

=== 历史经验参考 ===
{experience_text}

请逐条审核规则，给出最终判断（JSON）。
""".strip()

    raw_text = _call_ai(review_message, system_prompt=REVIEW_SYSTEM_PROMPT)
    if not raw_text:
        return None

    review = _parse_json_response(raw_text)
    if not review:
        logger.error(f"无法解析复审 JSON: {raw_text[:200]}")
        return None

    logger.info(
        f"复审完成: {review.get('verdict', '?')} | "
        f"初次{analysis['action']} → 复审{review.get('reviewed_action', '?')} | "
        f"{review.get('review_reasoning', '')[:80]}"
    )
    return review


