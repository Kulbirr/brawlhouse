"""Phase 1 (FT economy) domain logic.

Pure-ish domain layer between the HTTP handlers and the DB: born flow,
entry queue, official-battle draw, official settlement, and seasons.

Money rules (all amounts read from settings, never hardcoded):
  - Born fee: born_season_pct% -> season pool, rest -> treasury fee event.
  - Entry fee: entry_prize_pct% -> the drawn battle's prize pool,
    entry_season_pct% -> season pool (credited at entry time, even if the
    fighter is never drawn -- the fee paid for the shot, not the outcome).
  - Battle prize: 100% to the winner's owner. House-fighter win -> the
    treasury (fee event). Draw -> rolls into the season pool.
  - Season pool: born share + entry share + rolled-over battle pools and
    undistributed season remainders. Paid top-3 at season end.
Every prize is paid ONLY from revenue actually received; pools can never
go negative because they are only ever incremented by real splits.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena import (  # noqa: E402
    HOUSE_BOT_IDS,
    next_ft_id,
    roll_rarity,
    sign_cert,
    validate_born_input,
)
from backend import payouts  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round9(x: float) -> float:
    return round(float(x), 9)


def _slot_seconds(settings) -> int:
    """Seconds between official battles."""
    return max(1, int(settings.get("official_battle_interval_minutes"))) * 60


def seconds_to_next_battle(settings, now: float | None = None) -> int:
    """Seconds until the next slot boundary (the next official battle)."""
    slot = _slot_seconds(settings)
    t = time.time() if now is None else now
    return int((int(t) // slot + 1) * slot - t)


def entry_window_open(settings, now: float | None = None) -> bool:
    """True during the last entry_window_minutes before the next battle."""
    window = max(1, int(settings.get("entry_window_minutes"))) * 60
    return seconds_to_next_battle(settings, now) <= window


def check_entry_window(settings, now: float | None = None) -> None:
    """Raise ValueError when the entry window is closed."""
    if not entry_window_open(settings, now):
        wait = seconds_to_next_battle(settings, now)
        window_min = int(settings.get("entry_window_minutes"))
        opens_in = wait - window_min * 60
        raise ValueError(
            "Entry window is closed. It opens "
            f"{opens_in // 60}m {opens_in % 60}s before the next battle.")


# ------------------------------------------------------------------ seasons
def ensure_active_season(db, settings, length_days: int | None = None) -> dict:
    """Return the active season, creating one or rolling over as needed."""
    now = _utcnow_iso()
    season = db.get_active_season()
    if season is not None and season["ends_at"] > now:
        return season
    if season is not None:
        # Expired: roll over (pays out, starts the next season) exactly once.
        return rollover_season(db, settings, season)
    days = length_days if length_days is not None else int(
        settings.get("season_length_days"))
    return db.create_season(days)


def rollover_season(db, settings, season: dict) -> dict:
    """Pay the top 3 of a finished season and start the next one.

    Undistributed remainder (fewer than 3 ranked fighters) seeds the next
    season's pool instead of vanishing. Returns the new active season.
    """
    season_id = season["id"]
    pool = float(season["prize_pool_sol"] or 0.0)
    board = db.season_leaderboard(season_id, limit=3)
    pcts = [float(settings.get("season_prize_1_pct")),
            float(settings.get("season_prize_2_pct")),
            float(settings.get("season_prize_3_pct"))]
    distributed = 0.0
    payouts_live = bool(settings.get("payouts_live"))
    for place, (row, pct) in enumerate(zip(board, pcts), start=1):
        fighter = db.get_fighter(row["fighter_id"]) or {}
        owner = fighter.get("owner_wallet")
        amount = _round9(pool * pct / 100.0)
        if amount <= 0 or not owner:
            continue
        distributed = _round9(distributed + amount)
        if payouts_live:
            try:
                sig = payouts.pay_sol(settings, owner, amount)
                status, tx = "paid", sig
            except payouts.PayoutError:
                status, tx = "pending", None
        else:
            status, tx = "mock", None
        db.record_season_payout(season_id, place, row["fighter_id"], owner,
                               amount, status=status, paid_tx=tx)
    db.finish_season(season_id)
    days = int(settings.get("season_length_days"))
    new_season = db.create_season(days)
    remainder = _round9(pool - distributed)
    if remainder > 0:
        db.add_to_season_pool(new_season["id"], remainder)
        new_season = db.get_season(new_season["id"])
    return new_season


# --------------------------------------------------------------------- born
def born_fighter(db, settings, wallet: str, name: str, archetype: str,
                 color: str, rng: random.Random | None = None,
                 data_dir=None) -> tuple[dict, dict]:
    """Mock-mode born: validate, roll rarity, create the FT, split the fee.

    Returns (fighter_row, info) where info has rarity/stat_mult/season and
    treasury shares. Retries the FT id on a duplicate-key race.
    """
    clean_name, arch, col = validate_born_input(name, archetype, color)
    wallet = (wallet or "").strip()
    if not wallet:
        raise ValueError("wallet is required")
    fee = float(settings.get("born_fee_sol"))
    if fee <= 0:
        raise ValueError("born_fee_sol must be positive")
    rarity, mult = roll_rarity(rng)

    last_exc: Exception | None = None
    for _ in range(3):
        ft_id = next_ft_id(db.ft_ids())
        cert = sign_cert(ft_id, wallet, data_dir)
        try:
            row = db.create_ft_fighter(ft_id, clean_name, wallet, arch, col,
                                       rarity, mult, cert)
            break
        except sqlite3.IntegrityError as exc:
            last_exc = exc
    else:
        raise last_exc  # type: ignore[misc]

    db.record_birth_event(ft_id, clean_name, rarity, wallet)

    season = ensure_active_season(db, settings)
    season_share = _round9(fee * float(settings.get("born_season_pct")) / 100.0)
    treasury_share = _round9(fee - season_share)
    if season_share > 0:
        db.add_to_season_pool(season["id"], season_share)
    if treasury_share > 0:
        db.record_fee_event(None, "born", treasury_share)
    return row, {
        "rarity": rarity,
        "stat_mult": mult,
        "season_share_sol": season_share,
        "treasury_share_sol": treasury_share,
        "season_id": season["id"],
    }


def finalize_birth_intent(db, settings, intent: dict,
                          rng: random.Random | None = None,
                          data_dir=None) -> tuple[dict, dict]:
    """Live-mode born step 2: the payment is verified; create the FT.

    Same economics as born_fighter, driven by the stored intent.
    """
    row, info = born_fighter(
        db, settings, intent["wallet"], intent["name"],
        intent["archetype"], intent["color"], rng=rng, data_dir=data_dir)
    db.confirm_birth_intent(intent["id"], row["id"])
    return row, info


# ---------------------------------------------------------------- entry queue
def _entry_splits(settings, fee: float) -> tuple[float, float]:
    """(prize_share, season_share) for one entry fee."""
    prize_share = _round9(fee * float(settings.get("entry_prize_pct")) / 100.0)
    return prize_share, _round9(fee - prize_share)


def _get_own_active_fighter(db, fighter_id: str,
                            wallet: str) -> dict[str, Any]:
    row = db.get_fighter(fighter_id)
    if row is None or not row.get("owner_wallet"):
        raise ValueError(f"Unknown fighter: {fighter_id}")
    if row["owner_wallet"] != wallet:
        raise ValueError("Only the owner can enter this fighter")
    if row.get("status") != "active":
        raise ValueError(
            f"Fighter is {row.get('status')}, not available for entry")
    return row


def queue_entry_exists(db, fighter_id: str) -> bool:
    rows = db._q(
        "SELECT 1 FROM battle_queue WHERE fighter_id = ? AND status = 'queued'",
        (fighter_id,))
    return bool(rows)


def activate_queue_entry(db, settings, entry: dict) -> dict:
    """Move a paid entry into the drawable queue: fee splits + status."""
    fee = float(entry["entry_fee_sol"])
    prize_share, season_share = _entry_splits(settings, fee)
    season = ensure_active_season(db, settings)
    if season_share > 0:
        db.add_to_season_pool(season["id"], season_share)
    db.set_fighter_status(entry["fighter_id"], "queued")
    return db.get_queue_entry(entry["id"])


def enter_queue(db, settings, fighter_id: str, wallet: str) -> dict:
    """Mock-mode entry: validate, charge the splits, queue the fighter."""
    wallet = (wallet or "").strip()
    if not wallet:
        raise ValueError("wallet is required")
    check_entry_window(settings)
    _get_own_active_fighter(db, fighter_id, wallet)
    if queue_entry_exists(db, fighter_id):
        raise ValueError("Fighter is already queued for the next battle")
    fee = float(settings.get("entry_fee_sol"))
    if fee <= 0:
        raise ValueError("entry_fee_sol must be positive")
    prize_share, _ = _entry_splits(settings, fee)
    entry = db.enqueue_fighter(fighter_id, wallet, fee, prize_share, "mock")
    return activate_queue_entry(db, settings, entry)


def hire_house_bot(db, settings, house_bot_id: str, wallet: str,
                   fee_sol: float) -> dict:
    """Hire a house bot for the next official battle (mock mode).

    The bot enters under the hirer's wallet; if it wins, the prize goes to
    the hirer. House bots only — player-owned fighters cannot be hired
    (that's Phase 2 rentals). A wallet that already has a fighter queued
    (or a hire open) cannot hire: hire is the on-ramp for players without
    a fighter.
    """
    from arena.house_fighters import HOUSE_BOT_IDS
    wallet = (wallet or "").strip()
    if not wallet:
        raise ValueError("wallet is required")
    bot_id = (house_bot_id or "").strip().lower()
    if bot_id not in HOUSE_BOT_IDS:
        raise ValueError(f"Unknown house fighter: {house_bot_id!r}")
    check_entry_window(settings)
    # Hire is for players WITHOUT their own fighter in the queue.
    for e in db.list_queued_entries():
        if e["owner_wallet"] == wallet:
            raise ValueError(
                "You already have a fighter queued for the next battle")
    if db.house_hire_open_for_wallet(wallet):
        raise ValueError("You already hired a fighter for the next battle")
    if fee_sol <= 0:
        raise ValueError("hire fee must be positive")
    hire = db.create_house_hire(bot_id, wallet, fee_sol, "mock")
    if hire is None:
        raise ValueError(
            f"{bot_id} is already hired for the next battle")
    # The hire fee is house revenue: lands in the treasury ledger for the
    # buyback bot, like any other fee.
    db.record_fee_event("hire", "hire", fee_sol)
    return hire


# --------------------------------------------------------------- the draw
def draw_fighters(db, mode: str,
                  rng: random.Random | None = None) -> list[dict]:
    """Draw fighters for an official battle.

    Hired house bots go first (guaranteed slots — the hirer paid for a
    seat). Then a weighted random sample of the paid queue: weight = 1 +
    minutes waited, so the longest-waiting fighters are favored but the
    draw stays random. Short queues are filled with house fighters (never
    paid, never ranked).
    Returns ([{registry_id, owner_wallet|None, entry_id|None, prize_share,
    stat_mult, hired_by|None}], [hire_ids drawn]). hired_by is the hirer
    wallet for hired house bots (prize goes to them); None otherwise.
    """
    if mode not in ("duel", "royale"):
        raise ValueError(f"Unknown official mode: {mode!r}")
    rng = rng or random.Random()
    needed = 2 if mode == "duel" else 4

    picked: list[dict] = []
    hired_ids: list[int] = []

    # Hired house bots: guaranteed slots, in hire order. Cap at `needed`
    # (a duel only seats two).
    for h in db.list_open_house_hires():
        if len(picked) >= needed:
            break
        picked.append({
            "registry_id": h["house_bot_id"],
            "owner_wallet": h["hirer_wallet"],
            "entry_id": None,
            "prize_share": 0.0,
            "stat_mult": 1.0,
            "hired_by": h["hirer_wallet"],
        })
        hired_ids.append(h["id"])
    # Stash the drawn hire ids for run_official_battle to mark.
    picked_hired_ids = hired_ids

    now = time.time()
    candidates = []
    for e in db.list_queued_entries():
        f = db.get_fighter(e["fighter_id"])
        if f is None or not f.get("owner_wallet"):
            continue
        if f.get("status") not in ("queued", "active"):
            continue  # mid-fight or otherwise busy: stays for next battle
        try:
            entered = datetime.fromisoformat(e["entered_at"])
        except (ValueError, TypeError):
            entered = datetime.now(timezone.utc)
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=timezone.utc)
        waited_min = max(0.0, (now - entered.timestamp()) / 60.0)
        candidates.append((e, f, 1.0 + waited_min))

    pool = list(candidates)
    while pool and len(picked) < needed:
        weights = [w for _, _, w in pool]
        entry, fighter, _w = rng.choices(pool, weights=weights, k=1)[0]
        pool.remove((entry, fighter, _w))
        picked.append({
            "registry_id": fighter["id"],
            "owner_wallet": fighter["owner_wallet"],
            "entry_id": entry["id"],
            "prize_share": float(entry["prize_share_sol"] or 0.0),
            "stat_mult": float(fighter.get("stat_mult") or 1.0),
            "hired_by": None,
        })

    # House fill: random distinct house fighters for the empty slots.
    # Never pick a bot that's already hired for this battle.
    hired_bots = {p["registry_id"] for p in picked if p.get("hired_by")}
    shortfall = needed - len(picked)
    if shortfall > 0:
        available = [hid for hid in HOUSE_BOT_IDS if hid not in hired_bots]
        for hid in rng.sample(available, min(shortfall, len(available))):
            picked.append({
                "registry_id": hid,
                "owner_wallet": None,
                "entry_id": None,
                "prize_share": 0.0,
                "stat_mult": 1.0,
                "hired_by": None,
            })
    return picked, picked_hired_ids


# ------------------------------------------------------- official battles
def next_battle_info(db, settings) -> dict:
    """Countdown info for the next scheduled official battle."""
    interval = int(settings.get("official_battle_interval_minutes"))
    starts_in = seconds_to_next_battle(settings)
    starts_at = (int(time.time()) // _slot_seconds(settings) + 1) \
        * _slot_seconds(settings)
    last_mode = db.kv_get("last_official_mode")
    mode = "royale" if last_mode == "duel" else "duel"
    return {
        "starts_at": datetime.fromtimestamp(starts_at,
                                            tz=timezone.utc).isoformat(),
        "starts_in_seconds": starts_in,
        "mode": mode,
        "mode_size": 2 if mode == "duel" else 4,
        "interval_minutes": interval,
        "entry_window_minutes": int(settings.get("entry_window_minutes")),
        "entry_open": entry_window_open(settings),
        "queued_count": db.queue_count(),
    }


def run_official_battle(db, runner, settings, engine_cfg,
                        rng: random.Random | None = None):
    """Draw the queue and start one official battle. Returns the LiveBattle."""
    season = ensure_active_season(db, settings)
    last_mode = db.kv_get("last_official_mode")
    mode = "royale" if last_mode == "duel" else "duel"
    drawn, hire_ids = draw_fighters(db, mode, rng)
    registry_ids = [d["registry_id"] for d in drawn]
    stat_mults = {d["registry_id"]: d["stat_mult"] for d in drawn
                  if d["stat_mult"] != 1.0}
    prize_pool = _round9(sum(d["prize_share"] for d in drawn))
    # Flip fighter statuses BEFORE starting the battle thread: a fast
    # battle can settle before we return, and settlement only frees
    # fighters whose status is 'fighting'.
    for d in drawn:
        if d["entry_id"] is not None:
            db.set_fighter_status(d["registry_id"], "fighting")
    live = runner.create(
        registry_ids=registry_ids,
        seed=None,
        exhibition=False,
        playback_speed=1.0,
        engine_cfg=engine_cfg,
        hire_fee_sol=float(settings.get("hire_fee_sol")),
        official=True,
        mode=mode,
        prize_pool_sol=prize_pool,
        stat_mults=stat_mults,
    )
    db.kv_set("last_official_mode", mode)
    db.kv_set(f"official_season:{live.id}", str(season["id"]))
    entry_ids = [d["entry_id"] for d in drawn if d["entry_id"] is not None]
    db.mark_entries_drawn(entry_ids, live.id)
    # Mark hired bots as drawn; store the hire mapping on the battle so
    # settlement can pay the hirer (not the treasury) on a hired-bot win.
    db.mark_house_hires_drawn(hire_ids, live.id)
    hired_map = {d["registry_id"]: d["hired_by"] for d in drawn
                 if d.get("hired_by")}
    if hired_map:
        db.kv_set(f"official_hired:{live.id}", json.dumps(hired_map))
    return live


def settle_official_battle(db, settings, live) -> dict | None:
    """Pay the battle prize and update season standings. Never raises."""
    try:
        return _settle_official_battle(db, settings, live)
    except Exception:
        # Settlement must never break battle completion.
        return None


def _settle_official_battle(db, settings, live) -> dict:
    rec = db.get_battle(live.id)
    if rec is None or not rec["official"]:
        return {"skipped": True}
    result = live.result or {}
    winner_engine = result.get("winner")
    registry_ids = live.registry_ids
    engine_ids = live.engine_ids
    winner_registry = None
    if winner_engine and winner_engine in engine_ids:
        winner_registry = registry_ids[engine_ids.index(winner_engine)]
    pool = _round9(float(rec["prize_pool_sol"] or 0.0))

    season_id_raw = db.kv_get(f"official_season:{live.id}")
    try:
        season_id = int(season_id_raw) if season_id_raw else None
    except (TypeError, ValueError):
        season_id = None
    if season_id is None:
        season_id = ensure_active_season(db, settings)["id"]

    # Season standings for every player FT in the battle.
    for eng_id, reg_id in zip(engine_ids, registry_ids):
        fighter = db.get_fighter(reg_id)
        if not (fighter and fighter.get("owner_wallet")):
            continue
        if result.get("draw"):
            outcome = "draw"
        elif eng_id == winner_engine:
            outcome = "win"
        else:
            outcome = "loss"
        db.record_season_result(season_id, reg_id, outcome)

    payouts_live = bool(settings.get("payouts_live"))
    if result.get("draw") or not winner_registry:
        # No winner: the pool rolls into the season pool.
        if pool > 0:
            db.add_to_season_pool(season_id, pool)
        prize = db.record_battle_prize(
            live.id, pool, winner_registry, None, pool,
            status="paid" if payouts_live else "mock",
            destination="season_pool")
    else:
        fighter = db.get_fighter(winner_registry) or {}
        owner = fighter.get("owner_wallet")
        if not owner:
            # Hired house bot? The prize goes to the hirer, not the treasury.
            hired_raw = db.kv_get(f"official_hired:{live.id}")
            try:
                hired_map = json.loads(hired_raw) if hired_raw else {}
            except (ValueError, TypeError):
                hired_map = {}
            owner = hired_map.get(winner_registry)
        if owner:
            if payouts_live:
                try:
                    sig = payouts.pay_sol(settings, owner, pool)
                    status, tx = "paid", sig
                except payouts.PayoutError:
                    status, tx = "pending", None
            else:
                status, tx = "mock", None
            prize = db.record_battle_prize(
                live.id, pool, winner_registry, owner, pool,
                status=status, destination="owner", paid_tx=tx)
        else:
            # Unhired house fighter won: the prize stays in the treasury,
            # where the buyback bot can allocate it like any other fee.
            if pool > 0:
                db.record_fee_event(live.id, "battle_prize", pool)
            prize = db.record_battle_prize(
                live.id, pool, winner_registry, None, pool,
                status="paid" if payouts_live else "mock",
                destination="treasury")

    # Player notifications: hired-bot results go to the hirer, player-FT
    # results go to the owner. Unhired house bots notify nobody.
    try:
        hired_raw = db.kv_get(f"official_hired:{live.id}")
        hired_map = json.loads(hired_raw) if hired_raw else {}
    except (ValueError, TypeError):
        hired_map = {}
    if not result.get("draw") and winner_registry:
        for eng_id, reg_id in zip(engine_ids, registry_ids):
            fighter = db.get_fighter(reg_id) or {}
            name = fighter.get("name") or reg_id
            won = (eng_id == winner_engine)
            hirer = hired_map.get(reg_id)
            if hirer:
                if won:
                    db.create_notification(
                        hirer, "hire_win",
                        f"{name} won you {pool} SOL",
                        f"Your hired bot {name} won battle {live.id}. "
                        f"The {pool} SOL prize is yours.",
                        battle_id=live.id)
                else:
                    db.create_notification(
                        hirer, "hire_loss",
                        f"{name} was eliminated",
                        f"Your hired bot {name} lost battle {live.id}. "
                        f"Better luck next time.",
                        battle_id=live.id)
            elif fighter.get("owner_wallet"):
                owner = fighter["owner_wallet"]
                if won:
                    db.create_notification(
                        owner, "fighter_win",
                        f"{name} won {pool} SOL",
                        f"Your fighter {name} won battle {live.id}. "
                        f"The {pool} SOL prize is yours.",
                        battle_id=live.id)
                else:
                    db.create_notification(
                        owner, "fighter_loss",
                        f"{name} was eliminated",
                        f"Your fighter {name} lost battle {live.id}.",
                        battle_id=live.id)
    # Free the fighters for the next battle.
    for reg_id in registry_ids:
        fighter = db.get_fighter(reg_id)
        if fighter and fighter.get("owner_wallet"):
            if fighter.get("status") == "fighting":
                db.set_fighter_status(reg_id, "active")
    return {"prize": prize, "season_id": season_id}
