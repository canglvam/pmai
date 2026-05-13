"""
AI analysis module — the "brain" of the system
Supports DeepSeek V4 (cheap, fast) and Claude Opus/Sonnet (stronger)

Switch models in config.py — just change ACTIVE_MODEL:
  "deepseek-v4-pro"   → DeepSeek V4 Pro
  "deepseek-v4-flash" → DeepSeek V4 Flash (cheapest)
  "claude-opus"       → Claude Opus 4.6 (strongest)
  "claude-sonnet"     → Claude Sonnet 4.6 (balanced)
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
# Initialize AI client
# ============================================================
def _init_client():
    if ACTIVE_MODEL in DEEPSEEK_MODEL_MAP:
        from openai import OpenAI
        model_name = DEEPSEEK_MODEL_MAP[ACTIVE_MODEL]
        logger.info(f"AI brain: DeepSeek {model_name}")
        return "deepseek", model_name, OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
            timeout=60.0,
            max_retries=2,
        )
    elif ACTIVE_MODEL in CLAUDE_MODEL_MAP:
        import anthropic
        model_name = CLAUDE_MODEL_MAP[ACTIVE_MODEL]
        logger.info(f"AI brain: Claude {model_name}")
        return "claude", model_name, anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    else:
        raise ValueError(
            f"Unknown model '{ACTIVE_MODEL}'. Choose from config.py:\n"
            f"  deepseek-v4-pro / deepseek-v4-flash / claude-opus / claude-sonnet"
        )

CLIENT_TYPE, MODEL_NAME, ai_client = _init_client()

# ============================================================
# System prompt
# ============================================================
SYSTEM_PROMPT = """You are a professional prediction market analyst focused on Polymarket.

Your task:
1. Analyze the given prediction market event
2. Synthesize relevant news and historical experience to judge the true probability
3. Compare with current market pricing to find pricing gaps (alpha)
4. Give a clear action recommendation

Output format (strict JSON only, no other text):
{
  "title_cn": "Event name in English",
  "probability": 0.XX,
  "confidence": "HIGH/MEDIUM/LOW",
  "reasoning": "Detailed analysis reasoning",
  "key_factors": ["Key factor 1", "Key factor 2"],
  "action": "BUY_YES/BUY_NO/SKIP",
  "action_reason": "Reason for action",
  "risk_warning": "Risk warning"
}

Rules:
- Only recommend action when the gap vs market price is significant (>8%) AND confidence is HIGH
- Political/economic events are usually more predictable than entertainment
- Stay conservative with uncertain information
"""

# ============================================================
# Core AI call
# ============================================================
def _call_ai(user_message: str, system_prompt: str = SYSTEM_PROMPT) -> Optional[str]:
    """Unified AI call interface, abstracts away DeepSeek vs Claude differences"""
    try:
        if CLIENT_TYPE == "deepseek":
            response = ai_client.chat.completions.create(
                model=MODEL_NAME,
                max_tokens=AI_MAX_TOKENS,
                temperature=0.3,
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
        logger.error(f"AI call failed ({CLIENT_TYPE}/{MODEL_NAME}): {e}")
        return None


# ============================================================
# Market analysis
# ============================================================
def analyze_market(market: Dict, news_items: list) -> Optional[Dict]:
    """Core analysis: market data + news + history → analysis conclusion"""
    market_text = format_market_for_analysis(market)
    news_text = format_news_for_analysis(news_items)
    experience_text = format_experience_for_claude(max_records=20)

    user_message = f"""
Analyze the following prediction market event:

=== Market Info ===
{market_text}

=== Latest News ===
{news_text}

=== Historical Experience ===
{experience_text}

