"""
新闻搜索模块 - 为每个 Polymarket 事件搜索相关新闻
使用 Serper API（Google 搜索接口，$50/月 2500次搜索）
备选：Tavily API、NewsAPI
"""

import requests
import logging
from typing import List, Dict
from config import SERPER_API_KEY

logger = logging.getLogger(__name__)


def search_news_for_event(question: str, num_results: int = 5) -> List[Dict]:
    """
    根据市场问题搜索相关新闻
    返回新闻标题、摘要、来源、发布时间列表
    """
    if not SERPER_API_KEY or SERPER_API_KEY == "your_serper_api_key":
        logger.warning("未配置 SERPER_API_KEY，使用模拟新闻数据")
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

        logger.info(f"为 '{question[:50]}...' 找到 {len(news_items)} 条新闻")
        return news_items

    except requests.RequestException as e:
        logger.error(f"新闻搜索失败: {e}")
        return []


def format_news_for_analysis(news_items: List[Dict]) -> str:
    """把新闻列表格式化成适合 Claude 分析的文本"""
    if not news_items:
        return "未找到相关新闻"

    lines = []
    for i, item in enumerate(news_items, 1):
        lines.append(f"{i}. [{item['source']} {item['date']}] {item['title']}")
        if item['snippet']:
            lines.append(f"   摘要：{item['snippet']}")
    return "\n".join(lines)


def _mock_news(question: str) -> List[Dict]:
    """
    未配置 API 时返回模拟数据，用于开发测试
    生产环境请配置真实的 SERPER_API_KEY
    """
    return [
        {
            "title": f"[模拟新闻] 关于 '{question[:40]}' 的最新报道",
            "snippet": "这是模拟新闻摘要，请配置 SERPER_API_KEY 获取真实新闻。",
            "source": "MockNews",
            "date": "刚刚",
            "link": "#"
        }
    ]
