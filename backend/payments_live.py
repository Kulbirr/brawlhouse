"""Live (real-SOL) payments: user-signed transfers to the treasury wallet.

Mock mode NEVER imports this module: every solana/solders import below is
function-local, so the backend runs without chain libraries or network
until the owner flips a live flag.

Non-custodial model: the server holds NO private keys and signs nothing:

  1. initiate: the server builds an UNSIGNED SystemProgram.transfer
     transaction (user wallet -> treasury_wallet, exact lamports) with a
     fresh blockhash and returns it base64-encoded. The user's wallet
     (Phantom/Solflare via the frontend) signs and submits it.
  2. confirm: the server fetches the submitted transaction from Helius RPC
     and verifies it before marking the hire/bet paid:
       - the transaction exists and succeeded (meta.err is None)
       - it is recent (within SLOT_WINDOW slots of the current slot), so
         an old unrelated transfer can't be recycled as payment
       - it contains a System transfer from the user's wallet to the
         treasury wallet of >= the expected lamports (a succeeded transfer
         from the wallet implies the wallet signed it)
       - the signature was never used before (tx_claims replay protection:
         one signature confirms exactly one hire/bet)

Config (nothing is signed server-side, so no secret keys exist here):
  treasury_wallet setting (admin-editable, public-readable) and the
  HELIUS_API_KEY env var (reuses the RugRadar key) for RPC verification.
"""

from __future__ import annotations

import base64
import os
from typing import Any

# How far back a payment transaction may be and still count (~6 min).
SLOT_WINDOW = 900

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class PaymentNotConfigured(RuntimeError):
    """Live flag is on but treasury_wallet / HELIUS_API_KEY is missing."""


class RpcError(RuntimeError):
    """Helius RPC call failed."""


class ReplayDetected(RuntimeError):
    """Signature was already used to confirm a hire/bet."""


def lamports(sol: float) -> int:
    return int(round(float(sol) * 1_000_000_000))


def is_valid_solana_pubkey(s: Any) -> bool:
    """Pure-python base58 pubkey check (no solders import needed)."""
    if not isinstance(s, str) or not s:
        return False
    if any(c not in _B58_ALPHABET for c in s):
        return False
    return _b58_decoded_len(s) == 32


def _b58_decoded_len(s: str) -> int:
    """Length in bytes of the base58-decoded string, honoring that each
    leading '1' encodes a zero byte."""
    n = 0
    for c in s:
        n = n * 58 + _B58_ALPHABET.index(c)
    leading = len(s) - len(s.lstrip("1"))
    if n == 0:
        return leading
    return leading + (n.bit_length() + 7) // 8


def _config(settings) -> tuple[str, str]:
    """Returns (treasury_wallet, helius_api_key) or raises."""
    treasury = (settings.get("treasury_wallet") or "").strip()
    if not is_valid_solana_pubkey(treasury):
        raise PaymentNotConfigured(
            "Live payments need a valid treasury_wallet setting "
            "(admin panel).")
    helius = os.environ.get("HELIUS_API_KEY", "").strip()
    if not helius:
        raise PaymentNotConfigured(
            "Live payments need HELIUS_API_KEY in .env for verification.")
    return treasury, helius


def _rpc_client(api_key: str):
    """Helius RPC client. Imported lazily; mock mode never reaches here."""
    from solana.rpc.api import Client

    return Client(f"https://mainnet.helius-rpc.com/?api-key={api_key}")


def build_unsigned_transfer(from_wallet: str, to_wallet: str,
                            amount_lamports: int,
                            client=None) -> dict[str, Any]:
    """Build an UNSIGNED SOL transfer. No keys are created or used.

    Returns {"unsigned_tx_base64", "blockhash", "amount_lamports",
    "from", "to"}. Raises ValueError on bad input, RpcError on RPC failure.
    """
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    try:
        from_pk = Pubkey.from_string(from_wallet)
        to_pk = Pubkey.from_string(to_wallet)
    except Exception as exc:
        raise ValueError(f"Invalid wallet address: {exc}")
    amount_lamports = int(amount_lamports)
    if amount_lamports <= 0:
        raise ValueError("amount_lamports must be positive")

    if client is None:
        raise PaymentNotConfigured(
            "build_unsigned_transfer requires an RPC client")
    try:
        resp = client.get_latest_blockhash()
        blockhash = resp.value.blockhash
        blockhash = Hash.from_string(str(blockhash))
    except Exception as exc:
        raise RpcError(f"Could not fetch blockhash: {exc}")

    ix = transfer(TransferParams(from_pubkey=from_pk,
                                 to_pubkey=to_pk,
                                 lamports=amount_lamports))
    msg = Message.new_with_blockhash([ix], from_pk, blockhash)
    tx = Transaction.new_unsigned(msg)
    return {
        "unsigned_tx_base64": base64.b64encode(bytes(tx)).decode("ascii"),
        "blockhash": str(blockhash),
        "amount_lamports": amount_lamports,
        "from": str(from_pk),
        "to": str(to_pk),
    }


