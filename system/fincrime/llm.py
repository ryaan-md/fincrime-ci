"""Optional OpenRouter judge for EXCEPTIONS (README: only OpenRouter chat completions, key in OPENROUTER_API_KEY).
ON when OPENROUTER_API_KEY is set (FINCRIME_LLM=0 forces it off). Temperature 0, fixed seed, strict JSON, NEVER returns CLOSE. Owner: T-LLM.

An exception is a case the rules do not settle:
  1. no rule fits and no red flag (arbiter step F calls fallback());
  2. the ML edge signal disagrees with the rules' verdict (decide() calls second_look()). Counted only when
     ML_REVIEW_WEIGHT > 0; the default 0 disables it (see the REVIEW TRIGGERS block below).
The LLM gets a compact case summary (alerts, key transactions, parsed doc fields, the institution's limits and codes,
the deterministic default and the ML tip-off) and answers {verdict, reason_code, reason, evidence}.
Validation: verdict RFI/ESCALATE only, code must belong to the verdict, every evidence ID must exist in the case,
and on an ML disagreement the answer may only add caution. Anything else -> the deterministic decision.

Determinism: answers are cached in system/fincrime/llm_cache.jsonl keyed on a hash of the exact request, so a rerun
returns the same answers. No key, no network, timeout or budget exhausted -> the deterministic decision.
Budget per run (one institution): MAX_CALLS calls and TIME_BUDGET_S seconds (README: < ~$4, < 30 min / 200 cases)."""
import hashlib, json, os, time, urllib.request
from .model import CODES, CODE_TO_VERDICT

# ============================== REVIEW TRIGGERS (tune here) ==============================
# Master switch: the reviewer runs only when OPENROUTER_API_KEY is set; FINCRIME_LLM=0 forces it off.
# Trigger 1, no-rule-fits review (arbiter step F): ON with a key.
REVIEW_NO_RULE_FITS = os.environ.get("FINCRIME_LLM_REVIEW_NO_RULE_FITS", "1") == "1"
# Trigger 2, ML-disagreement review (decide() -> second_look): OFF by default (weight 0). It fires when
# ML_REVIEW_WEIGHT * edge score >= ML_DISAGREE_AT and the edge model's view is more cautious than the rules' verdict.
# Set ML_REVIEW_WEIGHT > 0 (e.g. 1.0) to enable it once the edge model has been validated.
ML_REVIEW_WEIGHT = float(os.environ.get("ML_REVIEW_WEIGHT", "0"))
ML_DISAGREE_AT = 0.8
# Model, per-call limits and per-run budget (README: < 30 min and < ~$4 per 200 cases).
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("FINCRIME_LLM_MODEL", "openai/gpt-4o-mini")
TIMEOUT = 8
RETRIES = 1
MAX_CALLS = int(os.environ.get("FINCRIME_LLM_MAX_CALLS", "300"))
TIME_BUDGET_S = float(os.environ.get("FINCRIME_LLM_BUDGET_S", "420"))
# ==========================================================================================
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_cache.jsonl")
RANK = {"CLOSE": 0, "RFI": 1, "ESCALATE": 2}
STANDARD = ("due_diligence_summary", "proof_of_address", "contact_note")

_state = {"calls": 0, "t0": None, "cache": None, "in_default": False}


def enabled():
    return os.environ.get("FINCRIME_LLM") != "0" and bool(os.environ.get("OPENROUTER_API_KEY"))


def _chat(messages):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": MODEL, "messages": messages, "temperature": 0, "seed": 7,
                       "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(URL, body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for _ in range(1 + RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except Exception:
            continue
    return None


def _cache():
    if _state["cache"] is None:
        _state["cache"] = {}
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        _state["cache"].setdefault(r["key"], r["answer"])
                    except Exception:
                        continue
    return _state["cache"]


def _ask(messages):
    """Cached chat: same request -> same answer. Budget exhausted -> None (deterministic fallback)."""
    key = hashlib.sha256(json.dumps([MODEL, messages], sort_keys=True).encode()).hexdigest()
    cache = _cache()
    if key in cache:
        return cache[key]
    if _state["t0"] is None:
        _state["t0"] = time.monotonic()
    if _state["calls"] >= MAX_CALLS or time.monotonic() - _state["t0"] > TIME_BUDGET_S:
        return None
    _state["calls"] += 1
    out = _chat(messages)
    if out is None:
        return None
    try:
        answer = json.loads(out)
    except Exception:
        return None
    cache[key] = answer
    try:
        with open(CACHE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "answer": answer}, sort_keys=True) + "\n")
    except OSError:
        pass
    return answer


