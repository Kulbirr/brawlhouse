"""Phase 1 (FT economy): player-owned fighters.

An FT ("Fighter Token", not an NFT) is a player-owned fighter: a name,
an archetype (one of the five house AI styles), a color, and a lottery
rarity that scales its stats. Behaviour is always one of the proven
house bots; rarity only multiplies HP and projectile damage, bounded so
upsets stay possible.

This module holds the pure domain logic (no DB, no HTTP):
  - RARITIES: the lottery table (probability -> stat multiplier)
  - roll_rarity(): weighted draw, injectable RNG for tests
  - next_ft_id(): FT-0001 style unique keys
  - sign_cert()/verify_cert(): HMAC ownership certificates, verifiable
    off-site without a blockchain
  - archetype_bot_class(): archetype id -> house bot class
"""

from __future__ import annotations

import hashlib
import hmac
import os
import random
from pathlib import Path

# (rarity name, probability, stat multiplier). Probabilities sum to 1.0.
RARITIES: tuple[tuple[str, float, float], ...] = (
    ("Common", 0.50, 1.00),
    ("Rare", 0.30, 1.08),
    ("Epic", 0.15, 1.16),
    ("Legendary", 0.05, 1.25),
)

RARITY_NAMES = [r[0] for r in RARITIES]
RARITY_MULT = {r[0]: r[2] for r in RARITIES}

# Archetype ids are house fighter ids; the mapping to bot classes is
# resolved lazily so importing this module never pulls in bot code
# unless asked.
ARCHETYPE_IDS = ("iron-1", "hawk-2", "aegis-4", "jackal-5", "wasp-6")


def roll_rarity(rng: random.Random | None = None) -> tuple[str, float]:
    """Lottery draw: fixed price for everyone, luck decides strength.

    Returns (rarity_name, stat_multiplier). Pass a seeded random.Random
    for deterministic tests.
    """
    rng = rng or random.Random()
    x = rng.random()
    lo = 0.0
    for name, prob, mult in RARITIES:
        lo += prob
        if x < lo:
            return name, mult
    return RARITIES[-1][0], RARITIES[-1][2]


def next_ft_id(existing_ids: list[str]) -> str:
    """Next unique FT key: FT-0001, FT-0002, ... (max existing + 1)."""
    best = 0
    for fid in existing_ids:
        if isinstance(fid, str) and fid.startswith("FT-"):
            try:
                best = max(best, int(fid[3:]))
            except ValueError:
                continue
    return f"FT-{best + 1:04d}"


def _cert_secret(data_dir: str | Path | None = None) -> bytes:
    """Server secret for ownership certificates.

    Env FT_CERT_SECRET wins; otherwise a random secret is generated once
    and persisted under the data dir (gitignored), so certs stay valid
    across restarts but never ship with the code.
    """
    env = os.environ.get("FT_CERT_SECRET", "").strip()
    if env:
        return env.encode()
    path = Path(data_dir) if data_dir else Path(__file__).resolve().parent.parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    secret_file = path / "ft_cert_secret"
    if secret_file.exists():
        return secret_file.read_bytes()
    secret = os.urandom(32)
    # best effort: never crash the app over the cert secret
    try:
        secret_file.write_bytes(secret)
    except OSError:
        pass
    return secret


def sign_cert(ft_id: str, owner_wallet: str,
              data_dir: str | Path | None = None) -> str:
    """Sign (ft_id, owner_wallet). Returns hex HMAC-SHA256."""
    msg = f"{ft_id}|{owner_wallet}".encode()
    return hmac.new(_cert_secret(data_dir), msg, hashlib.sha256).hexdigest()


def verify_cert(ft_id: str, owner_wallet: str, cert: str,
                data_dir: str | Path | None = None) -> bool:
    """Verify an ownership certificate. Safe against timing attacks."""
    expected = sign_cert(ft_id, owner_wallet, data_dir)
    return hmac.compare_digest(expected, cert or "")


def archetype_bot_class(archetype_id: str):
    """House bot class for an archetype id. Raises KeyError when unknown."""
    from .house_fighters import FIGHTERS

    key = (archetype_id or "").strip().lower()
    for entry in FIGHTERS:
        if entry["id"] == key:
            return entry["bot_class"]
    raise KeyError(f"Unknown archetype {archetype_id!r}. "
                   f"Available: {list(ARCHETYPE_IDS)}")


def validate_born_input(name: str, archetype: str,
                        color: str) -> tuple[str, str, str]:
    """Clean + validate born-form input. Returns (name, archetype, color).

    Raises ValueError with a human message on bad input.
    """
    import re

    clean = re.sub(r"<[^>]*>", "", name or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        raise ValueError("Fighter name is required")
    if len(clean) > 24:
        raise ValueError("Fighter name must be at most 24 characters")
    key = (archetype or "").strip().lower()
    if key not in ARCHETYPE_IDS:
        raise ValueError(f"Archetype must be one of {', '.join(ARCHETYPE_IDS)}")
    col = (color or "").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", col):
        raise ValueError("Color must be a hex color like #B6FF2E")
    return clean, key, col.upper()
