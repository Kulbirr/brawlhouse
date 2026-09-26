"""SQLite persistence (stdlib sqlite3). Tables:

  fighters  - per-fighter record (wins/losses/draws) + admin metadata overrides
  battles   - battle results, placement, seed, exhibition flag, status
  snapshots - per-tick engine snapshots (JSON), keyed (battle_id, tick)
  hires     - hire records: one per (battle_id, wallet)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS fighters (
    id TEXT PRIMARY KEY,
    name_override TEXT,
    tagline_override TEXT,
    description_override TEXT,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    -- Phase 1 (FT economy): player-owned fighters. NULL = house fighter.
    owner_wallet TEXT,
    archetype TEXT,
    colors TEXT,
    rarity TEXT,
    stat_mult REAL,
    born_at TEXT,
    owner_cert TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    rent_price_sol REAL,
    sale_price_sol REAL
);
CREATE TABLE IF NOT EXISTS battles (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    seed INTEGER,
    exhibition INTEGER NOT NULL DEFAULT 0,
    fighter_ids TEXT NOT NULL,      -- JSON list of engine ids
    registry_ids TEXT NOT NULL,    -- JSON list of registry ids (aligned)
    playback_speed REAL NOT NULL DEFAULT 1.0,
    hire_fee_sol REAL,
    winner TEXT,
    draw INTEGER,
    reason TEXT,
    ticks INTEGER,
    elimination_order TEXT,
    kills TEXT,
    final_hp TEXT,
    placement TEXT,
    notes TEXT,
    duration_ms INTEGER,
    -- Phase 1 (FT economy): scheduler-run official battles.
    official INTEGER NOT NULL DEFAULT 0,
    mode TEXT,              -- 'duel' | 'royale' (official battles only)
    prize_pool_sol REAL     -- winner's prize (official battles only)
);
CREATE TABLE IF NOT EXISTS snapshots (
    battle_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (battle_id, tick)
);
CREATE TABLE IF NOT EXISTS hires (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT NOT NULL,
    fighter_id TEXT NOT NULL,
    wallet TEXT NOT NULL,
    fee_sol REAL NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'mock',
    created_at TEXT NOT NULL,
    UNIQUE (battle_id, wallet)
);
CREATE INDEX IF NOT EXISTS idx_hires_battle ON hires (battle_id);

-- Phase 5: parimutuel betting
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT NOT NULL,
    fighter_id TEXT NOT NULL,   -- engine id, e.g. "hawk-2" or "hawk-2#1"
    wallet TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'mock',  -- 'mock' | 'pending' | 'confirmed'
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bets_battle ON bets (battle_id);
CREATE INDEX IF NOT EXISTS idx_bets_battle_wallet ON bets (battle_id, wallet);

-- one row per bet after settlement: what the bettor gets back
CREATE TABLE IF NOT EXISTS bet_payouts (    bet_id INTEGER PRIMARY KEY,
    battle_id TEXT NOT NULL,
    wallet TEXT NOT NULL,
    stake_sol REAL NOT NULL,
    payout_sol REAL NOT NULL,
    outcome TEXT NOT NULL,      -- 'win' | 'lose' | 'refund'
    paid_tx TEXT,               -- on-chain payout signature (live mode only)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payouts_battle ON bet_payouts (battle_id);

-- one row per settled battle (no row when the battle had zero bets)
CREATE TABLE IF NOT EXISTS settlements (
    battle_id TEXT PRIMARY KEY,
    winner TEXT,
    draw INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,  -- 'settled' | 'refunded_draw' |
                           -- 'refunded_no_winner_bets' | 'settlement_error'
    total_bets_sol REAL NOT NULL,
    house_cut_sol REAL NOT NULL,
    payout_pool_sol REAL NOT NULL,
    bet_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Phase 5: every fee the platform takes, in one ledger for the phase 6
-- buyback bot. source is 'betting' (house cut of a settled pool) or
-- 'hire' (hire fee recorded at hire time).
CREATE TABLE IF NOT EXISTS treasury_fee_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT,
    source TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fee_battle ON treasury_fee_events (battle_id);
CREATE INDEX IF NOT EXISTS idx_fee_source ON treasury_fee_events (source);

-- live-escrow only: claimed on-chain deposits, so one tx can't fund two bets
CREATE TABLE IF NOT EXISTS escrow_deposits (
    tx_sig TEXT PRIMARY KEY,
    battle_id TEXT NOT NULL,
    wallet TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    created_at TEXT NOT NULL
);

-- live (user-signed) payments: every signature that confirmed a hire or bet.
-- PRIMARY KEY on tx_sig is the replay protection: one signature confirms
-- exactly one hire/bet, even across battles.
CREATE TABLE IF NOT EXISTS tx_claims (
    tx_sig TEXT PRIMARY KEY,
    kind TEXT NOT NULL,            -- 'hire' | 'bet'
    ref_id INTEGER NOT NULL,       -- hires.id or bets.id
    battle_id TEXT NOT NULL,
    wallet TEXT NOT NULL,
    amount_lamports INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Phase 6: buyback allocation ledger. One row per treasury_fee_event the
-- buyback bot has consumed. A fee event is spendable exactly once: the
-- PRIMARY KEY on fee_event_id makes double-allocation impossible, even if
-- two bot runs overlap.
CREATE TABLE IF NOT EXISTS buyback_allocations (
    fee_event_id INTEGER PRIMARY KEY,
    amount_sol REAL NOT NULL,
    allocated_at TEXT NOT NULL
);

-- Phase 6: one row per buyback round the bot executed. dry_run=1 rows are
-- simulations (mock mode): zero network calls, fake tx ids prefixed
-- 'mock_'. dry_run=0 rows are real on-chain swap+burn rounds.
CREATE TABLE IF NOT EXISTS burn_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    sol_spent REAL NOT NULL,
    tokens_bought REAL NOT NULL,
    tokens_burned REAL NOT NULL,
    buy_tx TEXT,
    burn_tx TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0
);

-- Phase 6: the team_pct share of fees, accounted per round. Mock mode only
-- records the row; live mode may also sweep it on-chain (swept_tx).
CREATE TABLE IF NOT EXISTS team_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    swept_tx TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0
);

-- Arena chat: viewer messages per battle. Rate limiting is enforced in
-- Python (1 message per 2s per wallet per battle); the index keeps the
-- recent-window lookup cheap.
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id TEXT NOT NULL,
    wallet TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_battle ON chat_messages (battle_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_battle_wallet ON chat_messages (battle_id, wallet, id);

-- Phase 1 (FT economy): born flow -------------------------------------
-- A birth intent is the pending state for a live (wallet-signed) born
-- payment: the fighter is created only after /born/confirm verifies the
-- onchain transfer. Mock mode skips intents entirely.
CREATE TABLE IF NOT EXISTS birth_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT NOT NULL,
    name TEXT NOT NULL,
    archetype TEXT NOT NULL,
    color TEXT NOT NULL,
    fee_sol REAL NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'mock',  -- 'mock'|'pending'|'confirmed'
    fighter_id TEXT,                              -- set once the FT is born
    created_at TEXT NOT NULL
);

-- Public birth announcements: "a new fighter has been born".
CREATE TABLE IF NOT EXISTS birth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fighter_id TEXT NOT NULL,
    name TEXT NOT NULL,
    rarity TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_birth_events_created
    ON birth_events (created_at DESC);

-- Phase 1 (FT economy): official battle entry queue -------------------
-- prize_share_sol is the 80% of the entry fee earmarked for the battle
-- prize pool; it moves into the pool only when the entry is drawn.
-- payment_status 'pending' entries are unpaid and never drawn.
CREATE TABLE IF NOT EXISTS battle_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fighter_id TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    entry_fee_sol REAL NOT NULL,
    prize_share_sol REAL NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'mock',  -- 'mock'|'pending'|'confirmed'
    status TEXT NOT NULL DEFAULT 'queued',        -- 'queued'|'drawn'|'expired'
    battle_id TEXT,
    entered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON battle_queue (status, entered_at);

-- Phase 1 (FT economy): seasons ---------------------------------------
CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'finished'
    prize_pool_sol REAL NOT NULL DEFAULT 0,
    finished_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS season_standings (
    season_id INTEGER NOT NULL,
    fighter_id TEXT NOT NULL,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season_id, fighter_id)
);

-- One row per season prize paid (or mock-recorded) to a top-3 owner.
CREATE TABLE IF NOT EXISTS season_payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    place INTEGER NOT NULL,
    fighter_id TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'mock',  -- 'mock' | 'pending' | 'paid'
    paid_tx TEXT,
    created_at TEXT NOT NULL
);

-- One row per official battle prize: paid to the winner's owner, kept by
-- the treasury when a house fighter wins, or rolled into the season pool
-- on a draw. destination records which happened.
CREATE TABLE IF NOT EXISTS battle_prizes (
    battle_id TEXT PRIMARY KEY,
    prize_pool_sol REAL NOT NULL,
    winner_registry_id TEXT,
    owner_wallet TEXT,
    amount_sol REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'mock',  -- 'mock' | 'pending' | 'paid'
    destination TEXT NOT NULL,            -- 'owner' | 'treasury' | 'season_pool'
    paid_tx TEXT,
    created_at TEXT NOT NULL
);

-- Tiny key/value store for scheduler state (e.g. last official mode).
CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Additive migrations for DBs created before a schema change.

        CREATE TABLE IF NOT EXISTS never adds columns to an existing
        table, so every new column on an old table is added here.
        Additive only: never renames or drops.
        """
        migrations: dict[str, list[str]] = {
            "fighters": [
                "owner_wallet TEXT",
                "archetype TEXT",
                "colors TEXT",
                "rarity TEXT",
                "stat_mult REAL",
                "born_at TEXT",
                "owner_cert TEXT",
                "status TEXT NOT NULL DEFAULT 'active'",
                "rent_price_sol REAL",
                "sale_price_sol REAL",
            ],
            "battles": [
                "official INTEGER NOT NULL DEFAULT 0",
                "mode TEXT",
                "prize_pool_sol REAL",
            ],
        }
        for table, columns in migrations.items():
            existing = {r["name"] for r in
                        self._q(f"PRAGMA table_info({table})")}
            for coldef in columns:
                col = coldef.split()[0]
                if col not in existing:
                    self._q(f"ALTER TABLE {table} ADD COLUMN {coldef}")

    # ---------------------------------------------------------------- helpers
    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    # --------------------------------------------------------------- fighters
    def ensure_fighter(self, fighter_id: str) -> None:
        self._q("INSERT OR IGNORE INTO fighters (id) VALUES (?)", (fighter_id,))

    def get_fighter(self, fighter_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM fighters WHERE id = ?", (fighter_id,))
        return self._row_dict(rows[0]) if rows else None

    def ft_count(self) -> int:
        rows = self._q(
            "SELECT COUNT(*) AS n FROM fighters WHERE owner_wallet IS NOT NULL")
        return int(rows[0]["n"])

    def list_fighters(self, limit: int = 500,
                      offset: int = 0) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM fighters ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset))]

    def list_player_fts(self, limit: int = 200,
                        offset: int = 0) -> list[dict[str, Any]]:
        """Only player-born FTs (house fighters excluded)."""
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM fighters WHERE owner_wallet IS NOT NULL "
            "ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset))]

    def record_result(self, fighter_id: str, outcome: str) -> None:
        """outcome in {'win','loss','draw'}."""
        col = {"win": "wins", "loss": "losses", "draw": "draws"}[outcome]
        self.ensure_fighter(fighter_id)
        self._q(f"UPDATE fighters SET {col} = {col} + 1 WHERE id = ?", (fighter_id,))

    def set_fighter_overrides(
        self,
        fighter_id: str,
        name: str | None = None,
        tagline: str | None = None,
        description: str | None = None,
    ) -> None:
        self.ensure_fighter(fighter_id)
        if name is not None:
            self._q("UPDATE fighters SET name_override = ? WHERE id = ?", (name, fighter_id))
        if tagline is not None:
            self._q("UPDATE fighters SET tagline_override = ? WHERE id = ?", (tagline, fighter_id))
        if description is not None:
            self._q("UPDATE fighters SET description_override = ? WHERE id = ?",
                    (description, fighter_id))

    # ---------------------------------------------------------------- battles
    def create_battle(self, rec: dict[str, Any]) -> None:
        cols = ", ".join(rec.keys())
        placeholders = ", ".join("?" for _ in rec)
        self._q(f"INSERT INTO battles ({cols}) VALUES ({placeholders})",
                tuple(rec.values()))

    def get_battle(self, battle_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM battles WHERE id = ?", (battle_id,))
        return self._row_dict(rows[0]) if rows else None

    def list_battles(
        self,
        status: str | None = None,
        exhibition: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM battles WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if exhibition is not None:
            sql += " AND exhibition = ?"
            params.append(1 if exhibition else 0)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [self._row_dict(r) for r in self._q(sql, tuple(params))]

    def finish_battle(self, battle_id: str, result: dict[str, Any],
                      placement: dict[str, int], duration_ms: int) -> None:
        self._q(
            """UPDATE battles SET status='finished', finished_at=?, winner=?,
               draw=?, reason=?, ticks=?, elimination_order=?, kills=?,
               final_hp=?, placement=?, notes=?, duration_ms=?
               WHERE id = ?""",
            (
                _utcnow(),
                result["winner"],
                1 if result["draw"] else 0,
                result["reason"],
                result["ticks"],
                json.dumps(result["elimination_order"]),
                json.dumps(result["kills"]),
                json.dumps(result["final_hp"]),
                json.dumps(placement),
                json.dumps(result["notes"]),
                duration_ms,
                battle_id,
            ),
        )

    def mark_started(self, battle_id: str) -> None:
        self._q("UPDATE battles SET started_at = ?, status = 'running' "
                "WHERE id = ?",
                (_utcnow(), battle_id))

    # -------------------------------------------------------------- snapshots
    def save_snapshots(self, battle_id: str, snapshots: list[dict[str, Any]]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(
                "INSERT OR REPLACE INTO snapshots (battle_id, tick, payload) VALUES (?, ?, ?)",
                [(battle_id, s["tick"], json.dumps(s)) for s in snapshots],
            )
            self._conn.commit()

    def get_snapshots(self, battle_id: str) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT payload FROM snapshots WHERE battle_id = ? ORDER BY tick",
            (battle_id,),
        )
        return [json.loads(r["payload"]) for r in rows]

    # ------------------------------------------------------------------ hires
    def create_hire(self, battle_id: str, fighter_id: str, wallet: str,
                    fee_sol: float,
                    payment_status: str = "mock") -> dict[str, Any] | None:
        """Returns the hire row, or None if (battle_id, wallet) already exists.

        payment_status: 'mock' (default, unchanged legacy behavior),
        'pending' (live mode: awaiting on-chain confirmation).
        """
        try:
            with self._lock:
                cur = self._conn.execute(
                    """INSERT INTO hires (battle_id, fighter_id, wallet, fee_sol,
                       payment_status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (battle_id, fighter_id, wallet, fee_sol, payment_status,
                     _utcnow()),
                )
                hire_id = cur.lastrowid
                self._conn.commit()
        except sqlite3.IntegrityError:
            return None
        rows = self._q("SELECT * FROM hires WHERE id = ?", (hire_id,))
        return self._row_dict(rows[0]) if rows else None

    def hire_exists(self, battle_id: str, wallet: str) -> bool:
        rows = self._q("SELECT 1 FROM hires WHERE battle_id = ? AND wallet = ?",
                       (battle_id, wallet))
        return bool(rows)

    def get_hire(self, hire_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM hires WHERE id = ?", (hire_id,))
        return self._row_dict(rows[0]) if rows else None

    def get_hire_by_battle_wallet(self, battle_id: str,
                                  wallet: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM hires WHERE battle_id = ? AND wallet = ?",
                       (battle_id, wallet))
        return self._row_dict(rows[0]) if rows else None

    def confirm_hire(self, hire_id: int) -> dict[str, Any] | None:
        """Flip a 'pending' hire to 'confirmed'. Returns the row, or None
        when the hire doesn't exist or isn't pending."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE hires SET payment_status = 'confirmed' "
                "WHERE id = ? AND payment_status = 'pending'",
                (hire_id,),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get_hire(hire_id)

    def list_hires(self, battle_id: str) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM hires WHERE battle_id = ? ORDER BY created_at", (battle_id,))]

    # ------------------------------------------------------------------ bets
    def create_bet(self, battle_id: str, fighter_id: str, wallet: str,
                   amount_sol: float,
                   payment_status: str = "mock") -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO bets (battle_id, fighter_id, wallet, amount_sol,
                   payment_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (battle_id, fighter_id, wallet, amount_sol, payment_status,
                 _utcnow()),
            )
            bet_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM bets WHERE id = ?", (bet_id,))
        return self._row_dict(rows[0])

    def list_bets(self, battle_id: str,
                  wallet: str | None = None,
                  payment_statuses: tuple[str, ...] | None = None,
                  ) -> list[dict[str, Any]]:
        """payment_statuses filters to counted bets, e.g. ("mock", "confirmed")
        for the pool/settlement so unpaid 'pending' bets never move odds."""
        sql = "SELECT * FROM bets WHERE battle_id = ?"
        params: list[Any] = [battle_id]
        if wallet:
            sql += " AND wallet = ?"
            params.append(wallet)
        if payment_statuses:
            placeholders = ", ".join("?" for _ in payment_statuses)
            sql += f" AND payment_status IN ({placeholders})"
            params.extend(payment_statuses)
        sql += " ORDER BY created_at"
        rows = self._q(sql, tuple(params))
        return [self._row_dict(r) for r in rows]

    def get_bet(self, bet_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM bets WHERE id = ?", (bet_id,))
        return self._row_dict(rows[0]) if rows else None

    def confirm_bet(self, bet_id: int) -> dict[str, Any] | None:
        """Flip a 'pending' bet to 'confirmed'. Returns the row, or None
        when the bet doesn't exist or isn't pending."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE bets SET payment_status = 'confirmed' "
                "WHERE id = ? AND payment_status = 'pending'",
                (bet_id,),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get_bet(bet_id)

    # ------------------------------------------- live payment tx replay log
    def tx_claimed(self, tx_sig: str) -> bool:
        rows = self._q("SELECT 1 FROM tx_claims WHERE tx_sig = ?",
                       (tx_sig,))
        return bool(rows)

    def claim_tx(self, tx_sig: str, kind: str, ref_id: int, battle_id: str,
                 wallet: str, amount_lamports: int) -> bool:
        """Record that tx_sig paid for this hire/bet. False when the
        signature was already claimed (replay)."""
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO tx_claims
                       (tx_sig, kind, ref_id, battle_id, wallet,
                        amount_lamports, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tx_sig, kind, ref_id, battle_id, wallet,
                     amount_lamports, _utcnow()),
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            return False
        return True

    # ------------------------------------------------------------ settlement
    def record_settlement(self, battle_id: str, winner: str | None, draw: bool,
                          status: str, total_bets_sol: float,
                          house_cut_sol: float, payout_pool_sol: float,
                          bet_count: int) -> None:
        self._q(
            """INSERT OR REPLACE INTO settlements
               (battle_id, winner, draw, status, total_bets_sol, house_cut_sol,
                payout_pool_sol, bet_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (battle_id, winner, 1 if draw else 0, status, total_bets_sol,
             house_cut_sol, payout_pool_sol, bet_count, _utcnow()),
        )

    def get_settlement(self, battle_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM settlements WHERE battle_id = ?",
                       (battle_id,))
        return self._row_dict(rows[0]) if rows else None

    def record_payout(self, bet_id: int, battle_id: str, wallet: str,
                      stake_sol: float, payout_sol: float, outcome: str,
                      paid_tx: str | None = None) -> None:
        self._q(
            """INSERT OR REPLACE INTO bet_payouts
               (bet_id, battle_id, wallet, stake_sol, payout_sol, outcome,
                paid_tx, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bet_id, battle_id, wallet, stake_sol, payout_sol, outcome,
             paid_tx, _utcnow()),
        )

    def list_payouts(self, battle_id: str) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM bet_payouts WHERE battle_id = ? ORDER BY bet_id",
            (battle_id,))]

    # ------------------------------------------------------- treasury ledger
    def record_fee_event(self, battle_id: str | None, source: str,
                         amount_sol: float) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO treasury_fee_events
                   (battle_id, source, amount_sol, created_at)
                   VALUES (?, ?, ?, ?)""",
                (battle_id, source, amount_sol, _utcnow()),
            )
            event_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM treasury_fee_events WHERE id = ?",
                       (event_id,))
        return self._row_dict(rows[0])

    def fee_totals(self) -> dict[str, float]:
        rows = self._q(
            "SELECT source, SUM(amount_sol) AS total FROM treasury_fee_events "
            "GROUP BY source")
        totals = {r["source"]: (r["total"] or 0.0) for r in rows}
        betting = totals.get("betting", 0.0)
        hire = totals.get("hire", 0.0)
        out: dict[str, float] = {
            "betting_sol": betting, "hire_sol": hire,
            "total_sol": betting + hire,
            "grand_total_sol": round(sum(totals.values()), 9),
            "by_source": totals,
        }
        return out

    def list_fee_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM treasury_fee_events ORDER BY id DESC LIMIT ?",
            (limit,))]

    # -------------------------------------------- phase 6: buyback ledger
    def unallocated_fee_events(self) -> list[dict[str, Any]]:
        """Fee events no buyback round has consumed yet, oldest first."""
        return [self._row_dict(r) for r in self._q(
            """SELECT e.* FROM treasury_fee_events e
               LEFT JOIN buyback_allocations a
                 ON a.fee_event_id = e.id
               WHERE a.fee_event_id IS NULL
               ORDER BY e.id ASC""")]

    def unallocated_fees_total(self) -> float:
        rows = self._q(
            """SELECT SUM(e.amount_sol) AS total FROM treasury_fee_events e
               LEFT JOIN buyback_allocations a
                 ON a.fee_event_id = e.id
               WHERE a.fee_event_id IS NULL""")
        return round(rows[0]["total"] or 0.0, 9)

    def record_buyback_round(
        self,
        event_ids: list[int],
        event_amounts: list[float],
        sol_spent: float,
        tokens_bought: float,
        tokens_burned: float,
        buy_tx: str | None,
        burn_tx: str | None,
        dry_run: bool,
        team_sol: float,
        team_swept_tx: str | None = None,
    ) -> int:
        """Atomically: mark fee events allocated (exactly-once via PK),
        record the burn event, and account the team's share.

        If the spend fails before this is called, nothing is allocated, so
        a later run can retry - no fee is ever lost or double-spent.
        Returns the burn_events id.
        """
        now = _utcnow()
        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(
                "INSERT INTO buyback_allocations "
                "(fee_event_id, amount_sol, allocated_at) VALUES (?, ?, ?)",
                [(eid, amt, now) for eid, amt in
                 zip(event_ids, event_amounts)],
            )
            cur.execute(
                """INSERT INTO burn_events
                   (created_at, sol_spent, tokens_bought, tokens_burned,
                    buy_tx, burn_tx, dry_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now, sol_spent, tokens_bought, tokens_burned, buy_tx,
                 burn_tx, 1 if dry_run else 0),
            )
            burn_id = cur.lastrowid
            if team_sol > 0:
                cur.execute(
                    """INSERT INTO team_allocations
                       (created_at, amount_sol, swept_tx, dry_run)
                       VALUES (?, ?, ?, ?)""",
                    (now, team_sol, team_swept_tx, 1 if dry_run else 0),
                )
            self._conn.commit()
        return burn_id

    def list_burn_events(self, limit: int = 25,
                         offset: int = 0) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM burn_events ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset))]

    def burn_totals(self) -> dict[str, Any]:
        rows = self._q(
            "SELECT COUNT(*) AS n, SUM(tokens_burned) AS total FROM burn_events")
        return {
            "burn_count": rows[0]["n"] or 0,
            "total_burned_tokens": rows[0]["total"] or 0.0,
        }

    def team_totals(self) -> dict[str, Any]:
        rows = self._q(
            "SELECT COUNT(*) AS n, SUM(amount_sol) AS total "
            "FROM team_allocations")
        return {
            "allocation_count": rows[0]["n"] or 0,
            "total_team_sol": round(rows[0]["total"] or 0.0, 9),
        }

    def max_chat_id(self, battle_id: str) -> int:
        rows = self._q("SELECT MAX(id) AS m FROM chat_messages WHERE battle_id = ?",
                       (battle_id,))
        return rows[0]["m"] or 0

    # ------------------------------------------------------- escrow deposits
    def deposit_claimed(self, tx_sig: str) -> bool:
        rows = self._q("SELECT 1 FROM escrow_deposits WHERE tx_sig = ?",
                       (tx_sig,))
        return bool(rows)

    def claim_deposit(self, tx_sig: str, battle_id: str, wallet: str,
                      amount_sol: float) -> bool:
        """Claim a tx signature for one bet. False if already claimed."""
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO escrow_deposits
                       (tx_sig, battle_id, wallet, amount_sol, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tx_sig, battle_id, wallet, amount_sol, _utcnow()),
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            return False
        return True

    # ------------------------------------------------------------ arena chat
    def add_chat_message(self, battle_id: str, wallet: str,
                         message: str) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO chat_messages
                   (battle_id, wallet, message, created_at)
                   VALUES (?, ?, ?, ?)""",
                (battle_id, wallet, message, _utcnow()),
            )
            msg_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM chat_messages WHERE id = ?", (msg_id,))
        return self._row_dict(rows[0])

    def list_chat_messages(self, battle_id: str, limit: int = 50,
                           since_id: int | None = None) -> list[dict[str, Any]]:
        """Chat history for a battle, oldest first. The default (no since_id)
        returns the newest `limit` messages in chronological order; since_id
        returns only newer messages (used by the websocket broadcast poll)."""
        if since_id is not None:
            sql = """SELECT * FROM chat_messages
                     WHERE battle_id = ? AND id > ?
                     ORDER BY id ASC LIMIT ?"""
            return [self._row_dict(r) for r in self._q(sql, (battle_id, since_id, limit))]
        sql = """SELECT * FROM (
                     SELECT * FROM chat_messages WHERE battle_id = ?
                     ORDER BY id DESC LIMIT ?
                 ) ORDER BY id ASC"""
        return [self._row_dict(r) for r in self._q(sql, (battle_id, limit))]

    def chat_rate_limited(self, battle_id: str, wallet: str,
                          window_seconds: int = 2) -> bool:
        """True when this wallet posted in this battle within the window."""
        from datetime import timedelta
        rows = self._q(
            """SELECT created_at FROM chat_messages
               WHERE battle_id = ? AND wallet = ?
               ORDER BY id DESC LIMIT 1""",
            (battle_id, wallet),
        )
        if not rows:
            return False
        try:
            last = datetime.fromisoformat(rows[0]["created_at"])
        except (ValueError, TypeError):
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last) < timedelta(
            seconds=window_seconds)

    # ============================================ Phase 1: FT registry ===
    def create_ft_fighter(self, ft_id: str, name: str, owner_wallet: str,
                          archetype: str, colors: str, rarity: str,
                          stat_mult: float, owner_cert: str) -> dict[str, Any]:
        """Insert a born FT. The display name lives in name_override
        (same column the admin branding overrides use for house fighters).
        Colors are stored as a JSON object ({primary: ...}) so future
        multi-color designs fit the same column.
        Raises sqlite3.IntegrityError on duplicate id."""
        colors_json = json.dumps({"primary": colors})
        self._q(
            """INSERT INTO fighters
               (id, name_override, owner_wallet, archetype, colors, rarity,
                stat_mult, born_at, owner_cert, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (ft_id, name, owner_wallet, archetype, colors_json, rarity,
             stat_mult, _utcnow(), owner_cert),
        )
        row = self.get_fighter(ft_id)
        assert row is not None
        return row

    def is_ft(self, fighter_id: str) -> bool:
        row = self.get_fighter(fighter_id)
        return bool(row and row.get("owner_wallet"))

    def list_ft_fighters(self) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM fighters WHERE owner_wallet IS NOT NULL "
            "ORDER BY born_at DESC")]

    def ft_ids(self) -> list[str]:
        rows = self._q("SELECT id FROM fighters WHERE id LIKE 'FT-%'")
        return [r["id"] for r in rows]

    def set_fighter_status(self, fighter_id: str, status: str) -> None:
        self._q("UPDATE fighters SET status = ? WHERE id = ?",
                (status, fighter_id))

    def list_wallet_fighters(self, wallet: str) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM fighters WHERE owner_wallet = ? "
            "ORDER BY born_at DESC", (wallet,))]

    # ======================================= Phase 1: birth intents =====
    def create_birth_intent(self, wallet: str, name: str, archetype: str,
                            color: str, fee_sol: float,
                            payment_status: str = "mock") -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO birth_intents
                   (wallet, name, archetype, color, fee_sol, payment_status,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (wallet, name, archetype, color, fee_sol, payment_status,
                 _utcnow()),
            )
            intent_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM birth_intents WHERE id = ?",
                       (intent_id,))
        return self._row_dict(rows[0])

    def get_birth_intent(self, intent_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM birth_intents WHERE id = ?",
                       (intent_id,))
        return self._row_dict(rows[0]) if rows else None

    def confirm_birth_intent(self, intent_id: int,
                             fighter_id: str) -> dict[str, Any] | None:
        """Flip a 'pending' intent to 'confirmed' and stamp the FT id."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE birth_intents SET payment_status = 'confirmed', "
                "fighter_id = ? WHERE id = ? AND payment_status = 'pending'",
                (fighter_id, intent_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get_birth_intent(intent_id)

    # ======================================= Phase 1: birth feed ========
    def record_birth_event(self, fighter_id: str, name: str, rarity: str,
                           owner_wallet: str) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO birth_events
                   (fighter_id, name, rarity, owner_wallet, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (fighter_id, name, rarity, owner_wallet, _utcnow()),
            )
            event_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM birth_events WHERE id = ?",
                       (event_id,))
        return self._row_dict(rows[0])

    def recent_births(self, limit: int = 25) -> list[dict[str, Any]]:
        """Public birth feed: newest born FTs first."""
        return self.list_birth_events(limit=limit)

    def list_birth_events(self, limit: int = 25) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM birth_events ORDER BY id DESC LIMIT ?", (limit,))]

    # ======================================= Phase 1: battle queue ======
    def enqueue_fighter(self, fighter_id: str, owner_wallet: str,
                        entry_fee_sol: float, prize_share_sol: float,
                        payment_status: str = "mock") -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO battle_queue
                   (fighter_id, owner_wallet, entry_fee_sol, prize_share_sol,
                    payment_status, status, entered_at)
                   VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
                (fighter_id, owner_wallet, entry_fee_sol, prize_share_sol,
                 payment_status, _utcnow()),
            )
            entry_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM battle_queue WHERE id = ?",
                       (entry_id,))
        return self._row_dict(rows[0])

    def get_queue_entry(self, entry_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM battle_queue WHERE id = ?",
                       (entry_id,))
        return self._row_dict(rows[0]) if rows else None

    def confirm_queue_entry(self, entry_id: int) -> dict[str, Any] | None:
        """Flip a 'pending' queue entry to 'confirmed' (now drawable)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE battle_queue SET payment_status = 'confirmed' "
                "WHERE id = ? AND payment_status = 'pending'",
                (entry_id,),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get_queue_entry(entry_id)

    def list_queued_entries(self) -> list[dict[str, Any]]:
        """Drawable entries: queued + paid (mock/confirmed), oldest first."""
        return [self._row_dict(r) for r in self._q(
            """SELECT * FROM battle_queue
               WHERE status = 'queued'
                 AND payment_status IN ('mock', 'confirmed')
               ORDER BY entered_at ASC, id ASC""")]

    def queue_count(self) -> int:
        rows = self._q(
            """SELECT COUNT(*) AS n FROM battle_queue
               WHERE status = 'queued'
                 AND payment_status IN ('mock', 'confirmed')""")
        return rows[0]["n"] or 0

    def mark_entries_drawn(self, entry_ids: list[int],
                           battle_id: str) -> None:
        if not entry_ids:
            return
        placeholders = ", ".join("?" for _ in entry_ids)
        self._q(
            f"UPDATE battle_queue SET status = 'drawn', battle_id = ? "
            f"WHERE id IN ({placeholders})",
            (battle_id, *entry_ids),
        )

    def expire_queued_entry(self, entry_id: int) -> None:
        self._q("UPDATE battle_queue SET status = 'expired' "
                "WHERE id = ? AND status = 'queued'", (entry_id,))

    # ============================================ Phase 1: kv store =====
    def kv_get(self, key: str) -> str | None:
        rows = self._q("SELECT value FROM kv_store WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def kv_set(self, key: str, value: str) -> None:
        self._q("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (key, value))

    # ============================================ Phase 1: seasons ======
    def create_season(self, length_days: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        ends = now + timedelta(days=length_days)
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO seasons
                   (started_at, ends_at, status, prize_pool_sol, created_at)
                   VALUES (?, ?, 'active', 0, ?)""",
                (now.isoformat(), ends.isoformat(), _utcnow()),
            )
            season_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM seasons WHERE id = ?", (season_id,))
        return self._row_dict(rows[0])

    def get_active_season(self) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM seasons WHERE status = 'active' "
                       "ORDER BY id DESC LIMIT 1")
        return self._row_dict(rows[0]) if rows else None

    def get_season(self, season_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM seasons WHERE id = ?", (season_id,))
        return self._row_dict(rows[0]) if rows else None

    def list_seasons(self, limit: int = 12) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM seasons ORDER BY id DESC LIMIT ?", (limit,))]

    def add_to_season_pool(self, season_id: int, amount_sol: float) -> None:
        self._q("UPDATE seasons SET prize_pool_sol = "
                "ROUND(prize_pool_sol + ?, 9) WHERE id = ?",
                (amount_sol, season_id))

    def finish_season(self, season_id: int) -> None:
        self._q("UPDATE seasons SET status = 'finished', finished_at = ? "
                "WHERE id = ?", (_utcnow(), season_id))

    # --------------------------------------- Phase 1: season standings ==
    def record_season_result(self, season_id: int, fighter_id: str,
                             outcome: str) -> None:
        """outcome in {'win','loss','draw'}."""
        if outcome not in ("win", "loss", "draw"):
            raise ValueError(f"Bad outcome: {outcome!r}")
        inc = {"wins": 0, "losses": 0, "draws": 0}
        inc[{"win": "wins", "loss": "losses", "draw": "draws"}[outcome]] = 1
        self._q(
            """INSERT INTO season_standings
               (season_id, fighter_id, wins, losses, draws)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (season_id, fighter_id)
               DO UPDATE SET wins = wins + excluded.wins,
                             losses = losses + excluded.losses,
                             draws = draws + excluded.draws""",
            (season_id, fighter_id, inc["wins"], inc["losses"],
             inc["draws"]),
        )

    def season_leaderboard(self, season_id: int,
                           limit: int = 50) -> list[dict[str, Any]]:
        """Rank by wins; tiebreak by win rate (fighters with <3 battles
        rank below those with >=3, so a 1-0 record can't top the board)."""
        return [self._row_dict(r) for r in self._q(
            """SELECT s.*,
                      (s.wins + s.losses + s.draws) AS battles,
                      (s.wins * 3 + s.draws) AS points,
                      CASE WHEN (s.wins + s.losses + s.draws) > 0
                           THEN CAST(s.wins AS REAL)
                                / (s.wins + s.losses + s.draws)
                           ELSE 0 END AS win_rate,
                      CASE WHEN (s.wins + s.losses + s.draws) >= 3
                           THEN 1 ELSE 0 END AS qualified
               FROM season_standings s
               WHERE s.season_id = ?
               ORDER BY s.wins DESC, qualified DESC, win_rate DESC,
                        battles ASC
               LIMIT ?""", (season_id, limit))]

    # ----------------------------------------- Phase 1: season payouts =
    def record_season_payout(self, season_id: int, place: int,
                             fighter_id: str, owner_wallet: str,
                             amount_sol: float,
                             status: str = "mock",
                             paid_tx: str | None = None) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO season_payouts
                   (season_id, place, fighter_id, owner_wallet, amount_sol,
                    status, paid_tx, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, place, fighter_id, owner_wallet, amount_sol,
                 status, paid_tx, _utcnow()),
            )
            payout_id = cur.lastrowid
            self._conn.commit()
        rows = self._q("SELECT * FROM season_payouts WHERE id = ?",
                       (payout_id,))
        return self._row_dict(rows[0])

    def list_season_payouts(self, season_id: int) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM season_payouts WHERE season_id = ? "
            "ORDER BY place ASC", (season_id,))]

    def recent_season_payouts(self, limit: int = 50) -> list[dict[str, Any]]:
        return [self._row_dict(r) for r in self._q(
            "SELECT * FROM season_payouts ORDER BY id DESC LIMIT ?", (limit,))]

    # ----------------------------------------- Phase 1: battle prizes ==
    def record_battle_prize(self, battle_id: str, prize_pool_sol: float,
                            winner_registry_id: str | None,
                            owner_wallet: str | None, amount_sol: float,
                            status: str, destination: str,
                            paid_tx: str | None = None) -> dict[str, Any]:
        self._q(
            """INSERT OR REPLACE INTO battle_prizes
               (battle_id, prize_pool_sol, winner_registry_id, owner_wallet,
                amount_sol, status, destination, paid_tx, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (battle_id, prize_pool_sol, winner_registry_id, owner_wallet,
             amount_sol, status, destination, paid_tx, _utcnow()),
        )
        rows = self._q("SELECT * FROM battle_prizes WHERE battle_id = ?",
                       (battle_id,))
        return self._row_dict(rows[0])

    def get_battle_prize(self, battle_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM battle_prizes WHERE battle_id = ?",
                       (battle_id,))
        return self._row_dict(rows[0]) if rows else None
