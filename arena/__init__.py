"""Engine package: 2D top-down AI fighting arena simulation core."""

from .config import Config
from .bot_api import FighterBot, NullBot, SAFE_ACTION, validate_action
from .engine import Battle, simulate
from .house_fighters import (
    FIGHTERS,
    HOUSE_BOT_IDS,
    make_house_bot,
    build_house_bots,
    RusherBot,
    SniperBot,
    DefenderBot,
    OpportunistBot,
    WaspBot,
)
from .ft import (
    RARITIES,
    RARITY_NAMES,
    RARITY_MULT,
    ARCHETYPE_IDS,
    roll_rarity,
    next_ft_id,
    sign_cert,
    verify_cert,
    archetype_bot_class,
    validate_born_input,
)

__all__ = [
    "Config",
    "FighterBot",
    "NullBot",
    "SAFE_ACTION",
    "validate_action",
    "Battle",
    "simulate",
    "FIGHTERS",
    "HOUSE_BOT_IDS",
    "make_house_bot",
    "build_house_bots",
    "RusherBot",
    "SniperBot",
    "DefenderBot",
    "OpportunistBot",
    "WaspBot",
    "RARITIES",
    "RARITY_NAMES",
    "RARITY_MULT",
    "ARCHETYPE_IDS",
    "roll_rarity",
    "next_ft_id",
    "sign_cert",
    "verify_cert",
    "archetype_bot_class",
    "validate_born_input",
]
