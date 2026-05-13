"""
News search module — search Google News for each Polymarket event
Uses Serper API (Google Search interface)
"""

import requests
import logging
from typing import List, Dict
from config import SERPER_API_KEY

logger = logging.getLogger(__name__)


def search_news_for_event(question: str, num_results: int = 5) -> List[Dict]:
    """Search news for a market question. Returns title, snippet, source, date, link."""
    if not SERPER_API_KEY or SERPER_API_KEY == "your_serper_api_key":
        logger.warning("SERPER_API_KEY not configured, using mock news data")
        return _mock_news(question)

    try:
        resp = requests.post(
            "https://google.serper.dev/news",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "q": question,
                "num": num_results,
                "hl": "en",
                "gl": "us"
            },
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        news_items = []
        for item in data.get("news", []):
            news_items.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
                "link": item.get("link", ""),
            })

        logger.info(f"Found {len(news_items)} news items for '{question[:50]}...'")
        return news_items

    except requests.RequestException as e:
        logger.error(f"News search failed: {e}")
        return []


def format_news_for_analysis(news_items: List[Dict]) -> str:
    """Format news list for AI analysis"""
    if not news_items:
        return "No news found"

    lines = []
    for i, item in enumerate(news_items, 1):
        lines.append(f"{i}. [{item['source']} {item['date']}] {item['title']}")
        if item['snippet']:
            lines.append(f"   Summary: {item['snippet']}")
    return "\n".join(lines)


def _mock_news(question: str) -> List[Dict]:
    """Mock data for dev/testing when no API key configured"""
    return [
        {
            "title": f"[Mock news] Latest reports on '{question[:40]}'",
            "snippet": "This is mock news. Configure SERPER_API_KEY for real news.",
            "source": "MockNews",
            "date": "just now",
            "link": "#"
        }
    ]
