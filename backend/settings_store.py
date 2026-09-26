"""Settings store: every brandable value and tunable number lives here.

Priority: environment variables > data/settings.json > built-in defaults.

Public branding and numbers are served at GET /api/settings; admin writes go
through PUT /api/admin/settings and are persisted to the JSON file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# field -> (env var, type, default)
FIELDS: dict[str, tuple[str, type, Any]] = {
    "project_name": ("ARENA_PROJECT_NAME", str, "BRAWLHOUSE"),
    "token_ticker": ("ARENA_TOKEN_TICKER", str, "BRAWL"),
    "token_mint": ("ARENA_TOKEN_MINT", str, ""),
    "hire_fee_sol": ("ARENA_HIRE_FEE_SOL", float, 0.10),
    # Live (real-SOL) payments. hiring_live gates the hire flow the same way
    # betting_live gates bets. treasury_wallet is the non-custodial
    # destination for user-signed hire/bet transfers; it is PUBLIC because
    # wallets need to know where the SOL goes. max_bet_sol caps a single
    # bet in both mock and live mode.
    "treasury_wallet": ("ARENA_TREASURY_WALLET", str, ""),
    "hiring_live": ("ARENA_HIRING_LIVE", bool, False),
    "max_bet_sol": ("ARENA_MAX_BET_SOL", float, 10.0),
    "betting_house_cut_pct": ("ARENA_BETTING_HOUSE_CUT_PCT", float, 5.0),
    "betting_live": ("ARENA_BETTING_LIVE", bool, False),
    # Betting window: seconds between battle creation and engine start
    # during which bets are accepted. The engine runs to completion in
    # milliseconds, so without this window there is effectively no time
    # to bet on a newly created battle.
    "betting_window_sec": ("ARENA_BETTING_WINDOW_SEC", int, 60),
    "buyback_pct": ("ARENA_BUYBACK_PCT", float, 50.0),
    "team_pct": ("ARENA_TEAM_PCT", float, 50.0),
    "buyback_interval_minutes": ("ARENA_BUYBACK_INTERVAL_MINUTES", int, 60),
    "buyback_enabled": ("ARENA_BUYBACK_ENABLED", bool, True),
    # Phase 6: buyback bot. buyback_live defaults to mock mode (false):
    # zero network calls, swaps simulated at buyback_mock_rate, burns
    # recorded with dry_run=true. Live mode is the owner's explicit opt-in.
    "buyback_live": ("ARENA_BUYBACK_LIVE", bool, False),
    "buyback_mock_rate": ("ARENA_BUYBACK_MOCK_RATE", float, 10000.0),
    "buyback_hot_sol_cap": ("ARENA_BUYBACK_HOT_SOL_CAP", float, 5.0),
    "team_sweep_enabled": ("ARENA_TEAM_SWEEP_ENABLED", bool, False),
    "fight_max_ticks": ("ARENA_FIGHT_MAX_TICKS", int, 1200),
    "playback_tick_ms": ("ARENA_PLAYBACK_TICK_MS", int, 100),
    # Phase 1 (FT economy). Every number is admin-configurable; the code
    # only reads them through this store, never hardcodes them.
    "born_fee_sol": ("ARENA_BORN_FEE_SOL", float, 0.1),
    "born_season_pct": ("ARENA_BORN_SEASON_PCT", float, 50.0),
    "born_live": ("ARENA_BORN_LIVE", bool, False),
    "entry_fee_sol": ("ARENA_ENTRY_FEE_SOL", float, 0.02),
    "entry_prize_pct": ("ARENA_ENTRY_PRIZE_PCT", float, 80.0),
    "entry_season_pct": ("ARENA_ENTRY_SEASON_PCT", float, 20.0),
    "entry_live": ("ARENA_ENTRY_LIVE", bool, False),
    "official_battle_interval_minutes": ("ARENA_OFFICIAL_BATTLE_INTERVAL_MINUTES", int, 5),
    "entry_window_minutes": ("ARENA_ENTRY_WINDOW_MINUTES", int, 2),
    "season_length_days": ("ARENA_SEASON_LENGTH_DAYS", int, 15),
    "season_prize_1_pct": ("ARENA_SEASON_PRIZE_1_PCT", float, 60.0),
    "season_prize_2_pct": ("ARENA_SEASON_PRIZE_2_PCT", float, 25.0),
    "season_prize_3_pct": ("ARENA_SEASON_PRIZE_3_PCT", float, 15.0),
    # Phase 1: prize payouts (battle + season) in live mode move real SOL
    # from the treasury hot wallet. Mock by default: payouts are recorded
    # with status='mock' and nothing moves. In-house flag, like buyback_live.
    "payouts_live": ("ARENA_PAYOUTS_LIVE", bool, False),
}

# Fields hidden from the public GET /api/settings (in-house only).
PRIVATE_FIELDS = {"buyback_enabled", "buyback_live", "buyback_mock_rate",
                  "buyback_hot_sol_cap", "team_sweep_enabled", "payouts_live"}


def _coerce(raw: str, typ: type) -> Any:
    if typ is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return typ(raw)


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._file_values: dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------ persistence
    def load(self) -> None:
        self._file_values = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
            self._file_values = {
                k: v for k, v in data.items() if k in FIELDS
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._file_values, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # ---------------------------------------------------------------- access
    def get(self, key: str) -> Any:
        if key not in FIELDS:
            raise KeyError(f"Unknown setting: {key}")
        env_var, typ, default = FIELDS[key]
        raw = os.environ.get(env_var)
        if raw is not None and raw != "":
            try:
                return _coerce(raw, typ)
            except (ValueError, TypeError):
                pass  # fall through to file value on bad env input
        if key in self._file_values:
            return self._file_values[key]
        return default

    def all(self, include_private: bool = False) -> dict[str, Any]:
        out = {}
        for key in FIELDS:
            if not include_private and key in PRIVATE_FIELDS:
                continue
            out[key] = self.get(key)
        return out

    def env_overridden(self) -> list[str]:
        """Keys currently pinned by an env var (admin JSON writes won't show)."""
        return [k for k, (var, _, _) in FIELDS.items() if os.environ.get(var)]

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        """Merge admin-provided values into the JSON file. Returns full store."""
        for key, value in values.items():
            if key not in FIELDS:
                raise KeyError(f"Unknown setting: {key}")
            _, typ, _ = FIELDS[key]
            if not isinstance(value, typ) or isinstance(value, bool) != (typ is bool):
                # bool is subclass of int; keep the check strict for bools
                if typ is bool and not isinstance(value, bool):
                    raise TypeError(f"{key} must be bool")
                if typ is not bool:
                    raise TypeError(f"{key} must be {typ.__name__}")
            self._file_values[key] = value
        self.save()
        return self.all(include_private=True)
