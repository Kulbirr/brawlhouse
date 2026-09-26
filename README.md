# Agent Arena - Game Engine (Phase 1)

2D top-down battle-royale simulation core. Fighters are AI bots; the engine
runs a fixed-timestep tick loop, resolves combat, and emits JSON-serializable
per-tick snapshots that later phases will stream over WebSocket.

Stdlib-only Python. No project names, token names, or tickers anywhere in
this phase.

## Layout

```
agent-arena/
  arena/
    __init__.py     public surface: Config, FighterBot, Battle, simulate, ...
    config.py       ALL tunables, driven by env vars with sane defaults
    bot_api.py      the bot contract phase 2 codes against (FighterBot, validation)
    engine.py       Battle: fixed-tick simulation, combat, snapshots, fault handling
    bots.py         built-in trivial bots (spinner/chaser/fleer/random/null) for CLI/tests
    __main__.py     headless CLI runner
  tests/
    test_engine.py  unittest suite (stdlib only)
  requirements.txt  pinned deps (currently: none, stdlib-only)
  .gitignore        ignores .env and secrets
  README.md         this file
```

## Setup

```bash
cd ~/workspace/agent-arena
python3 -m arena sim --bots chaser,chaser --ticks 1200 --seed 42
```

No install step needed (stdlib only). Python 3.10+ recommended
(`X | None` syntax is used).

## Configuration

Every tunable lives in `arena/config.py` (`Config.from_env()`), overridable
via environment variables. Defaults in parentheses.

| Env var | Default | Meaning |
|---|---|---|
| `ARENA_SIZE` | 1000.0 | square arena side length (units) |
| `TICK_RATE` | 10 | simulation ticks per second |
| `MAX_TICKS` | 1200 | fight cap; at cap, highest HP wins, exact tie = draw |
| `SEED` | (unset) | determinism seed; unset = nondeterministic |
| `FIGHTER_HP` | 100.0 | starting HP |
| `FIGHTER_RADIUS` | 12.0 | collision radius |
| `MAX_SPEED` | 120.0 | normal move speed, units/second |
| `SPAWN_RADIUS_FRAC` | 0.35 | spawn circle radius as fraction of arena size |
| `PROJECTILE_SPEED` | 500.0 | units/second |
| `PROJECTILE_DAMAGE` | 8.0 | damage per hit |
| `PROJECTILE_LIFETIME` | 40 | ticks before a projectile fizzles |
| `PROJECTILE_RADIUS` | 2.0 | collision radius |
| `FIRE_COOLDOWN_TICKS` | 5 | ticks between shots |
| `SHIELD_MAX_ENERGY` | 100.0 | shield energy pool |
| `SHIELD_DRAIN` | 2.0 | energy/tick drained while shield is up |
| `SHIELD_REGEN` | 1.0 | energy/tick regenerated while shield is down |
| `SHIELD_HIT_COST` | 15.0 | extra energy cost per blocked hit |
| `DASH_SPEED` | 600.0 | burst velocity during a dash tick, units/second |
| `DASH_COOLDOWN_TICKS` | 30 | ticks between dashes |
| `BOT_TIMEOUT_MS` | 50 | per-tick `decide()` wall-clock budget |
| `BOT_MAX_VIOLATIONS` | 3 | timeouts/exceptions before a bot is benched |

`Config.from_env(overrides: dict)` also accepts a dict of overrides for
programmatic use (the backend will use this).

## Bot API contract (for phase 2)

Subclass `arena.bot_api.FighterBot` and implement:

```python
from arena.bot_api import FighterBot

class MyBot(FighterBot):
    name = "mybot"

    def decide(self, state: dict) -> dict:
        ...
        return {"move": [x, y], "aim": angle, "fire": True,
                "shield": False, "dash": False}
```

**Input `state` (fresh dict every tick, JSON-serializable):**

- `tick`: int, current tick (0-based)
- `fighter`: your own fighter - `id, x, y, vx, vy` (units, units/sec),
  `heading` (radians, body facing), `hp`, `fire_cooldown` / `dash_cooldown`
  (ticks remaining, 0 = ready), `shield_energy`, `shield_active`
- `enemies`: list of `{id, x, y, vx, vy, heading, hp, alive}` for every other fighter
- `projectiles`: list of `{id, x, y, vx, vy, owner, damage, life}` (life = ticks remaining)
- `arena`: `{size, tick_rate}`
- `alive_count`: int

**Output action (all fields optional; missing/invalid -> safe defaults):**

- `move`: `[x, y]` desired velocity in units/sec; magnitude clamped to `MAX_SPEED`;
  NaN/inf/non-numeric -> `[0, 0]`
- `aim`: float radians for the turret; NaN/inf -> `0.0`
- `fire` / `shield` / `dash`: coerced to bool; fire and dash still subject to
  their cooldowns, shield needs `shield_energy > 0`

**Fault policy (the engine never trusts bot output and never crashes):**

- `decide()` runs in a daemon thread with a `BOT_TIMEOUT_MS` budget per tick.
- Exception or timeout -> violation + safe default action (stand still).
- `BOT_MAX_VIOLATIONS` violations -> bot is benched (silently replaced by
  `NullBot`) and the benching is recorded in `result["notes"]`.
- Non-dict returns, NaN/inf vectors, and absurd speeds are clamped server-side.

**Determinism note:** same `SEED` + same bots + same decisions ->
bit-identical result. This holds for bots that are themselves deterministic
and stay within the timeout budget (the timeout path is wall-clock based).

## Combat rules

- Battle royale, 2-8 fighters, last fighter standing wins.
- Firing spawns a projectile at the turret nose in the `aim` direction;
  projectiles die on walls, after `PROJECTILE_LIFETIME` ticks, or on first hit
  (they never hit their owner).
- Shield up: blocks all damage while active but drains `SHIELD_DRAIN`/tick
  plus `SHIELD_HIT_COST` per blocked hit; drops at 0 energy; regenerates
  `SHIELD_REGEN`/tick while down.
- Dash: one-tick burst at `DASH_SPEED` in the move direction (or heading if
  standing still), then `DASH_COOLDOWN_TICKS` cooldown.
- Fighter at 0 HP is eliminated (elimination order is recorded; killer gets a kill).
- At `MAX_TICKS`: highest HP among the living wins; exact HP tie (or zero
  survivors) = draw.

Design choices worth knowing: body `heading` follows movement while `aim` is
an independent turret; fighters spawn evenly on a circle (seeded angle
offset) facing the center; snapshots round floats to 3 decimals (engine keeps
full precision internally).

## CLI

```bash
# full fight, print result
python -m arena sim --bots chaser,chaser --ticks 1200 --seed 42

# watch it as ASCII art (uppercase letter = shield up)
python -m arena sim --bots chaser,spinner,fleer --seed 7 --ascii --ascii-every 50

# dump per-tick snapshots to JSON (this is what the backend will stream)
python -m arena sim --bots chaser,chaser --seed 42 --dump-json /tmp/fight.json

# 8-fighter royale
python -m arena sim --bots chaser,spinner,fleer,random,chaser,spinner,fleer,random --seed 99
```

Built-in bot ids: `spinner`, `chaser`, `fleer`, `random`, `null`
(plus `exploding` and `slow` for fault-tolerance demos).
`--ticks` / `--seed` override `MAX_TICKS` / `SEED`.

