"""
Circle Developer Wallets module — agent's USDC treasury managed via Circle API

Setup:
  1. Go to https://console.circle.com → create API Key (SANDBOX)
  2. Generate Entity Secret (32-byte hex) as instructed
  3. Register Entity Secret (one-time)
  4. Add to .env:
     CIRCLE_API_KEY=SANDOX_...
     ENTITY_SECRET=your_32_byte_entity_secret

This replaces the Polymarket private key approach: all agent funds go through Circle.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Circle testnet API
CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"

CIRCLE_API_KEY = os.getenv("CIRCLE_API_KEY", "")
ENTITY_SECRET = os.getenv("ENTITY_SECRET", "")

# Agent wallet address (runtime cache)
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
    """Return the agent's current Circle wallet address"""
    return _agent_wallet_address


def init_agent_wallet() -> dict:
    """
    Initialize agent wallet — create or reuse Circle Dev Wallet on Arc Testnet.
    Call once; subsequent calls return cached value.

    Returns: {"address": "0x...", "wallet_set_id": "...", "wallet_id": "..."}
    """
    global _agent_wallet_address, _wallet_set_id, _wallet_id

    if not is_configured():
        logger.info("Circle Wallet not configured (CIRCLE_API_KEY not set), using Polymarket private key mode")
        return {"address": None, "wallet_set_id": None, "wallet_id": None}

    if _agent_wallet_address:
        return {"address": _agent_wallet_address, "wallet_set_id": _wallet_set_id, "wallet_id": _wallet_id}

    logger.info("Initializing Circle Developer Wallet (Arc Testnet)...")

    try:
        ws_id = _find_or_create_wallet_set()
        _wallet_set_id = ws_id

        w_id, addr = _find_or_create_wallet(ws_id)
        _wallet_id = w_id
        _agent_wallet_address = addr

        logger.info(f"Agent wallet ready: {addr} (Circle Wallet ID: {w_id})")
        logger.info(f"ArcScan: https://testnet.arcscan.app/address/{addr}")

        return {"address": addr, "wallet_set_id": ws_id, "wallet_id": w_id}

    except Exception as e:
        logger.error(f"Circle wallet init failed: {e}")
        return {"address": None, "wallet_set_id": None, "wallet_id": None}


def _find_or_create_wallet_set() -> str:
    """Find existing wallet set or create a new one"""
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
                logger.info(f"Reusing existing Wallet Set: {ws['id']}")
                return ws["id"]

    # Create new
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
    logger.info(f"Created Wallet Set: {ws_id}")
    return ws_id


def _find_or_create_wallet(wallet_set_id: str) -> tuple[str, str]:
    """Find existing wallet or create new one (Arc Testnet, chain=5042002)"""
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
                logger.info(f"Reusing existing wallet: {addr} ({w['id']})")
                return w["id"], addr

    # Create new wallet
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
    logger.info(f"Created new wallet: {addr} ({w_id})")
    return w_id, addr


def _make_ciphertext() -> str:
    """Build entity secret ciphertext for Circle API"""
    from base64 import b64encode
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    # Fetch Circle's public key
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
    Query agent's Circle wallet USDC balance on Arc.
    Returns: {"balance_usdc": 123.45, "address": "0x..."}
    """
    addr = address or _agent_wallet_address
    if not addr:
        return {"balance_usdc": 0, "address": "", "error": "Wallet not initialized"}

    if not _wallet_id:
        return {"balance_usdc": 0, "address": addr, "error": "wallet_id unknown"}

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

        logger.info(f"Circle wallet balance: ${usdc:.2f} USDC ({addr})")
        return {"balance_usdc": usdc, "address": addr}

    except Exception as e:
        logger.warning(f"Failed to query Circle balance: {e}")
        return {"balance_usdc": 0, "address": addr, "error": str(e)}


def send_usdc(to_address: str, amount: float) -> dict:
    """
    Send USDC from the agent's Circle wallet.
    amount: human-readable USDC amount (e.g. 10.0 = $10 USDC)
    """
    if not is_configured() or not _wallet_id:
        return {"status": "FAILED", "reason": "Circle Wallet not initialized"}

    amount_raw = str(int(amount * 1e6))

    try:
        resp = requests.post(
            f"{CIRCLE_API_BASE}/developer/transactions/transfer",
            headers=_headers(with_idempotency=True),
            json={
                "idempotencyKey": str(uuid.uuid4()),
                "entitySecretCiphertext": _make_ciphertext(),
                "walletId": _wallet_id,
                "tokenId": "USDC-ARC",
                "destinationAddress": to_address,
                "amount": amount_raw,
                "feeLevel": "MEDIUM",
            },
            timeout=15,
        )
        resp.raise_for_status()
        tx_data = resp.json()["data"]
        tx_id = tx_data["id"]

        logger.info(f"Circle transfer submitted: {amount} USDC → {to_address} (tx: {tx_id})")
        return {"status": "SUBMITTED", "tx_id": tx_id, "amount_usdc": amount, "to": to_address}

    except Exception as e:
        logger.error(f"Circle transfer failed: {e}")
        return {"status": "FAILED", "reason": str(e)}


def wait_for_transaction(tx_id: str, max_wait: int = 60) -> dict:
    """Poll for transaction completion (up to max_wait seconds)"""
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
                logger.info(f"Transaction {tx_id} final state: {state}")
                return {"status": state, "tx_id": tx_id}

            time.sleep(2)
        except Exception as e:
            logger.warning(f"Poll transaction status failed: {e}")
            time.sleep(3)

    return {"status": "TIMEOUT", "tx_id": tx_id}


def generate_agent_report() -> str:
    """Generate a public-facing agent wallet report (X-ready)"""
    addr = _agent_wallet_address
    if not addr:
        return "[PMAI] Circle Wallet: not configured"

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
