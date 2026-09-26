"""Phase 5: parimutuel betting engine + settlement.

Rules (plain version also lives in README.md, "Betting rules"):

* Every bet goes into one pool per battle. Pools are tracked per fighter.
* When the battle finishes, the house takes its cut
  (`betting_house_cut_pct` from settings) off the TOP of the total pool.
  The rest is the payout pool.
* Each bettor on the winning fighter gets:
      payout_pool x (their stake on the winner / total staked on the winner)
  Bettors on any other fighter get nothing.
* Draw -> every bettor gets their FULL stake back, no house cut.
* No bets on the winner -> every bettor gets their FULL stake back,
  no house cut.
* A fighter that is benched or crashes mid-fight simply loses the fight;
  bets on it lose like any other losing bet.
* A battle with zero bets writes no settlement record at all.
* Bets on exhibition battles or on finished battles are refused at bet
  time (400), so settlement never has to handle them.

Mock mode (default): `payment_status="mock"`, nothing on-chain; payouts
are ledger credits in `bet_payouts`. Live mode (`betting_live=true`) is
the owner's explicit future decision; see backend/escrow_live.py.
"""

from __future__ import annotations

from typing import Any

# SOL amounts are rounded to 9 decimals (lamport precision).
_ROUND = 9


def _r(x: float) -> float:
    return round(float(x), _ROUND)


def compute_settlement(bets: list[dict[str, Any]], winner: str | None,
                       draw: bool, house_cut_pct: float) -> dict[str, Any]:
    """Pure parimutuel math. No DB, no network: fully unit-testable.

    `bets`: list of {id, fighter_id, amount_sol}.
    Returns {status, payouts: {bet_id: payout_sol}, outcomes: {bet_id:
    'win'|'lose'|'refund'}, total_bets_sol, house_cut_sol, payout_pool_sol,
    bet_count}.

    Rounding note: each winning payout is rounded to lamports, so the sum
    of payouts can differ from the payout pool by a lamport or two. The
    house cut is always computed from the unrounded total first.
    """
    total = _r(sum(b["amount_sol"] for b in bets))
    count = len(bets)
    stakes = {b["id"]: _r(b["amount_sol"]) for b in bets}

    def refunds(status: str) -> dict[str, Any]:
        return {
            "status": status,
            "payouts": dict(stakes),
            "outcomes": {bid: "refund" for bid in stakes},
            "total_bets_sol": total,
            "house_cut_sol": 0.0,
            "payout_pool_sol": total,
            "bet_count": count,
        }

    # Draw (or no winner at all): full refunds, the house takes nothing.
    if draw or not winner:
        return refunds("refunded_draw")

    winner_stake = _r(sum(b["amount_sol"] for b in bets
                          if b["fighter_id"] == winner))
    # Nobody bet on the winner: full refunds, the house takes nothing.
    if winner_stake <= 0:
        return refunds("refunded_no_winner_bets")

    house_cut = _r(total * house_cut_pct / 100.0)
    pool = _r(total - house_cut)
    payouts: dict[Any, float] = {}
    outcomes: dict[Any, str] = {}
    for b in bets:
        if b["fighter_id"] == winner:
            # A benched/crashed fighter is just a loser: it can never be
            # the winner, so bets on it land in the else branch below.
            payouts[b["id"]] = _r(pool * (_r(b["amount_sol"]) / winner_stake))
            outcomes[b["id"]] = "win"
        else:
            payouts[b["id"]] = 0.0
            outcomes[b["id"]] = "lose"
    return {
        "status": "settled",
        "payouts": payouts,
        "outcomes": outcomes,
        "total_bets_sol": total,
        "house_cut_sol": house_cut,
        "payout_pool_sol": pool,
        "bet_count": count,
    }


def settle_battle(db, battle_id: str, result: dict[str, Any],
                  house_cut_pct: float, betting_live: bool = False
                  ) -> dict[str, Any] | None:
    """Run parimutuel settlement for a finished battle.

    Writes one `settlements` row, one `bet_payouts` row per bet, and (when
    there is a real cut) one `treasury_fee_events` row with
    source='betting'. Returns the settlement dict, or None when the battle
    had zero bets (no settlement record is written in that case).

    Mock mode: payouts are ledger credits only. Live mode: real payouts
    are attempted for `payment_status='confirmed'` bets AFTER the ledger
    rows are written; the on-chain signature is stored in
    `bet_payouts.paid_tx`. If a live send fails, the ledger row stays with
    paid_tx NULL (needs manual handling) rather than losing the record.
    """
    bets = db.list_bets(battle_id, payment_statuses=("mock", "confirmed"))
    if not bets:
        return None
    res = compute_settlement(bets, result.get("winner"),
                             bool(result.get("draw")), house_cut_pct)
    db.record_settlement(
        battle_id,
        result.get("winner"),
        bool(result.get("draw")),
        res["status"],
        res["total_bets_sol"],
        res["house_cut_sol"],
        res["payout_pool_sol"],
        res["bet_count"],
    )
    for b in bets:
        db.record_payout(b["id"], battle_id, b["wallet"], b["amount_sol"],
                         res["payouts"][b["id"]], res["outcomes"][b["id"]])

    if res["house_cut_sol"] > 0:
        # House cut -> treasury ledger, the phase 6 buyback bot's input.
        db.record_fee_event(battle_id, "betting", res["house_cut_sol"])

    if betting_live:
        _send_live_payouts(db, battle_id, bets, res)

    return res


def _send_live_payouts(db, battle_id: str, bets: list[dict[str, Any]],
                       res: dict[str, Any]) -> None:
    """Live-mode real-SOL payouts. Never imported in mock mode."""
    from backend.escrow_live import send_payouts  # noqa: E402  (live only)

    # Aggregate per wallet; only bets whose deposit was verified on-chain.
    per_wallet: dict[str, float] = {}
    bet_ids: dict[str, list[Any]] = {}
    for b in bets:
        payout = res["payouts"][b["id"]]
        if b["payment_status"] == "confirmed" and payout > 0:
            per_wallet[b["wallet"]] = _r(per_wallet.get(b["wallet"], 0.0)
                                         + payout)
            bet_ids.setdefault(b["wallet"], []).append(b["id"])
    if not per_wallet:
        return
    try:
        txs = send_payouts(per_wallet)
    except Exception:
        # Ledger rows are already written; paid_tx stays NULL so the
        # missing on-chain payout is visible and can be handled manually.
        return
    for wallet, sig in txs.items():
        for bet_id in bet_ids.get(wallet, []):
            db.record_payout(bet_id, battle_id, wallet,
                             next(b["amount_sol"] for b in bets
                                  if b["id"] == bet_id),
                             res["payouts"][bet_id], res["outcomes"][bet_id],
                             paid_tx=sig)
