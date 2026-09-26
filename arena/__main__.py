"""Headless CLI runner.

Usage:
    python -m arena sim --bots chaser,chaser --ticks 1200 --seed 42
    python -m arena sim --bots chaser,spinner,fleer --ascii --ascii-every 100
    python -m arena sim --bots chaser,chaser --dump-json /tmp/fight.json
"""

import argparse
import json
import sys

from .bots import BUILTIN_BOTS
from .house_fighters import HOUSE_BOT_IDS, make_house_bot
from .config import Config
from .engine import Battle


def build_bots(spec: str, seed: int) -> list:
    ids = [b.strip() for b in spec.split(",") if b.strip()]
    if not 2 <= len(ids) <= 8:
        raise SystemExit(f"Need 2-8 bots, got {len(ids)}: {spec!r}")
    bots = []
    for i, bid in enumerate(ids):
        key = bid.lower()
        if key in BUILTIN_BOTS:
            bots.append((f"{bid}-{i+1}", BUILTIN_BOTS[key](seed, i)))
        elif key in HOUSE_BOT_IDS:
            bots.append((f"{bid}-{i+1}", make_house_bot(key, seed, i)))
        else:
            raise SystemExit(
                f"Unknown bot {bid!r}. Available: "
                f"{', '.join(sorted(BUILTIN_BOTS) + HOUSE_BOT_IDS)}")
    return bots


def render_ascii(snapshot: dict, size: float, width: int = 60, height: int = 30) -> str:
    grid = [["." for _ in range(width)] for _ in range(height)]

    def to_cell(x, y):
        cx = min(width - 1, max(0, int(x / size * width)))
        cy = min(height - 1, max(0, int(y / size * height)))
        return cx, cy

    for p in snapshot["projectiles"]:
        cx, cy = to_cell(p["x"], p["y"])
        if grid[cy][cx] == ".":
            grid[cy][cx] = "*"
    for idx, f in enumerate(snapshot["fighters"]):
        if not f["alive"]:
            continue
        cx, cy = to_cell(f["x"], f["y"])
        ch = chr(ord("A") + idx % 26)
        grid[cy][cx] = ch.upper() if f["shield_active"] else ch.lower()
    border = "#" * (width + 2)
    lines = [border]
    for row in grid:
        lines.append("#" + "".join(row) + "#")
    lines.append(border)
    legend = " ".join(
        f"{chr(ord('A')+i)}={f['id']}(hp={f['hp']:.0f})"
        for i, f in enumerate(snapshot["fighters"]) if f["alive"])
    lines.append(f"tick {snapshot['tick']}  alive: {legend or 'none'}")
    return "\n".join(lines)


def cmd_sim(args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    if args.ticks is not None:
        cfg.max_ticks = args.ticks
    if args.seed is not None:
        cfg.seed = args.seed
    bots = build_bots(args.bots, cfg.seed if cfg.seed is not None else 0)
    battle = Battle(bots, cfg)

    snapshots: list[dict] = []
    dump_every = args.snapshot_every if args.dump_json else 0
    ascii_every = args.ascii_every if args.ascii else 0

    if ascii_every:
        print(render_ascii(battle.snapshot(), cfg.arena_size))
    tick = 0
    while not battle.is_over():
        battle.step()
        tick += 1
        if dump_every and tick % dump_every == 0:
            snapshots.append(battle.snapshot())
        if ascii_every and tick % ascii_every == 0:
            print()
            print(render_ascii(battle.snapshot(), cfg.arena_size))
    if dump_every:
        snapshots.append(battle.snapshot())

    result = battle.result()
    print()
    print(f"winner: {result['winner'] or 'DRAW'}  "
          f"reason: {result['reason']}  ticks: {result['ticks']}")
    print(f"elimination order: "
          f"{' > '.join(result['elimination_order']) or '(none)'}")
    print(f"kills: {result['kills']}")
    if result["notes"]:
        for note in result["notes"]:
            print(f"note: {note}")

    if args.dump_json:
        payload = {"config": {k: getattr(cfg, k) for k in vars(cfg)},
                   "result": result, "snapshots": snapshots}
        with open(args.dump_json, "w") as fh:
            json.dump(payload, fh)
        print(f"wrote {len(snapshots)} snapshots to {args.dump_json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arena",
                                     description="AI fighting arena simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sim", help="run a headless fight")
    p.add_argument("--bots", required=True,
                   help=f"comma-separated bot ids (2-8). Available: "
                        f"{', '.join(sorted(BUILTIN_BOTS) + HOUSE_BOT_IDS)}")
    p.add_argument("--ticks", type=int, default=None,
                   help="max ticks (overrides MAX_TICKS)")
    p.add_argument("--seed", type=int, default=None,
                   help="random seed (overrides SEED)")
    p.add_argument("--ascii", action="store_true",
                   help="print ASCII arena snapshots during the fight")
    p.add_argument("--ascii-every", type=int, default=100,
                   help="tick interval for ASCII snapshots")
    p.add_argument("--dump-json", default=None, metavar="PATH",
                   help="write per-tick snapshots + result to a JSON file")
    p.add_argument("--snapshot-every", type=int, default=1,
                   help="tick interval for snapshots in the JSON dump")
    p.set_defaults(func=cmd_sim)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
