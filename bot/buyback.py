"""Phase 6 buyback bot: spends allocated fee revenue on the project token
and BURNS it.

Usage:
    cd ~/workspace/agent-arena
    python -m bot.buyback --once        # single buyback cycle
    python -m bot.buyback --loop        # repeat every buyback_interval_minutes

MOCK BY DEFAULT (hard project rule): unless the in-house `buyback_live`
setting is true, the bot makes ZERO network calls. The swap is simulated at
a fixed mock rate from config (`buyback_mock_rate` tokens per SOL) and the
burn event is recorded with dry_run=true and fake tx ids prefixed 'mock_'.

Live mode is the owner's explicit opt-in and fails LOUDLY (never silently
mocks) when misconfigured. See the README "Phase 6" section for the env
vars and the hot/cold wallet discipline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import Database  # noqa: E402
from backend.settings_store import SettingsStore  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Native SOL mint on Solana mainnet.
SOL_MINT = "So11111111111111111111111111111111111111112"

# Jupiter swap endpoints (free keyless tier). The old quote-api.jup.ag/v6/*
# host is retired; lite-api.jup.ag/swap/v1 is the current free tier and
# api.jup.ag/swap/v1 is the paid-tier mirror if the free tier is exhausted.
JUPITER_ENDPOINTS = (
    "https://lite-api.jup.ag/swap/v1",
    "https://api.jup.ag/swap/v1",
)
JUPITER_SLIPPAGE_BPS = 100  # 1%

# SOL kept untouched in the hot wallet for tx fees / rent (live mode).
HOT_TX_RESERVE_SOL = 0.02


class BuybackError(RuntimeError):
    """Anything that stops a buyback round (misconfig, RPC failure, ...)."""


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] buyback: {msg}", flush=True)


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines, no overrides."""
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


# ------------------------------------------------------------------ cycle
def run_cycle(settings: SettingsStore, db: Database) -> dict:
    """Execute one buyback cycle. Returns a status dict.

    Statuses: 'paused' (buyback_enabled=false, fees untouched), 'no_fees'
    (nothing new since the last round), 'burned' (round executed).

    Mock mode (default) performs zero network calls and never imports
    solana/solders. Live mode raises BuybackError loudly on any
    misconfiguration instead of silently mocking.
    """
    if not settings.get("buyback_enabled"):
        _log("buyback_enabled=false: paused by in-house toggle; "
             "fees accumulate untouched.")
        return {"status": "paused"}

    events = db.unallocated_fee_events()
    new_sol = round(sum(e["amount_sol"] for e in events), 9)
    if not events or new_sol <= 0:
        _log("no new fee events since last round; nothing to do.")
        return {"status": "no_fees", "unallocated_sol": db.unallocated_fees_total()}

    buyback_pct = float(settings.get("buyback_pct"))
    team_pct = float(settings.get("team_pct"))
    budget_sol = round(new_sol * buyback_pct / 100.0, 9)
    team_sol = round(new_sol * team_pct / 100.0, 9)
    live = bool(settings.get("buyback_live"))

    _log(f"{len(events)} new fee event(s), {new_sol:.9f} SOL: "
         f"buyback {buyback_pct}% = {budget_sol:.9f} SOL, "
         f"team {team_pct}% = {team_sol:.9f} SOL "
         f"({'LIVE' if live else 'mock'})")

    if live:
        spent = _live_round(settings, budget_sol)
        team_swept_tx = _team_sweep(settings, team_sol) if team_sol > 0 else None
        result = {
            "status": "burned", "dry_run": False,
            "sol_spent": spent["sol_spent"],
            "tokens_bought": spent["tokens_bought"],
            "tokens_burned": spent["tokens_burned"],
            "buy_tx": spent["buy_tx"], "burn_tx": spent["burn_tx"],
            "team_sol": team_sol, "team_swept_tx": team_swept_tx,
        }
    else:
        spent = _mock_round(settings, budget_sol)
        result = {
            "status": "burned", "dry_run": True,
            "sol_spent": spent["sol_spent"],
            "tokens_bought": spent["tokens_bought"],
            "tokens_burned": spent["tokens_burned"],
            "buy_tx": spent["buy_tx"], "burn_tx": spent["burn_tx"],
            "team_sol": team_sol, "team_swept_tx": None,
        }

    # Atomic: allocations + burn row + team row land together, so a fee
    # event can never be allocated twice or lost between steps.
    db.record_buyback_round(
        event_ids=[e["id"] for e in events],
        event_amounts=[e["amount_sol"] for e in events],
        sol_spent=result["sol_spent"],
        tokens_bought=result["tokens_bought"],
        tokens_burned=result["tokens_burned"],
        buy_tx=result["buy_tx"],
        burn_tx=result["burn_tx"],
        dry_run=result["dry_run"],
        team_sol=team_sol,
        team_swept_tx=result["team_swept_tx"],
    )
    _log(f"round recorded: spent {result['sol_spent']:.9f} SOL, "
         f"burned {result['tokens_burned']} tokens "
         f"(buy {result['buy_tx']}, burn {result['burn_tx']})")
    return result


