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

import threading
import time

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import ft_economy  # noqa: E402


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

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                pass  # the scheduler must never die on a bad tick
            self._stop_event.wait(self.tick_seconds)

    def tick(self, force: bool = False):
        """Start the slot's battle if due and none is live. Returns the
        LiveBattle or None. force=True bypasses the slot-boundary gate
        (used by tests and manual runs, never by the live loop)."""
        from backend import ft_economy

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
            return None  # already ran this slot
        if self.runner.any_live():
            return None  # wait for the live fight to finish
        # Fire only near a slot boundary. On startup (or after a long
        # pause) this skips the current slot instead of starting a battle
        # immediately, so the public countdown always matches reality.
        if not force and now - slot_id * slot > self.tick_seconds:
            return None
        live = ft_economy.run_official_battle(
            self.db, self.runner, self.settings, self.engine_cfg)
        # Mark the slot only after the battle exists, so a failure here
        # retries on the next tick instead of swallowing the slot.
        self.db.kv_set("official_last_slot", str(slot_id))
        return live
