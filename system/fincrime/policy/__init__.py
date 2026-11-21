"""Per-institution policy modules. Values come from each POLICY.md (cite the section in a comment).
Nothing here may reference dev case IDs, names, amounts or doc IDs - only rules, limits and doc TYPES."""
from . import bank, emoney, investment, payments_fx
POLICIES = {"bank": bank, "emoney": emoney, "investment": investment, "payments_fx": payments_fx}

def get(inst):
    if inst not in POLICIES:
        raise ValueError(f"unknown institution folder name: {inst}")
    return POLICIES[inst]
