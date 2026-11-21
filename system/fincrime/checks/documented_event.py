"""DOCUMENTED_EVENT: a one-off event document explains the alert's money: an estate (solicitor letter), an insurance
payout (insurer letter), a settlement, a property completion, a private sale (vehicle receipt + insurance schedule,
item receipt + advert), a tenancy deposit move (deposit return letter + holding deposit receipt) or a redundancy
(redundancy letter + customer email, TM10).
A document explains a transaction only when every substantive fact holds: amount to the penny, the payer is the
issuer, any person the document names as recipient/seller/client/holder is the customer, the document is dated on or
before the transaction and on file by the window end. Whatever the documents leave unexplained must sit inside the
rule's own limit (TM01 multiple, TM07 amount), or be own-name money (TM02); otherwise the explanation is incomplete
-> Proof(passed=False, RFI). A document that contradicts the transaction explains nothing (None). Doc TYPES only."""
import re
from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

RULES = {"TM01", "TM02", "TM05", "TM07", "TM08", "TM10"}
LETTERS = {"solicitor_letter", "insurer_letter", "settlement_letter", "completion_statement"}
SALES = {"vehicle_sale_receipt": "insurance_schedule", "sale_receipt": "advert"}   # receipt -> corroborating doc
DEPOSITS = {"deposit_return_letter": "in", "holding_deposit_receipt": "out"}
EVENT_TYPES = LETTERS | set(SALES) | set(SALES.values()) | set(DEPOSITS) | {"redundancy_letter", "customer_email"}
RFI_BY_RULE = {"TM02": "RFI_PURPOSE_DOCUMENTS", "TM10": "RFI_PURPOSE_DOCUMENTS"}   # else RFI_SOURCE_OF_FUNDS
TITLE_FROM = re.compile(r"\bfrom\s+(.+?)\s*$")
MONEY = re.compile(r"[\d,]+(?:\.\d{1,2})?")
TOLERANCE = 0.01   # policy.AMOUNT_TOLERANCE: generator rounding, never a substantive gap