def _cut(v, n=240):
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True, default=str)
    return s if len(s) <= n else s[:n] + "..."


def summary(case, policy, kind, default, tip):
    """Compact, deterministic case summary (sorted keys, truncated text)."""
    rules = sorted({a["rule_id"] for a in case.alerts})
    txns = [t for a in case.alerts for t in case.alert_txns.get(a["alert_id"], [])][:12]
    docs = [d for d in case.docs_by_window_end() if d.type not in STANDARD][:8]
    limits = getattr(policy, "LIMITS", {})
    return {
        "exception": kind,
        "institution": case.institution,
        "review_window": case.window,
        "customer": {k: _cut(v, 80) for k, v in sorted(case.customer.items())},
        "alerts": [{"alert_id": a["alert_id"], "rule_id": a["rule_id"], "message": _cut(a.get("message", "")),
                    "measured": a.get("measured") or {}} for a in case.alerts],
        "alert_transactions": [{k: _cut(v, 80) for k, v in sorted(t.items())} for t in txns],
        "documents_on_file": [{"doc_id": d.doc_id, "type": d.type, "title": d.title, "dated": d.dated,
                               "amounts": d.amounts[:6],
                               "fields": {k: _cut(v, 120) for k, v in sorted(d.fields.items())
                                          if not k.startswith("_")}} for d in docs],
        "other_document_types": sorted({d.type for d in case.docs if d not in docs}),
        "policy": {"limits": {r: limits[r] for r in rules if r in limits},
                   "rfi_codes": dict(sorted(getattr(policy, "RFI_ASK", {}).items())),
                   "escalate_codes": CODES["ESCALATE"]},
        "deterministic_default": default,
        "ml_tip_off": tip,
    }


SYSTEM = ("You are an AML analyst judging an alert case the rule engine could not settle. You may answer only RFI "
          "(no suspicion yet; one specific document or answer from the customer would settle it) or ESCALATE "
          "(suspicion, or nothing the customer could send would settle it). You may NEVER close a case. "
          "Use only the facts given. reason_code must be one of the codes for your verdict: "
          + json.dumps({"RFI": CODES["RFI"], "ESCALATE": CODES["ESCALATE"]}) + ". "
          "evidence: the alert, transaction and document IDs (from the case) that support your verdict. "
          "reason: 2-4 sentences; for RFI say exactly what to ask the customer for. "
          'Reply as JSON only: {"verdict": "RFI|ESCALATE", "reason_code": "...", "reason": "...", "evidence": ["..."]}')


def _case_ids(case):
    ids = {a["alert_id"] for a in case.alerts} | {d.doc_id for d in case.docs}
    ids |= {t["transaction_id"] for t in case.txns} | {t["transaction_id"] for ts in case.alert_txns.values() for t in ts}
    return ids


def parse_answer(ans, case):
    """Strict validation. Tolerant only on noise (case, whitespace). Returns (verdict, code, reason, evidence) or None."""
    if not isinstance(ans, dict):
        return None
    v = str(ans.get("verdict", "")).strip().upper()
    c = str(ans.get("reason_code", "")).strip().upper()
    if v not in ("RFI", "ESCALATE") or CODE_TO_VERDICT.get(c) != v:
        return None
    ev = ans.get("evidence") or []
    if not isinstance(ev, list) or not all(isinstance(i, str) for i in ev):
        return None
    ev = [i.strip() for i in ev]
    ids = _case_ids(case)
    if any(i not in ids for i in ev):
        return None
    reason = ans.get("reason")
    return v, c, reason.strip() if isinstance(reason, str) and reason.strip() else None, ev


def _grounded_reason(case, v, c, ev, reason):
    """Keep the LLM reason only if the explainer's validator accepts it; else the template is used."""
    if not reason:
        return None
    from .explain import validate
    from .model import Decision
    return reason if validate(reason, case, Decision(case.case_id, [], v, c, "", ev, [], {}), "") else None


