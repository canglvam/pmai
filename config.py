"""
配置文件 - 所有 API 密钥和系统参数
"""

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ============================================================
# API 密钥（建议用环境变量，不要硬编码）
# ============================================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your_anthropic_api_key")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "your_serper_api_key")  # 新闻搜索

# Polymarket 交易钱包（仅自动下注时需要）
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")  # 以太坊私钥

# ============================================================
# 监控参数
# ============================================================
MONITOR_INTERVAL_MINUTES = 30        # 每隔多少分钟检查一次
MAX_MARKETS_TO_ANALYZE = 10          # 每次最多分析多少个市场
MIN_VOLUME_USD = 10000               # 只关注交易量大于此值的市场（美元）

# ============================================================
# 投资决策参数
# ============================================================
PROBABILITY_EDGE_THRESHOLD = 0.12    # 市场概率 vs AI判断 差值超过12%才操作（避免薄利被滑点吃掉）
MAX_BET_PER_TRADE_USD = 50           # 单次最大下注金额（美元）
AUTO_TRADE_ENABLED = False           # 是否自动下注（False=只通知，不实际下单）

# ============================================================
# AI 分析模型配置
# 改这一行即可切换模型：
#   "deepseek-v4-pro"   → DeepSeek V4 Pro（便宜，先跑通用这个）
#   "deepseek-v4-flash" → DeepSeek V4 Flash（更便宜，速度更快）
#   "claude-opus"       → Claude Opus 4.6（最强，跑通后升级用这个）
#   "claude-sonnet"     → Claude Sonnet 4.6（中档，性价比高）
# ============================================================
ACTIVE_MODEL = "deepseek-v4-flash"

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "your_deepseek_api_key")
DEEPSEEK_MODEL_MAP = {
    "deepseek-v4-pro":   "deepseek-v4-pro",    # $0.14/$1.74 per 1M tokens
    "deepseek-v4-flash": "deepseek-v4-flash",  # $0.014/$0.28 per 1M tokens（更便宜）
}

# Claude 配置
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your_anthropic_api_key")
CLAUDE_MODEL_MAP = {
    "claude-opus":   "claude-opus-4-6",    # 最强，约 $15/$75 per 1M tokens
    "claude-sonnet": "claude-sonnet-4-6",  # 均衡，约 $3/$15 per 1M tokens
}

AI_MAX_TOKENS = 2000

# ============================================================
# 文件路径
# ============================================================
# ============================================================
# Circle 开发者钱包配置（黑客松加分：Circle 工具使用 20% 权重）
# 在 https://console.circle.com 注册 → API Keys → SANDBOX
# ============================================================
CIRCLE_API_KEY = os.getenv("CIRCLE_API_KEY", "")
ENTITY_SECRET = os.getenv("ENTITY_SECRET", "")  # 32-byte hex
CIRCLE_WALLET_ENABLED = bool(CIRCLE_API_KEY and ENTITY_SECRET)

# ============================================================
# 风控参数
# ============================================================
DAILY_BET_LIMIT_USD = 200          # 单日下注总金额上限
DAILY_LOSS_LIMIT_USD = 100         # 单日亏损上限（超过暂停）

# ============================================================
# 文件路径
# ============================================================
EXPERIENCE_LOG_FILE = "data/experience_log.json"
TRADE_LOG_FILE = "logs/trade_log.json"