Exit result shows winner, reason, ticks, elimination order, kills, and any
bot benching notes.

## Tests

```bash
cd ~/workspace/agent-arena
python -m unittest discover -s tests -v
```

Covers: (a) a 2-bot fight completes with a winner, plus an 8-fighter royale
and the max-ticks draw rule; (b) same seed twice -> identical result AND
identical per-tick snapshots (snapshots also asserted JSON-serializable);
(c) engine survives a bot that raises every tick, a bot returning garbage
actions, and a bot that sleeps past the timeout (benched after N violations).

## Determinism check

```bash
SEED=42 python -m arena sim --bots chaser,spinner --dump-json /tmp/a.json >/dev/null
SEED=42 python -m arena sim --bots chaser,spinner --dump-json /tmp/b.json >/dev/null
diff /tmp/a.json /tmp/b.json && echo IDENTICAL
```

## For phase 2 (house bots)

- Code against `FighterBot.decide(state) -> dict` exactly as documented above;
  keep `decide()` fast (well under `BOT_TIMEOUT_MS`, default 50ms) and
  deterministic if you want replayable fights.
- The engine imports bots as `(fighter_id, bot_instance)` pairs; `simulate()`
  / `Battle` are the entry points the backend will call.
- Per-tick `Battle.snapshot()` dicts are the streaming payload format;
  field names are stable - do not rename without coordinating with phase 3/4.

---

# Agent Arena - House Fighters (Phase 2)

Five deterministic, stdlib-only house bots built on the Phase 1 engine.
Every tuning value is a named class attribute; no magic numbers inline.
`decide()` runs in ~0.01ms (budget is 50ms). No network, no randomness
outside the seeded constructor.

## The fighters

| ID | Codename | Archetype | Style |
|----|----------|-----------|-------|
| `iron-1` | IRON-1 | Rusher | Charges headfirst and never backs down. Sprints at the nearest enemy, weaves on approach, circle-strafes point-blank, dashes to close gaps. Retreats at low HP only while 3+ fighters remain. |
| `hawk-2` | HAWK-2 | Sniper / keeper | Death from a distance, patience as a weapon. Holds a 380-560 comfort zone with predictive lead aim, kites when crowded, panic-dashes, and turns aggressive (180-320) in the endgame. |
| `aegis-4` | AEGIS-4 | Defender | Come closer. It has a shield for that. Anchors near arena center, repositions conservatively, shields incoming fire and point-blank attackers, punishes visitors through its own shield. |
| `jackal-5` | JACKAL-5 | Opportunist | It does not fight fair. It fights wounded targets. Hunts the lowest-HP fighter, third-parties busy enemies, avoids the strongest, finishes isolated victims, duels at close range in the endgame. |
| `wasp-6` | WASP-6 | Wild card (hit-and-run) | Sting, vanish, repeat. Stalks by orbiting at mid range, dives through the weakest prey while healthy, then breaks contact firing backward. |