def _find_transfer(tx, wallet: str, treasury: str,
                   expected_lamports: int) -> int | None:
    """Return matched lamports if tx holds a System transfer
    wallet -> treasury of >= expected_lamports, else None."""
    candidates: list = []
    try:
        msg = tx.transaction.message
        candidates.extend(getattr(msg, "instructions", []) or [])
        meta = getattr(tx, "meta", None)
        for inner in getattr(meta, "inner_instructions", None) or []:
            candidates.extend(getattr(inner, "instructions", []) or [])
    except Exception:
        return None
    for ix in candidates:
        parsed = getattr(ix, "parsed", None)
        if not isinstance(parsed, dict) or parsed.get("type") != "transfer":
            continue
        info = parsed.get("info", {}) or {}
        if info.get("source") != wallet or info.get("destination") != treasury:
            continue
        try:
            moved = int(info.get("lamports", -1))
        except (TypeError, ValueError):
            continue
        if moved >= expected_lamports:
            return moved
    return None


def verify_transfer(db, settings, *, kind: str, ref_id: int, battle_id: str,
                    wallet: str, expected_lamports: int, signature: str,
                    client=None) -> dict[str, Any]:
    """Verify a user-submitted payment transaction and claim it.

    Raises ValueError (client-fixable: bad sig, tx not found/failed/stale/
    no matching transfer), ReplayDetected (signature already used),
    PaymentNotConfigured / RpcError (server side).
    """
    from solders.signature import Signature

    try:
        sig = Signature.from_string(signature)
    except Exception:
        raise ValueError("Invalid transaction signature")
    sig_str = str(sig)

    if db.tx_claimed(sig_str):
        raise ReplayDetected("This transaction was already used")

    treasury, api_key = _config(settings)
    if client is None:
        client = _rpc_client(api_key)
    try:
        tx = client.get_transaction(
            sig, encoding="jsonParsed",
            max_supported_transaction_version=0).value
    except Exception as exc:
        raise RpcError(f"Could not fetch transaction: {exc}")
    if tx is None:
        raise ValueError("Transaction not found onchain (yet)")
    if getattr(getattr(tx, "meta", None), "err", None) is not None:
        raise ValueError("Transaction failed onchain")

    try:
        current_slot = client.get_slot().value
    except Exception as exc:
        raise RpcError(f"Could not fetch slot: {exc}")
    tx_slot = getattr(tx, "slot", None)
    if tx_slot is None or current_slot - tx_slot > SLOT_WINDOW:
        raise ValueError(
            "Transaction is too old; sign a fresh payment")

    matched = _find_transfer(tx, wallet, treasury, expected_lamports)
    if matched is None:
        raise ValueError(
            f"No transfer of >= {expected_lamports} lamports from {wallet} "
            f"to {treasury} in this transaction")

    if not db.claim_tx(sig_str, kind, ref_id, battle_id, wallet, matched):
        raise ReplayDetected("This transaction was already used")
    return {"signature": sig_str, "slot": tx_slot, "lamports": matched}


# ------------------------------------------------------------------ hires
def initiate_hire(db, settings, battle_id: str, engine_id: str,
                  wallet: str, fee_sol: float,
                  client=None) -> dict[str, Any] | None:
    """Live hire: build the unsigned payment tx.

    Returns None when (battle_id, wallet) already hired (caller -> 409).
    A still-pending hire is returned with a FRESH unsigned tx so a user
    whose blockhash expired can simply retry.
    Raises PaymentNotConfigured / RpcError / ValueError.
    """
    treasury, api_key = _config(settings)
    if not is_valid_solana_pubkey(wallet):
        raise ValueError("Invalid wallet address")
    expected = lamports(fee_sol)
    if expected <= 0:
        raise PaymentNotConfigured(
            "hire_fee_sol must be positive when hiring_live is on")

    existing = db.get_hire_by_battle_wallet(battle_id, wallet)
    if existing is not None:
        if existing["payment_status"] != "pending":
            return None  # already hired (mock/confirmed) -> 409
        hire = existing
    else:
        if client is None:
            client = _rpc_client(api_key)
        built = build_unsigned_transfer(wallet, treasury, expected,
                                        client=client)
        hire = db.create_hire(battle_id, engine_id, wallet, fee_sol,
                              payment_status="pending")
        if hire is None:
            return None  # raced with another initiate -> 409
        hire = {**hire, **built}
        return hire

    # Pending hire: refresh the unsigned tx (new blockhash).
    if client is None:
        client = _rpc_client(api_key)
    built = build_unsigned_transfer(wallet, treasury, expected, client=client)
    return {**hire, **built}