Synthesize the above and output JSON analysis. Only recommend action when probability gap > {PROBABILITY_EDGE_THRESHOLD*100:.0f}% AND confidence is HIGH.
""".strip()

    raw_text = _call_ai(user_message)
    if not raw_text:
        return None

    analysis = _parse_json_response(raw_text)
    if not analysis:
        logger.error(f"Failed to parse AI JSON output: {raw_text[:200]}")
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
        f"Analysis [{CLIENT_TYPE}]: '{display_name}' | "
        f"Market:{market_price*100:.0f}% → AI:{analysis['probability']*100:.0f}% | "
        f"Action:{analysis['action']}"
    )
    return analysis


def batch_analyze_markets(markets: list, news_map: dict) -> list:
    """Batch analyze multiple markets, sort by opportunity size"""
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
    """Generate scan summary report"""
    if not analyses:
        return "No significant opportunities found this round."

    opportunities = [
        a for a in analyses
        if abs(a.get("probability_edge", 0)) >= PROBABILITY_EDGE_THRESHOLD
        and a.get("confidence") == "HIGH"
        and a.get("action") != "SKIP"
    ]

    lines = [
        f"[Report] PMAI Scan [{CLIENT_TYPE}/{MODEL_NAME}]",
        f"Analyzed {len(analyses)} markets, found {len(opportunities)} opportunities\n",
    ]

    if opportunities:
        lines.append("[>>] Recommendations:")
        for a in opportunities:
            edge = a['probability_edge'] * 100
            direction = "[BUY YES]" if a['action'] == "BUY_YES" else "[BUY NO]"
            title_cn = a.get('title_cn', '')
            title_display = f"{title_cn} | {a['market_question'][:80]}" if title_cn else a['market_question'][:80]
            lines.append(
                f"\n{direction} | Edge: {edge:+.1f}%"
                f"{' [Review: ' + a['review'].get('verdict', '?') + ']' if a.get('review') else ''}\n"
                f"Event: {title_display}\n"
                f"Market:{a['market_yes_price']*100:.0f}% → AI:{a['probability']*100:.0f}%\n"
                f"Reason: {a['action_reason']}\n"
                f"Link: {a['market_url']}"
            )
    else:
        lines.append("No high-confidence opportunities. Standing by.")

    return "\n".join(lines)


def _parse_json_response(text: str) -> Optional[Dict]:
    """Extract JSON from AI response"""
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
# Secondary review: re-check settlement rules
# ============================================================
REVIEW_SYSTEM_PROMPT = """You are a strict Polymarket rule reviewer.

Your task is to perform a "secondary review" of an investment analysis:
1. Read the full market settlement rules line by line
2. Check if the initial analysis is consistent with the rules
3. Pay special attention to: additional info, rule clarifications, edge cases, exclusion clauses
4. If the initial analysis missed a key rule, you must correct it

Output format (strict JSON):
{
  "verdict": "CONFIRM/OVERRIDE/ADJUST",
  "reviewed_probability": 0.XX,
  "reviewed_action": "BUY_YES/BUY_NO/SKIP",
  "reviewed_confidence": "HIGH/MEDIUM/LOW",
  "review_reasoning": "Review reasoning based on rule analysis",
  "rule_conflicts": ["Discovered rule conflicts", "..."],
  "final_recommendation": "Overall conclusion"
}

Rules:
- If settlement rules clearly conflict with initial analysis, set verdict=OVERRIDE with correct conclusion
- If rules have ambiguous areas, reduce confidence, set verdict=ADJUST
- If rules fully support initial analysis, set verdict=CONFIRM
- Pay special attention to exclusion clauses (what does NOT count) — these are common traps"""


def review_decision(analysis: Dict, market: Dict) -> Optional[Dict]:
    """Secondary review: feed full settlement rules to AI for re-check"""
    rules_text = format_rules_for_review(market)
    experience_text = format_experience_for_claude(max_records=10)

    review_message = f"""
Review whether the initial analysis is consistent with market settlement rules:

=== Full Settlement Rules ===
{rules_text}

=== Initial Analysis ===
- Event: {analysis.get('title_cn', 'N/A')}
- Probability: {analysis['probability']*100:.1f}%
- Market price: {analysis['market_yes_price']*100:.1f}%
- Edge: {analysis['probability_edge']*100:+.1f}%
- Action: {analysis['action']}
- Reasoning: {analysis.get('reasoning', 'None')}
- Action reason: {analysis.get('action_reason', 'None')}
- Risk warning: {analysis.get('risk_warning', 'None')}

=== Historical Experience ===
{experience_text}

Review the rules and give your final judgment (JSON).
""".strip()

    raw_text = _call_ai(review_message, system_prompt=REVIEW_SYSTEM_PROMPT)
    if not raw_text:
        return None

    review = _parse_json_response(raw_text)
    if not review:
        logger.error(f"Failed to parse review JSON: {raw_text[:200]}")
        return None

    logger.info(
        f"Review complete: {review.get('verdict', '?')} | "
        f"Initial {analysis['action']} → Review {review.get('reviewed_action', '?')} | "
        f"{review.get('review_reasoning', '')[:80]}"
    )
    return review
