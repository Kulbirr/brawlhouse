"""Phase 3 FastAPI backend.

Run with:
    cd ~/workspace/agent-arena
    .venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000

Admin auth: shared secret in the X-Admin-Token header, value from ADMIN_TOKEN
in the gitignored .env file (see .env.example). Dry-run/mock by default:
hires are recorded with payment_status="mock", never charged.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect
from pydantic import BaseModel, Field

from arena import Config, FIGHTERS, HOUSE_BOT_IDS  # noqa: E402
from backend import ft_economy  # noqa: E402
from backend.battle_runner import BattleRunner  # noqa: E402
from backend.db import Database  # noqa: E402
from backend.official_scheduler import OfficialScheduler  # noqa: E402
from backend.settings_store import PRIVATE_FIELDS, SettingsStore  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ .env load
def load_dotenv(path: Path) -> None:
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


# ------------------------------------------------------------------- models
class CreateBattleBody(BaseModel):
    fighter_ids: list[str] = Field(..., min_length=2, max_length=8)
    seed: Optional[int] = None
    exhibition: bool = False
    playback_speed: float = Field(1.0, gt=0)


class HireBody(BaseModel):
    fighter_id: str
    wallet: str = Field(..., min_length=1, max_length=128)


class BetBody(BaseModel):
    fighter_id: str  # ENGINE id, e.g. "hawk-2" or "hawk-2#1"
    wallet: str = Field(default="", max_length=128)
    amount_sol: float  # validated manually -> 400 (not 422) on bad input


class HireConfirmBody(BaseModel):
    hire_id: int
    signature: str = Field(..., min_length=1, max_length=128)


class BetConfirmBody(BaseModel):
    bet_id: int
    signature: str = Field(..., min_length=1, max_length=128)


class ChatBody(BaseModel):
    wallet: str = Field(default="", max_length=128)
    message: str = Field(default="")  # validated manually -> 400 (not 422)


class FighterOverrideBody(BaseModel):
    name: Optional[str] = None
    tagline: Optional[str] = None
    description: Optional[str] = None


# Phase 1 (FT economy) request bodies.
class BornBody(BaseModel):
    wallet: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=64)
    archetype: str = Field(..., min_length=1, max_length=32)
    color: str = Field(..., min_length=1, max_length=16)


class BornConfirmBody(BaseModel):
    intent_id: int
    signature: str = Field(..., min_length=1, max_length=128)


class QueueEnterBody(BaseModel):
    fighter_id: str = Field(..., min_length=1, max_length=32)
    wallet: str = Field(..., min_length=1, max_length=128)


class QueueConfirmBody(BaseModel):
    entry_id: int
    signature: str = Field(..., min_length=1, max_length=128)


# ---------------------------------------------------------------- app factory
def create_app(data_dir: str | Path | None = None,
               env_path: str | Path | None = None,
               start_scheduler: bool = False) -> FastAPI:
    """Build the FastAPI app.

    start_scheduler=True launches the official-battle scheduler thread
    (production only). Tests create apps with it off so background
    battles never leak into test runs.
    """
    data_dir = Path(data_dir) if data_dir else PROJECT_ROOT / "data"
    load_dotenv(Path(env_path) if env_path else PROJECT_ROOT / ".env")

    settings = SettingsStore(data_dir / "settings.json")
    db = Database(data_dir / "arena.db")
    runner = BattleRunner(db, settings)
    for fid in HOUSE_BOT_IDS:
        db.ensure_fighter(fid)

    app = FastAPI(title="Agent Arena Backend (Phase 3)")
    app.state.settings = settings
    app.state.db = db
    app.state.runner = runner

    # ------------------------------------------------------------- admin auth
    def require_admin(x_admin_token: str | None = Header(None, alias="X-Admin-Token")):
        expected = os.environ.get("ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(503, "Admin not configured: set ADMIN_TOKEN in .env")
        if x_admin_token != expected:
            raise HTTPException(401, "Invalid admin token")
        return True

    # ---------------------------------------------------------------- helpers
    # ------------------------------------------- strength-based hire pricing
    # Per-fighter hire prices are NOT admin-set. Each fighter's price is
    # derived from its recorded win rate:
    #     price = base_fee * clamp(win_rate / avg_win_rate, 0.5, 2.0)
    # Fighters with no recorded fights (or when nobody has any history yet)
    # price at exactly the base fee. The admin only controls the overall
    # level via hire_fee_sol; the spread between fighters is automatic and
    # moves with real results. All five house fighters share identical
    # physics (same HP, same projectile damage), so win rate is the honest
    # measure of "how strong" a fighter is.
    def _win_rate(row: dict[str, Any]) -> float | None:
        total = row.get("wins", 0) + row.get("losses", 0) + row.get("draws", 0)
        if not total:
            return None
        return row.get("wins", 0) / total

    def hire_fee_for(fid: str) -> float:
        base = settings.get("hire_fee_sol")
        rates = {x: _win_rate(db.get_fighter(x) or {}) for x in HOUSE_BOT_IDS}
        known = [r for r in rates.values() if r is not None]
        avg = sum(known) / len(known) if known else 0.0
        mine = rates.get(fid)
        if mine is None or avg <= 0:
            mult = 1.0
        else:
            mult = min(2.0, max(0.5, mine / avg))
        return round(base * mult, 4)

    def fighter_view(fid: str) -> dict[str, Any]:
        reg = next((f for f in FIGHTERS if f["id"] == fid), None)
        if reg is None:
            raise HTTPException(404, f"Unknown fighter: {fid}")
        row = db.get_fighter(fid) or {}
        return {
            "id": fid,
            "name": row.get("name_override") or reg["name"],
            "tagline": row.get("tagline_override") or reg["tagline"],
            "description": row.get("description_override") or reg["description"],
            "wins": row.get("wins", 0),
            "losses": row.get("losses", 0),
            "draws": row.get("draws", 0),
            "hire_fee_sol": hire_fee_for(fid),
        }

    def battle_summary(rec: dict[str, Any]) -> dict[str, Any]:        return {
            "id": rec["id"],
            "created_at": rec["created_at"],
            "status": rec["status"],
            "seed": rec["seed"],
            "exhibition": bool(rec["exhibition"]),
            "official": bool(rec.get("official")),
            "mode": rec.get("mode"),
            "prize_pool_sol": rec.get("prize_pool_sol") or 0.0,
            "fighter_ids": json.loads(rec["fighter_ids"]),
            "registry_ids": json.loads(rec["registry_ids"]),
            "playback_speed": rec["playback_speed"],
            "hire_fee_sol": rec["hire_fee_sol"],
            "winner": rec["winner"],
            "reason": rec["reason"],
            "ticks": rec["ticks"],
            "duration_ms": rec["duration_ms"],
        }

    def battle_result(rec: dict[str, Any]) -> dict[str, Any]:
        return {
            "winner": rec["winner"],
            "draw": bool(rec["draw"]) if rec["draw"] is not None else None,
            "reason": rec["reason"],
            "ticks": rec["ticks"],
            "elimination_order": json.loads(rec["elimination_order"] or "[]"),
            "kills": json.loads(rec["kills"] or "{}"),
            "final_hp": json.loads(rec["final_hp"] or "{}"),
            "placement": json.loads(rec["placement"] or "{}"),
            "notes": json.loads(rec["notes"] or "[]"),
        }

    # ---------------------------------------------------------------- routes
    @app.get("/health")
    def health():
        return {"ok": True}

    # -------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        return settings.all(include_private=False)

    @app.put("/api/admin/settings", dependencies=[Depends(require_admin)])
    def update_settings(values: dict[str, Any]):
        if "treasury_wallet" in values:
            # Lazy import: payments_live never pulls in solana/solders at
            # module level, so mock mode stays chain-free.
            from backend.payments_live import is_valid_solana_pubkey
            tw = values["treasury_wallet"]
            if tw not in ("", None) and not is_valid_solana_pubkey(tw):
                raise HTTPException(
                    400, "treasury_wallet must be a valid Solana pubkey "
                         "(base58, 32 bytes) or empty to clear")
            if tw is None:
                values["treasury_wallet"] = ""
        if "max_bet_sol" in values:
            try:
                if float(values["max_bet_sol"]) <= 0:
                    raise HTTPException(
                        400, "max_bet_sol must be a positive number")
            except (TypeError, ValueError):
                raise HTTPException(
                    400, "max_bet_sol must be a positive number")
        # Phase 1 (FT economy) economics: keep every number sane.
        for key in ("born_fee_sol", "entry_fee_sol"):
            if key in values:
                try:
                    if float(values[key]) < 0:
                        raise HTTPException(
                            400, f"{key} must be zero or positive")
                except (TypeError, ValueError):
                    raise HTTPException(
                        400, f"{key} must be a number")
        for key in ("born_season_pct", "entry_prize_pct", "entry_season_pct",
                    "season_prize_1_pct", "season_prize_2_pct",
                    "season_prize_3_pct"):
            if key in values:
                try:
                    v = float(values[key])
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{key} must be a number")
                if not 0 <= v <= 100:
                    raise HTTPException(
                        400, f"{key} must be between 0 and 100")
        for key, lo, hi in (("official_battle_interval_minutes", 1, 1440),
                            ("season_length_days", 1, 365),
                            ("entry_window_minutes", 1, 1440),
                            ("betting_window_sec", 0, 3600)):
            if key in values:
                try:
                    v = int(values[key])
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{key} must be an integer")
                if not lo <= v <= hi:
                    raise HTTPException(
                        400, f"{key} must be between {lo} and {hi}")
        # Fee splits must add up: the combined new + stored values must
        # total 100 for each split, otherwise money appears or vanishes.
        def _combined(keys: tuple[str, ...]) -> float:
            return sum(float(values[k]) if k in values
                       else float(settings.get(k)) for k in keys)
        if abs(_combined(("entry_prize_pct", "entry_season_pct")) - 100) > 1e-9:
            raise HTTPException(
                400, "entry_prize_pct + entry_season_pct must equal 100")
        if abs(_combined(("season_prize_1_pct", "season_prize_2_pct",
                          "season_prize_3_pct")) - 100) > 1e-9:
            raise HTTPException(
                400, "season prize percentages must total 100")
        # The entry window must fit inside the battle interval.
        interval = int(values["official_battle_interval_minutes"]
                       if "official_battle_interval_minutes" in values
                       else settings.get("official_battle_interval_minutes"))
        window = int(values["entry_window_minutes"]
                     if "entry_window_minutes" in values
                     else settings.get("entry_window_minutes"))
        if window > interval:
            raise HTTPException(
                400, "entry_window_minutes cannot exceed "
                     "official_battle_interval_minutes")
        try:
            full = settings.update(values)
        except (KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc))
        return {
            "settings": full,
            "env_overridden": settings.env_overridden(),
        }

    # -------------------------------------------------------------- fighters
    @app.get("/api/fighters")
    def list_fighters():
        return {"fighters": [fighter_view(fid) for fid in HOUSE_BOT_IDS]}

    @app.get("/api/fighters/{fighter_id}")
    def get_fighter(fighter_id: str):
        return fighter_view(fighter_id)

    @app.put("/api/admin/fighters/{fighter_id}", dependencies=[Depends(require_admin)])
    def override_fighter(fighter_id: str, body: FighterOverrideBody):
        if fighter_id not in HOUSE_BOT_IDS:
            raise HTTPException(404, f"Unknown fighter: {fighter_id}")
        if not any([body.name, body.tagline, body.description]):
            raise HTTPException(400, "Provide at least one of name/tagline/description")
        db.set_fighter_overrides(fighter_id, body.name, body.tagline, body.description)
        return fighter_view(fighter_id)

    # --------------------------------------------------------------- battles
    @app.post("/api/battles", status_code=202)
    def create_battle(body: CreateBattleBody):
        # User-created battles are never official: only the scheduler may
        # start official, prize-paying battles (runner.create defaults
        # official=False). The exhibition flag keeps its V1 meaning and is
        # the user's choice, so hiring/betting/replay flows are preserved.
        try:
            engine_cfg = Config.from_env({"max_ticks": settings.get("fight_max_ticks")})
            live = runner.create(
                registry_ids=body.fighter_ids,
                seed=body.seed,
                exhibition=body.exhibition,
                playback_speed=body.playback_speed,
                engine_cfg=engine_cfg,
                hire_fee_sol=settings.get("hire_fee_sol"),
            )
        except KeyError as exc:
            raise HTTPException(400, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        rec = db.get_battle(live.id)
        assert rec is not None
        return battle_summary(rec)

    @app.get("/api/battles")
    def list_battles(
        status: str | None = Query(None, pattern="^(running|finished)$"),
        exhibition: bool | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return {
            "battles": [battle_summary(r)
                        for r in db.list_battles(status, exhibition, limit, offset)]
        }

    @app.get("/api/battles/{battle_id}")
    def get_battle(battle_id: str, snapshots: bool = Query(True)):
        rec = db.get_battle(battle_id)
        if rec is None:
            raise HTTPException(404, "Battle not found")
        out = battle_summary(rec)
        if rec["status"] == "finished":
            out["result"] = battle_result(rec)
        if snapshots and rec["status"] == "finished":
            out["snapshots"] = db.get_snapshots(battle_id)
        return out

    # ----------------------------------------------------------------- hires
    @app.post("/api/battles/{battle_id}/hire", status_code=201)
    def hire_fighter(battle_id: str, body: HireBody):
        rec = db.get_battle(battle_id)
        if rec is None:
            raise HTTPException(404, "Battle not found")
        engine_ids = json.loads(rec["fighter_ids"])
        registry_ids = json.loads(rec["registry_ids"])
        if body.fighter_id in engine_ids:
            engine_id = body.fighter_id
            reg_id = registry_ids[engine_ids.index(body.fighter_id)]
        elif body.fighter_id in registry_ids:
            idx = registry_ids.index(body.fighter_id)
            engine_id = engine_ids[idx]
            reg_id = body.fighter_id
        else:
            raise HTTPException(400, f"Fighter {body.fighter_id!r} not in this battle")
        wallet = body.wallet.strip()
        # Strength-based pricing: this fighter's fee comes from its win rate,
        # not from the admin panel.
        fee_sol = hire_fee_for(reg_id)

        if settings.get("hiring_live"):
            # Live path: non-custodial user-signed payment. The server
            # builds an UNSIGNED transfer (wallet -> treasury_wallet);
            # the frontend has the wallet sign it, then POSTs the
            # signature to /hire/confirm. Imported lazily so mock mode
            # never needs solana/solders.
            from backend import payments_live
            try:
                hire = payments_live.initiate_hire(
                    db, settings, battle_id, engine_id, wallet, fee_sol)
            except payments_live.PaymentNotConfigured as exc:
                raise HTTPException(503, str(exc))
            except payments_live.RpcError as exc:
                raise HTTPException(503, str(exc))
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            if hire is None:
                raise HTTPException(409, "Wallet already hired in this battle")
            return {
                "hire_id": hire["id"],
                "payment_status": hire["payment_status"],
                "transaction_base64": hire["unsigned_tx_base64"],
                "wallet": hire["wallet"],
                "treasury_wallet": hire["to"],
                "amount_sol": hire["fee_sol"],
            }

        hire = db.create_hire(battle_id, engine_id, wallet, fee_sol)
        if hire is None:
            raise HTTPException(409, "Wallet already hired in this battle")
        # Phase 5: every fee lands in the treasury ledger for the buyback bot.
        db.record_fee_event(battle_id, "hire", hire["fee_sol"])
        return hire

    @app.post("/api/battles/{battle_id}/hire/confirm", status_code=200)
    def confirm_hire(battle_id: str, body: HireConfirmBody):
        """Live hire step 2: verify the user's signed payment onchain.

        Only 'pending' hires (created by POST /hire with hiring_live on)
        can be confirmed. On success the hire is marked paid and the fee
        lands in the treasury ledger.
        """
        hire = db.get_hire(body.hire_id)
        if hire is None or hire["battle_id"] != battle_id:
            raise HTTPException(404, "Hire not found")
        if hire["payment_status"] != "pending":
            raise HTTPException(
                409, f"Hire is already {hire['payment_status']}")
        from backend import payments_live
        try:
            row = payments_live.confirm_hire_payment(
                db, settings, hire, body.signature.strip())
        except payments_live.PaymentNotConfigured as exc:
            raise HTTPException(503, str(exc))
        except payments_live.RpcError as exc:
            raise HTTPException(503, str(exc))
        except payments_live.ReplayDetected as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        db.record_fee_event(battle_id, "hire", row["fee_sol"])
        return row

    @app.get("/api/battles/{battle_id}/hires")
    def list_hires(battle_id: str):
        if db.get_battle(battle_id) is None:
            raise HTTPException(404, "Battle not found")
        return {"hires": db.list_hires(battle_id)}

    # ---------------------------------------------------------------- betting
    # Phase 5 parimutuel engine. Contract matches the Phase 4 BetsAPI
    # adapter exactly (see the header comment of frontend/app.js). Note:
    # the Phase 4 sketch said 403 for exhibition battles; the Phase 5
    # spec standardizes bet refusals on 400, which the adapter treats the
    # same way (any error surfaces as the bet error message).
    @app.post("/api/battles/{battle_id}/bets", status_code=201)
    def place_bet(battle_id: str, body: BetBody):
        rec = db.get_battle(battle_id)
        if rec is None:
            raise HTTPException(404, "Battle not found")
        if rec["exhibition"]:
            raise HTTPException(400,
                                "Betting is not allowed on exhibition battles")
        if rec["status"] == "finished":
            raise HTTPException(400, "Battle already finished; betting is closed")
        engine_ids = json.loads(rec["fighter_ids"])
        if body.fighter_id not in engine_ids:
            raise HTTPException(
                400, f"Fighter {body.fighter_id!r} not in this battle")
        wallet = (body.wallet or "").strip()
        if not wallet:
            raise HTTPException(400, "wallet is required")
        amount = body.amount_sol
        if not isinstance(amount, (int, float)) or not (amount > 0) \
                or amount in (float("inf"), float("-inf")):
            raise HTTPException(400, "amount_sol must be a positive number")
        max_bet = settings.get("max_bet_sol")
        if amount > max_bet:
            raise HTTPException(
                400, f"amount_sol exceeds the max bet of {max_bet} SOL")

        if settings.get("betting_live"):
            # Live path: non-custodial user-signed payment, same model as
            # hires. The server builds an UNSIGNED transfer
            # (wallet -> treasury_wallet); the frontend has the wallet
            # sign it, then POSTs the signature to /bets/confirm, which
            # is what credits the bet into the parimutuel pool.
            # Imported lazily so mock mode never needs solana/solders.
            from backend import payments_live
            try:
                bet = payments_live.initiate_bet(
                    db, settings, battle_id, body.fighter_id, wallet, amount)
            except payments_live.PaymentNotConfigured as exc:
                raise HTTPException(503, str(exc))
            except payments_live.RpcError as exc:
                raise HTTPException(503, str(exc))
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return {
                "bet_id": bet["id"],
                "payment_status": bet["payment_status"],
                "transaction_base64": bet["unsigned_tx_base64"],
                "wallet": bet["wallet"],
                "treasury_wallet": bet["to"],
                "amount_sol": bet["amount_sol"],
            }

        return db.create_bet(battle_id, body.fighter_id, wallet, amount,
                             "mock")

    @app.post("/api/battles/{battle_id}/bets/confirm", status_code=200)
    def confirm_bet(battle_id: str, body: BetConfirmBody):
        """Live bet step 2: verify the user's signed payment onchain.

        Only 'pending' bets (created by POST /bets with betting_live on)
        can be confirmed, and only while the battle is still running —
        confirming is what credits the bet into the parimutuel pool.
        """
        bet = db.get_bet(body.bet_id)
        if bet is None or bet["battle_id"] != battle_id:
            raise HTTPException(404, "Bet not found")
        if bet["payment_status"] != "pending":
            raise HTTPException(
                409, f"Bet is already {bet['payment_status']}")
        rec = db.get_battle(battle_id)
        if rec is None:
            raise HTTPException(404, "Battle not found")
        if rec["status"] == "finished" or rec["exhibition"]:
            raise HTTPException(
                400, "Betting is closed for this battle; the transfer "
                     "is visible onchain — contact the team for a refund")
        from backend import payments_live
        try:
            return payments_live.confirm_bet_payment(
                db, settings, bet, body.signature.strip())
        except payments_live.PaymentNotConfigured as exc:
            raise HTTPException(503, str(exc))
        except payments_live.RpcError as exc:
            raise HTTPException(503, str(exc))
        except payments_live.ReplayDetected as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/battles/{battle_id}/bets")
    def list_bets(battle_id: str, wallet: str | None = Query(None)):
        if db.get_battle(battle_id) is None:
            raise HTTPException(404, "Battle not found")
        return {"bets": db.list_bets(battle_id, wallet=wallet)}

    @app.get("/api/battles/{battle_id}/pool")
    def battle_pool(battle_id: str):
        rec = db.get_battle(battle_id)
        if rec is None:
            raise HTTPException(404, "Battle not found")
        engine_ids = json.loads(rec["fighter_ids"])
        pools = {fid: 0.0 for fid in engine_ids}
        total = 0.0
        count = 0
        # Only counted bets move the odds: 'pending' (unpaid) bets are
        # listed by /bets but excluded here until /bets/confirm verifies
        # the onchain payment.
        for bet in db.list_bets(battle_id,
                                payment_statuses=("mock", "confirmed")):
            pools[bet["fighter_id"]] = round(
                pools.get(bet["fighter_id"], 0.0) + bet["amount_sol"], 9)
            total = round(total + bet["amount_sol"], 9)
            count += 1
        return {
            "battle_id": battle_id,
            "pools": pools,
            "total_sol": total,
            "bet_count": count,
            # read live from settings on every call
            "house_cut_pct": settings.get("betting_house_cut_pct"),
        }

    # ------------------------------------------------------------ arena chat
    def _clean_chat_message(raw: str) -> str:
        """Strip HTML tags (XSS) and collapse whitespace."""
        import re
        no_tags = re.sub(r"<[^>]*>", "", raw)
        return re.sub(r"\s+", " ", no_tags).strip()

    @app.post("/api/battles/{battle_id}/chat", status_code=201)
    def post_chat(battle_id: str, body: ChatBody):
        if db.get_battle(battle_id) is None:
            raise HTTPException(404, "Battle not found")
        wallet = (body.wallet or "").strip()
        if not wallet:
            raise HTTPException(400, "wallet is required")
        raw = body.message or ""
        if not raw.strip():
            raise HTTPException(400, "message must not be empty")
        if len(raw) > 200:
            raise HTTPException(400, "message must be at most 200 characters")
        text = _clean_chat_message(raw)
        if not text:
            raise HTTPException(400, "message must not be empty")
        if db.chat_rate_limited(battle_id, wallet):
            raise HTTPException(
                429, "Slow down: one message per 2 seconds per battle")
        return db.add_chat_message(battle_id, wallet, text)

    @app.get("/api/battles/{battle_id}/chat")
    def get_chat(battle_id: str, limit: int = Query(50, ge=1, le=200)):
        if db.get_battle(battle_id) is None:
            raise HTTPException(404, "Battle not found")
        return {"messages": db.list_chat_messages(battle_id, limit=limit)}

    # ------------------------------------------------- FT economy (Phase 1)
    # Fighters are BORN here, not minted. Born fee: born_fee_sol, split
    # born_season_pct% to the season pool and the rest to the treasury.
    def ft_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row.get("name_override"),
            "owner_wallet": row.get("owner_wallet"),
            "archetype": row.get("archetype"),
            "colors": json.loads(row.get("colors") or "{}"),
            "rarity": row.get("rarity"),
            "stat_mult": row.get("stat_mult"),
            "owner_cert": row.get("owner_cert"),
            "status": row.get("status"),
            "born_at": row.get("born_at"),
            "wins": row.get("wins", 0),
            "losses": row.get("losses", 0),
            "draws": row.get("draws", 0),
        }

    @app.post("/api/ft/born/initiate", status_code=201)
    def born_initiate(body: BornBody):
        """Born a fighter. Mock mode (born_live off): the FT is created
        immediately. Live mode: returns the birth intent plus an unsigned
        payment tx for the wallet to sign, then POST /confirm."""
        from backend import payments_live

        if not bool(settings.get("born_live")):
            try:
                row, info = ft_economy.born_fighter(
                    db, settings, body.wallet, body.name, body.archetype,
                    body.color, data_dir=data_dir)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return {"payment": "mock", "fighter": ft_view(row), **info}
        try:
            fee = float(settings.get("born_fee_sol"))
            built = payments_live.initiate_born(
                db, settings, body.wallet, body.name, body.archetype,
                body.color, fee)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except payments_live.PaymentNotConfigured as exc:
            raise HTTPException(503, str(exc))
        except payments_live.RpcError as exc:
            raise HTTPException(503, str(exc))
        return {
            "payment": "live",
            "intent_id": built["id"],
            "payment_status": built["payment_status"],
            "transaction_base64": built["unsigned_tx_base64"],
            "wallet": built["wallet"],
            "treasury_wallet": built["to"],
            "amount_sol": built["fee_sol"],
        }

    @app.post("/api/ft/born/confirm", status_code=201)
    def born_confirm(body: BornConfirmBody):
        """Live-mode born step 2: verify the birth payment, then create
        the FT (rarity is rolled only here, at payment)."""
        from backend import payments_live

        intent = db.get_birth_intent(body.intent_id)
        if intent is None:
            raise HTTPException(404, "Birth intent not found")
        if intent["payment_status"] != "pending":
            raise HTTPException(409, "Birth intent is not pending")
        try:
            payments_live.confirm_born_payment(
                db, settings, intent, body.signature)
        except payments_live.ReplayDetected as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except (payments_live.PaymentNotConfigured,
                payments_live.RpcError) as exc:
            raise HTTPException(503, str(exc))
        row, info = ft_economy.finalize_birth_intent(
            db, settings, intent, data_dir=data_dir)
        return {"payment": "live", "fighter": ft_view(row), **info}

    @app.get("/api/ft/registry")
    def ft_registry(limit: int = Query(50, ge=1, le=200),
                    offset: int = Query(0, ge=0)):
        rows = db.list_player_fts(limit=limit, offset=offset)
        return {"fighters": [ft_view(r) for r in rows], "count": db.ft_count()}

    @app.get("/api/ft/fighters/{fighter_id}")
    def ft_detail(fighter_id: str):
        row = db.get_fighter(fighter_id)
        if row is None or not row.get("owner_wallet"):
            raise HTTPException(404, "FT not found")
        return ft_view(row)

    @app.get("/api/ft/wallet/{wallet}/fighters")
    def ft_wallet_fighters(wallet: str):
        return {"fighters": [ft_view(r) for r in
                             db.list_wallet_fighters(wallet)]}

    @app.get("/api/ft/births")
    def ft_births(limit: int = Query(25, ge=1, le=100)):
        return {"births": db.recent_births(limit)}

    # ------------------------------------------------------- entry queue
    @app.get("/api/ft/next-battle")
    def ft_next_battle():
        return ft_economy.next_battle_info(db, settings)

    @app.get("/api/ft/queue")
    def ft_queue():
        entries = db.list_queued_entries()
        out = []
        for e in entries:
            f = db.get_fighter(e["fighter_id"]) or {}
            out.append({
                "id": e["id"],
                "fighter_id": e["fighter_id"],
                "fighter_name": f.get("name_override"),
                "owner_wallet": e["owner_wallet"],
                "entered_at": e["entered_at"],
                "entry_fee_sol": e["entry_fee_sol"],
            })
        return {"queued": out, "count": len(out),
                "next_battle": ft_economy.next_battle_info(db, settings)}

    @app.post("/api/ft/queue/enter", status_code=201)
    def queue_enter(body: QueueEnterBody):
        """Enter an owned FT into the next official battle. Mock mode
        (entry_live off): queued immediately. Live mode: returns the entry
        plus an unsigned payment tx, then POST /confirm."""
        from backend import payments_live

        if not bool(settings.get("entry_live")):
            try:
                entry = ft_economy.enter_queue(db, settings, body.fighter_id,
                                               body.wallet)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return {"payment": "mock", "entry": entry}
        try:
            ft_economy.check_entry_window(settings)
            row = db.get_fighter(body.fighter_id)
            if row is None or not row.get("owner_wallet"):
                raise HTTPException(404, "FT not found")
            if row["owner_wallet"] != body.wallet:
                raise HTTPException(400, "Only the owner can enter this fighter")
            if row.get("status") != "active":
                raise HTTPException(
                    400, f"Fighter is {row.get('status')}, not available")
            if ft_economy.queue_entry_exists(db, body.fighter_id):
                raise HTTPException(
                    400, "Fighter is already queued for the next battle")
            fee = float(settings.get("entry_fee_sol"))
            prize_share, _ = ft_economy._entry_splits(settings, fee)
            built = payments_live.initiate_entry(
                db, settings, body.fighter_id, body.wallet, fee, prize_share)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except payments_live.PaymentNotConfigured as exc:
            raise HTTPException(503, str(exc))
        except payments_live.RpcError as exc:
            raise HTTPException(503, str(exc))
        return {
            "payment": "live",
            "entry_id": built["id"],
            "payment_status": built["payment_status"],
            "transaction_base64": built["unsigned_tx_base64"],
            "wallet": built["owner_wallet"],
            "treasury_wallet": built["to"],
            "amount_sol": built["entry_fee_sol"],
        }

    @app.post("/api/ft/queue/confirm", status_code=200)
    def queue_confirm(body: QueueConfirmBody):
        """Live-mode entry step 2: verify the entry payment, then queue."""
        from backend import payments_live

        entry = db.get_queue_entry(body.entry_id)
        if entry is None:
            raise HTTPException(404, "Queue entry not found")
        if entry["payment_status"] != "pending":
            raise HTTPException(409, "Entry is not pending")
        try:
            entry = payments_live.confirm_entry_payment(
                db, settings, entry, body.signature)
        except payments_live.ReplayDetected as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except (payments_live.PaymentNotConfigured,
                payments_live.RpcError) as exc:
            raise HTTPException(503, str(exc))
        entry = ft_economy.activate_queue_entry(db, settings, entry)
        return {"payment": "live", "entry": entry}

    # ------------------------------------------------------------- season
    @app.get("/api/ft/season")
    def ft_season():
        season = ft_economy.ensure_active_season(db, settings)
        board = db.season_leaderboard(season["id"], limit=25)
        leaders = []
        for rank, row in enumerate(board, start=1):
            f = db.get_fighter(row["fighter_id"]) or {}
            leaders.append({
                "rank": rank,
                "fighter_id": row["fighter_id"],
                "name": f.get("name_override"),
                "owner_wallet": f.get("owner_wallet"),
                "rarity": f.get("rarity"),
                "wins": row["wins"],
                "losses": row["losses"],
                "draws": row["draws"],
                "points": row["points"],
            })
        return {
            "season": season,
            "season_length_days": int(settings.get("season_length_days")),
            "prize_split_pct": [float(settings.get("season_prize_1_pct")),
                                float(settings.get("season_prize_2_pct")),
                                float(settings.get("season_prize_3_pct"))],
            "leaderboard": leaders,
            "recent_payouts": db.recent_season_payouts(limit=20),
        }

    @app.get("/api/ft/season/payouts")
    def ft_season_payouts(limit: int = Query(50, ge=1, le=200)):
        return {"payouts": db.recent_season_payouts(limit)}

    # -------------------------------------------------------- treasury (ph5)
    @app.get("/api/treasury/fees", dependencies=[Depends(require_admin)])
    def treasury_fees(limit: int = Query(100, ge=1, le=1000)):
        """Fee ledger for the phase 6 buyback bot: every settled betting
        house cut (source='betting') and every hire fee (source='hire')."""
        return {
            "totals": db.fee_totals(),
            "events": db.list_fee_events(limit),
        }

    # ---------------------------------------- treasury (ph6, PUBLIC)
    @app.get("/api/treasury/stats")
    def treasury_stats():
        """Public treasury dashboard numbers.

        treasury_balance_sol is ledger-based: fees taken but not yet
        allocated to a buyback round. Never exposes buyback_enabled or any
        other in-house field.
        """
        totals = db.fee_totals()
        burned = db.burn_totals()
        return {
            "treasury_balance_sol": db.unallocated_fees_total(),
            "total_fees_sol": totals["total_sol"],
            "total_burned_tokens": burned["total_burned_tokens"],
            "burn_count": burned["burn_count"],
            "token_ticker": settings.get("token_ticker"),
            "project_name": settings.get("project_name"),
        }

    @app.get("/api/treasury/burns")
    def treasury_burns(limit: int = Query(25, ge=1, le=200),
                       offset: int = Query(0, ge=0)):
        """Public paginated burn history, newest first."""
        return {
            "burns": db.list_burn_events(limit, offset),
            "total": db.burn_totals()["burn_count"],
        }

    # ------------------------------------------------------------- websocket
    async def _close_ws_quietly(ws: WebSocket) -> None:
        # A viewer closing the page mid-stream already tore the connection
        # down; closing again raises RuntimeError. Swallow it so disconnects
        # don't spam the logs with tracebacks.
        try:
            await ws.close()
        except RuntimeError:
            pass

    @app.websocket("/ws/battles/{battle_id}")
    async def battle_ws(ws: WebSocket, battle_id: str):
        rec = db.get_battle(battle_id)
        if rec is None:
            await ws.close(code=4404)
            return
        pacing = settings.get("playback_tick_ms") / 1000.0 / max(rec["playback_speed"], 0.01)

        # Chat broadcast: each connection polls for new chat messages and
        # forwards them as {"type": "chat", ...} so open viewers update
        # live. last_chat_id starts at the current max so a fresh viewer
        # doesn't get the history it already fetched via GET /chat re-sent.
        last_chat_id = db.max_chat_id(battle_id)
        last_chat_poll = 0.0

        async def _drain_chat() -> None:
            nonlocal last_chat_id, last_chat_poll
            now = asyncio.get_event_loop().time()
            if now - last_chat_poll < 0.5:
                return
            last_chat_poll = now
            for m in db.list_chat_messages(battle_id, since_id=last_chat_id):
                last_chat_id = m["id"]
                await ws.send_json({"type": "chat", "id": m["id"],
                                    "battle_id": battle_id,
                                    "wallet": m["wallet"],
                                    "message": m["message"],
                                    "created_at": m["created_at"]})

        async def _chat_tail() -> None:
            # After the battle is over the viewer stays on the page: keep
            # draining chat until they disconnect. The receive() with a
            # timeout both detects the disconnect (raises
            # WebSocketDisconnect) and avoids leaking the socket when the
            # viewer just idles with no new messages.
            while True:
                await _drain_chat()
                try:
                    await asyncio.wait_for(ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                except RuntimeError:
                    # Starlette's TestClient surfaces "disconnect already
                    # received" as RuntimeError instead of
                    # WebSocketDisconnect when a timed-out receive raced
                    # the close. Either way the viewer is gone.
                    break

        live = runner.get_live(battle_id)
        if live is not None and not live.finished.is_set():
            # Live stream: send current tick snapshot, then follow as they arrive.
            await ws.accept()
            await ws.send_json({"type": "info", "status": "live",
                                "battle_id": battle_id,
                                "battle_status": rec["status"],
                                "fighter_ids": live.engine_ids})
            idx = 0
            try:
                while True:
                    snaps = live.snapshots
                    while idx < len(snaps):
                        # EXACT Phase 1 snapshot fields (tick/fighters/projectiles)
                        # plus a "type" envelope the frontend can key on.
                        await ws.send_json({"type": "snapshot", **snaps[idx]})
                        idx += 1
                        await asyncio.sleep(pacing)
                    await _drain_chat()
                    if live.finished.is_set():
                        break
                    await asyncio.sleep(0.02)
                await ws.send_json({"type": "done", "result": live.result})
                # Keep the socket open for post-battle chat until the viewer
                # leaves; _chat_tail raises WebSocketDisconnect on close.
                await _chat_tail()
            except WebSocketDisconnect:
                pass
            finally:
                await _close_ws_quietly(ws)
            return

        # Finished (or never-started) battle: replay stored snapshots, then done.
        if rec["status"] != "finished":
            await ws.close(code=4404)
            return
        await ws.accept()
        try:
            await ws.send_json({"type": "info", "status": "replay",
                                "battle_id": battle_id,
                                "fighter_ids": json.loads(rec["fighter_ids"])})
            for snap in db.get_snapshots(battle_id):
                await ws.send_json({"type": "snapshot", **snap})
                await _drain_chat()
                await asyncio.sleep(pacing)
            await ws.send_json({"type": "done", "result": battle_result(rec)})
            # Keep the socket open for post-battle chat until the viewer
            # leaves; _chat_tail raises WebSocketDisconnect on close.
            await _chat_tail()
        except WebSocketDisconnect:
            pass
        finally:
            await _close_ws_quietly(ws)

    # --------------------------------------------------- frontend (Phase 4)
    # Mount AFTER all API/WS routes so they take precedence: one server
    # serves both the API and the static site. html=True serves index.html
    # at "/" and for unknown non-API paths.
    app.mount("/", StaticFiles(directory=PROJECT_ROOT / "web" / "dist", html=True),
              name="frontend")

    if start_scheduler:
        # Phase 1: official battles run on a schedule in production.
        engine_cfg = Config.from_env({"max_ticks": settings.get("fight_max_ticks")})
        scheduler = OfficialScheduler(db, runner, settings, engine_cfg)
        scheduler.start()
        app.state.scheduler = scheduler

    return app


# Module import must stay side-effect-light: the official-battle scheduler
# starts by default (the arena comes alive on boot) and is disabled by
# setting ARENA_OFFICIAL_SCHEDULER=0 in the environment or .env. Tests and
# plain imports get the app without background battles.
load_dotenv(PROJECT_ROOT / ".env")
app = create_app(
    start_scheduler=os.environ.get("ARENA_OFFICIAL_SCHEDULER", "1") == "1")