def confirm_hire_payment(db, settings, hire: dict[str, Any],
                         signature: str, client=None) -> dict[str, Any]:
    """Verify the hire payment onchain and mark the hire confirmed."""
    _config(settings)  # validates treasury + helius key presence
    verify_transfer(db, settings, kind="hire", ref_id=hire["id"],
                    battle_id=hire["battle_id"], wallet=hire["wallet"],
                    expected_lamports=lamports(hire["fee_sol"]),
                    signature=signature, client=client)
    row = db.confirm_hire(hire["id"])
    if row is None:
        raise ValueError("Hire is no longer pending")
    return row


# ------------------------------------------------------------------- bets
def initiate_bet(db, settings, battle_id: str, fighter_id: str,
                 wallet: str, amount_sol: float,
                 client=None) -> dict[str, Any]:
    """Live bet: create the pending bet and build the unsigned payment tx."""
    treasury, api_key = _config(settings)
    if not is_valid_solana_pubkey(wallet):
        raise ValueError("Invalid wallet address")
    expected = lamports(amount_sol)
    if expected <= 0:
        raise ValueError("amount_sol must be positive")
    if client is None:
        client = _rpc_client(api_key)
    built = build_unsigned_transfer(wallet, treasury, expected, client=client)
    bet = db.create_bet(battle_id, fighter_id, wallet, amount_sol,
                        payment_status="pending")
    return {**bet, **built}


def confirm_bet_payment(db, settings, bet: dict[str, Any],
                        signature: str, client=None) -> dict[str, Any]:
    """Verify the bet payment onchain and mark the bet confirmed (counted)."""
    _config(settings)
    verify_transfer(db, settings, kind="bet", ref_id=bet["id"],
                    battle_id=bet["battle_id"], wallet=bet["wallet"],
                    expected_lamports=lamports(bet["amount_sol"]),
                    signature=signature, client=client)
    row = db.confirm_bet(bet["id"])
    if row is None:
        raise ValueError("Bet is no longer pending")
    return row


# ------------------------------------------------------------------ born
def initiate_born(db, settings, wallet: str, name: str, archetype: str,
                  color: str, fee_sol: float, client=None) -> dict[str, Any]:
    """Live born: create the pending birth intent and build the unsigned
    payment tx. The FT itself is created by confirm_born_payment."""
    from arena import validate_born_input

    treasury, api_key = _config(settings)
    if not is_valid_solana_pubkey(wallet):
        raise ValueError("Invalid wallet address")
    clean_name, arch, col = validate_born_input(name, archetype, color)
    expected = lamports(fee_sol)
    if expected <= 0:
        raise ValueError("born_fee_sol must be positive when born_live is on")
    if client is None:
        client = _rpc_client(api_key)
    built = build_unsigned_transfer(wallet, treasury, expected, client=client)
    intent = db.create_birth_intent(wallet, clean_name, arch, col, fee_sol,
                                    payment_status="pending")
    return {**intent, **built}


def confirm_born_payment(db, settings, intent: dict[str, Any],
                         signature: str, client=None) -> dict[str, Any]:
    """Verify the born payment onchain. Returns the intent; the caller
    creates the FT via ft_economy.finalize_birth_intent."""
    _config(settings)
    verify_transfer(db, settings, kind="born", ref_id=intent["id"],
                    battle_id="", wallet=intent["wallet"],
                    expected_lamports=lamports(intent["fee_sol"]),
                    signature=signature, client=client)
    return intent


# ----------------------------------------------------------------- entry
def initiate_entry(db, settings, fighter_id: str, wallet: str,
                   fee_sol: float, prize_share_sol: float,
                   client=None) -> dict[str, Any]:
    """Live entry: create the pending queue entry and build the unsigned
    payment tx. The entry becomes drawable on confirm_entry_payment."""
    treasury, api_key = _config(settings)
    if not is_valid_solana_pubkey(wallet):
        raise ValueError("Invalid wallet address")
    expected = lamports(fee_sol)
    if expected <= 0:
        raise ValueError("entry_fee_sol must be positive when entry_live is on")
    if client is None:
        client = _rpc_client(api_key)
    built = build_unsigned_transfer(wallet, treasury, expected, client=client)
    entry = db.enqueue_fighter(fighter_id, wallet, fee_sol, prize_share_sol,
                               payment_status="pending")
    return {**entry, **built}


def confirm_entry_payment(db, settings, entry: dict[str, Any],
                          signature: str, client=None) -> dict[str, Any]:
    """Verify the entry payment onchain and mark the entry confirmed."""
    _config(settings)
    verify_transfer(db, settings, kind="entry", ref_id=entry["id"],
                    battle_id="", wallet=entry["owner_wallet"],
                    expected_lamports=lamports(entry["entry_fee_sol"]),
                    signature=signature, client=client)
    row = db.confirm_queue_entry(entry["id"])
    if row is None:
        raise ValueError("Entry is no longer pending")
    return row