# ------------------------------------------------------------------ mock
def _mock_round(settings: SettingsStore, budget_sol: float) -> dict:
    """Zero network calls. Simulates the swap at the fixed mock rate."""
    rate = float(settings.get("buyback_mock_rate"))  # tokens per SOL
    if rate <= 0:
        raise BuybackError("buyback_mock_rate must be positive")
    tokens = round(budget_sol * rate, 6)
    tag = uuid.uuid4().hex[:12]
    return {
        "sol_spent": budget_sol,
        "tokens_bought": tokens,
        "tokens_burned": tokens,  # mock: everything bought is burned
        "buy_tx": f"mock_buy_{tag}",
        "burn_tx": f"mock_burn_{tag}",
    }


# ------------------------------------------------------------------ live
def _live_config(settings: SettingsStore) -> dict:
    """Config check FIRST: raises loudly before any solana/solders import,
    so misconfigured live mode can never degrade into a silent mock."""
    mint = str(settings.get("token_mint") or "").strip()
    wallet = os.environ.get("TREASURY_WALLET", "").strip()
    secret = os.environ.get("TREASURY_PRIVATE_KEY", "").strip()
    helius = os.environ.get("HELIUS_API_KEY", "").strip()
    team_wallet = os.environ.get("TEAM_WALLET", "").strip()
    need_team = bool(settings.get("team_sweep_enabled"))
    missing = [name for name, val in (
        ("TREASURY_WALLET", wallet),
        ("TREASURY_PRIVATE_KEY", secret),
        ("HELIUS_API_KEY", helius),
        ("TEAM_WALLET", team_wallet if need_team else "x"),
    ) if not val]
    if missing:
        raise BuybackError(
            "buyback_live is ON but these are missing from .env: "
            + ", ".join(missing)
            + ". Refusing to run: live mode must fail loudly, never silently mock.")
    if not mint:
        raise BuybackError(
            "buyback_live is ON but token_mint is empty in settings. "
            "Launch the token and set token_mint before enabling live buybacks.")
    return {"mint": mint, "wallet": wallet, "secret": secret,
            "helius": helius,
            "rpc_url": f"https://mainnet.helius-rpc.com/?api-key={helius}"}


