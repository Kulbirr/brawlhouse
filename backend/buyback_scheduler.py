"""Buyback bot scheduler: runs the Phase 6 buyback cycle on a timer
inside the backend process, so burns happen automatically without anyone
running bot/buyback.py by hand.

Mock by default (buyback_live=false): cycles execute, fees get allocated,
and burn rounds are recorded with dry_run=true. Nothing moves onchain
until the owner flips buyback_live.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


class BuybackScheduler(threading.Thread):
    """Daemon thread: every buyback_interval_minutes, run one buyback
    cycle. Never raises out of the loop; a bad cycle is recorded and the
    thread sleeps until the next slot."""

    def __init__(self, db, settings):
        super().__init__(daemon=True, name="buyback-scheduler")
        self.db = db
        self.settings = settings
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_run_at: str | None = None
        self.last_status: str | None = None
        self.last_detail: str = ""
        self.cycles = 0

    def stop(self):
        self._stop.set()

    def status(self) -> dict:
        # Public: never exposes buyback_enabled (in-house pause toggle).
        with self._lock:
            return {
                "running": self.is_alive(),
                "interval_minutes": int(self.settings.get("buyback_interval_minutes")),
                "buyback_live": bool(self.settings.get("buyback_live")),
                "last_run_at": self.last_run_at,
                "last_status": self.last_status,
                "last_detail": self.last_detail,
                "cycles": self.cycles,
            }

    def run_cycle_now(self) -> dict:
        """Run one cycle immediately (admin trigger / tests)."""
        from bot.buyback import run_cycle
        try:
            result = run_cycle(self.settings, self.db)
        except Exception as exc:  # never kill the scheduler thread
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        with self._lock:
            self.last_run_at = datetime.now(timezone.utc).isoformat()
            self.last_status = result.get("status")
            detail = result.get("error", "")
            if result.get("status") == "burned":
                detail = (f"spent {result.get('sol_spent')} SOL, burned "
                          f"{result.get('tokens_burned')} tokens"
                          f"{' (mock)' if result.get('dry_run') else ''}")
            elif result.get("status") == "paused":
                detail = "paused by in-house toggle"
            elif result.get("status") == "no_fees":
                detail = "no new fees to allocate"
            self.last_detail = detail
            self.cycles += 1
        return result

    def run(self):
        while not self._stop.is_set():
            interval = int(self.settings.get("buyback_interval_minutes") or 30)
            if self._stop.wait(interval * 60):
                break
            self.run_cycle_now()
