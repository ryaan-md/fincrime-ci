"""4. EXPLAINER. Runs AFTER the arbiter has fixed verdict, code and evidence. Owner: Rio (T-EXPLAIN);
refine(): T-EXPLAIN-DET.
  explain(case, decision) -> reason text (2-4 sentences)

Deterministic, no LLM: template (reasons/templates.py) -> refine() -> validate() -> else the plain template.
refine() adds only facts the rules engine has already established, so an analyst can act on the reason:
  1. names the triggering transaction by ID where the template gives only its amount (IDs in `evidence` only);
  2. names the customer where the template says "the customer";
  3. OWN_ACCOUNT: states the 5x threshold the own-account checker enforced;
  4. several alerts, not all explained: says which alert a document accounts for and which it does not
     (from decide.run_rules, re-run read-only);
  5. RFI or ESCALATE: names the evidence documents the reason leaves out, as on file but not documenting it.
It never changes verdict, code or evidence (those are the reproducibility-scored fields)."""
import json, re
from .reasons import render
from .reasons.templates import MONTHS, RULE_NAMES
from .decide import is_filler

ID_RE = re.compile(r"\b[A-Z]{2,4}-[A-Z]{0,3}\d{3,}\b")
GBP_RE = re.compile(r"£\s?[\d,]+(?:\.\d{1,2})?")
SENT_SPLIT = re.compile(r"(?<=[.!?])(?<!\b[a-z]\.)\s+(?=[A-Z£(\"])")
OWN_ACCOUNT_GROUNDS = re.compile(r"\b5 ?x|5 times|five times|threshold", re.I)


def _allowed_tokens(case, decision, template):
    blob = json.dumps(decision.facts, default=str) + " " + " ".join(decision.evidence) + " " + template
    blob += " " + " ".join(d.doc_id for d in case.docs) + " " + " ".join(a["alert_id"] for a in case.alerts)
    blob += " " + " ".join(f"£{t['amount_gbp']:,.2f}" for t in case.txns)
    return set(ID_RE.findall(blob)), {g.replace(" ", "") for g in GBP_RE.findall(blob)}


def validate(text, case, decision, template):
    if not text or not (1 <= len(_sentences(text)) <= 5):   # abbreviation-aware ("Sp. z o.o.")
        return False
    ids, gbp = _allowed_tokens(case, decision, template)
    if any(i not in ids for i in ID_RE.findall(text)):
        return False
    if any(g.replace(" ", "") not in gbp for g in GBP_RE.findall(text)):
        return False
    if decision.verdict == "RFI" and not re.search(r"\b(ask|request|provide|send)\b", text, re.I):
        return False
    return True


# ---------------------------------------------------------------- deterministic refinements
def _unquoted(text, needle, start=0):
    """Index of the first `needle` at or after `start` that is not inside double quotes, else -1."""
    i = text.find(needle, start)
    while i >= 0:
        if text.count('"', 0, i) % 2 == 0:
            return i
        i = text.find(needle, i + 1)
    return -1


def _sentences(text):
    return [s for s in SENT_SPLIT.split(text.strip()) if s]


def name_transaction(case, decision, text):
    """'£11,900.00 on 19 August' -> '£11,900.00 (TXN-ID) on 19 August' for the largest triggering transaction whose
    amount the reason states, when no triggering transaction ID is named yet. Only IDs already in `evidence`."""
    txns = list({t["transaction_id"]: t for a in case.alerts for t in case.alert_txns.get(a["alert_id"], [])}.values())
    if any(t["transaction_id"] in text for t in txns):
        return text
    ev = set(decision.evidence)
    amounts = [f"£{float(t['amount_gbp']):,.2f}" for t in txns]
    for t in sorted(txns, key=lambda t: (-float(t["amount_gbp"]), t["transaction_id"])):
        amt = f"£{float(t['amount_gbp']):,.2f}"
        if t["transaction_id"] not in ev or amounts.count(amt) != 1:
            continue
        i = _unquoted(text, amt)
        if i >= 0 and not text[i + len(amt):].startswith(" ("):
            j = i + len(amt)
            return text[:j] + f" ({t['transaction_id']})" + text[j:]
    return text


def name_customer(case, text):
    """'without contacting the customer' -> 'without contacting <name>' when the name is not in the reason yet."""
    who = (case.customer or {}).get("name")
    if not who or who.lower() in text.lower():
        return text
    for needle, repl in (("the customer", who), ("The customer", who)):
        i = _unquoted(text, needle)
        if i >= 0 and not text[i + len(needle):i + len(needle) + 2].startswith("'"):
            return text[:i] + repl + text[i + len(needle):]
    return text


