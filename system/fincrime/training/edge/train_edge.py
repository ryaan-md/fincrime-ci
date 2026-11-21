#!/usr/bin/env python3
"""Train global and archetype edge statistics offline from raw institution folders.

Run with --input /path/to/institutions --output /path/to/new-model.json.
Only aggregate feature statistics, rule IDs and document types are stored.
This command never overwrites the shipped edge model. Validate a new model's
thresholds on held-out data before using it for final decisions.
"""
import argparse, json, os, statistics, sys
from . import signature as S
from fincrime.loader import load  # noqa: E402
from fincrime import policy as P, edge  # noqa: E402
from fincrime.decide import run_rules  # noqa: E402

INSTS = ["bank", "emoney", "investment", "payments_fx"]


def collect(source):
    """-> [(case, rules, policy)] over all dev folders, in a fixed order."""
    out = []
    for inst in INSTS:
        _, cases = load(os.path.join(source, inst))
        pol = P.get(inst)
        out += [(c, run_rules(c, pol), pol) for c in cases]
    return out


def build(triples, z_cap, z_feature, min_n):
    glob = [edge.features(c, r) for c, r, _ in triples]
    stats = {k: {"mean": round(statistics.fmean(f[k] for f in glob), 6), "std": round(statistics.pstdev(f[k] for f in glob), 6)}
             for k in sorted(glob[0])}
    arch = S.fit([(S.signature(c), S.arch_features(c, r)) for c, r, _ in triples],
                 {"z_cap": z_cap, "z_feature": z_feature, "min_n": min_n})
    return {"kind": "zscore+archetype", "version": 1, "z_cap": z_cap, "z_feature": z_feature, "stats": stats,
            "params": arch["params"], "struct_levels": S.STRUCT, "archetypes": arch["archetypes"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z-cap", type=float, default=S.DEFAULT_PARAMS["z_cap"])
    ap.add_argument("--z-feature", type=float, default=S.DEFAULT_PARAMS["z_feature"])
    ap.add_argument("--min-n", type=int, default=S.DEFAULT_PARAMS["min_n"])
    ap.add_argument("--input", required=True, help="Parent of the raw institution folders")
    ap.add_argument("--output", required=True, help="New trained edge-model JSON; do not overwrite the shipped model")
    a = ap.parse_args()
    triples = collect(a.input)
    model = build(triples, a.z_cap, a.z_feature, a.min_n)
    out = a.output
    with open(out, "x") as f:
        json.dump(model, f, indent=1, sort_keys=True)
        f.write("\n")
    small = sum(1 for v in model["archetypes"].values() if v["n"] < a.min_n)
    print(f"wrote {out}: {len(triples)} cases, {len(model['archetypes'])} archetypes ({small} with n < {a.min_n})")


if __name__ == "__main__":
    main()