Public names/taglines/descriptions live only in the `FIGHTERS` registry
(the backend's source of truth for the frontend). Bot classes carry no
marketing copy.

## CLI

```bash
# five-fighter house royale (seed 42)
python -m arena sim --bots iron-1,hawk-2,aegis-4,jackal-5,wasp-6 --seed 42

# watch it live as ASCII
python -m arena watch --bots iron-1,hawk-2,aegis-4,jackal-5,wasp-6 --seed 42

# house bots mix freely with builtins
python -m arena sim --bots iron-1,chaser --seed 7
```

House IDs resolve through `arena.house_fighters.make_house_bot`, which mirrors
the CLI factory convention: same `(bot_id, seed, index)` always builds a
behaviourally identical bot. Seed-derived internal phase offsets keep mirror
matchups from locking into degenerate loops while staying fully replayable.

## Registry schema (for the backend)

```python
from arena.house_fighters import FIGHTERS, HOUSE_BOT_IDS, make_house_bot

FIGHTERS = [
    {
        "id": "iron-1",            # stable technical id (CLI + spawn key)
        "name": "IRON-1",          # public codename
        "tagline": "...",          # one-liner for cards
        "description": "...",      # paragraph for detail views
        "bot_class": RusherBot,    # FighterBot subclass
    },
    ...
]
```

`HOUSE_BOT_IDS` is the ordered id list. `make_house_bot(bot_id, seed, index)`
instantiates one fighter; `build_house_bots("iron-1,hawk-2,...", seed)` parses
a CLI-style spec into `(fighter_id, bot)` pairs ready for `Battle`/`simulate`.
Phase 3 should read names/taglines/descriptions from `FIGHTERS`, never from
bot classes.

## Balance (measured, reproducible)

Five-fighter royales, 30 seeds each (5000-5029 and a held-out 6000-6029):

| Fighter | Seeds 5000-5029 | Seeds 6000-6029 | Combined |
|---------|:---------------:|:---------------:|:--------:|
| IRON-1  | 17% | 7%  | 12% |
| HAWK-2  | 23% | 33% | 28% |
| AEGIS-4 | 23% | 17% | 20% |
| JACKAL-5| 10% | 13% | 12% |
| WASP-6  | 23% | 30% | 27% |

Draws: 1/60 (a mutual elimination). Max-tick hits: 0/60. Average duration
~445 ticks (limit 1200). No fighter exceeds ~60%; none is shut out.

Reproduce with:

```bash
cd ~/workspace/agent-arena
python -m arena sim --bots iron-1,hawk-2,aegis-4,jackal-5,wasp-6 --seed 42
```

Pairwise 1v1s (10 seeds per pair) show rock-paper-scissors dynamics rather
than a strict ordering: AEGIS-4 dominates 1v1s as a stationary turret,
IRON-1 beats HAWK-2/WASP-6/JACKAL-5 10-0 but loses 0-10 to AEGIS-4, and
JACKAL-5 beats everyone 1v1 from full HP. These 1v1 specializations do not
transfer to 5-way royales, which is the product format and the balance
target above.

## Tests

```bash
python -m unittest discover -s tests -v   # 19 tests: 11 engine + 8 house
```

Phase 2 tests (`tests/test_house_fighters.py`): each house fighter completes
a 1v1 vs `NullBot` without crashing or benching; a five-fighter royale
produces a winner by elimination; same seed twice gives identical
winner/ticks/elimination order; the registry carries complete metadata; every
`decide()` runs far under 50ms.

## Engine fix found during Phase 2

Genuine Phase 1 bug: when two fighters closed to within the projectile spawn
offset (~15 units), shots spawned *past* the target and - because the hit
check ran after movement - flew away and always missed. Stacked fighters
became mutually invulnerable and stalled to max ticks. Fixed minimally in
`arena/engine.py` by resolving point-blank hits at spawn time (`_find_hit` /
`_resolve_hit` helpers shared by the firing and projectile phases). All 11
Phase 1 tests still pass; behavior is unchanged for every normal-range shot.

---

# Agent Arena - Backend API (Phase 3)

FastAPI server over the Phase 1 engine and Phase 2 house fighters:
battle lifecycle, live WebSocket streaming, fighter records, hire records,
and the settings store that all later phases build on. Dry-run/mock by
default: no SOL moves, no swaps, hires are recorded with
`payment_status="mock"` and never charged.

## Layout

```
agent-arena/
  backend/
    app.py            FastAPI app + all routes/websocket (uvicorn entrypoint)
    settings_store.py brandable/tunable settings (JSON file + env overlay)
    db.py             SQLite persistence (fighters, battles, snapshots, hires)
    battle_runner.py  runs engine battles in background threads, live snapshots
  data/               created on first run (gitignored):
    settings.json     admin-editable settings (defaults on first run)
    arena.db          battles, snapshots, fighter records, hires
  tests/test_backend.py  12 tests (FastAPI TestClient)
  .env                gitignored; ADMIN_TOKEN lives here (see .env.example)
```

## Setup

```bash
cd ~/workspace/agent-arena
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then set ADMIN_TOKEN in .env
.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

## Settings store

Every brandable value and tunable number lives in `data/settings.json`,
overlaid by env vars (env wins). `GET /api/settings` is public and hides
in-house fields; `PUT /api/admin/settings` persists to the JSON file.

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `project_name` | `ARENA_PROJECT_NAME` | `"ARENA-PROJECT"` | public project name (placeholder) |
| `token_ticker` | `ARENA_TOKEN_TICKER` | `"TKN"` | public token ticker (placeholder) |
| `token_mint` | `ARENA_TOKEN_MINT` | `""` | token mint address (empty until launch) |
| `hire_fee_sol` | `ARENA_HIRE_FEE_SOL` | `0.10` | SOL fee recorded per hire (mock) |
| `betting_house_cut_pct` | `ARENA_BETTING_HOUSE_CUT_PCT` | `5.0` | house cut of betting pools (phase 5) |
| `betting_live` | `ARENA_BETTING_LIVE` | `false` | real-SOL escrow mode (phase 5); default mock |
| `buyback_pct` | `ARENA_BUYBACK_PCT` | `50.0` | % of fees earmarked for buyback (phase 6) |
| `team_pct` | `ARENA_TEAM_PCT` | `50.0` | % of fees to team treasury (phase 6) |
| `buyback_interval_minutes` | `ARENA_BUYBACK_INTERVAL_MINUTES` | `60` | buyback bot cadence (phase 6) |
| `buyback_enabled` | `ARENA_BUYBACK_ENABLED` | `true` | in-house buyback pause toggle - NOT public |
| `fight_max_ticks` | `ARENA_FIGHT_MAX_TICKS` | `1200` | engine tick cap for new battles |
| `playback_tick_ms` | `ARENA_PLAYBACK_TICK_MS` | `100` | WS pacing per tick |

No real project/token names anywhere in code - everything brandable comes
from this store. Phase 7 builds the admin UI on `PUT /api/admin/settings`.

Admin auth: `X-Admin-Token` header, compared against `ADMIN_TOKEN` from the
gitignored `.env`. Without it, admin endpoints return 401 (503 if `ADMIN_TOKEN`
is unset). Fighter metadata overrides live at
`PUT /api/admin/fighters/{id}` (`name`/`tagline`/`description`, stored in DB).

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | - | liveness |
| GET | `/api/settings` | - | public branding + numbers (`buyback_enabled` hidden) |
| PUT | `/api/admin/settings` | admin | update settings store (JSON body of fields) |
| GET | `/api/fighters` | - | house fighters: metadata + win/loss/draw records |
| GET | `/api/fighters/{id}` | - | one fighter |
| PUT | `/api/admin/fighters/{id}` | admin | override name/tagline/description |
| POST | `/api/battles` | - | create battle → 202, runs in background thread |
| GET | `/api/battles` | - | list; filters `status=running\|finished`, `exhibition`, `limit`, `offset` |
| GET | `/api/battles/{id}` | - | full result + placement + snapshots (`?snapshots=false` to skip) |
| POST | `/api/battles/{id}/hire` | - | hire record: `{fighter_id, wallet}` → 201; 409 on duplicate wallet |
| GET | `/api/battles/{id}/hires` | - | hires for a battle |
| WS | `/ws/battles/{id}` | - | live stream or finished-battle replay (below) |

`POST /api/battles` body: `{fighter_ids: [...2-8 registry ids...],
seed?: int, exhibition?: bool (default false), playback_speed?: float (>0,
default 1.0)}`. Exhibition battles are flagged `exhibition=true` - phase 5
must refuse bets on them. Duplicate fighter ids are allowed (engine ids get
`#n` suffixes, e.g. `aegis-4#1`); records are tracked per registry id.

`POST /api/battles/{id}/hire` records the hire at the *current*
`hire_fee_sol` with `payment_status="mock"`. One hire per wallet per battle
(409 on duplicate). Real SOL verification is a later phase.

## WebSocket protocol (`/ws/battles/{id}`)

Messages are JSON. Every per-tick message is the **exact Phase 1
`Battle.snapshot()` dict** (`tick`, `fighters[]`, `projectiles[]` - field
names unchanged) plus a `"type": "snapshot"` envelope key.

1. `{"type":"info","status":"live"|"replay","battle_id":...,"fighter_ids":[...]}`
2. `{"type":"snapshot","tick":N,"fighters":[...],"projectiles":[...]}` × N
3. `{"type":"done","result":{winner,draw,reason,ticks,elimination_order,
   kills,final_hp,placement,notes}}` - then the server closes.

Behavior: connecting mid-fight streams from the current tick live; connecting
after finish replays all stored snapshots then sends `done`. A battle is
"live" only once - once finished, every connect gets the DB replay. Unknown
or unfinished battle ids close with code 4404. Pacing is
`playback_tick_ms / playback_speed` per tick (default ~100ms).

Snapshot field reference (stable contract - do not rename):

- tick-level: `tick`, `fighters[]`, `projectiles[]`
- fighter: `id, x, y, vx, vy, heading, hp, alive, fire_cooldown,
  dash_cooldown, shield_energy, shield_active, kills, benched`
- projectile: `id, owner, x, y, vx, vy, damage, life`

## Tests

```bash
cd ~/workspace/agent-arena
.venv/bin/python -m unittest discover -s tests -v   # 31 tests: 11 engine + 8 house + 12 backend
```

Backend tests (`tests/test_backend.py`, temp data dir per test): settings
get/update + admin auth enforced, fighter list + record update after a
battle, full battle create→result flow (incl. snapshot field contract),
exhibition flag, `fight_max_ticks` propagation from settings, hire +
duplicate-hire 409, WebSocket replay of a finished battle.

## Notes for Phase 4 (frontend)

- Snapshot format above is the render contract; field names are stable.
- Default pacing ~100ms/tick; `playback_speed` on the battle scales it.
- Mid-fight connects start at the current tick (no catch-up backlog);
  finished battles replay from tick 0.
- `GET /api/battles/{id}?snapshots=false` for metadata without the replay.
- Fighter cards: `GET /api/fighters` (metadata + records); admin-editable
  overrides merge over the Phase 2 registry defaults.
- Betting UI (phase 5): refuse `exhibition=true` battles; hire flow posts to
  `/api/battles/{id}/hire` (mock payment for now).

---

# Agent Arena - Frontend (Phase 4)

Single-server web UI: plain HTML/CSS/JS, no npm, no frameworks, no build
step, no CDN — the whole site works offline from the one uvicorn process.

## How it is served

`backend/app.py` mounts `frontend/` with Starlette `StaticFiles` at `/`
(`html=True`, registered AFTER all API/WebSocket routes so they take
precedence). `uvicorn backend.app:app` therefore serves both the API and
the site; `/` returns `index.html`, `/app.js` and `/styles.css` are served
as static files.

## Files

```
agent-arena/
  frontend/
    index.html   page shell: topbar/nav, 4 views, hire + bets panels, modals
    styles.css   dark theme, no external assets
    app.js       the whole app (API client, router, canvas renderer, BetsAPI)
```

## Page-by-page guide

All branding comes from `GET /api/settings` at boot (`project_name` /
`token_ticker` rendered into the header, titles, footer, and copy). No
project/token names, tickers, or fighter ids are hardcoded anywhere in the
frontend. The public settings endpoint hides `buyback_enabled` (in-house
only) and the frontend never references it.

- **Arena (`#/arena`, `#/arena/<battle-id>`)** — the centerpiece. A canvas
  renders the fight: arena walls + grid, fighters as colored circles (color
  hashed deterministically from the fighter id) with name labels and HP
  bars, projectiles as glowing tracers, shield rings (+ energy arc) when
  `shield_active`, motion streaks on dash, explosion particles on
  elimination, dimmed rendering for benched bots, kill badges. Positions are
  interpolated between ticks for smooth motion. Connects to
  `WS /ws/battles/{id}` and handles both envelopes: `info(status=live)` →
  live badge + streaming ticks, `info(status=replay)` → replay of a finished
  battle, then `done` → result overlay (winner/draw, reason, tick count,
  elimination order, rewatch/new-battle buttons). The sidebar lists live and
  recent battles (from `GET /api/battles`), hosts the hire panel, and hosts
  the betting panel. A "New battle" button posts to `POST /api/battles`
  (fighter multi-select, optional seed, exhibition toggle) and jumps
  straight into the live stream. Battles start immediately on creation, so
  there is no "upcoming" queue in v1.
- **Fighters (`#/fighters`)** — card grid from `GET /api/fighters`: name,
  tagline, description, W/L/D record, win rate (+ bar), and a canvas-drawn
  avatar (colored ring + initial, no external images).
- **Leaderboard (`#/leaderboard`)** — fighters sorted by wins, then win
  rate; W/L/D, win rate, total fights.
- **Battles (`#/battles`)** — paginated history from `GET /api/battles`:
  fighters, status, exhibition tag, winner, ticks, reason; each row links
  into the arena replay viewer.

**Hire flow:** pick a battle → pick a fighter (engine id) → enter wallet →
`POST /api/battles/{id}/hire`. The fee line shows the live `hire_fee_sol`
from settings. Payment is labeled mock/simulation throughout v1 and the
receipt quotes the backend's `payment_status="mock"`. Hiring is disabled
once the battle is over.

**Betting UI:** full UI (pick fighter, SOL amount, live pool odds as
parimutuel share + payout multiplier, "my bets" per wallet) with a
`BetsAPI` adapter in `app.js`. The adapter probes
`GET /api/battles/{id}/pool`: if it 404s (Phase 5 not deployed), the panel
switches to a clearly-labeled **DEMO** mode — a deterministic,
seeded-by-battle-id simulated pool plus the user's own demo bets in
`localStorage`, no real SOL anywhere. On `exhibition=true` battles the
betting panel is replaced with "Exhibition — no betting".

## Bets API contract (Phase 5 must implement exactly this)

The UI calls only these endpoints, with these shapes:

```
POST /api/battles/{battle_id}/bets
  body: { fighter_id: string, wallet: string, amount_sol: number }
        fighter_id is the ENGINE id (e.g. "hawk-2" or "hawk-2#1")
  201:  { id, battle_id, fighter_id, wallet, amount_sol, created_at,
          payment_status }
  400:  unknown fighter / invalid amount
  403:  exhibition battle (betting refused)
  404:  unknown battle

GET /api/battles/{battle_id}/bets[?wallet=...]
  200:  { bets: [ { id, battle_id, fighter_id, wallet, amount_sol,
                   created_at, payment_status } ] }

GET /api/battles/{battle_id}/pool
  200:  { battle_id,
          pools: { "<engine_fighter_id>": total_sol, ... },
          total_sol: number, bet_count: number, house_cut_pct: number }
```

Odds shown are parimutuel: share = pool_i / total, payout ≈ total / pool_i.
The same contract is documented in the header comment of
`frontend/app.js`.

## Verification (Phase 4)

- `node --check frontend/app.js`: clean.
- Full suite `python -m unittest discover -s tests`: 31/31 pass after the
  StaticFiles change (11 engine + 8 house + 12 backend).
- `GET /` returns the page; `/api/*` and `/ws/*` unaffected (routes
  registered before the mount take precedence).
- A Python script simulating the page (`/tmp/verify_phase4.py`, throwaway):
  connected to a live battle and a finished battle over WebSocket and
  asserted every field the canvas reads (`tick`, fighter
  `id/x/y/vx/vy/heading/hp/alive/fire_cooldown/dash_cooldown/shield_energy/shield_active/kills/benched`,
  projectile `id/owner/x/y/vx/vy/damage/life`, `done.result.*`) exists in the
  streamed messages; also asserted the betting endpoints are absent so the
  UI takes the demo path.
- No real browser was available in this environment, so the rendering logic
  got a careful code-review pass instead (interpolation, particle
  lifecycle, stale-socket/race guards, exhibition gating).

## Known limitations

- Rendering was code-reviewed, not browser-tested (no browser in this env).
- Arena size is derived from observed coordinates (floor 1000, the engine
  default); the snapshot stream does not carry it.
- The live `done` message's `result` omits `placement` (replay includes it) —
  Phase 3 backend quirk; the UI does not depend on it.
- Hires and demo bets are simulations; no wallets, signing, or real SOL.
- `POST` to a not-yet-implemented API path under the static mount answers
  405 (StaticFiles) rather than 404; the BetsAPI adapter treats any error
  as "Phase 5 not here yet" and uses demo mode.

---

# Agent Arena - Betting Engine (Phase 5)

Parimutuel betting on battle outcomes, settled automatically when each
battle finishes. Custodial escrow, **mock by default**: no real SOL moves
unless the owner explicitly enables live mode.

## Betting rules (plain language - this is the user-facing ruleset)

- Pick a fighter in a battle and bet SOL on it. Every bet goes into one
  shared pool for that battle.
- When the fight ends, the house takes its cut off the top of the pool
  (the `betting_house_cut_pct` setting, default 5%). Everything left is
  the payout pool.
- If your fighter won, you get back:
  `payout pool x (your stake on the winner / all stakes on the winner)`.
  The more of the winning pool is yours, the bigger your share.
- If your fighter lost, you get nothing back. That includes fighters that
  got benched or crashed mid-fight: a benched fighter simply loses, and
  bets on it lose like any other losing bet.
- If the fight is a draw, EVERYONE gets their full stake back and the
  house takes nothing.
- If nobody bet on the winner, everyone gets their full stake back and
  the house takes nothing.
- You cannot bet on exhibition (demo) battles, and you cannot bet once a
  battle has finished. The bet button refuses these up front.
- A battle with no bets settles nothing; there is no settlement record.
- Payouts are rounded to lamports (9 decimals), so winners' payouts can
  differ from the exact payout pool by a lamport or two.

## API

New files: `backend/betting.py` (parimutuel math + settlement),
`backend/escrow_live.py` (real-SOL path, live mode only),
`tests/test_betting.py` (20 tests). New tables in `backend/db.py`: `bets`,
`bet_payouts`, `settlements`, `treasury_fee_events`, `escrow_deposits`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/battles/{id}/bets` | - | place a bet → 201 |
| GET | `/api/battles/{id}/bets[?wallet=]` | - | bets for a battle, optional wallet filter |
| GET | `/api/battles/{id}/pool` | - | live pool totals + `house_cut_pct` from settings |
| GET | `/api/treasury/fees` | admin | fee ledger totals + recent events |

`POST /api/battles/{id}/bets` body: `{fighter_id, wallet, amount_sol}`
(`fighter_id` is the ENGINE id, e.g. `"hawk-2"` or `"hawk-2#1"`).
201 response: `{id, battle_id, fighter_id, wallet, amount_sol, created_at,
payment_status}`. Errors: 400 on exhibition battle, finished battle,
unknown fighter, empty wallet, or `amount_sol <= 0`; 404 on unknown
battle. (The Phase 4 sketch said 403 for exhibition; Phase 5
standardizes bet refusals on 400 - the UI adapter treats any error the
same way.)

`GET /api/battles/{id}/pool` → `{battle_id, pools: {engine_id: total_sol},
total_sol, bet_count, house_cut_pct}`. `house_cut_pct` is read live from
settings on every call, so admin changes show up immediately. Because this
endpoint now answers 200 with real JSON, the Phase 4 `BetsAPI` adapter's
probe succeeds and the betting panel switches from DEMO mode to live
pools with no frontend change.

Settlement runs inside the battle-finish path (`BattleRunner._run`): it
reads `betting_house_cut_pct` / `betting_live` from settings, writes one
`settlements` row, one `bet_payouts` row per bet (`win`/`lose`/`refund`),
and - only when a real cut was taken - one `treasury_fee_events` row with
`source="betting"`. Settlement is contained: if it ever throws, the error
is recorded as a `settlement_error` row instead of breaking battle
completion. If the backend itself crashes mid-battle, the result is a
draw, so bettors are refunded in full.

## Mock vs live mode

`betting_live` setting (env `ARENA_BETTING_LIVE`), default **false**.
Mock mode: `payment_status="mock"`, balances exist only in the DB ledger,
payouts are ledger credits. The whole flow works with zero network
access, and the mock path never imports `solana`/`solders` (asserted by a
test).

Live mode is the owner's explicit future decision, not a default. Code
path (in `backend/escrow_live.py`, imported lazily only when live):

- Bet placement: the bettor first sends `amount_sol` SOL to the escrow
  wallet, then POSTs. The backend scans recent escrow-wallet signatures
  via Helius RPC and accepts the bet only on finding an UNCLAIMED
  transaction with sender == wallet, destination == escrow, matching
  amount, inside a recent slot window. The signature is claimed in
  `escrow_deposits` so one tx can't fund two bets. `payment_status`
  becomes `"confirmed"`. Misconfigured live mode (flag on, keys missing)
  answers 503, never a silent mock bet.
- Settlement: payouts for `confirmed` bets are sent as real transfers
  from the escrow wallet; signatures land in `bet_payouts.paid_tx`. The
  ledger rows are written FIRST, so a failed send leaves `paid_tx` NULL
  (visible, needs manual handling) instead of losing the record. Mock
  bets placed before the flag was flipped always settle as ledger
  credits, never on-chain.

Enabling live mode requires, in the gitignored `.env`:

```
ARENA_BETTING_LIVE=1
ESCROW_WALLET=<base58 pubkey of the backend custodial wallet>
ESCROW_PRIVATE_KEY=<base58 secret key, wallet must be FUNDED>
HELIUS_API_KEY=<helius key, reuses the RugRadar key>
```

Owner checklist before flipping: keep only a small hot balance in the
escrow wallet (sweep the rest to cold storage); the house cut accumulates
there until the buyback bot moves it; deposit verification trusts Helius
RPC responses. Pinned deps for this path only (`requirements.txt`):
`solana==0.36.9`, `solders==0.26.0`.

## Treasury fee ledger (for Phase 6)

`treasury_fee_events` rows: `{id, battle_id, source, amount_sol,
created_at}`. `source` is `"betting"` (house cut of a settled pool,
written by settlement) or `"hire"` (hire fee, written by the hire
endpoint - every hire is now logged here too, so ALL fee revenue sits in
one ledger). `GET /api/treasury/fees` (admin token) returns
`{totals: {betting_sol, hire_sol, total_sol}, events: [...]}`.

## Tests

```bash
cd ~/workspace/agent-arena
.venv/bin/python -m unittest discover -s tests -v   # 51 tests: 11 engine + 8 house + 12 backend + 20 betting
```

`tests/test_betting.py` covers: bet placement contract shape + mock
status; 404/400 validation matrix (unknown battle, unknown fighter,
bad amount, empty wallet, exhibition refused, finished battle refused);
one wallet placing multiple bets; hire duplicate-409 unchanged; pool
math + `house_cut_pct` reflecting live admin changes; bets list + wallet
filter; parimutuel math against HAND-COMPUTED stakes (7.0 total at 10%
cut -> 0.7 cut, 6.3 pool, payouts 3.15 / 1.05 / 2.10 / 0); draw ->
full refunds, no cut, no fee event; no-winning-bets -> full refunds;
zero bets -> no settlement row; benched fighter bets lose; hire fees
logged to the ledger; admin auth on `/api/treasury/fees`; end-to-end
battle-finish settlement with ledger invariant
(payouts + cut == total stakes); mock mode never importing
solana/solders; live flag without escrow config -> 503 with no bet
recorded.

## Notes for Phase 6 (buyback bot)

- Read fees with `Database.fee_totals()` or `GET /api/treasury/fees`
  (admin). `betting_sol` + `hire_sol` = everything the platform has
  taken; nothing else writes to this table.
- House-cut events reference their `battle_id`; hire events do too.
- `bet_payouts.paid_tx` is NULL for all mock-mode payouts; in live mode
  it holds the on-chain payout signature, or NULL if the send failed
  (handle manually before counting it as paid).
- The in-house `buyback_enabled` toggle (Phase 3 settings) stays the
  pause switch; this phase only records the fees.

---

# Agent Arena - Treasury + Buyback Bot (Phase 6)

The buyback bot turns platform fee revenue into token buybacks and burns.
MOCK BY DEFAULT: the bot simulates swaps locally with zero network calls
until the owner explicitly enables live mode.

## How a cycle works

`python -m bot.buyback --once` (single cycle) or `--loop` (repeats every
`buyback_interval_minutes`):

1. **Pause check (in-house only).** If `buyback_enabled` is false, the bot
   logs "paused" and exits without touching anything. Fees keep
   accumulating in the ledger untouched. This toggle is never public.
2. **Read new fees.** The bot reads only *unallocated* fee events from
   `treasury_fee_events` (via a `LEFT JOIN` against the
   `buyback_allocations` table). Each round takes `buyback_pct`% of the
   NEW fees as its SOL budget; `team_pct`% is accounted as the team's
   share. Unsplit remainder stays in the treasury.
3. **Spend + burn.** Mock: simulate the swap at `buyback_mock_rate`
   tokens/SOL and record fake tx ids prefixed `mock_`. Live: quote + swap
   SOL->token via the Jupiter API, then burn the bought tokens with the
   SPL Token Burn instruction.
4. **Atomic record.** `Database.record_buyback_round()` inserts, in ONE
   transaction: `buyback_allocations` rows for each consumed fee event,
   one `burn_events` row
   (`created_at, sol_spent, tokens_bought, tokens_burned, buy_tx, burn_tx,
   dry_run`), and one `team_allocations` row. If the spend fails before
   this call, nothing is allocated and the next run retries.

### Allocation / idempotency design

- A fee event is spendable **exactly once**. `buyback_allocations` has
  `fee_event_id` as its PRIMARY KEY, so even two overlapping runs cannot
  double-allocate: the second INSERT fails on the conflict.
- Allocation happens only AFTER a successful swap+burn. A failed round
  never "loses" fees - the next cycle picks them up unchanged.
- `GET /api/treasury/stats`'s `treasury_balance_sol` = unallocated fees
  (taken minus allocated). It is ledger-based in both modes.

## Mock vs live mode

`buyback_live` setting (env `ARENA_BUYBACK_LIVE`), default **false**.
Mock mode: zero network calls, `dry_run=true`, and the module never
imports `solana`/`solders` (asserted by a test). The whole flow works
offline and is the default forever until the owner opts in.

Live mode (owner's explicit decision, default off) runs the real flow in
`bot/buyback.py` with stdlib-only HTTP (`urllib`) and `solders` for
signing; all live-only imports are lazy, inside the live functions. It
requires, in the gitignored `.env`:

```
ARENA_BUYBACK_LIVE=1            # the explicit opt-in; without it nothing live runs
TREASURY_WALLET=<base58 pubkey of the bot's hot wallet>
TREASURY_PRIVATE_KEY=<base58 secret key of the hot wallet>
TEAM_WALLET=<base58 pubkey>      # only needed when team_sweep_enabled=true
HELIUS_API_KEY=<helius key, reuses the RugRadar key>
```

plus `token_mint` set in settings (empty by default). Live-mode
misconfiguration fails LOUDLY (`BuybackError` with the exact missing
pieces, non-zero exit) - it can never silently fall back to mock.
The config check runs BEFORE any `solders` import.

Live flow detail:

- Jupiter swap API: `https://lite-api.jup.ag/swap/v1` (free keyless tier,
  falls back to `https://api.jup.ag/swap/v1`). Quote
  (`inputMint=So111...`, `outputMint=token_mint`, `slippageBps=100`), then
  POST `/swap` (`quoteResponse`, `userPublicKey`, `wrapAndUnwrapSol=True`)
  and sign + send the returned versioned transaction.
