"""OWN_ACCOUNT: money moved between accounts the customer is SHOWN to hold (POLICY.md: "An account in the customer's own
name elsewhere counts as their own account only when a document on file shows the customer holds it").
A transaction is proven own-account when a bank_statement on file by the window end
  - names the customer as a holder (sole or joint, normalised: case, "Ltd"/"Limited", whitespace),
  - is for the transaction's counterparty_account (sort code + number, IBAN, or the stated last 4 digits),
  - lists a transfer of the same amount to the penny, dated within MAX_DATE_GAP days of the booking, and, when the
    line states a direction, the other side of the move (money out of the own account for an inflow, and back);
each statement line proves at most one transaction.
Per rule, the proven transactions must explain the alert:
  TM01  received minus proven own-account inflows is within the limit; TM02  the largest inflow and outflow are
        proven; TM06 / TM08  every alert transaction is proven.
  payments_fx TM01 only: funding in the client's own name passed on within PASS_THROUGH_DAYS to a proven own account
        counts for the forwarded amount only (POLICY.md: "Transfers to accounts in the client's own name do not"
        need an invoice or contract; the declared funding channel is "Bank transfer from own account"). The
        document-shows-the-customer-holds-it test is applied to the own account the money is moved to.
TM01 measured.multiple >= OWN_ACCOUNT_RFI_MULTIPLE -> RFI_UPDATED_PROFILE (SOP), below -> CLOSE.
A customer-held statement for the counterparty account whose amounts do not reconcile -> RFI_SOURCE_OF_FUNDS."""
import re
from datetime import date
from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

APPLIES_TO = {"TM01", "TM02", "TM06", "TM08"}
MAX_DATE_GAP = 2          # days between the booking and the statement line (value-date drift)
_OTHER_SIDE = {"in": "out", "out": "in"}
PASS_THROUGH_DAYS = 3     # payments_fx: own-name funding forwarded to the own account within this many days


def _days(a, b):
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def _acct(s):
    """Account identifiers in a counterparty_account string: (full, last4)."""
    s = (s or "").upper()
    m = re.search(r"\b(\d{2})[- ]?(\d{2})[- ]?(\d{2})\s+(\d{8})\b", s)
    if m:
        return "".join(m.groups()), m.group(4)[-4:]
    m = re.search(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?)\b", s)
    if m:
        iban = m.group(1).replace(" ", "")
        return iban, iban[-4:]
    m = re.search(r"\bENDING\s+([A-Z0-9]{4})\b", s)
    return (None, m.group(1)) if m else (None, None)


def _stmt_accts(f):
    """(full ids, last4s) the statement states for its account."""
    full, last4 = set(), set()
    if f.get("sort_code") and f.get("account_number"):
        full.add(f["sort_code"].replace("-", "") + f["account_number"])
    if f.get("iban"):
        full.add(f["iban"])
    for k in ("account_last4", "iban_last4"):
        if f.get(k):
            last4.add(f[k].upper())
    for x in full:
        last4.add(x[-4:])
    return full, last4


def _same_account(txn_acct, stmt):
    full, last4 = _acct(txn_acct)
    s_full, s_last4 = _stmt_accts(stmt.fields)
    if full and s_full:
        return full in s_full
    return bool(last4) and last4 in s_last4


def _holds(stmt, cust):
    f = stmt.fields
    names = list(f.get("holder_names") or []) + [f.get("holder_name"), f.get("iban_holder")]
    return bool(cust) and any(norm_name(n) == cust for n in names if n)


def _own_name(t, cust, stmts):
    n = norm_name(t.get("counterparty_name"))
    return bool(n) and (n == cust or any(n == norm_name(s.fields.get("account_name")) for s in stmts))


def _proof_for(t, stmts, consumed):
    """(statement, line) proving t moved to/from an own account, else (statement for that account or None, None).
    Lines in `consumed` already prove another transaction; the line used here is added to it."""
    linked = None
    for s in stmts:
        if not _same_account(t.get("counterparty_account"), s):
            continue
        linked = linked or s
        for i, line in enumerate(s.fields.get("transfers") or []):
            if (s.doc_id, i) not in consumed and (line.get("direction") or "") in ("", _OTHER_SIDE[t["direction"]]) \
                    and amt_eq(line.get("amount"), t["amount_gbp"]) and line.get("date") \
                    and _days(line["date"], t["booked_at"]) <= MAX_DATE_GAP:
                consumed.add((s.doc_id, i))
                return s, line
    return linked, None


