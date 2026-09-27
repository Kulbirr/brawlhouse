"""Phase 1 (FT economy): official battle scheduler.

A lightweight thread that wakes up every few seconds and starts the next
scheduled official battle when its slot arrives. Battles alternate
Duel/Royale (1v1 / 4-fighter free-for-all), at least
official_battle_interval_minutes apart, both admin-configurable.

The draw is longest-wait weighted random; short queues are filled with
house fighters. Entries are marked drawn only after the battle is
created, and unpaid (pending) entries are never drawn.
"""

from __future__ import annotations

import logging
import threading
import time

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import ft_economy  # noqa: E402

log = logging.getLogger("brawlhouse.scheduler")


class OfficialScheduler(threading.Thread):
    daemon = True

    def __init__(self, db, runner, settings, engine_cfg,
                 tick_seconds: float = 5.0):
        super().__init__(name="official-scheduler")
        self.db = db
        self.runner = runner
        self.settings = settings
        self.engine_cfg = engine_cfg
        self.tick_seconds = tick_seconds
        self._stop_event = threading.Event()
        # Observability: the run() loop used to swallow every tick
        # exception silently, which made a dead scheduler
        # indistinguishable from a healthy idle one (the public countdown
        # kept ticking while no battle ever started). The last tick's
        # timing, outcome, and error are now recorded and exposed via
        # status() for /health.
        self.last_tick_at: float | None = None
        self.last_outcome: str = "never_ticked"
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def _record_tick_error(self, exc: BaseException) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self.last_outcome = "error"
        log.exception("official scheduler tick failed")

    def run(self) -> None:
        first = True
        while not self._stop_event.is_set():
            try:
                # The first tick fires immediately so the arena comes alive
                # on boot instead of idling until the next slot boundary.
                self.tick(force=first)
            except Exception as exc:
                # The scheduler must never die on a bad tick, but it must
                # never go silent either.
                self._record_tick_error(exc)
            first = False
            self._stop_event.wait(self.tick_seconds)

    def tick(self, force: bool = False):
        """Start the slot's battle if due and none is live. Returns the
        LiveBattle or None. force=True is kept for tests and manual runs
        (the admin fallback endpoint); the live loop never needs it."""
        from backend import ft_economy

        self.last_tick_at = time.time()
        # Season progression rides on the scheduler: an expired season is
        # rolled over (standings paid out) before the next battle is drawn.
        try:
            ft_economy.ensure_active_season(self.db, self.settings)
        except Exception:
            pass  # never let season bookkeeping kill the battle loop
        interval = int(self.settings.get("official_battle_interval_minutes"))
        slot = max(1, interval) * 60
        now = time.time()
        slot_id = int(now) // slot
        if self.db.kv_get("official_last_slot") == str(slot_id):
            self.last_outcome = "skipped:slot_already_ran"
            return None  # already ran this slot
        if self.runner.any_live():
            self.last_outcome = "skipped:battle_live"
            return None  # wait for the live fight to finish
        # No boundary gate: once the slot's boundary has passed and its
        # battle hasn't run, the next tick starts it. A delayed tick
        # (thread jitter, a swallowed error on the boundary tick) used to
        # silently skip the whole slot. The queue countdown would hit 0
        # and nothing would happen. Now the battle just starts a few
        # seconds late instead.
        live = ft_economy.run_official_battle(
            self.db, self.runner, self.settings, self.engine_cfg)
        # Mark the slot only after the battle exists, so a failure here
        # retries on the next tick instead of swallowing the slot.
        self.db.kv_set("official_last_slot", str(slot_id))
        self.last_outcome = f"started:{live.id}"
        self.last_error = None  # a good tick clears a past error
        return live

    def status(self) -> dict:
        """Point-in-time scheduler diagnostics, exposed via /health."""
        try:
            last_slot = self.db.kv_get("official_last_slot")
        except Exception:
            last_slot = None
        try:
            battle_live = self.runner.any_live()
        except Exception:
            battle_live = None
        return {
            "thread_alive": self.is_alive(),
            "tick_seconds": self.tick_seconds,
            "last_tick_at": (
                datetime.fromtimestamp(
                    self.last_tick_at, tz=timezone.utc).isoformat()
                if self.last_tick_at else None
            ),
            "last_outcome": self.last_outcome,
            "last_error": self.last_error,
            "battle_live": battle_live,
            "official_last_slot": last_slot,
        }
