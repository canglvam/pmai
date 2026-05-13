"""
Polymarket client — fetch market events and odds data
Uses Polymarket Gamma API (public, no auth required)
"""

import json
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import List, Dict, Optional
from config import MIN_VOLUME_USD, MAX_MARKETS_TO_ANALYZE

logger = logging.getLogger(__name__)

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
    """Fetch active prediction markets, sorted by volume"""
    try:
        resp = _retry_session().get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit * 3,
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

            outcomes = m.get("outcomePrices", "[]")
            if isinstance(outcomes, str):
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
                "yes_price": yes_price,
                "yes_token_id": m.get("clobTokenIds", [""])[0] if m.get("clobTokenIds") else "",
                "no_token_id": m.get("clobTokenIds", ["", ""])[1] if m.get("clobTokenIds") and len(m.get("clobTokenIds")) > 1 else "",
                "tags": [t.get("label", "") for t in m.get("tags", [])],
                "url": f"https://polymarket.com/event/{m.get('slug', '')}",
            })

            if len(markets) >= limit:
                break

        logger.info(f"Fetched {len(markets)} active markets")
        return markets

    except requests.RequestException as e:
        logger.error(f"Failed to fetch markets: {e}")
        return []


def get_market_orderbook(token_id: str) -> Optional[Dict]:
    """Get orderbook depth for a market token"""
    try:
        resp = _retry_session().get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch orderbook token={token_id}: {e}")
        return None


def format_market_for_analysis(market: Dict) -> str:
    """Format market data for AI analysis"""
    yes_pct = f"{market['yes_price'] * 100:.1f}%" if market['yes_price'] else "N/A"
    tags_str = ", ".join(market['tags']) if market['tags'] else "No tags"

    return f"""
Event: {market['question']}
Description: {market['description'][:300] if market['description'] else 'None'}
Market price (YES probability): {yes_pct}
24h volume: ${market['volume_usd']:,.0f}
Tags: {tags_str}
End date: {market['end_date']}
Link: {market['url']}
""".strip()


def format_rules_for_review(market: Dict) -> str:
    """Extract full settlement rules for secondary review"""
    description = market.get("description", "")
    if not description:
        return "No detailed rule description"

    yes_display = f"{market['yes_price']*100:.1f}%" if market['yes_price'] else "N/A"

    return f"""
Market event: {market['question']}
Market price (YES): {yes_display}

=== Settlement Rules / Additional Info ===
{description}

=== Key Points ===
Pay special attention to:
1. Which conditions trigger "Yes" settlement
2. Which conditions are explicitly excluded (not "Yes")
3. Any "Additional Information" or "Clarifications"
4. Any room for dispute or interpretation
5. End date specifics
""".strip()