- Burn: the bot reads the bought token balance from the wallet's
  associated token account and burns ALL of it via the SPL Token Burn
  instruction (index 8), then waits for confirmation.
- Confirmation timeout: if a tx is sent but not confirmed within 90s, the
  round aborts WITHOUT recording (no burn row, fees unallocated) and the
  owner must check the signature on an explorer before any manual retry
  (double-spend risk).

## Hot-balance discipline (live mode)

Bulk treasury stays in the owner's COLD wallet; the bot only ever touches
its own hot wallet. Before each round:

- `buyback_hot_sol_cap` (default 5.0 SOL): if the hot wallet holds MORE
  than the cap, the bot refuses to run and logs "sweep the excess to the
  cold wallet first".
- The round spends at most the hot wallet's spendable balance (minus a
  0.02 SOL tx-fee reserve). If the hot balance is smaller than the
  budgeted amount, it spends what is there and logs the shortfall; if it
  is empty, the round fails loudly and the owner tops it up from cold.
- The bot NEVER moves money into or out of the cold wallet - funding the
  hot wallet is an owner-side manual step.

## Team fee accounting

`team_pct`% of each round's fees belongs to the team, not to buybacks.
Every round writes a `team_allocations` row (amount, timestamp). Mock
mode just records the row. Live mode: `team_sweep_enabled` (default
false) optionally sweeps the round's team share to `TEAM_WALLET` and
stores the signature in `swept_tx`; with it off, the row stays unswept
for manual handling. Enabling the sweep without `TEAM_WALLET` set is a
loud config error.

