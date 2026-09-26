"""Phase 1 (FT economy): prize payouts to owner wallets.

MOCK BY DEFAULT (hard project rule): unless the in-house `payouts_live`
setting is true, this module records payouts with status='mock' and makes
ZERO network calls. The caller (battle settlement / season rollover)
decides the status; this module only moves SOL in live mode.

Live mode is the owner's explicit opt-in and fails LOUDLY (never silently
mocks) when misconfigured: it needs TREASURY_PRIVATE_KEY + HELIUS_API_KEY
in .env and a valid treasury_wallet setting. The private key signs a
plain SOL transfer from the treasury hot wallet to the winner's wallet.
"""

from __future__ import annotations

import base64
import os
import urllib.request
import json as _json


class PayoutError(RuntimeError):
    """Live payout misconfigured or failed."""


def _live_config(settings) -> dict:
    """Validate live payout config BEFORE any solders import."""
    treasury = str(settings.get("treasury_wallet") or "").strip()
    secret = os.environ.get("TREASURY_PRIVATE_KEY", "").strip()
    helius = os.environ.get("HELIUS_API_KEY", "").strip()
    missing = [name for name, val in (
        ("treasury_wallet setting", treasury),
        ("TREASURY_PRIVATE_KEY", secret),
        ("HELIUS_API_KEY", helius),
    ) if not val]
    if missing:
        raise PayoutError(
            "payouts_live is ON but these are missing: " + ", ".join(missing)
            + ". Refusing to run: live mode must fail loudly, never silently mock.")
    return {"treasury": treasury, "secret": secret,
            "rpc_url": f"https://mainnet.helius-rpc.com/?api-key={helius}"}


def _rpc_call(rpc_url: str, method: str, params: list,
              timeout: int = 60) -> dict:
    payload = _json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read().decode())
    except Exception as exc:
        raise PayoutError(f"RPC call {method} failed: {exc}")
    if body.get("error"):
        raise PayoutError(f"RPC call {method} error: {body['error']}")
    return body["result"]


def pay_sol(settings, to_wallet: str, amount_sol: float) -> str | None:
    """Send SOL from the treasury hot wallet to `to_wallet`.

    Returns the tx signature in live mode, None in mock mode (caller
    records status='mock'). Raises PayoutError in live mode on any
    misconfiguration or RPC failure.
    """
    if not bool(settings.get("payouts_live")):
        return None
    cfg = _live_config(settings)  # raises loudly before solders import
    lamports = int(round(float(amount_sol) * 1_000_000_000))
    if lamports <= 0:
        raise PayoutError(f"payout amount must be positive, got {amount_sol}")

    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    keypair = Keypair.from_base58_string(cfg["secret"])
    if str(keypair.pubkey()) != cfg["treasury"]:
        raise PayoutError("TREASURY_PRIVATE_KEY does not match treasury_wallet")
    ix = transfer(TransferParams(
        from_pubkey=keypair.pubkey(),
        to_pubkey=Pubkey.from_string(to_wallet),
        lamports=lamports))
    blockhash = _rpc_call(cfg["rpc_url"], "getLatestBlockhash",
                          [{"commitment": "confirmed"}])["value"]["blockhash"]
    tx = Transaction.new_signed_with_payer(
        [ix], keypair.pubkey(), [keypair], blockhash)
    b64 = base64.b64encode(bytes(tx)).decode()
    sig = _rpc_call(cfg["rpc_url"], "sendTransaction",
                    [b64, {"encoding": "base64"}])
    return str(sig)
