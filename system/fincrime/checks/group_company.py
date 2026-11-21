"""GROUP_COMPANY: the counterparty is a verified company in the same group (POLICY.md reason-code table).
A group_structure chart on file by the window end, dated on or before the payment, must show the customer and the
counterparty linked by control (> 50% at every step): parent, subsidiary, or sister companies under a common owner.
TM08: every alert txn is with a group company. TM02: every inflow from anyone other than the client is from a group
company. For either rule, a held payment of £10k or more to anyone other than the client (group companies included)
needs a matching invoice or contract on file (payments_fx SOP: "Payments of £10,000 or more to anyone other than the
client need an invoice or contract on file"). A contract matches only when it states the held amount to the penny.
Held payment >= £10k with no invoice or contract at all -> RFI_PURPOSE_DOCUMENTS. Any substantive mismatch -> None."""
from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

APPLIES_TO = {"TM02", "TM08"}
CONTROL_PCT = 50.0
INVOICE_TYPES = ("supplier_invoice", "invoice")
CONTRACT_TYPES = ("supply_contract", "consulting_contract", "contract_schedule")


def _parents(doc):
    """company -> controlling owner (normalised names), from the parsed chart."""
    out = {}
    for row in doc.fields.get("group") or []:
        if row.get("share_pct") is not None and row["share_pct"] > CONTROL_PCT and row.get("owner"):
            out[norm_name(row["company"])] = norm_name(row["owner"])
    return out


def _chain(parents, name):
    seen = [name]
    while parents.get(seen[-1]) and parents[seen[-1]] not in seen:
        seen.append(parents[seen[-1]])
    return seen


def _same_group(doc, a, b):
    """Both a and b are companies on the chart and share a controlling ancestor (or one controls the other)."""
    parents = _parents(doc)
    if not a or not b or a == b or a not in parents or b not in parents:
        return False
    return bool(set(_chain(parents, a)) & set(_chain(parents, b)))


def _chart_for(charts, cust, tx):
    who = norm_name(tx.get("counterparty_name"))
    return next((d for d in charts if d.dated <= tx["booked_at"][:10] and _same_group(d, cust, who)), None)


def _support_for(case, cust, held):
    payee = norm_name(held.get("counterparty_name"))
    for d in case.docs_by_window_end():
        if d.dated > held["booked_at"][:10]:
            continue
        f = d.fields
        if d.type in INVOICE_TYPES:
            seller = norm_name(f.get("seller_name") or f.get("supplier"))
            buyer = norm_name(f.get("buyer_name") or f.get("buyer"))
            amounts = [f.get("total_amount")]
        elif d.type in CONTRACT_TYPES:
            seller = norm_name(f.get("supplier_name") or f.get("counterparty_name"))
            stated = f.get("client_name") or f.get("buyer_name")
            buyer = norm_name(stated) if stated else cust
            amounts = [f.get("fee_amount")] + [st.get("amount") for st in f.get("stages") or []]
        else:
            continue
        if seller == payee and buyer == cust and any(amt_eq(a, held["amount_gbp"]) for a in amounts):
            return d
    return None


def check_group_company(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    charts = [d for d in case.docs_by_window_end() if d.type == "group_structure"]
    txs = case.alert_txns.get(alert["alert_id"], [])
    cust = norm_name(case.customer.get("name"))
    if not charts or not txs or not cust:
        return None
    if alert["rule_id"] == "TM08":
        funded = txs
    else:
        funded = [t for t in txs if t["direction"] == "in" and norm_name(t.get("counterparty_name")) != cust]
    if not funded:
        return None
    used = [_chart_for(charts, cust, t) for t in funded]
    if not all(used):
        return None
    chart = used[0]
    ev = [chart.doc_id] + [t["transaction_id"] for t in funded][:3]
    facts = {"doc_id": chart.doc_id, "doc_title": chart.title, "doc_date": chart.dated,
             "counterparty": funded[0].get("counterparty_name"), "amount": fmt_gbp(funded[0]["amount_gbp"]),
             "txn_id": funded[0]["transaction_id"], "txn_date": funded[0]["booked_at"][:10], "rule": alert["rule_id"]}
    held, limit = case.held_payment, getattr(policy, "INVOICE_REQUIRED_GBP", None)
    if (held and limit is not None and held["direction"] == "out" and held["amount_gbp"] >= limit
            and norm_name(held.get("counterparty_name")) != cust):
        inv = _support_for(case, cust, held)
        if not inv:
            if any(d.type in INVOICE_TYPES + CONTRACT_TYPES for d in case.docs_by_window_end()):
                return None
            return Proof(alert["alert_id"], False, "RFI_PURPOSE_DOCUMENTS", ev + [held["transaction_id"]], facts,
                         "group_company")
        ev += [inv.doc_id, held["transaction_id"]]
        facts.update({"invoice_id": inv.doc_id, "held_amount": fmt_gbp(held["amount_gbp"]),
                      "held_payee": held.get("counterparty_name")})
    return Proof(alert["alert_id"], True, "GROUP_COMPANY", ev, facts, "group_company")