## Public dashboard

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/treasury/stats` | - | `{treasury_balance_sol, total_fees_sol, total_burned_tokens, burn_count, token_ticker, project_name}` |
| GET | `/api/treasury/burns` | - | paginated burn history (`limit`, `offset`), newest first |

`buyback_enabled` (and every other in-house field: `buyback_live`,
`buyback_hot_sol_cap`, `team_sweep_enabled`, `buyback_mock_rate`) is
hidden from `GET /api/settings` and never appears in these responses.

The frontend adds a **Treasury** nav item and view (`#/treasury`):
stat cards for treasury balance / total fees / total burned / round
count, plus a paginated burn-history table. All branding comes from
`GET /api/settings` at boot (`project_name`, `token_ticker`) - no names,
tickers, or fighter ids are hardcoded in the frontend.

## Running it on a schedule

Cron (simplest, matches the mock-first posture):

```cron
# buyback cycle every 30 minutes
*/30 * * * * cd /home/hatch/workspace/agent-arena && .venv/bin/python -m bot.buyback --once >> /var/log/agent-arena/buyback.log 2>&1
```

systemd alternative: a `agent-arena-buyback.service` (oneshot,
`ExecStart=.../.venv/bin/python -m bot.buyback --once`,
`WorkingDirectory=/home/hatch/workspace/agent-arena`) plus a
`agent-arena-buyback.timer` (`OnCalendar=*:0/30`). Or just run
`python -m bot.buyback --loop` under a process manager - it sleeps
`buyback_interval_minutes` between cycles and logs to stdout.