def own_account_threshold(case, decision, text, policy):
    """A passing own-account proof means activity stays below policy.OWN_ACCOUNT_RFI_MULTIPLE (checks/own_account.py);
    say so when the reason does not."""
    if decision.reason_code != "OWN_ACCOUNT" or OWN_ACCOUNT_GROUNDS.search(text) or not text.endswith("."):
        return text
    mult = getattr(policy, "OWN_ACCOUNT_RFI_MULTIPLE", None)
    if not mult:
        return text
    return text[:-1] + (f", which stays below the {mult:g} times threshold at which the policy asks for an updated "
                        "expected income or volume.")


def alert_coverage(case, decision, text, rules):
    """Several alerts, some explained and some not (RFI or ESCALATE): one sentence saying which, inserted before the
    conclusion. Skipped when the reason already says the explaining document does not explain something."""
    if decision.verdict == "CLOSE" or len(case.alerts) < 2 or rules is None:
        return text
    sents = _sentences(text)
    if len(sents) >= 4:
        return text
    doc_ids = {d.doc_id for d in case.docs}
    done, open_ = {}, []
    for a in sorted(case.alerts, key=lambda a: (a["rule_id"], a["alert_id"])):
        p = rules["proofs"].get(a["alert_id"])
        docs = sorted(i for i in (p.evidence if p and p.passed else []) if i in doc_ids)
        if docs:
            done.setdefault(a["rule_id"], set()).update(docs)
        elif a["rule_id"] not in open_:
            open_.append(a["rule_id"])
    open_ = [r for r in open_ if r not in done]
    if not done or not open_:
        return text
    cited = sorted({i for v in done.values() for i in v})
    if any(i in s and re.search(r"\bnot\b|n't\b", s) for s in sents for i in cited):
        return text
    phrase = lambda rs: " and ".join(f"{r} alert ({RULE_NAMES.get(r, r)})" for r in rs)
    verb = lambda rs: "are" if len(rs) > 1 else "is"
    ex = sorted(done)
    line = (f"The {phrase(ex)} {verb(ex)} accounted for by {' and '.join(cited)}, but the {phrase(open_)} "
            f"{verb(open_)} not.")
    return " ".join(sents[:-1] + [line, sents[-1]])


def _step(decision):
    return next((str(t[1]).split(":")[0] for t in decision.trace if t and t[0] == "arbiter"), "")


def weighed_documents(case, decision, text, rules):
    """No rule fits (arbiter steps G, H, I): documents in `evidence` that the reason does not mention and that no
    check accepted, even partly, get one sentence before the conclusion saying they do not document the activity.
    At most two, and only within 4 sentences."""
    if rules is None or _step(decision) not in ("G", "H", "I"):
        return text
    used = {i for p in rules["proofs"].values() for i in p.evidence}
    sents = _sentences(text)
    if len(sents) >= 4:
        return text
    docs = {d.doc_id: d for d in case.docs}
    low = text.lower()
    missing = [docs[i] for i in decision.evidence if i in docs and not is_filler(case, docs[i]) and i not in used
               and i not in text and (docs[i].title or "").lower() not in low][:2]
    if not missing:
        return text
    def ph(d):
        title = (d.title or d.type.replace("_", " ")).strip()
        return f"{title[:1].upper() + title[1:] if not missing.index(d) else title[:1].lower() + title[1:]} " \
               f"({d.doc_id}, dated {_long_date(d.dated)})"
    verb = "are on file but do" if len(missing) > 1 else "is on file but does"
    line = f"{' and '.join(ph(d) for d in missing)} {verb} not document the activity."
    return " ".join(sents[:-1] + [line, sents[-1]])


def _long_date(iso):
    try:
        y, m, d = (int(x) for x in iso[:10].split("-"))
        return f"{d} {MONTHS[m - 1]} {y}"
    except (ValueError, IndexError):
        return iso


def refine(case, decision, template, policy=None, rules=None):
    text = template.strip()
    text = name_transaction(case, decision, text)
    text = name_customer(case, text)
    text = own_account_threshold(case, decision, text, policy)
    text = alert_coverage(case, decision, text, rules)
    text = weighed_documents(case, decision, text, rules)
    return text


def _policy_and_rules(case):
    from . import policy as policies
    from .decide import run_rules
    pol = policies.get(case.institution)
    return pol, run_rules(case, pol)


def explain(case, decision):
    facts = dict(decision.facts)
    facts.pop("llm_reason", None)
    template = decision.facts.get("llm_reason") or render(case, decision.verdict, decision.reason_code, facts)
    try:
        pol, rules = _policy_and_rules(case)
    except Exception:   # unknown institution or a checker failing on odd data: refine what does not need them
        pol, rules = None, None
    text = refine(case, decision, template, pol, rules)
    if text != template and 2 <= len(_sentences(text)) <= 4 and validate(text, case, decision, template):
        return text
    return template
