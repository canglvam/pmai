"""
主程序 - 整个系统的入口和调度器
运行方式：
  单次运行：python main.py --once
  持续监控：python main.py
  更新结果：python main.py --update-result <record_id> <WIN/LOSS> <pnl>
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

import schedule

from config import MONITOR_INTERVAL_MINUTES, CIRCLE_WALLET_ENABLED
from polymarket_client import get_active_markets
from news_searcher import search_news_for_event
from claude_analyzer import batch_analyze_markets, generate_summary_report, review_decision
from trader import execute_trade_decision
from experience_manager import update_trade_result, get_stats, get_recent_market_ids
from auto_review import run_auto_review
from notifier import notify
import circle_wallet

# ============================================================
# 日志配置
# ============================================================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/monitor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
# 强制 stdout 使用 utf-8 编码，避免 Windows GBK emoji 报错
import io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logger = logging.getLogger(__name__)

# 全局异常钩子：把未捕获的崩溃 traceback 也写进日志
def _log_uncaught_exception(exc_type, exc_value, exc_tb):
    import traceback
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical(f"未捕获异常，程序崩溃:\n{tb_text}")
sys.excepthook = _log_uncaught_exception


def _find_market_by_id(markets, market_id):
    """从市场列表中查找指定 ID 的市场"""
    for m in markets:
        if m["id"] == market_id:
            return m
    return None


def run_scan() -> None:
    """
    完整的一次扫描流程：
    抓取市场 → 搜索新闻 → Claude分析 → 执行决策 → 发通知
    """
    start_time = datetime.now()
    logger.info(f"\n{'='*60}")
    logger.info(f"[Scan] 开始扫描 {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*60}")

    # Step 0: 自动复盘已结算的市场
    reviewed = run_auto_review()
    if reviewed:
        logger.info(f"自动复盘：更新了 {reviewed} 笔历史记录")

    # Step 1: 获取活跃市场（带重试，应对网络波动）
    logger.info("Step 1/4: 获取 Polymarket 市场列表...")
    markets = []
    for attempt in range(3):
        markets = get_active_markets()
        if markets:
            break
        logger.warning(f"获取市场失败，第 {attempt+1}/3 次重试...")
        time.sleep(5 * (attempt + 1))  # 递增等待：5s, 10s, 15s
    if not markets:
        logger.warning("重试3次仍未获取到市场数据，本次跳过")
        return
    logger.info(f"获取到 {len(markets)} 个市场")

    # 去重：跳过最近已分析过的市场，节省 API 费用
    recent_ids = get_recent_market_ids(cooldown_hours=6)
    if recent_ids:
        fresh_markets = [m for m in markets if m["question"][:80] not in recent_ids]
        skipped = len(markets) - len(fresh_markets)
        if skipped:
            logger.info(f"跳过 {skipped} 个近期已分析的市场（6小时内），实际分析 {len(fresh_markets)} 个")
        markets = fresh_markets

    if not markets:
        logger.info("所有市场均已近期分析过，本次跳过")
        return

    # Step 2: 为每个市场搜索新闻
    logger.info("Step 2/4: 搜索相关新闻...")
    news_map = {}
    for market in markets:
        news = search_news_for_event(market["question"])
        news_map[market["id"]] = news
        time.sleep(0.5)  # 避免请求过于频繁

    # Step 3: Claude 批量分析
    logger.info("Step 3/4: Claude AI 分析中...")
    analyses = batch_analyze_markets(markets, news_map)
    logger.info(f"分析完成，共 {len(analyses)} 个结果")

    # Step 3.5: 二审复核 — 对有机会的市场重新审核规则
    logger.info("Step 3.5/4: 规则复审中...")
    for analysis in analyses:
        if analysis.get("action") == "SKIP":
            continue
        market = _find_market_by_id(markets, analysis.get("market_id"))
        if not market:
            continue
        review = review_decision(analysis, market)
        if review:
            analysis["review"] = review
            verdict = review.get("verdict", "?")
            if verdict in ("OVERRIDE", "ADJUST"):
                old_action = analysis.get("action")
                analysis["action"] = review.get("reviewed_action", analysis["action"])
                analysis["probability"] = review.get("reviewed_probability", analysis["probability"])
                analysis["confidence"] = review.get("reviewed_confidence", analysis["confidence"])
                analysis["action_reason"] = review.get("final_recommendation", analysis.get("action_reason", ""))
                logger.info(
                    f"复审修正 [{verdict}]: '{analysis.get('title_cn', analysis['market_question'][:30])}' | "
                    f"{old_action} → {analysis['action']}"
                )
            else:
                logger.info(f"复审确认 [CONFIRM]: '{analysis.get('title_cn', analysis['market_question'][:30])}'")
        time.sleep(1)

    # Step 4: 执行交易决策 + 发通知
    logger.info("Step 4/4: 执行决策...")
    trade_results = []
    for analysis in analyses:
        result = execute_trade_decision(analysis)
        trade_results.append(result)
        time.sleep(1)  # 避免 API 限流

    # 生成报告并通知
    report = generate_summary_report(analyses)
    stats = get_stats()
    report += f"\n\n[Stats] 累计战绩：{stats['total']}笔 | 胜率{stats['win_rate']*100:.0f}% | 总盈亏${stats['total_pnl']:+.2f}"

    # Circle 钱包状态
    if CIRCLE_WALLET_ENABLED:
        wallet_balance = circle_wallet.get_usdc_balance()
        addr = wallet_balance.get("address", "N/A")
        bal = wallet_balance.get("balance_usdc", 0)
        report += f"\n\n[Circle Wallet] {addr} | Balance: ${bal:.2f} USDC"
        report += f"\n[ArcScan] https://testnet.arcscan.app/address/{addr}"

    duration = (datetime.now() - start_time).seconds
    report += f"\n[Time] 本次扫描用时 {duration}秒"

    notify(report, urgent=any(r["status"] in ("ORDER_PLACED", "PAPER_TRADE") for r in trade_results))
    logger.info("[OK] 本次扫描完成")


def main():
    parser = argparse.ArgumentParser(description="Polymarket AI 监控系统")
    parser.add_argument("--once", action="store_true", help="只运行一次，不持续监控")
    parser.add_argument(
        "--update-result",
        nargs=4,
        metavar=("RECORD_ID", "RESULT", "PNL", "LESSON"),
        help="更新交易结果: --update-result <id> <WIN/LOSS/PUSH> <pnl_usd> <教训>",
    )
    args = parser.parse_args()

    # 更新历史记录结果
    if args.update_result:
        record_id, result, pnl, lesson = args.update_result
        update_trade_result(int(record_id), result, float(pnl), lesson)
        logger.info(f"记录 #{record_id} 已更新：{result} ${float(pnl):+.2f}")
        return

    # 初始化 Circle 钱包（黑客松加分）
    if CIRCLE_WALLET_ENABLED:
        wallet_info = circle_wallet.init_agent_wallet()
        if wallet_info.get("address"):
            logger.info(f"[Circle] Agent Wallet: {wallet_info['address']}")
    else:
        logger.info("[Circle] 未配置，使用 Polymarket 私钥模式")

    # 单次运行
    if args.once:
        run_scan()
        return

    # 持续监控模式
    logger.info(f"[Start] 启动持续监控，每 {MONITOR_INTERVAL_MINUTES} 分钟扫描一次")
    logger.info("按 Ctrl+C 停止")

    # 立刻运行一次
    run_scan()

    # 定时调度
    schedule.every(MONITOR_INTERVAL_MINUTES).minutes.do(run_scan)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            logger.info("收到停止信号，监控已停止")
            break
        except Exception as e:
            logger.error(f"运行出错: {e}")
            time.sleep(60)  # 出错等1分钟再重试


if __name__ == "__main__":
    main()