def _explained(alert, txs, proven, case, cust):
    """Do the proven transactions explain this alert? Returns (ok, ids of the transactions relied on)."""
    rule, m = alert["rule_id"], alert.get("measured") or {}
    if rule in ("TM06", "TM08"):
        return bool(txs) and all(t["transaction_id"] in proven for t in txs), [t["transaction_id"] for t in txs]
    if rule == "TM02":
        legs = [max((t for t in txs if t["direction"] == d), key=lambda t: (t["amount_gbp"], t["transaction_id"]),
                    default=None) for d in ("in", "out")]
        legs = [t for t in legs if t]
        return bool(legs) and all(t["transaction_id"] in proven for t in legs), [t["transaction_id"] for t in legs]
    # TM01: without the own-account inflows the account would have stayed within the limit.
    received, baseline, limit = m.get("received_gbp"), m.get("baseline_gbp"), m.get("limit_multiple")
    if received is None or not baseline or not limit:
        return False, []
    used = [t for t in txs if t["direction"] == "in" and t["transaction_id"] in proven]
    covered = sum(t["amount_gbp"] for t in used)
    if not used and case.institution == "payments_fx":   # own-name funding passed on to a proven own account
        outs = sorted((t for t in case.txns if t["direction"] == "out" and t["transaction_id"] in proven),
                      key=lambda t: (-t["amount_gbp"], t["transaction_id"]))
        for t in sorted(txs, key=lambda t: (-t["amount_gbp"], t["transaction_id"])):
            if t["direction"] != "in" or norm_name(t.get("counterparty_name")) != cust:
                continue
            o = next((o for o in outs if o["booked_at"] >= t["booked_at"] and o["amount_gbp"] <= t["amount_gbp"]
                      and _days(o["booked_at"], t["booked_at"]) <= PASS_THROUGH_DAYS), None)
            if o:
                used, covered = [t, o], o["amount_gbp"]
                break
    return bool(used) and received - covered <= limit * baseline + 0.01, [t["transaction_id"] for t in used]


def check_own_account(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    cust = norm_name(case.customer.get("name"))
    stmts = [d for d in case.docs_by_window_end() if d.type == "bank_statement" and _holds(d, cust)]
    txs = case.alert_txns.get(alert["alert_id"], [])
    if not stmts or not txs:
        return None
    w = case.window
    pool = {t["transaction_id"]: t for t in txs}
    for t in case.txns:   # the other leg of an own-account move can sit outside the alert's own transactions
        if w["from"] <= t["booked_at"][:10] <= w["to"] and _own_name(t, cust, stmts):
            pool.setdefault(t["transaction_id"], t)
    alert_ids = {t["transaction_id"] for t in txs}
    proven, linked, consumed = {}, None, set()
    for tid in sorted(pool, key=lambda tid: (tid not in alert_ids, tid)):
        t = pool[tid]
        if not t.get("counterparty_account"):
            continue
        s, line = _proof_for(t, stmts, consumed)
        if line:
            proven[tid] = (s, line)
        elif s and tid in alert_ids:
            linked = linked or (s, t)
    ok, used = _explained(alert, txs, proven, case, cust)
    if not ok:
        if linked:
            s, t = linked
            return Proof(alert["alert_id"], False, "RFI_SOURCE_OF_FUNDS", [s.doc_id, t["transaction_id"]],
                         {"doc_id": s.doc_id, "doc_title": s.title, "txn_id": t["transaction_id"],
                          "amount": fmt_gbp(t["amount_gbp"]), "rule": alert["rule_id"]}, "own_account")
        return None
    docs = []
    for tid in used:
        if tid in proven and proven[tid][0].doc_id not in docs:
            docs.append(proven[tid][0].doc_id)
    stmt = next(s for s in stmts if s.doc_id == docs[0])
    key = next(tid for tid in used if tid in proven)
    multiple = max([a.get("measured", {}).get("multiple") or 0 for a in case.alerts if a["rule_id"] == "TM01"] or [0])
    facts = {"doc_id": stmt.doc_id, "doc_title": stmt.title, "doc_date": stmt.dated, "multiple": multiple,
             "rule": alert["rule_id"], "txn_id": key, "txn_date": pool[key]["booked_at"][:10],
             "amount": fmt_gbp(pool[key]["amount_gbp"])}
    ev = docs + used
    if multiple >= policy.OWN_ACCOUNT_RFI_MULTIPLE:
        return Proof(alert["alert_id"], False, "RFI_UPDATED_PROFILE", ev, facts, "own_account")
    return Proof(alert["alert_id"], True, "OWN_ACCOUNT", ev, facts, "own_account")
