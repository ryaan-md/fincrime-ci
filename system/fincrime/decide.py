"""RULE-BASED ARBITER. Pipeline per case (see docs/ARCHITECTURE.md):

  1 preprocess (loader)  ->  2a rules engine: run_rules()  ->  flags, per-alert proofs
                         ->  2b edge-case detector: edge.score()  ->  EdgeSignal
                         ->  3  arbiter: arbitrate()  ->  verdict, code, evidence, facts   (this file)
                         ->  4  explainer: explain.explain()  ->  reason text              (explain.py)

Arbiter table (fixed policy, never learned; order matters):
  A. any red flag                                   -> ESCALATE  (flag's code)
  B. rules CLOSE  + edge unusual + suspicious       -> ESCALATE  ESC_UNEXPLAINED_FUNDS
  C. rules CLOSE  + edge unusual                    -> RFI       policy.EDGE_DOWNGRADE_RFI
  D. rules CLOSE                                    -> CLOSE     (primary proof's code)
  E. a proof says 'explanation incomplete'          -> RFI       (that proof's RFI code)
  F. no rule fits + LLM judge answers (opt-in)      -> RFI/ESCALATE only, never CLOSE
  G. no rule fits + edge unusual                    -> ESCALATE  ESC_UNEXPLAINED_FUNDS
  H. no rule fits + every rule has an RFI default   -> RFI
  I. otherwise                                      -> ESCALATE  ESC_UNEXPLAINED_FUNDS
Deterministic: inputs sorted, no randomness, no clock. Reason text is NOT produced here."""
from .model import Decision, CODE_TO_VERDICT
from .checks import CHECKERS
from .flags import SCANNERS
from . import edge, llm


def _dedupe(ids):
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i); out.append(i)
    return out


def run_rules(case, policy):
    """2a. Rules engine: red flags + one proof per alert (first passing checker wins)."""
    flags = sorted([f for s in SCANNERS for f in (s(case, policy) or [])], key=lambda f: (-f.severity, f.code, f.evidence))
    proofs, trace = {}, []
    for a in case.alerts:
        for chk in CHECKERS:
            p = chk(case, a, policy)
            if p is not None:
                proofs[a["alert_id"]] = p
                trace.append(("proof", a["alert_id"], p.checker, p.passed, p.code))
                if p.passed:
                    break
    return {"flags": flags, "proofs": proofs, "trace": trace}


def arbitrate(case, policy, rules, sig):
    ids = [a["alert_id"] for a in case.alerts]
    tr = list(rules["trace"]) + [("edge", sig.score, sig.unusual, sig.suspicious, sig.features)]
    base = {"customer": case.customer.get("name"), "alerts": [(a["rule_id"], a.get("message", "")) for a in case.alerts],
            "edge_features": sig.features}
    D = lambda v, c, ev, facts, step: Decision(case.case_id, ids, v, c, "", _dedupe(ev), tr + [("arbiter", step)], {**base, **facts})

    flags = rules["flags"]
    if flags:                                                                     # A
        top = flags[0]
        ev = [i for f in flags if f.code == top.code for i in f.evidence]
        return D("ESCALATE", top.code, ev, top.facts, "A:flag:" + top.scanner)

    proofs = [rules["proofs"].get(i) for i in ids]
    all_pass = bool(ids) and all(p is not None and p.passed for p in proofs)
    alert_tx = [t["transaction_id"] for a in case.alerts for t in case.alert_txns.get(a["alert_id"], [])][:6]
    if all_pass:
        ev = [i for p in proofs for i in p.evidence]
        if sig.unusual and sig.suspicious:                                        # B
            return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", ev, proofs[0].facts, "B:close_but_suspicious_edge")
        if sig.unusual:                                                           # C
            return D("RFI", policy.EDGE_DOWNGRADE_RFI, ev, proofs[0].facts, "C:close_but_unusual_edge")
        return D("CLOSE", proofs[0].code, ev, proofs[0].facts, "D:all_alerts_proven")   # D

    failed = [p for p in proofs if p is not None and not p.passed]
    if failed:                                                                    # E
        return D("RFI", failed[0].code, failed[0].evidence, failed[0].facts, "E:incomplete_explanation")

    unexplained = [a for a in case.alerts if not (rules["proofs"].get(a["alert_id"]) and rules["proofs"][a["alert_id"]].passed)]
    fb = llm.fallback(case, policy)                                               # F
    if fb and fb[0] in ("RFI", "ESCALATE") and CODE_TO_VERDICT.get(fb[1]) == fb[0]:
        return D(fb[0], fb[1], fb[3] or alert_tx, {"llm_reason": fb[2]}, "F:llm_judge")
    if sig.unusual:                                                               # G
        return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", alert_tx, {}, "G:unexplained_and_unusual")
    rfi = [policy.RFI_BY_RULE.get(a["rule_id"]) for a in unexplained]
    if unexplained and all(rfi):                                                  # H
        return D("RFI", rfi[0], alert_tx, {}, "H:rfi_default")
    return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", alert_tx, {}, "I:default_escalate")   # I


def decide(case, policy):
    rules = run_rules(case, policy)          # 2a
    sig = edge.score(case, rules, policy)    # 2b
    return arbitrate(case, policy, rules, sig)   # 3


def safe_escalate(raw, err):
    """If anything throws: a missing line counts as ESCALATE anyway, so emit a valid one."""
    return Decision(raw["case_id"], raw.get("alert_ids", []), "ESCALATE", "ESC_UNEXPLAINED_FUNDS",
                    "The case could not be fully assessed automatically, so it is escalated to the MLRO for manual review.",
                    [], [("error", str(err))], {})