def _gbp(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = MONEY.search(str(v or ""))
    return float(m.group(0).replace(",", "")) if m else None


def _day(t): return t["booked_at"][:10]
def _same(a, b): return bool(norm_name(a)) and norm_name(a) == norm_name(b)
def _is_customer(case, name): return _same(name, case.customer.get("name"))
def _up(s): return re.sub(r"\s+", " ", str(s or "").upper()).strip()


def _payer_is(t, issuer):
    cp = re.sub(r"\s+client account$", "", norm_name(t.get("counterparty_name")))
    return bool(cp) and cp == norm_name(issuer)


def _issuer(d):
    m = TITLE_FROM.search(d.title or "")
    return d.fields.get("payer") or d.fields.get("issuer") or (m.group(1) if m else None)


def _ref_tokens_ok(d, t):
    """A reference token in the payment (CLAIM n, ESTATE OF name) must be the one the letter gives."""
    ref, claim = _up(t.get("reference")), _up(d.fields.get("claim_ref"))
    if re.search(r"\bCLAIM\b", ref) and not (claim and claim in ref):
        return False
    deceased = norm_name(d.fields.get("deceased_name"))
    if "ESTATE OF" in ref and not (deceased and deceased in norm_name(ref)):
        return False
    return True


def _reconciles(f):
    """Completion statement: sale - mortgage - fees == paid to client, within the policy's one-penny rounding."""
    parts = [_gbp(f.get(k)) for k in ("sale_price", "mortgage_redeemed", "fees_total", "paid_to_client")]
    return None not in parts and round(abs(parts[0] - parts[1] - parts[2] - parts[3]), 2) <= TOLERANCE


def _letter(case, d, t):
    f = d.fields
    if t.get("direction") != "in" or not _payer_is(t, _issuer(d)) or not amt_eq(_gbp(f.get("amount")), t["amount_gbp"]):
        return None
    if any(f.get(k) and not _is_customer(case, f[k]) for k in ("addressee", "payee", "client_name")):
        return None
    if d.type == "completion_statement" and not (f.get("client_name") and _reconciles(f)):
        return None
    return ("ok", []) if _ref_tokens_ok(d, t) and d.dated <= _day(t) else None


def _sale(case, d, t, docs):
    f = d.fields
    if t.get("direction") != "in" or "cash" not in (t.get("channel") or "") or not amt_eq(_gbp(f.get("amount")), t["amount_gbp"]):
        return None
    if not _is_customer(case, f.get("seller")) or not f.get("buyer") or _is_customer(case, f["buyer"]):
        return None
    if f.get("payment_method") not in (None, "cash") or d.dated > _day(t):
        return None
    kin = [c for c in docs if c.type == SALES[d.type] and c.dated <= d.dated]
    if not kin:
        return ("incomplete", [])
    reg = lambda x: re.sub(r"\s+", "", str(x.fields.get("registration") or "")).upper()
    for c in kin:
        holder = c.fields.get("policyholder") if c.type == "insurance_schedule" else c.fields.get("seller")
        if _is_customer(case, holder) and (c.type != "insurance_schedule" or (reg(c) and reg(c) == reg(d))):
            return ("ok", [c.doc_id])
    return None


def _deposit(case, d, t):
    amounts = sorted({round(a, 2) for a in d.amounts})
    if t.get("direction") != DEPOSITS[d.type] or len(amounts) != 1 or not amt_eq(amounts[0], t["amount_gbp"]):
        return None
    if not _same(t.get("counterparty_name"), _issuer(d)):
        return None
    # a letter announcing money in predates the credit; a receipt for money out may be issued on/after it
    return ("ok", []) if t.get("direction") == "out" or d.dated <= _day(t) else None


def _verify(case, d, t, docs):
    if d.type in LETTERS:
        return _letter(case, d, t)
    if d.type in SALES:
        return _sale(case, d, t, docs)
    if d.type in DEPOSITS:
        return _deposit(case, d, t)
    return None


def _within_limit(case, alert, rest):
    m, rule = alert.get("measured") or {}, alert["rule_id"]
    inflow = sum(t["amount_gbp"] for t in rest if t.get("direction") == "in")
    if rule == "TM01" and m.get("limit_multiple") and m.get("baseline_gbp"):
        return all(t.get("direction") == "in" for t in rest) and inflow <= m["limit_multiple"] * m["baseline_gbp"]
    if rule == "TM07" and m.get("limit_gbp"):
        return sum(t["amount_gbp"] for t in rest) < m["limit_gbp"]
    if rule == "TM02" and m.get("limit_share") and m.get("received_gbp"):
        # own-name money in; what goes out unexplained must stay under the rule's pass-through share by itself
        outflow = sum(t["amount_gbp"] for t in rest if t.get("direction") == "out")
        return (all(_is_customer(case, t.get("counterparty_name")) for t in rest if t.get("direction") == "in")
                and outflow < m["limit_share"] * m["received_gbp"])
    return not rest


def _tm10(case, alert, docs):
    sells = [t for t in case.alert_txns.get(alert["alert_id"], []) if t.get("direction") == "in"]
    employer = case.customer.get("employer")
    if not sells or not employer:
        return None
    sell = max(sells, key=lambda t: (t["amount_gbp"], t["transaction_id"]))
    letter = next((d for d in docs if d.type == "redundancy_letter" and d.fields.get("redundancy_confirmed") is True
                   and _same(d.fields.get("employer") or _issuer(d), employer) and d.dated <= _day(sell)), None)
    if not letter:
        return None
    paid_away = [t for t in case.txns if t.get("direction") == "out" and t["booked_at"] > sell["booked_at"]
                 and amt_eq(t["amount_gbp"], sell["amount_gbp"]) and not _is_customer(case, t.get("counterparty_name"))]
    if paid_away:
        return None
    email = next((d for d in docs if d.type == "customer_email" and d.fields.get("made_redundant") is True
                  and d.dated <= _day(sell)), None)
    ev = [letter.doc_id] + ([email.doc_id] if email else []) + [sell["transaction_id"]]
    facts = _facts(letter, sell, alert)
    if not email:
        return Proof(alert["alert_id"], False, RFI_BY_RULE["TM10"], ev, facts, "documented_event")
    return Proof(alert["alert_id"], True, "DOCUMENTED_EVENT", ev, facts, "documented_event")


def _facts(d, t, alert):
    return {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "amount": fmt_gbp(t["amount_gbp"]),
            "txn_id": t["transaction_id"], "txn_date": _day(t), "rule": alert["rule_id"]}


def check_documented_event(case, alert, policy):
    if alert["rule_id"] not in RULES:
        return None
    docs = [d for d in case.docs_by_window_end() if d.type in EVENT_TYPES]
    if not docs:
        return None
    if alert["rule_id"] == "TM10":
        return _tm10(case, alert, docs)
    txs = case.alert_txns.get(alert["alert_id"], [])
    links, incomplete, used = {}, [], set()
    for t in sorted(txs, key=lambda t: (t["booked_at"], t["transaction_id"])):
        found = [(d, r) for d in docs if d.doc_id not in used for r in [_verify(case, d, t, docs)] if r]
        ok = next(((d, r) for d, r in found if r[0] == "ok"), None)
        if ok:
            used.add(ok[0].doc_id)
            links[t["transaction_id"]] = (t, ok[0], ok[1][1])
        elif found:
            incomplete.append((t, found[0][0]))
    if not links and not incomplete:
        return None
    rest = [t for t in txs if t["transaction_id"] not in links]
    order = sorted(links.values(), key=lambda x: (x[0].get("direction") != "in", -x[0]["amount_gbp"], x[0]["transaction_id"]))
    t0, d0 = (order[0][0], order[0][1]) if order else incomplete[0]
    ev = [d0.doc_id] + [i for _, d, extra in order for i in [d.doc_id] + extra] + [t["transaction_id"] for t, _, _ in order]
    ev = list(dict.fromkeys(ev + ([] if order else [t0["transaction_id"]])))
    facts = _facts(d0, t0, alert)
    if incomplete or not _within_limit(case, alert, rest):
        return Proof(alert["alert_id"], False, RFI_BY_RULE.get(alert["rule_id"], "RFI_SOURCE_OF_FUNDS"), ev, facts,
                     "documented_event")
    return Proof(alert["alert_id"], True, "DOCUMENTED_EVENT", ev, facts, "documented_event")


def _checkable(d, t):
    """True when the event document and the transaction carry the identity facts this checker verifies."""
    if d.type in SALES:
        return bool(d.fields.get("seller"))
    if d.type in LETTERS or d.type in DEPOSITS:
        return bool(_issuer(d)) and bool(t and t.get("counterparty_name"))
    return True


def defer_to_events(chk):
    """Register an older generic checker so it never closes on an event document that check_documented_event can
    verify (and so owns): a rejected payee, seller or issuer cannot be re-closed on the amount alone."""
    def wrapped(case, alert, policy):
        p = chk(case, alert, policy)
        if p is None:
            return None
        txs = {t["transaction_id"]: t for t in case.alert_txns.get(alert["alert_id"], [])}
        t = next((txs[i] for i in p.evidence if i in txs), None)
        if any(d.doc_id in p.evidence and d.type in EVENT_TYPES and _checkable(d, t) for d in case.docs):
            return None
        return p
    wrapped.__name__ = chk.__name__
    return wrapped
