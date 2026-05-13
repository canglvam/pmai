"""
Configuration — API keys and system parameters
"""

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ============================================================
# API Keys (use environment variables, never hardcode)
# ============================================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your_anthropic_api_key")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "your_serper_api_key")

# Polymarket trading wallet (live trading only)
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")

# ============================================================
# Monitor parameters
# ============================================================
MONITOR_INTERVAL_MINUTES = 30
MAX_MARKETS_TO_ANALYZE = 10
MIN_VOLUME_USD = 10000

# ============================================================
# Trading parameters
# ============================================================
PROBABILITY_EDGE_THRESHOLD = 0.12
MAX_BET_PER_TRADE_USD = 50
AUTO_TRADE_ENABLED = False

# ============================================================
# AI model configuration
# Change this line to switch models:
#   "deepseek-v4-pro"   → DeepSeek V4 Pro
#   "deepseek-v4-flash" → DeepSeek V4 Flash (cheaper, faster)
#   "claude-opus"       → Claude Opus 4.6 (strongest)
#   "claude-sonnet"     → Claude Sonnet 4.6 (balanced)
# ============================================================
ACTIVE_MODEL = "deepseek-v4-flash"

# DeepSeek config
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "your_deepseek_api_key")
DEEPSEEK_MODEL_MAP = {
    "deepseek-v4-pro":   "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
}

# Claude config
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your_anthropic_api_key")
CLAUDE_MODEL_MAP = {
    "claude-opus":   "claude-opus-4-6",
    "claude-sonnet": "claude-sonnet-4-6",
}

AI_MAX_TOKENS = 2000

# ============================================================
# Circle Developer Wallets (hackathon: 20% Circle tool usage score)
# Register at https://console.circle.com → API Keys → SANDBOX
# ============================================================
CIRCLE_API_KEY = os.getenv("CIRCLE_API_KEY", "")
ENTITY_SECRET = os.getenv("ENTITY_SECRET", "")
CIRCLE_WALLET_ENABLED = bool(CIRCLE_API_KEY and ENTITY_SECRET)

# ============================================================
# Risk controls
# ============================================================
DAILY_BET_LIMIT_USD = 200
DAILY_LOSS_LIMIT_USD = 100

# ============================================================
# File paths
# ============================================================
EXPERIENCE_LOG_FILE = "data/experience_log.json"
TRADE_LOG_FILE = "logs/trade_log.json"
