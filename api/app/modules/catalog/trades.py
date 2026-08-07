"""Trades taxonomy. Data-driven per ARCHITECTURE.md §7 — this seed list becomes a
DB table with per-country licensing rules in Phase 1. Launch trades are the
unlicensed ones (§10 strategy note)."""

LAUNCH_TRADES: frozenset[str] = frozenset(
    {
        "cleaning",
        "moving",
        "handyman",
        "assembly",
        "yard_work",
        "painting",
    }
)

# Regulated trades require license verification before they can be enabled.
REGULATED_TRADES: frozenset[str] = frozenset(
    {
        "electrical",
        "plumbing",
        "hvac",
    }
)

ALL_TRADES: frozenset[str] = LAUNCH_TRADES | REGULATED_TRADES


def is_bookable(trade: str) -> bool:
    """Only launch (unlicensed) trades are bookable until the licensing pipeline ships."""
    return trade in LAUNCH_TRADES
