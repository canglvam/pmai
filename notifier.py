"""
通知模块 - 把分析结果发送到你的手机/邮箱
支持：Telegram Bot（推荐）、邮件
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")  # Gmail App 密码


def notify(message: str, urgent: bool = False) -> None:
    """发送通知，有配置用 Telegram，否则只打印到日志"""
    logger.info(f"\n{'='*60}\n{message}\n{'='*60}")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        _send_telegram(message, urgent)
    elif EMAIL_FROM and EMAIL_TO:
        _send_email("Polymarket 监控通知", message)
    else:
        logger.warning("未配置通知渠道（Telegram/Email），结果仅输出到日志")


def _send_telegram(message: str, urgent: bool = False) -> None:
    """发送 Telegram 消息"""
    prefix = "[!!!] " if urgent else "[==] "
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": prefix + message,
                "parse_mode": "HTML",
            },
            timeout=10
        )
        if resp.ok:
            logger.info("Telegram 通知已发送")
        else:
            logger.error(f"Telegram 发送失败: {resp.text}")
    except Exception as e:
        logger.error(f"Telegram 通知异常: {e}")


def _send_email(subject: str, body: str) -> None:
    """发送邮件通知（Gmail）"""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        logger.info("邮件通知已发送")
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