## Tests

```bash
cd ~/workspace/agent-arena
.venv/bin/python -m unittest discover -s tests -v   # 62 tests: 51 (ph1-5) + 11 (ph6)
```

`tests/test_buyback.py` covers: (a) mock full cycle - seed betting+hire
fee events, run, burn event has `sol_spent` = buyback_pct of new fees
(1.25 SOL of 2.5 at 50%), `dry_run=true`, `mock_` tx ids, correct token
math, and zero network (urlopen blocked) + no solana/solders import;
(b) second run allocates nothing (no double-spend); (c)
`buyback_enabled=false` -> "paused", fees untouched, re-enable picks them
up; (d) team_pct accounting (70/20 split leaves the 10% remainder
unallocated); (e) live mode with missing env or empty `token_mint`
raises loudly and records nothing - never a silent mock; plus the public
`/api/treasury/stats` + `/api/treasury/burns` shapes, the in-house fields
hidden from public settings, and the treasury view's branding-via-settings.

## Notes for Phase 7 (admin panel)

- The admin panel will flip `buyback_enabled` (the in-house pause toggle)
  and edit `buyback_pct`, `team_pct`, `buyback_interval_minutes`,
  `buyback_live`, `buyback_hot_sol_cap`, `team_sweep_enabled`,
  `buyback_mock_rate` via the existing `PUT /api/admin/settings`.
  All five new fields are already in `PRIVATE_FIELDS`, so flipping any of
  them never leaks into `GET /api/settings` or the public dashboard.
- Reminder for public copy (hard project rule from Phase 5): the site must
  say fees *fund buybacks* - never "every fee is auto-burned in real
  time". The dashboard shows ledger-based balances, not live chain
  balances.
- Still owner-side before any live flip: launch the token, set
  `token_mint`, fund the hot wallet (under `buyback_hot_sol_cap`), keep
  `TREASURY_PRIVATE_KEY` in the gitignored `.env`, and legally review the
  betting/buyback flow.

