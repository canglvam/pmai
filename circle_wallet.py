"""
Circle 开发者钱包模块 — 代理的 USDC 金库通过 Circle API 管理

集成方式：
  1. 去 https://console.circle.com 注册 → 创建 API Key（SANDBOX）
  2. 按指引生成 Entity Secret（32字节16进制）
  3. 注册 Entity Secret（只需做一次）
  4. 填入 .env:
     CIRCLE_API_KEY=SANDOX_...
     ENTITY_SECRET=你的32字节entity_secret

这会替换 Polymarket 私钥方案：代理的所有链上资金走 Circle 钱包。
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Circle 测试网 API
CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"

CIRCLE_API_KEY = os.getenv("CIRCLE_API_KEY", "")
ENTITY_SECRET = os.getenv("ENTITY_SECRET", "")

# 当前代理的钱包地址（运行时缓存）
_agent_wallet_address: str | None = None
_wallet_set_id: str | None = None
_wallet_id: str | None = None


def _headers(with_idempotency: bool = False) -> dict:
    h = {
        "Authorization": f"Bearer {CIRCLE_API_KEY}",
        "Content-Type": "application/json",
    }
    if with_idempotency:
        h["X-Idempotency-Key"] = str(uuid.uuid4())
    return h


def is_configured() -> bool:
    return bool(CIRCLE_API_KEY and ENTITY_SECRET)


def get_agent_wallet_address() -> str | None:
    """返回代理当前使用的 Circle 钱包地址"""
    return _agent_wallet_address


def init_agent_wallet() -> dict:
    """
    初始化代理的钱包 — 创建或复用 Circle Dev Wallet on Arc Testnet
    调用一次即可，后续调用返回缓存值

    返回: {"address": "0x...", "wallet_set_id": "...", "wallet_id": "..."}
    """
    global _agent_wallet_address, _wallet_set_id, _wallet_id

    if not is_configured():
        logger.info("Circle Wallet 未配置（CIRCLE_API_KEY 未设置），使用 Polymarket 私钥方案")
        return {"address": None, "wallet_set_id": None, "wallet_id": None}

    if _agent_wallet_address:
        return {"address": _agent_wallet_address, "wallet_set_id": _wallet_set_id, "wallet_id": _wallet_id}

    logger.info("初始化 Circle 开发者钱包（Arc Testnet）...")

    try:
        ws_id = _find_or_create_wallet_set()
        _wallet_set_id = ws_id

        w_id, addr = _find_or_create_wallet(ws_id)
        _wallet_id = w_id
        _agent_wallet_address = addr

        logger.info(f"代理钱包就绪: {addr} (Circle Wallet ID: {w_id})")
        logger.info(f"Arc 浏览器: https://testnet.arcscan.app/address/{addr}")

        return {"address": addr, "wallet_set_id": ws_id, "wallet_id": w_id}

    except Exception as e:
        logger.error(f"Circle 钱包初始化失败: {e}")
        return {"address": None, "wallet_set_id": None, "wallet_id": None}


def _find_or_create_wallet_set() -> str:
    """查找已有 wallet set 或创建新的"""
    resp = requests.get(
        f"{CIRCLE_API_BASE}/developer/walletSets",
        headers=_headers(),
        timeout=15,
    )
    if resp.ok:
        data = resp.json()
        existing = data.get("data", {}).get("walletSets", [])
        for ws in existing:
            if ws.get("name") == "pmai-agent-arc":
                logger.info(f"复用已有 Wallet Set: {ws['id']}")
                return ws["id"]

    # 创建新的
    resp = requests.post(
        f"{CIRCLE_API_BASE}/developer/walletSets",
        headers=_headers(with_idempotency=True),
        json={
            "name": "pmai-agent-arc",
            "entitySecretCiphertext": _make_ciphertext(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    ws_id = resp.json()["data"]["walletSet"]["id"]
    logger.info(f"创建 Wallet Set: {ws_id}")
    return ws_id


def _find_or_create_wallet(wallet_set_id: str) -> tuple[str, str]:
    """查找已有钱包或创建新的（Arc Testnet, chain=5042002）"""
    resp = requests.get(
        f"{CIRCLE_API_BASE}/developer/wallets",
        params={"walletSetId": wallet_set_id},
        headers=_headers(),
        timeout=15,
    )
    if resp.ok:
        wallets = resp.json().get("data", {}).get("wallets", [])
        for w in wallets:
            if w.get("blockchain") == "ARC" and w.get("state") == "COMPLETE":
                addr = w.get("address")
                logger.info(f"复用已有钱包: {addr} ({w['id']})")
                return w["id"], addr

    # 创建新钱包
    resp = requests.post(
        f"{CIRCLE_API_BASE}/developer/wallets",
        headers=_headers(with_idempotency=True),
        json={
            "idempotencyKey": str(uuid.uuid4()),
            "entitySecretCiphertext": _make_ciphertext(),
            "blockchain": "ARC",
            "count": 1,
            "walletSetId": wallet_set_id,
            "accountType": "SCA",
        },
        timeout=15,
    )
    resp.raise_for_status()
    wallet_info = resp.json()["data"]["wallets"][0]
    w_id = wallet_info["id"]
    addr = wallet_info["address"]
    logger.info(f"创建新钱包: {addr} ({w_id})")
    return w_id, addr


def _make_ciphertext() -> str:
    """构造 entity secret ciphertext"""
    from base64 import b64encode
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    # 获取 Circle 的公钥
    resp = requests.get(
        f"{CIRCLE_API_BASE}/config/entity/publicKey",
        timeout=10,
    )
    resp.raise_for_status()
    pubkey_pem = resp.json()["data"]["publicKey"]

    pubkey = serialization.load_pem_public_key(
        pubkey_pem.encode(), backend=default_backend()
    )

    ciphertext = pubkey.encrypt(
        bytes.fromhex(ENTITY_SECRET),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    return b64encode(ciphertext).decode()


def get_usdc_balance(address: str | None = None) -> dict:
    """
    查询代理 Circle 钱包的 USDC 余额（Arc 链上）
    返回: {"balance_usdc": 123.45, "address": "0x..."}
    """
    addr = address or _agent_wallet_address
    if not addr:
        return {"balance_usdc": 0, "address": "", "error": "钱包未初始化"}

    if not _wallet_id:
        return {"balance_usdc": 0, "address": addr, "error": "wallet_id 未知"}

    try:
        resp = requests.get(
            f"{CIRCLE_API_BASE}/wallets/{_wallet_id}/balances",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        tokens = resp.json().get("data", {}).get("tokenBalances", [])

        usdc = 0.0
        for t in tokens:
            if t.get("token", {}).get("symbol") == "USDC":
                raw = int(t.get("amount", "0"))
                usdc = raw / 1e6  # USDC 6 decimals
                break

        logger.info(f"Circle 钱包余额: ${usdc:.2f} USDC ({addr})")
        return {"balance_usdc": usdc, "address": addr}

    except Exception as e:
        logger.warning(f"查询 Circle 余额失败: {e}")
        return {"balance_usdc": 0, "address": addr, "error": str(e)}


def send_usdc(to_address: str, amount: float) -> dict:
    """
    从代理的 Circle 钱包发送 USDC
    amount: USDC 数量（人类可读，如 10.0 = $10 USDC）
    """
    if not is_configured() or not _wallet_id:
        return {"status": "FAILED", "reason": "Circle Wallet 未初始化"}

    amount_raw = str(int(amount * 1e6))  # 转 6 小数位

    try:
        resp = requests.post(
            f"{CIRCLE_API_BASE}/developer/transactions/transfer",
            headers=_headers(with_idempotency=True),
            json={
                "idempotencyKey": str(uuid.uuid4()),
                "entitySecretCiphertext": _make_ciphertext(),
                "walletId": _wallet_id,
                "tokenId": "USDC-ARC",  # Arc 上的 USDC
                "destinationAddress": to_address,
                "amount": amount_raw,
                "feeLevel": "MEDIUM",
            },
            timeout=15,
        )
        resp.raise_for_status()
        tx_data = resp.json()["data"]
        tx_id = tx_data["id"]

        logger.info(f"Circle 转账已提交: {amount} USDC → {to_address} (tx: {tx_id})")
        return {"status": "SUBMITTED", "tx_id": tx_id, "amount_usdc": amount, "to": to_address}

    except Exception as e:
        logger.error(f"Circle 转账失败: {e}")
        return {"status": "FAILED", "reason": str(e)}


def wait_for_transaction(tx_id: str, max_wait: int = 60) -> dict:
    """轮询等待交易完成（最多 max_wait 秒）"""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = requests.get(
                f"{CIRCLE_API_BASE}/transactions/{tx_id}",
                headers=_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            state = resp.json()["data"]["transaction"]["state"]

            if state in ("COMPLETE", "FAILED", "DENIED", "CANCELLED"):
                logger.info(f"交易 {tx_id} 终态: {state}")
                return {"status": state, "tx_id": tx_id}

            time.sleep(2)
        except Exception as e:
            logger.warning(f"轮询交易状态失败: {e}")
            time.sleep(3)

    return {"status": "TIMEOUT", "tx_id": tx_id}


def generate_agent_report() -> str:
    """生成本代理钱包的可公开报告（可发 X）"""
    addr = _agent_wallet_address
    if not addr:
        return "[PMAI] Circle Wallet: 未配置"

    bal = get_usdc_balance()

    lines = [
        "[PMAI Agent Wallet]",
        f"Address: {addr}",
        f"Balance: ${bal['balance_usdc']:.2f} USDC",
        f"ArcScan: https://testnet.arcscan.app/address/{addr}",
    ]

    if bal.get("error"):
        lines.append(f"Note: {bal['error']}")

    return "\n".join(lines)
