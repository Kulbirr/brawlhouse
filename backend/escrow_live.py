"""Phase 5 live escrow: REAL SOL moves. Mock mode NEVER imports this module.

NOTE (superseded for placement): bet/hire placement now uses the
non-custodial user-signed flow in backend/payments_live.py (the server
builds unsigned transfers; the user's wallet signs). This module's
verify_and_claim_deposit is no longer called. send_payouts is still used
by settlement for live payouts when escrow keys are configured; without
them, payouts stay manual (paid_tx NULL).

This file is only imported when the `betting_live` setting is true, so the
`solana`/`solders` dependencies below are only needed for live mode.

Enabling live mode is the project owner's explicit decision and requires,
in the gitignored `.env` file:

    ARENA_BETTING_LIVE=1            # or betting_live=true via admin settings
    ESCROW_WALLET=<base58 pubkey>   # backend custodial wallet receiving bets
    ESCROW_PRIVATE_KEY=<base58>      # secret key of the escrow wallet (FUNDED)
    HELIUS_API_KEY=<key>            # Helius RPC (reuses the RugRadar key)

Design (v1 custodial escrow):

* Bet placement: the bettor sends `amount_sol` SOL to ESCROW_WALLET first,
  then POSTs the bet. `verify_and_claim_deposit` scans recent signatures on
  the escrow address via Helius RPC and accepts the bet only when it finds
  an UNCLAIMED transaction with sender == wallet, destination == escrow,
  lamports == amount_sol (within tolerance), inside a recent slot window.
  The signature is claimed in `escrow_deposits` so one tx can't fund two
  bets. payment_status becomes "confirmed".
* Settlement: `send_payouts` transfers each confirmed winner's payout
  (aggregated per wallet) from the escrow wallet. The tx signature is
  stored in `bet_payouts.paid_tx`.

Security notes for the owner before flipping live:
- ESCROW_PRIVATE_KEY lives only in the gitignored .env, never in code/logs.
- Keep only a small hot balance in the escrow wallet; sweep the rest to a
  cold wallet regularly. The house cut accumulates here until the phase 6
  buyback bot moves it.
- `verify_and_claim_deposit` trusts Helius RPC responses; run it against
  your own RPC endpoint if that trust assumption is unacceptable.
"""

from __future__ import annotations

import os

# How far back a deposit may be and still fund a bet (~6 min of slots).
RECENT_SLOT_WINDOW = 900
# Lamport tolerance when matching a deposit to a bet amount.
LAMPORT_TOLERANCE = 1_000
# How many recent escrow signatures to scan per bet.
SIGNATURE_SCAN_LIMIT = 200


class EscrowNotConfigured(RuntimeError):
    pass


class DepositNotFound(RuntimeError):
    pass


def _config() -> tuple[str, str, str]:
    wallet = os.environ.get("ESCROW_WALLET", "").strip()
    secret = os.environ.get("ESCROW_PRIVATE_KEY", "").strip()
    helius = os.environ.get("HELIUS_API_KEY", "").strip()
    missing = [name for name, val in
               (("ESCROW_WALLET", wallet),
                ("ESCROW_PRIVATE_KEY", secret),
                ("HELIUS_API_KEY", helius)) if not val]
    if missing:
        raise EscrowNotConfigured(
            "Live betting is enabled but these are missing from .env: "
            + ", ".join(missing))
    return wallet, secret, helius


def _rpc():
    """Helius RPC client. Imported lazily so mock mode never needs solana."""
    from solana.rpc.api import Client

    _, _, helius = _config()
    return Client(f"https://mainnet.helius-rpc.com/?api-key={helius}")


def _transfer_matches(tx, wallet: str, escrow: str,
                      expected_lamports: int) -> bool:
    """True if the parsed transaction contains a System transfer
    wallet -> escrow of expected_lamports (± tolerance)."""
    candidates: list[dict] = []
    try:
        msg = tx.transaction.message
        candidates.extend(getattr(msg, "instructions", []) or [])
        meta = getattr(tx, "meta", None)
        for inner in getattr(meta, "inner_instructions", None) or []:
            candidates.extend(getattr(inner, "instructions", []) or [])
    except Exception:
        return False
    for ix in candidates:
        parsed = getattr(ix, "parsed", None)
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") != "transfer":
            continue
        info = parsed.get("info", {}) or {}
        if info.get("source") != wallet or info.get("destination") != escrow:
            continue
        try:
            lamports = int(info.get("lamports", -1))
        except (TypeError, ValueError):
            continue
        if abs(lamports - expected_lamports) <= LAMPORT_TOLERANCE:
            return True
    return False


def verify_and_claim_deposit(db, battle_id: str, wallet: str,
                             amount_sol: float) -> str:
    """Find a recent unclaimed escrow deposit matching (wallet, amount_sol)
    and claim it for this bet. Returns the tx signature.

    Raises EscrowNotConfigured, DepositNotFound, or ValueError (bad wallet).

    The config check runs BEFORE any solana/solders import, so a
    misconfigured live mode fails clean without touching those deps.
    """
    escrow, _, _ = _config()
    from solders.pubkey import Pubkey

    try:
        escrow_pk = Pubkey.from_string(escrow)
        Pubkey.from_string(wallet)  # validates the bettor's address
    except Exception as exc:
        raise ValueError(f"Invalid wallet address: {exc}")

    client = _rpc()
    expected_lamports = int(round(amount_sol * 1_000_000_000))
    current_slot = client.get_slot().value
    sigs = client.get_signatures_for_address(
        escrow_pk, limit=SIGNATURE_SCAN_LIMIT).value

    for sig_info in sigs:
        if current_slot - sig_info.slot > RECENT_SLOT_WINDOW:
            continue
        sig = str(sig_info.signature)
        if db.deposit_claimed(sig):
            continue
        try:
            tx = client.get_transaction(
                sig_info.signature, encoding="jsonParsed",
                max_supported_transaction_version=0).value
        except Exception:
            continue
        if tx is None:
            continue
        if not _transfer_matches(tx, wallet, escrow, expected_lamports):
            continue
        if db.claim_deposit(sig, battle_id, wallet, amount_sol):
            return sig
    raise DepositNotFound(
        f"No unclaimed deposit of {amount_sol} SOL from {wallet} to the "
        f"escrow wallet in the last ~{RECENT_SLOT_WINDOW} slots. Send the "
        f"deposit first, then place the bet.")


def send_payouts(payouts: dict[str, float]) -> dict[str, str]:
    """Transfer SOL from the escrow wallet to each wallet.

    `payouts`: {wallet: amount_sol}. Returns {wallet: tx_signature}.
    Raises EscrowNotConfigured on missing config; RPC errors propagate to
    the caller (betting.settle_battle records the ledger first, so a
    failed send leaves paid_tx NULL rather than losing the record).
    """
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    escrow, secret, _ = _config()
    client = _rpc()
    keypair = Keypair.from_base58_string(secret)
    if str(keypair.pubkey()) != escrow:
        raise EscrowNotConfigured(
            "ESCROW_PRIVATE_KEY does not match ESCROW_WALLET")

    out: dict[str, str] = {}
    for wallet, amount_sol in payouts.items():
        lamports = int(round(amount_sol * 1_000_000_000))
        if lamports <= 0:
            continue
        ix = transfer(TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=Pubkey.from_string(wallet),
            lamports=lamports,
        ))
        blockhash = client.get_latest_blockhash().value.blockhash
        tx = Transaction.new_signed_with_payer(
            [ix], keypair.pubkey(), [keypair], blockhash)
        sig = client.send_transaction(tx).value
        out[wallet] = str(sig)
    return out