---

# Agent Arena - Admin Panel (Phase 7)

The private control room: every brandable value and every number is editable
here - no code changes ever. Plain HTML/CSS/JS, no frameworks, no CDNs,
consistent with the Phase 4 frontend.

## URL and access

`http://<host>:8000/admin.html` - served by the same StaticFiles mount as
the public site (no extra server needed). It is deliberately obscure AND
authenticated:

- **Not linked** from any public nav in `frontend/index.html` (asserted by
  test), plus `<meta name="robots" content="noindex">`.
- **Token gate**: on load the page asks for the admin token (password
  input). The token lives in `sessionStorage` only - never in code, never
  in a URL, never logged. Every API call sends it as the `X-Admin-Token`
  header. A 401 clears it and re-shows the lock screen with "invalid
  token".

## Token setup

In the gitignored `.env` (see `.env.example`):

```
ADMIN_TOKEN=<generate: python3 -c "import secrets; print(secrets.token_hex(32))">
```

Without it, all admin endpoints answer 503 ("Admin not configured").

## Panels

- **Branding** (`project_name`, `token_ticker`, `token_mint`) - the
  placeholders the owner fills in later; the public site reads them from
  `GET /api/settings` immediately after saving.
- **Fees & economics** (`hire_fee_sol`, `betting_house_cut_pct`,
  `buyback_pct`, `team_pct`) - with a live-computed sanity line: "of every
  1 SOL in fees: X to buybacks, Y to team" (plus the unsplit remainder,
  which stays in the treasury). Changes apply to new hires, new bets, and
  new buyback rounds immediately. Fields pinned by environment variables
  show an `ENV-LOCKED` badge: file writes won't take effect until the env
  var is unset.
- **Buyback control** (marked IN-HOUSE): the big unmistakable
  `buyback_enabled` pause toggle (saves immediately - it is the kill
  switch), `buyback_interval_minutes`, `buyback_live` (typed "GO LIVE"
  confirmation + real-money warning text), `buyback_hot_sol_cap`,
  `team_sweep_enabled`, `buyback_mock_rate`. Helper text carries the
  standing rule: public copy must say fees *fund* buybacks, never promise
  real-time auto-burn.
- **Betting control**: `betting_live` behind the same typed-confirmation
  treatment (mock ledger vs real SOL escrow).
- **Fighters**: table of all house fighters with W/L/D; inline edit of
  name/tagline/description via `PUT /api/admin/fighters/{id}` - this is how
  rebranding happens, no code changes.
- **Treasury ops** (read-only): fee totals from `GET /api/treasury/fees`
  (betting cuts, hire fees), recent fee events, burn history. The loop
  handles execution; the panel only watches.
- **Danger zone**: no destructive actions in v1 - no fund-moving buttons.
  Documents that live-money actions stay manual/owner-side (below).

## Field reference

| Field | Type | Default | What it does |
|---|---|---|---|
| `project_name` | text | `"ARENA-PROJECT"` | public site name (placeholder) |
| `token_ticker` | text | `"TKN"` | public token ticker (placeholder) |
| `token_mint` | text | `""` | token mint address; empty until launch |
| `hire_fee_sol` | float | `0.10` | SOL recorded per fighter hire |
| `betting_house_cut_pct` | float | `5.0` | % house cut taken off the top of each settled betting pool |
| `buyback_pct` | float | `50.0` | % of each buyback round's new fees budgeted for SOL->token swaps |
| `team_pct` | float | `50.0` | % of each round's fees accounted as the team's share |
| `buyback_interval_minutes` | int | `60` | buyback bot cadence (`--loop` mode) |
| `buyback_enabled` | bool (in-house) | `true` | the pause switch: off = bot logs "paused", fees accumulate untouched |
| `buyback_live` | bool (in-house) | `false` | off = mock swaps at `buyback_mock_rate`; on = real Jupiter swaps + on-chain burns |
| `buyback_mock_rate` | float (in-house) | `10000.0` | simulated tokens per SOL in mock mode |
| `buyback_hot_sol_cap` | float (in-house) | `5.0` | live mode refuses to run if the hot wallet holds more; sweep excess to cold first |
| `team_sweep_enabled` | bool (in-house) | `false` | live mode optionally sweeps the round's team share to `TEAM_WALLET` |
| `betting_live` | bool (in-house) | `false` | off = mock ledger bets; on = real SOL escrow, deposit verification via Helius |

In-house fields are exactly `PRIVATE_FIELDS` in `backend/settings_store.py`
and never appear on `GET /api/settings`, `/api/treasury/stats`, or
`/api/treasury/burns`.

## In-house toggle rules

- `buyback_enabled=false` pauses everything buyback-side without losing a
  lamport: fees keep accumulating in the ledger, the bot exits cleanly, and
  re-enabling picks them up. This is the safe state to leave the platform
  in while anything is uncertain.
- `buyback_live` / `betting_live` only flip off->on behind the typed
  "GO LIVE" confirmation, which spells out the real-money consequences and
  the owner checklist. Flipping back off is always one click.
- Public wording must say fees *fund* buybacks - never "every fee is
  auto-burned in real time". The pause toggle exists precisely so that
  promise can never be contradicted onchain.

## Security notes

- Token auth on every admin route (`X-Admin-Token` vs `ADMIN_TOKEN`);
  401 without/with a wrong token, 503 when unconfigured.
- `noindex` meta, unlinked from public nav, separate page (not a view in
  `index.html`).
- No secrets in code; the panel never moves funds - the only
  money-adjacent writes are settings values and fighter metadata.
- JS quirk handled client-side: the backend type-checks settings strictly
  (JSON ints are rejected for float fields), so `admin.js` serializes
  whole-number floats as `5.0` form before PUT.

## Still owner-side

Funding the treasury/escrow hot wallets (small hot balance, bulk in cold),
sweeping excess hot balance to cold, flipping the live flags, launching the
token on pump.fun and pasting the mint into Branding, handling failed live
payouts manually (NULL `paid_tx` in the ledger), and legal review of the
betting/buyback flow.

## Tests

```bash
cd ~/workspace/agent-arena
.venv/bin/python -m unittest discover -s tests -v   # 70 tests: 62 (ph1-6) + 8 (ph7)
node --check frontend/admin.js
```

`tests/test_admin_privacy.py` (8 tests): public `GET /api/settings`
contains none of the private fields (before and after admin writes);
admin endpoints 401 without token and 503 when `ADMIN_TOKEN` is unset; a
branding change via the admin API appears on the next public
`GET /api/settings`; `/api/treasury/stats` + `/api/treasury/burns` carry no
private fields; `/admin.html` serves 200 with the noindex meta and no
hardcoded brand or secret; `index.html`'s nav has no link to it; the token
is only ever sent as a header, never in a URL.

## Notes for Phase 8 (final e2e)

- The admin UI itself was code-reviewed, not browser-tested (no browser in
  this env): Phase 8 should drive `/admin.html` in a real browser -
  unlock with the token, flip `buyback_enabled`, save a branding change,
  edit a fighter, and confirm the public site reflects branding while
  private fields stay hidden in DevTools network responses.
- The scary-confirm modal path (type "GO LIVE") needs a real-browser pass;
  unit tests cover the API it calls, not the modal.
