"""Archetype signatures for the edge-case detector (T-EDGE-TRAIN). Stdlib only, deterministic.

The dev data is generated from ~128 templates. A case's *signature* is (institution, rules fired, non-standard
document types on file); it identifies its template ("archetype"). Institution is part of it because the four
policies differ (rules, limits) and measured values are not comparable across them. The model built from this answers one question:
**how familiar is this case?**  It never says what the verdict should be, and it stores no verdicts.

Score = max(structural novelty, feature novelty), each 0..1:
  structural  exact archetype seen >= min_n times                    0.0
              same docs, rules a strict subset of a known archetype   STRUCT["rule_subset"]  (one alert did not fire)
              same rules and docs, seen only at another institution   STRUCT["other_institution"]
              exact archetype but seen fewer than min_n times         STRUCT["rare"]
              same docs at this institution, new rule combination     STRUCT["new_rule_combo"]
              document-type set never seen at this institution        STRUCT["new_doc_set"]
  feature     max |z| of data-only features against the matched archetype's own mean/std, / z_cap

Everything here is a pure function of a loaded Case and the model dict, so it can move into
fincrime/edge.py (T-EDGE) unchanged. The model holds rule IDs, document TYPES and feature statistics only."""
import hashlib, os, sys

from fincrime import edge  # noqa: E402
from fincrime.checks.util import norm_name  # noqa: E402
from fincrime.model import EdgeSignal  # noqa: E402

STANDARD_DOCS = {"due_diligence_summary", "proof_of_address", "contact_note"}
REGISTER = "company_register_extract"
THIRD_PARTY_REGISTER = "REG_3P"     # a register extract for a company other than the customer's own
NO_DOCS = "NONE"

STRUCT = {"exact": 0.0, "rule_subset": 0.25, "other_institution": 0.5, "rare": 0.6, "new_rule_combo": 0.75,
          "new_doc_set": 1.0}
# Features that depend only on the data, never on which checkers exist (so the model survives checker merges).
ARCH_FEATURES = ("n_alerts", "n_alert_txns", "n_decisive_docs", "decisive_docs_without_amount",
                 "max_measured_over_limit", "round_amount_share", "third_party_share")
DEFAULT_PARAMS = {"min_n": 5, "z_cap": 6.0, "z_feature": 3.0, "std_floor_abs": 0.5, "std_floor_rel": 0.1}


def doc_kind(case, doc):
    """None for standard paperwork every customer has, else the type label used in the signature."""
    if doc.type in STANDARD_DOCS:
        return None
    if doc.type == REGISTER:
        own = norm_name(case.customer.get("name"))
        return None if own and own in norm_name(doc.title) else THIRD_PARTY_REGISTER
    return doc.type


def signature(case):
    rules = tuple(sorted({a["rule_id"] for a in case.alerts}))
    docs = tuple(sorted({k for k in (doc_kind(case, d) for d in case.docs) if k}))
    return case.institution, rules, docs


def sig_key(sig):
    inst, rules, docs = sig
    return inst + ":" + "+".join(rules) + "|" + (",".join(docs) or NO_DOCS)


def parse_key(key):
    inst, rest = key.split(":", 1)
    r, d = rest.split("|", 1)
    return inst, tuple(r.split("+")) if r else (), () if d == NO_DOCS else tuple(d.split(","))


def arch_features(case, rules):
    f = edge.features(case, rules)
    return {k: float(f[k]) for k in ARCH_FEATURES}


def fold_of(case_id, k):
    """Deterministic CV fold from the case ID (offline evaluation only, never used at runtime)."""
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % k


def _mean_std(xs):
    xs = sorted(xs)  # summation order fixed, so stats never depend on input order
    m = sum(xs) / len(xs)
    return m, (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def fit(rows, params=None):
    """rows: [(signature, {feature: float})] -> archetype table {key: {n, stats}}. Order-independent."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    groups = {}
    for sig, f in rows:
        groups.setdefault(sig_key(sig), []).append(f)
    out = {}
    for key in sorted(groups):
        fs = groups[key]
        stats = {}
        for name in ARCH_FEATURES:
            m, s = _mean_std([f[name] for f in fs])
            stats[name] = {"mean": round(m, 6), "std": round(s, 6)}
        out[key] = {"n": len(fs), "stats": stats}
    return {"params": p, "archetypes": out}


def match(sig, archetypes, min_n):
    """-> (level, matched_key or None). Deterministic: candidates scanned in sorted key order."""
    inst, rules, docs = sig
    key = sig_key(sig)
    a = archetypes.get(key)
    if a and a["n"] >= min_n:
        return "exact", key
    same_docs = sorted(k for k in archetypes if parse_key(k)[0] == inst and parse_key(k)[2] == docs)
    parents = [k for k in same_docs if archetypes[k]["n"] >= min_n and set(rules) < set(parse_key(k)[1])]
    if parents:
        return "rule_subset", max(parents, key=lambda k: (archetypes[k]["n"], k))
    elsewhere = sorted(k for k in archetypes if archetypes[k]["n"] >= min_n and parse_key(k)[1:] == (rules, docs))
    if elsewhere:
        return "other_institution", None
    if a:
        return "rare", key
    if same_docs:
        return "new_rule_combo", None
    return "new_doc_set", None


def familiarity(sig, feats, model):
    """-> (score 0..1, level, matched_key, {feature: z}) for one case."""
    p, arch = model["params"], model["archetypes"]
    level, key = match(sig, arch, p["min_n"])
    zs = {}
    if level == "exact":
        for name, st in sorted(arch[key]["stats"].items()):
            floor = max(st["std"], p["std_floor_abs"], p["std_floor_rel"] * abs(st["mean"]))
            zs[name] = abs(feats[name] - st["mean"]) / floor
    feat_score = min(max(zs.values(), default=0.0) / p["z_cap"], 1.0)
    return round(max(model.get("struct_levels", STRUCT)[level], feat_score), 4), level, key, zs


def to_signal(fam, z_feature, threshold):
    """familiarity() result -> EdgeSignal, with the same semantics as edge.score."""
    score, level, _, zs = fam
    unusual = score >= threshold
    hot = sorted(k for k, z in zs.items() if z >= z_feature)
    names = ([] if level == "exact" else ["signature:" + level]) + hot
    return EdgeSignal(score, unusual, unusual and any(k in edge.SUSPICIOUS for k in hot), names)


def edge_signal(case, rules, model, threshold):
    """The EdgeSignal fincrime/edge.py would return with this model."""
    fam = familiarity(signature(case), arch_features(case, rules), model)
    return to_signal(fam, model["params"]["z_feature"], threshold)