def ml_view(sig):
    """The ML's view from the EdgeSignal interface only: (verdict it points to, weighted strength)."""
    strength = round(ML_REVIEW_WEIGHT * float(getattr(sig, "score", 0.0) or 0.0), 4)
    if ML_REVIEW_WEIGHT <= 0 or strength < ML_DISAGREE_AT:
        return None, strength
    from .edge import SUSPICIOUS
    sus = getattr(sig, "suspicious", False) or any(f in SUSPICIOUS for f in (getattr(sig, "features", []) or []))
    return ("ESCALATE" if sus else "RFI"), strength


def _tip(sig):
    view, strength = ml_view(sig)
    return {"ml_view": view, "weighted_score": strength, "raw_score": getattr(sig, "score", 0.0),
            "unusual_features": list(getattr(sig, "features", []) or [])} if view else None


def judge(case, policy, kind, default, sig):
    if not enabled():
        return None
    ans = _ask([{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(summary(case, policy, kind, default, _tip(sig)),
                                                       sort_keys=True, default=str)}])
    got = parse_answer(ans, case)
    if not got:
        return None
    v, c, reason, ev = got
    return v, c, _grounded_reason(case, v, c, ev, reason), ev


def fallback(case, policy):
    """Arbiter step F: no rule fits and no red flag. Returns (verdict, code, reason|None, evidence) or None."""
    if not enabled() or not REVIEW_NO_RULE_FITS or _state["in_default"]:
        return None
    from .decide import run_rules, arbitrate
    from . import edge
    rules = run_rules(case, policy)
    sig = edge.score(case, rules, policy)
    _state["in_default"] = True
    try:
        d = arbitrate(case, policy, rules, sig)          # the deterministic G/H/I answer, without step F
    finally:
        _state["in_default"] = False
    return judge(case, policy, "no_rule_fits", {"verdict": d.verdict, "reason_code": d.reason_code}, sig)


def second_look(case, policy, sig, decision):
    """ML disagreement: the edge signal is more cautious than the rules' verdict. The LLM may keep the decision or
    move it to RFI/ESCALATE, never to CLOSE and never to less caution. ML_REVIEW_WEIGHT 0 -> decision returned as is.
    (Rules ESCALATE + ML "clearly normal" is not reviewed here: caution only. Unflagged no-rule-fits escalations are
    already reviewed in step F; flagged escalations are never downgraded.)"""
    view, strength = ml_view(sig)
    if view is None or RANK[view] <= RANK.get(decision.verdict, 2) or not enabled():
        return decision
    if decision.trace and decision.trace[-1] == ("arbiter", "F:llm_judge"):
        return decision
    fb = judge(case, policy, "ml_disagrees_with_rules",
               {"verdict": decision.verdict, "reason_code": decision.reason_code}, sig)
    if not fb or RANK[fb[0]] <= RANK[decision.verdict]:     # agreeing with the rules (or less caution) keeps them
        return decision
    from .model import Decision
    return Decision(decision.case_id, decision.alert_ids, fb[0], fb[1], "", fb[3] or decision.evidence,
                    decision.trace + [("arbiter", "F2:llm_ml_disagreement")],
                    {**{k: v for k, v in decision.facts.items() if k in ("customer", "alerts", "edge_features")},
                     "llm_reason": fb[2]})


def rewrite_reason(verdict, code, evidence, facts, template):
    """T-EXPLAIN: rewrite the template into 2-4 precise sentences using ONLY the facts. Returns text or None."""
    if not os.environ.get("OPENROUTER_API_KEY"):
        return None
    sys = ("You write the reason line for an AML alert decision that has ALREADY been made. Use ONLY the facts given. "
           "2-4 sentences. Name the specific documents (by ID), amounts, dates and people. Do not change the verdict. "
           "For RFI, say exactly what to ask the customer for. Never invent IDs or amounts. "
           'Reply as JSON: {"reason": "..."}')
    user = json.dumps({"verdict": verdict, "reason_code": code, "evidence": evidence, "facts": facts,
                       "draft": template}, default=str)
    out = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}])
    try:
        return json.loads(out)["reason"] if out else None
    except Exception:
        return None