- Confirm the full fee loop end to end: bet settlement writes fee events,
  `python -m bot.buyback --once` (mock) allocates them, burn rows appear,
  and the admin Treasury ops panel shows them.
- Re-verify `index.html` has no admin link after any Phase 8 frontend
  touch-ups (the privacy test guards this in CI).

---

# Agent Arena - End-to-End Money Loop (Phase 8)

One repeatable script that proves the ENTIRE v1 money loop headlessly:
`tests/test_e2e.py`. It uses an isolated temp data dir per run (the real
`data/` dir is never touched) and a fixed battle seed, so it is fully
deterministic.

## Run it

```bash
cd ~/workspace/agent-arena
.venv/bin/python -m pytest tests/test_e2e.py -v   # the e2e loop only
.venv/bin/python -m pytest tests/ -q               # full suite (71 tests)
# or the unittest runner, as in earlier phases:
.venv/bin/python -m unittest discover -s tests
```

(`pytest` lives in `.venv`; install once with `.venv/bin/pip install pytest`.)

## What it proves, step by step

1. **Admin settings**: `PUT /api/admin/settings` sets placeholder branding
   (`E2E-TEST-PROJECT` / `E2E`), `hire_fee_sol=0.1`,
   `betting_house_cut_pct=10`, `buyback_pct=50`, `team_pct=50`,
   `buyback_enabled=true`. Public `GET /api/settings` reflects the branding
   and numbers while hiding every `PRIVATE_FIELDS` entry.
2. **Battle**: `POST /api/battles` with 4 house fighters + fixed seed → 202.
3. **Hires**: 2 wallets hire 2 fighters → 201s with `payment_status="mock"`
   and the fee read from settings (0.1 SOL each); duplicate wallet → 409.
4. **Bets**: 3 wallets place known stakes (2.0 + 1.0 on hawk-2, 3.0 on
   iron-1) → 201s with the exact response shape; `GET .../pool` totals match
   the stakes exactly (pools `{iron-1: 3.0, hawk-2: 3.0, ...}`, total 6.0).
5. **Settlement**: after the battle finishes (winner hawk-2, pinned as a
   determinism regression net), the test hand-computes the parimutuel
   outcome from the known stakes — total 6.0, house cut 0.6 (10%), payout
   pool 5.4, winner stake 3.0 → WA 3.6 win, WB 1.8 win, WC 0.0 lose — and
   asserts the DB rows match to the lamport. Ledger invariant
   (payouts + cut == total stakes) holds; fighter W/L records update
   (winner +1 win, rest +1 loss); treasury fee events exist for the 0.6
   betting cut AND both 0.1 hire fees.
6. **Buyback**: `bot.buyback.run_cycle()` is called in-process in mock mode
   against the test app's DB/settings (zero network: `urlopen` is blocked
   during the call, and `solana`/`solders` stay unimported). It records one
   `burn_events` row with `dry_run=true`, `sol_spent=0.4` (50% of the 0.8
   SOL in new fees), `mock_` tx ids; the fee events are marked allocated
   (unallocated total 0.0); a second run is a no-op (`no_fees`) — no
   double-spend.
7. **Public treasury**: `GET /api/treasury/stats` shows `total_fees_sol=0.8`,
   `treasury_balance_sol=0.0`, `burn_count=1`, `total_burned_tokens=4000.0`;
   `GET /api/treasury/burns` lists the burn. A recursive sweep asserts no
   private field appears in ANY public response touched during the test.
8. **Frontend**: `GET /` → 200; `/admin.html` → 200 with the noindex meta
   and no link to it from `/`.

The test prints a `[E2E money-loop trace]` summary (battle id, winner,
stakes, expected vs actual payouts, fee events, burn row) on success.

## Bugs found and fixed by this phase

- **Phase 3 — battle seed ignored by the engine** (`backend/battle_runner.py`):
  `POST /api/battles {"seed": N}` stored the seed and seeded the bots, but
  the engine's spawn-geometry RNG was seeded from `Config.seed`, which the
  backend never set — so identical requests produced different fights and
  the "same seed → same result" contract was broken. The e2e determinism
  pin caught it (CLI seed-7 fight ≠ backend seed-7 fight). Fixed minimally:
  `create()` now builds the battle on `dataclasses.replace(engine_cfg,
  seed=seed)` with the resolved battle seed. All 70 prior tests still pass.
- **Phase 5 test race** (`tests/test_betting.py`): `fight_max_ticks=30`
  let the background battle finish before the three bet POSTs landed,
  flaking with 400 ("betting is closed"). Raised to 300 ticks (still ~0.2s);
  no assertion changed.

## Still needs a real browser (not covered headlessly)

- Driving `/admin.html` end to end: unlock with the token, flip
  `buyback_enabled`, save branding, edit a fighter, and confirm the public
  site reflects branding while private fields stay hidden in DevTools.
- The typed "GO LIVE" scary-confirm modal path.
- Canvas rendering of the arena view itself.

---

# Agent Arena - FT Economy (V2 Phase 1)

Fighters are **born**, not minted. They are called **FTs**, not NFTs.

## Player flow

1. **Born** (`/#/born`): pay the born fee (default 0.1 SOL, mock by default),
   pick a name, archetype, and color. Rarity is rolled at payment: Common
   50% / Rare 30% / Epic 15% / Legendary 5%, with a 1.00x-1.25x stat
   multiplier applied to HP and projectile damage. Each FT gets a unique
   `FT-XXXX` id and an HMAC ownership certificate. Half the born fee feeds
   the season prize pool, half goes to the treasury.
2. **Enter** (`/#/my-fighters` -> `/#/queue`): enter an owned FT into the next
   official battle (default 0.02 SOL entry fee, mock by default). Entries
   are only accepted during the **entry window** (default 5 minutes before
   each battle, configurable via `entry_window_minutes`).
3. **Official battles**: the scheduler (off by default; set
   `ARENA_OFFICIAL_SCHEDULER=1`) runs one battle every
   `official_battle_interval_minutes` (default 10), alternating Duel (1v1)
   and Royale (4-way free-for-all). The draw is weighted random with
   longest-wait priority; short queues are filled with house fighters, who
   never take prize money. Only scheduler-created battles are official.
   Entry fee: 80% to the battle prize pool, 20% to the season pool.
4. **Settlement**: the winner's owner takes the whole battle pool. A draw
   rolls the pool into the season pool. A house-fighter win goes to the
   treasury. Season standings: win 3 pts, draw 1 pt.
5. **Season** (`/#/season`): 15 days, top-3 split 60/25/15 of the season
   pool (born shares + entry shares + rolled-over battle pools). Season
   rollover and payouts run on the scheduler tick.

All numbers are admin-configurable (`/#/admin`, token-gated, not linked in
the nav). Birth, entry, and payouts are **mock by default** — no SOL moves
until the owner flips `born_live`, `entry_live`, and `payouts_live`
(in-house only) with the treasury and Helius configured.

## Legal / safety notes (read before going live)

- The **birth rarity lottery** is a paid random draw: get legal review for
  your jurisdiction before enabling `born_live`.
- **Betting** (V1 parimutuel) carries gambling/regulatory exposure: legal
  review before `betting_live`.
- Paid sparring/friend matches (later phase) also need legal review.
- Never enable a live flag until its owner checklist is done (treasury
  wallet set, hot balance capped, Helius key set). Public wording must say
  "fees fund buybacks", never "every fee is auto-burned".
