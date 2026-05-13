"""
Polymarket 客户端 - 抓取市场事件和赔率数据
使用 Polymarket Gamma API（公开，无需认证）
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from typing import List, Dict, Optional
from config import MIN_VOLUME_USD, MAX_MARKETS_TO_ANALYZE

logger = logging.getLogger(__name__)

# 带重试的请求 Session，应对间歇性网络波动
def _retry_session(total: int = 3, backoff: float = 1.0) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=total,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def get_active_markets(limit: int = MAX_MARKETS_TO_ANALYZE) -> List[Dict]:
    """
    获取当前活跃的预测市场，按交易量排序
    返回最有价值（流动性高）的市场列表
    """
    try:
        resp = _retry_session().get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit * 3,          # 多取一些，过滤后留够
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=10
        )
        resp.raise_for_status()
        markets_raw = resp.json()

        markets = []
        for m in markets_raw:
            volume = float(m.get("volume", 0) or 0)
            if volume < MIN_VOLUME_USD:
                continue

            # 解析 outcome 价格（YES 概率）
            outcomes = m.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
                import json
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []

            yes_price = float(outcomes[0]) if outcomes else None

            markets.append({
                "id": m.get("id"),
                "condition_id": m.get("conditionId"),
                "question": m.get("question"),
                "description": m.get("description", ""),
                "end_date": m.get("endDate"),
                "volume_usd": volume,
                "yes_price": yes_price,           # 当前市场对YES的定价（即隐含概率）
                "yes_token_id": m.get("clobTokenIds", [""])[0] if m.get("clobTokenIds") else "",
                "no_token_id": m.get("clobTokenIds", ["", ""])[1] if m.get("clobTokenIds") and len(m.get("clobTokenIds")) > 1 else "",
                "tags": [t.get("label", "") for t in m.get("tags", [])],
                "url": f"https://polymarket.com/event/{m.get('slug', '')}",
            })

            if len(markets) >= limit:
                break

        logger.info(f"获取到 {len(markets)} 个活跃市场")
        return markets

    except requests.RequestException as e:
        logger.error(f"获取市场数据失败: {e}")
        return []


def get_market_orderbook(token_id: str) -> Optional[Dict]:
    """
    获取某个市场 token 的订单薄（买卖深度）
    用于判断流动性和实际可成交价格
    """
    try:
        resp = _retry_session().get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"获取订单薄失败 token={token_id}: {e}")
        return None


def format_market_for_analysis(market: Dict) -> str:
    """把市场数据格式化成适合给 Claude 分析的文本"""
    yes_pct = f"{market['yes_price'] * 100:.1f}%" if market['yes_price'] else "未知"
    tags_str = ", ".join(market['tags']) if market['tags'] else "无标签"

    return f"""
事件：{market['question']}
描述：{market['description'][:300] if market['description'] else '无'}
当前市场定价（YES概率）：{yes_pct}
24小时交易量：${market['volume_usd']:,.0f}
标签分类：{tags_str}
截止日期：{market['end_date']}
链接：{market['url']}
""".strip()


def format_rules_for_review(market: Dict) -> str:
    """提取市场的完整清算规则，用于二审复核"""
    description = market.get("description", "")
    if not description:
        return "无详细规则描述"

    yes_display = f"{market['yes_price']*100:.1f}%" if market['yes_price'] else "未知"

    return f"""
市场事件：{market['question']}
当前市场定价（YES概率）：{yes_display}

=== 完整清算规则 / 补充信息 ===
{description}

=== 关键信息提示 ===
请特别注意以上规则中：
1. 哪些情况会触发"Yes"结算
2. 哪些情况明确被排除（不算"Yes"）
3. 是否有"补充信息"或"澄清"内容
4. 是否存在争议或解释空间
5. 截止日期的具体条件
""".strip()
