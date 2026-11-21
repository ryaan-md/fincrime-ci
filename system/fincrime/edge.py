"""EDGE-CASE DETECTOR (the one ML component). Owners: Chingis (T-EDGE), Martin (T-EDGE-TRAIN).
Job: score how unusual a case is, unsupervised. It never decides a verdict; the arbiter (decide.py) does.

  features(case, rules) -> {name: float}        deterministic numeric features (extend freely)
  score(case, rules, policy) -> EdgeSignal       loads edge_model.json if present, else disabled (score 0)

edge_model.json is produced OFFLINE by eval/edge/train_edge.py from feature STATISTICS (means/stds/weights) only:
never case IDs, names, amounts or doc IDs (`make leak` checks). Ask the organisers whether trained stats are OK.
Threshold: policy.EDGE_THRESHOLD, tuned with `make mutate` (raise trap catch, keep false alarms at 0)."""
import json, os
from .model import EdgeSignal

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_model.json")
_MODEL = None
SUSPICIOUS = {"third_party_share", "foreign_high_risk_share", "round_amount_share"}   # features that point to crime


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = json.load(open(MODEL_PATH)) if os.path.exists(MODEL_PATH) else {}
    return _MODEL


def features(case, rules):
    """`rules` = dict from decide.run_rules (flags, proofs). Keep every feature cheap and deterministic."""
    alerts = case.alerts
    proofs = rules.get("proofs", {})
    n_alerts = len(alerts)
    txs = [t for a in alerts for t in case.alert_txns.get(a["alert_id"], [])]
    amounts = [t["amount_gbp"] for t in txs] or [0]
    decisive = [d for d in case.docs if d.type not in ("due_diligence_summary", "proof_of_address", "contact_note")]
    return {
        "n_alerts": float(n_alerts),
        "n_alert_txns": float(len(txs)),
        "share_alerts_proven": sum(1 for a in alerts if proofs.get(a["alert_id"]) and proofs[a["alert_id"]].passed) / max(n_alerts, 1),
        "n_decisive_docs": float(len(decisive)),
        "decisive_docs_without_amount": float(sum(1 for d in decisive if not d.amounts)),
        "max_measured_over_limit": max([_over_limit(a) for a in alerts] or [0.0]),
        "round_amount_share": sum(1 for x in amounts if x and float(x).is_integer() and x % 100 == 0) / len(amounts),
        "third_party_share": sum(1 for t in txs if t.get("counterparty_name") and case.customer.get("name")
                                 and t["counterparty_name"] != case.customer["name"]) / max(len(txs), 1),
        "foreign_high_risk_share": 0.0,  # TODO(T-EDGE): counterparty_country in policy.HIGH_RISK_COUNTRIES
    }


def _over_limit(alert):
    m = alert.get("measured") or {}
    for v, l in (("multiple", "limit_multiple"), ("distinct_counterparties", "limit"), ("count", "limit_count"),
                 ("amount_gbp", "limit_gbp"), ("moved_gbp", "limit_gbp"), ("total_gbp", "limit_gbp")):
        if m.get(v) is not None and m.get(l):
            return float(m[v]) / float(m[l])
    return 0.0


def score(case, rules, policy):
    m = _model()
    f = features(case, rules)
    if not m:
        return EdgeSignal(0.0, False, False, [])
    # default model form: max absolute z-score over features, squashed to 0..1 (replace freely in T-EDGE)
    zs = {}
    for k, v in f.items():
        st = m.get("stats", {}).get(k)
        if st and st.get("std"):
            zs[k] = abs((v - st["mean"]) / st["std"])
    z = max(zs.values() or [0.0])
    s = min(z / m.get("z_cap", 6.0), 1.0)
    unusual = s >= policy.EDGE_THRESHOLD
    feats = sorted([k for k, v in zs.items() if v >= m.get("z_feature", 3.0)])
    return EdgeSignal(round(s, 4), unusual, unusual and any(k in SUSPICIOUS for k in feats), feats)
