"""Battle lifecycle: run engine battles in background threads.

A LiveBattle keeps the Battle object, its per-tick snapshots, and completion
state in memory so the websocket can stream live. On completion the result,
snapshots, and fighter records are persisted to SQLite and the live entry is
dropped (later connects get a DB replay).
"""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena import (  # noqa: E402
    Battle, Config, make_house_bot, archetype_bot_class, HOUSE_BOT_IDS,
)
from backend.db import Database  # noqa: E402


@dataclass
class LiveBattle:
    id: str
    battle: Battle
    engine_ids: list[str]
    registry_ids: list[str]
    snapshots: list[dict] = field(default_factory=list)
    finished: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    duration_ms: int = 0
    error: str | None = None
    # Phase 1 (FT economy): scheduler-run official battles carry a prize.
    official: bool = False
    mode: str | None = None  # 'duel' | 'royale' for official battles
    # Betting window: monotonic deadline before which the engine must not
    # start, so spectators get a real window to place bets. None = run now.
    betting_deadline: float | None = None


class BattleRunner:
    def __init__(self, db: Database, settings=None):
        self.db = db
        # Phase 5: the settings store supplies betting_house_cut_pct and
        # betting_live for settlement. None = no settlement (tests that
        # don't care about betting).
        self.settings = settings
        self._lock = threading.Lock()
        self._live: dict[str, LiveBattle] = {}

    # ---------------------------------------------------------------- create
    def _resolve_bot(self, registry_id: str, seed: int, index: int):
        """(bot, stat_mult) for a registry id: house fighter or born FT.

        House fighters resolve from the code registry at 1.0x; FTs resolve
        from the DB by archetype with their rarity multiplier. Anything
        else raises KeyError, same as before.
        """
        try:
            return make_house_bot(registry_id, seed, index), 1.0
        except KeyError:
            pass
        row = self.db.get_fighter(registry_id)
        if row is None or not row.get("owner_wallet"):
            raise KeyError(
                f"Unknown fighter: {registry_id!r}. Available: {HOUSE_BOT_IDS}")
        bot_class = archetype_bot_class(row["archetype"])
        mult = float(row.get("stat_mult") or 1.0)
        return bot_class(seed=seed * 1000 + index + 7), mult

    def create(self, registry_ids: list[str], seed: int | None,
               exhibition: bool, playback_speed: float,
               engine_cfg: Config, hire_fee_sol: float,
               official: bool = False, mode: str | None = None,
               prize_pool_sol: float = 0.0,
               stat_mults: dict[str, float] | None = None) -> LiveBattle:
        if not 2 <= len(registry_ids) <= 8:
            raise ValueError("Need 2-8 fighters")
        if official and mode not in ("duel", "royale"):
            raise ValueError("Official battles need mode 'duel' or 'royale'")
        if official and exhibition:
            raise ValueError("Official battles are never exhibitions")

        seed = random.randrange(2**31) if seed is None else int(seed)
        unique = len(set(registry_ids)) == len(registry_ids)
        pairs: list[tuple[str, Any]] = []
        engine_ids: list[str] = []
        mults: dict[str, float] = dict(stat_mults or {})
        for i, fid in enumerate(registry_ids):
            engine_id = fid if unique else f"{fid}#{i + 1}"
            bot, mult = self._resolve_bot(fid, seed, i)
            pairs.append((engine_id, bot))
            engine_ids.append(engine_id)
            mults.setdefault(engine_id, mult)

        # The battle's seed must drive the ENGINE's rng too (spawn geometry),
        # not just the bots: otherwise POST /api/battles {"seed": N} is not
        # reproducible. Copy the caller's config with the resolved seed so a
        # given (fighters, seed) always replays the identical fight.
        engine_cfg = replace(engine_cfg, seed=seed)
        battle = Battle(pairs, cfg=engine_cfg, stat_mults=mults)
        live = LiveBattle(
            id=f"battle-{uuid.uuid4().hex[:12]}",
            battle=battle,
            engine_ids=engine_ids,
            registry_ids=list(registry_ids),
            official=official,
            mode=mode,
        )
        # Betting window: the battle sits in 'open' status so spectators can
        # bet before the engine runs (it completes in milliseconds, so
        # without the wait there would be no usable betting window).
        try:
            window = int((self.settings.get("betting_window_sec")
                          if self.settings else 60) or 0)
        except (TypeError, ValueError):
            window = 60
        if window > 0:
            live.betting_deadline = time.monotonic() + window
        rec = {
            "id": live.id,
            "created_at": _utcnow(),
            "status": "open" if live.betting_deadline else "running",
            "seed": seed,
            "exhibition": 1 if exhibition else 0,
            "fighter_ids": json.dumps(engine_ids),
            "registry_ids": json.dumps(registry_ids),
            "playback_speed": playback_speed,
            "hire_fee_sol": hire_fee_sol,
            "official": 1 if official else 0,
            "mode": mode,
            "prize_pool_sol": prize_pool_sol,
        }
        self.db.create_battle(rec)
        with self._lock:
            self._live[live.id] = live
        t = threading.Thread(target=self._run, args=(live,), daemon=True,
                             name=f"battle-{live.id}")
        t.start()
        return live

    # ------------------------------------------------------------------- run
    def _run(self, live: LiveBattle) -> None:
        # Honor the betting window: wait for the deadline before the engine
        # starts. Sleeps in short chunks so a shutdown is never stuck long.
        if live.betting_deadline is not None:
            while True:
                remaining = live.betting_deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.5))
        self.db.mark_started(live.id)
        start = time.monotonic()
        try:
            b = live.battle
            live.snapshots.append(b.snapshot())  # tick 0
            while not b.is_over():
                b.step()
                live.snapshots.append(b.snapshot())
            live.result = b.result()
            live.duration_ms = int((time.monotonic() - start) * 1000)
            placement = self._placement(live)
            self.db.finish_battle(live.id, live.result, placement, live.duration_ms)
            self.db.save_snapshots(live.id, live.snapshots)
            self._update_records(live)
            self._settle_bets(live)  # Phase 5: parimutuel settlement
            self._settle_official(live)  # Phase 1: FT prize + standings
        except Exception as exc:  # never leave a battle stuck in 'running'
            live.error = f"{type(exc).__name__}: {exc}"
            live.result = {
                "winner": None, "draw": True, "reason": "backend_error",
                "ticks": live.battle.tick,
                "elimination_order": list(live.battle.elimination_order),
                "kills": {}, "final_hp": {}, "notes": [live.error],
            }
            live.duration_ms = int((time.monotonic() - start) * 1000)
            self.db.finish_battle(live.id, live.result, {}, live.duration_ms)
            self.db.save_snapshots(live.id, live.snapshots)
            # Backend error -> result is a draw, so bettors get full refunds.
            self._settle_bets(live)
        finally:
            live.finished.set()
            with self._lock:
                self._live.pop(live.id, None)

    # ----------------------------------------------------------------- query
    def get_live(self, battle_id: str) -> LiveBattle | None:
        with self._lock:
            return self._live.get(battle_id)

    def any_live(self) -> bool:
        """True while any battle is still running (used by the official
        scheduler so it never starts a second battle on top of a live one)."""
        with self._lock:
            return any(not live.finished.is_set()
                       for live in self._live.values())

    # ---------------------------------------------------------------- records
    @staticmethod
    def _placement(live: LiveBattle) -> dict[str, int]:
        """Winner -> 1; eliminated fighters ranked by elimination order."""
        if live.result is None:
            return {}
        n = len(live.engine_ids)
        placement: dict[str, int] = {}
        for i, fid in enumerate(live.result["elimination_order"]):
            placement[fid] = n - i  # first eliminated places last
        winner = live.result["winner"]
        if winner:
            placement[winner] = 1
        else:  # draw: everyone still standing shares placement 1
            for fid in live.engine_ids:
                placement.setdefault(fid, 1)
        return placement

    def _update_records(self, live: LiveBattle) -> None:
        if live.result is None:
            return
        winner = live.result["winner"]
        draw = live.result["draw"]
        for engine_id, registry_id in zip(live.engine_ids, live.registry_ids):
            if draw:
                outcome = "draw"
            elif engine_id == winner:
                outcome = "win"
            else:
                outcome = "loss"
            self.db.record_result(registry_id, outcome)

    # ----------------------------------------------------------- settlement
    def _settle_official(self, live: LiveBattle) -> None:
        """Phase 1: official battles pay the winner's owner and update the
        season standings. Contained like _settle_bets: settlement must
        never break battle completion."""
        if not live.official:
            return
        try:
            from backend import ft_economy

            ft_economy.settle_official_battle(self.db, self.settings, live)
        except Exception:
            pass

    def _settle_bets(self, live: LiveBattle) -> None:
        """Phase 5: settle the parimutuel pool when a battle finishes.

        Settlement must never break battle completion, so any error is
        contained: it is recorded as a 'settlement_error' settlement row
        instead of propagating (the _run handler would otherwise overwrite
        a good result with backend_error).
        """
        if live.result is None or self.settings is None:
            return
        try:
            from backend.betting import settle_battle

            settle_battle(
                self.db,
                live.id,
                live.result,
                house_cut_pct=self.settings.get("betting_house_cut_pct"),
                betting_live=bool(self.settings.get("betting_live")),
            )
        except Exception:
            try:
                self.db.record_settlement(
                    live.id, live.result.get("winner"),
                    bool(live.result.get("draw")), "settlement_error",
                    0.0, 0.0, 0.0, 0)
            except Exception:
                pass


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
