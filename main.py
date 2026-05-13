"""
PMAI — Polymarket AI Prediction Market Monitor
Entry point & scheduler

Usage:
  python main.py --once               Single scan
  python main.py                       Continuous monitoring
  python main.py --update-result <id> <WIN/LOSS> <pnl>
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
# Logging
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
import io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logger = logging.getLogger(__name__)

# Global exception hook — log uncaught crashes
def _log_uncaught_exception(exc_type, exc_value, exc_tb):
    import traceback
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical(f"Uncaught exception, program crashed:\n{tb_text}")
sys.excepthook = _log_uncaught_exception


def _find_market_by_id(markets, market_id):
    for m in markets:
        if m["id"] == market_id:
            return m
    return None


def run_scan() -> None:
    """
    Full scan cycle:
    Fetch markets → Search news → AI analysis → Execute decisions → Notify
    """
    start_time = datetime.now()
    logger.info(f"\n{'='*60}")
    logger.info(f"[Scan] Start {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*60}")

    # Step 0: Auto-review settled markets
    reviewed = run_auto_review()
    if reviewed:
        logger.info(f"Auto-review: updated {reviewed} historical records")

    # Step 1: Fetch active markets (with retry)
    logger.info("Step 1/4: Fetching Polymarket markets...")
    markets = []
    for attempt in range(3):
        markets = get_active_markets()
        if markets:
            break
        logger.warning(f"Market fetch failed, attempt {attempt+1}/3 retrying...")
        time.sleep(5 * (attempt + 1))
    if not markets:
        logger.warning("Failed to fetch markets after 3 retries, skipping this round")
        return
    logger.info(f"Fetched {len(markets)} markets")

    # Dedup: skip markets analyzed in the last 6 hours
    recent_ids = get_recent_market_ids(cooldown_hours=6)
    if recent_ids:
        fresh_markets = [m for m in markets if m["question"][:80] not in recent_ids]
        skipped = len(markets) - len(fresh_markets)
        if skipped:
            logger.info(f"Skipping {skipped} recently analyzed markets (within 6h), analyzing {len(fresh_markets)}")
        markets = fresh_markets

    if not markets:
        logger.info("All markets recently analyzed, skipping this round")
        return

    # Step 2: Search news for each market
    logger.info("Step 2/4: Searching news...")
    news_map = {}
    for market in markets:
        news = search_news_for_event(market["question"])
        news_map[market["id"]] = news
        time.sleep(0.5)

    # Step 3: AI batch analysis
    logger.info("Step 3/4: AI analyzing...")
    analyses = batch_analyze_markets(markets, news_map)
    logger.info(f"Analysis complete: {len(analyses)} results")

    # Step 3.5: Secondary review — re-check rules for actionable markets
    logger.info("Step 3.5/4: Rule review...")
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
                    f"Review override [{verdict}]: '{analysis.get('title_cn', analysis['market_question'][:30])}' | "
                    f"{old_action} → {analysis['action']}"
                )
            else:
                logger.info(f"Review confirmed [CONFIRM]: '{analysis.get('title_cn', analysis['market_question'][:30])}'")
        time.sleep(1)

    # Step 4: Execute trade decisions + notify
    logger.info("Step 4/4: Executing decisions...")
    trade_results = []
    for analysis in analyses:
        result = execute_trade_decision(analysis)
        trade_results.append(result)
        time.sleep(1)

    # Generate report and notify
    report = generate_summary_report(analyses)
    stats = get_stats()
    report += f"\n\n[Stats] {stats['total']} trades | Win rate {stats['win_rate']*100:.0f}% | PnL ${stats['total_pnl']:+.2f}"

    # Circle wallet status
    if CIRCLE_WALLET_ENABLED:
        wallet_balance = circle_wallet.get_usdc_balance()
        addr = wallet_balance.get("address", "N/A")
        bal = wallet_balance.get("balance_usdc", 0)
        report += f"\n\n[Circle Wallet] {addr} | Balance: ${bal:.2f} USDC"
        report += f"\n[ArcScan] https://testnet.arcscan.app/address/{addr}"

    duration = (datetime.now() - start_time).seconds
    report += f"\n[Time] Scan completed in {duration}s"

    notify(report, urgent=any(r["status"] in ("ORDER_PLACED", "PAPER_TRADE") for r in trade_results))
    logger.info("[OK] Scan complete")


def main():
    parser = argparse.ArgumentParser(description="PMAI — Polymarket AI Prediction Market Monitor")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument(
        "--update-result",
        nargs=4,
        metavar=("RECORD_ID", "RESULT", "PNL", "LESSON"),
        help="Update trade result: --update-result <id> <WIN/LOSS/PUSH> <pnl_usd> <lesson>",
    )
    args = parser.parse_args()

    if args.update_result:
        record_id, result, pnl, lesson = args.update_result
        update_trade_result(int(record_id), result, float(pnl), lesson)
        logger.info(f"Record #{record_id} updated: {result} ${float(pnl):+.2f}")
        return

    # Init Circle wallet (hackathon bonus)
    if CIRCLE_WALLET_ENABLED:
        wallet_info = circle_wallet.init_agent_wallet()
        if wallet_info.get("address"):
            logger.info(f"[Circle] Agent Wallet: {wallet_info['address']}")
    else:
        logger.info("[Circle] Not configured, using Polymarket private key mode")

    if args.once:
        run_scan()
        return

    # Continuous monitoring mode
    logger.info(f"[Start] Continuous monitoring every {MONITOR_INTERVAL_MINUTES} min")
    logger.info("Press Ctrl+C to stop")

    run_scan()
    schedule.every(MONITOR_INTERVAL_MINUTES).minutes.do(run_scan)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            logger.info("Received stop signal, monitoring stopped")
            break
        except Exception as e:
            logger.error(f"Runtime error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