def _rpc_call(rpc_url: str, method: str, params: list, timeout: int = 60):
    """Raw JSON-RPC call (stdlib urllib only - no solana-py version risk)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(
        rpc_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
    except Exception as exc:
        raise BuybackError(f"RPC call {method} failed: {exc}")
    if out.get("error"):
        raise BuybackError(f"RPC call {method} errored: {out['error']}")
    return out["result"]


def _hot_balance_check(rpc_url: str, wallet: str, cap_sol: float,
                       want_sol: float) -> float:
    """Hot-balance discipline: the bot wallet must never hold more than the
    cap. Each round is funded only by what the hot wallet already holds
    (the owner tops it up from cold); nothing here ever touches cold keys.

    Returns the SOL the round may spend (capped by spendable balance).
    """
    lamports = _rpc_call(rpc_url, "getBalance", [wallet])["value"]
    balance = lamports / 1_000_000_000
    if balance > cap_sol + 1e-9:
        raise BuybackError(
            f"Hot wallet holds {balance:.4f} SOL, above the "
            f"buyback_hot_sol_cap of {cap_sol:.4f} SOL. Sweep the excess to "
            f"the cold wallet first; refusing to run.")
    spendable = balance - HOT_TX_RESERVE_SOL
    if spendable <= 0:
        raise BuybackError(
            f"Hot wallet has no spendable SOL (balance {balance:.4f}, "
            f"reserve {HOT_TX_RESERVE_SOL}). Owner: top up from cold first.")
    if spendable < want_sol:
        _log(f"hot wallet only has {spendable:.9f} spendable SOL (< "
             f"{want_sol:.9f} budgeted); spending what is available.")
        return round(spendable, 9)
    return want_sol


def _jup_request(method: str, path: str, params: dict | None = None,
                 body: dict | None = None, timeout: int = 60) -> dict:
    """Jupiter swap API call, trying the free tier first then the paid
    mirror. Raises BuybackError when both fail."""
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    payload = json.dumps(body).encode() if body is not None else None
    last_err: Exception | None = None
    for base in JUPITER_ENDPOINTS:
        url = base + path + query
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # try the next endpoint
            last_err = exc
    raise BuybackError(f"Jupiter API unreachable ({method} {path}): {last_err}")


def _live_round(settings: SettingsStore, budget_sol: float) -> dict:
    """Real swap + burn. All solana/solders imports happen here, only when
    live mode is actually executing."""
    cfg = _live_config(settings)

    # Config is valid; now the heavy deps are allowed.
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction, VersionedTransaction

    TOKEN_PROGRAM_ID = Pubkey.from_string(
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string(
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

    wallet_pk = Pubkey.from_string(cfg["wallet"])
    keypair = Keypair.from_base58_string(cfg["secret"])
    if str(keypair.pubkey()) != cfg["wallet"]:
        raise BuybackError("TREASURY_PRIVATE_KEY does not match TREASURY_WALLET")
    mint = Pubkey.from_string(cfg["mint"])
    rpc_url = cfg["rpc_url"]

    spend_sol = _hot_balance_check(rpc_url, cfg["wallet"],
                                   float(settings.get("buyback_hot_sol_cap")),
                                   budget_sol)
    if spend_sol <= 0:
        raise BuybackError("Round budget is zero after hot-balance check.")
    lamports = int(round(spend_sol * 1_000_000_000))
    _log(f"live swap: {spend_sol:.9f} SOL -> {cfg['mint']}")

    # 1) quote
    quote = _jup_request("GET", "/quote", params={
        "inputMint": SOL_MINT, "outputMint": cfg["mint"],
        "amount": str(lamports), "slippageBps": str(JUPITER_SLIPPAGE_BPS)})
    out_amount = int(quote.get("outAmount", 0))
    if out_amount <= 0:
        raise BuybackError("Jupiter returned no route (outAmount=0); "
                           "round aborted, fees stay unallocated.")

    # 2) build + sign + send the swap (versioned transaction)
    swap_resp = _jup_request("POST", "/swap", body={
        "quoteResponse": quote,
        "userPublicKey": cfg["wallet"],
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto",
    })
    swap_b64 = swap_resp.get("swapTransaction")
    if not swap_b64:
        raise BuybackError("Jupiter did not return a swapTransaction")
    import base64
    swap_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_b64))
    swap_tx.sign([keypair])
    buy_sig = _send_raw(rpc_url, base64.b64encode(bytes(swap_tx)).decode())
    _log(f"swap sent: {buy_sig}")

    # 3) read the bought balance from the token's associated account
    ata, _ = Pubkey.find_program_address(
        [bytes(wallet_pk), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID)
    token_balance = _rpc_call(rpc_url, "getTokenAccountBalance",
                              [str(ata)])["value"]
    token_amount = int(token_balance.get("amount", 0))
    if token_amount <= 0:
        raise BuybackError(
            f"Swap confirmed ({buy_sig}) but bought 0 tokens. "
            "Fees stay unallocated; investigate before retrying.")

    # 4) burn everything bought (SPL Token Burn instruction, index 8)
    burn_data = bytes([8]) + token_amount.to_bytes(8, "little")
    burn_ix = Instruction(
        TOKEN_PROGRAM_ID, burn_data,
        [AccountMeta(ata, is_signer=False, is_writable=True),
         AccountMeta(mint, is_signer=False, is_writable=True),
         AccountMeta(wallet_pk, is_signer=True, is_writable=False)],
    )
    blockhash = _rpc_call(rpc_url, "getLatestBlockhash",
                          [{"commitment": "confirmed"}])["value"]["blockhash"]
    burn_tx = Transaction.new_signed_with_payer(
        [burn_ix], wallet_pk, [keypair], blockhash)
    import base64 as _b64
    burn_sig = _send_raw(rpc_url, _b64.b64encode(bytes(burn_tx)).decode())
    _log(f"burn sent: {burn_sig} ({token_amount} base units)")

    return {
        "sol_spent": spend_sol,
        "tokens_bought": token_amount,
        "tokens_burned": token_amount,
        "buy_tx": buy_sig,
        "burn_tx": burn_sig,
    }


def _send_raw(rpc_url: str, b64_tx: str) -> str:
    """Submit a signed base64 transaction and wait for confirmation."""
    sig = _rpc_call(rpc_url, "sendTransaction",
                    [b64_tx, {"encoding": "base64",
                              "preflightCommitment": "confirmed",
                              "maxRetries": 3}])
    deadline = time.time() + 90
    while time.time() < deadline:
        statuses = _rpc_call(
            rpc_url, "getSignatureStatuses", [[sig],
                                              {"searchTransactionHistory": False}])
        st = (statuses.get("value") or [None])[0]
        if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
            if st.get("err"):
                raise BuybackError(f"Transaction {sig} failed on-chain: "
                                   f"{st['err']}")
            return sig
        time.sleep(2)
    raise BuybackError(f"Transaction {sig} not confirmed within 90s. "
                       "Treat as failed: fees stay unallocated; check the "
                       "tx on a block explorer before any manual retry "
                       "(double-spend risk).")


def _team_sweep(settings: SettingsStore, team_sol: float) -> str | None:
    """Live mode only: send the team's share to TEAM_WALLET. Off by default
    (team_sweep_enabled=false); when off, the row is recorded unswept."""
    if not bool(settings.get("team_sweep_enabled")):
        return None
    cfg = _live_config(settings)  # re-validates (incl. TEAM_WALLET presence)
    team_wallet = os.environ.get("TEAM_WALLET", "").strip()

    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction

    keypair = Keypair.from_base58_string(cfg["secret"])
    lamports = int(round(team_sol * 1_000_000_000))
    if lamports <= 0:
        return None
    ix = transfer(TransferParams(
        from_pubkey=keypair.pubkey(),
        to_pubkey=Pubkey.from_string(team_wallet),
        lamports=lamports))
    blockhash = _rpc_call(cfg["rpc_url"], "getLatestBlockhash",
                          [{"commitment": "confirmed"}])["value"]["blockhash"]
    tx = Transaction.new_signed_with_payer(
        [ix], keypair.pubkey(), [keypair], blockhash)
    import base64
    sig = _send_raw(cfg["rpc_url"], base64.b64encode(bytes(tx)).decode())
    _log(f"team sweep: {team_sol:.9f} SOL -> {team_wallet} ({sig})")
    return sig


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent Arena buyback bot: buy the project token with fee "
                    "revenue and burn it. Mock mode by default (no network).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true",
                      help="run a single buyback cycle and exit")
    mode.add_argument("--loop", action="store_true",
                      help="repeat cycles, sleeping buyback_interval_minutes "
                           "between them")
    parser.add_argument("--data-dir", default=None,
                        help="data directory (default: <project>/data)")
    args = parser.parse_args(argv)

    _load_dotenv(PROJECT_ROOT / ".env")
    data_dir = Path(args.data_dir) if args.data_dir else PROJECT_ROOT / "data"
    settings = SettingsStore(data_dir / "settings.json")
    db = Database(data_dir / "arena.db")

    def cycle() -> int:
        try:
            result = run_cycle(settings, db)
        except BuybackError as exc:
            _log(f"ERROR: {exc}")
            return 1
        except Exception as exc:  # unexpected: never silently swallow
            _log(f"UNEXPECTED ERROR: {type(exc).__name__}: {exc}")
            return 2
        _log(f"cycle done: {result['status']}")
        return 0

    if args.once:
        return cycle()

    interval = float(settings.get("buyback_interval_minutes"))
    _log(f"loop mode: every {interval} minute(s), mock="
         f"{not settings.get('buyback_live')}")
    while True:
        try:
            cycle()
        except KeyboardInterrupt:
            _log("interrupted; exiting.")
            return 0
        _log(f"sleeping {interval} minute(s)...")
        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            _log("interrupted; exiting.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
