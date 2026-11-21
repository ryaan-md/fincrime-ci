"""REFUND_OF_EARLIER_PAYMENT (T-CHK-REFUND): money that comes back is a refund of an identifiable earlier payment.
A refund pair is: a decisive document on file by the window end, the RETURN transaction (to the penny, the same
counterparty or own account the document names, dated on/after the document) and the ORIGINAL transaction in the
other direction (same counterparty or account, earlier, amount >= the return). Pairs by document type:
  venue_email / credit_note   credit from the venue / supplier refunding an earlier payment out to it (BOOKING ref
                              must match when the document gives one; a credit note must be addressed to the customer)
  cancellation_confirmation   investment cancelled in the cooling-off period: subscription in, same amount returned
                              to the own-name account the confirmation names (sort code + account number)
  support_chat                customer states the cash deposit is cash they withdrew earlier (cash_redeposit):
                              a cash withdrawal from the same account, before the deposit, in the stated month and
                              of EXACTLY the deposit amount (ruling 3, PR #57); otherwise RFI_SOURCE_OF_FUNDS
An alert passes when the covered transactions explain it: TM01 / TM06 would not fire on what is left, TM02 has
less than £2,000 left received, TM05 / TM08 have every alert transaction covered. A pair proven except for one
named fact (a cancellation refund account the confirmation does not say is in the customer's name, a re-deposit
with no matching withdrawal) gives an RFI for that fact. Partial or unidentifiable -> None: a refund that cannot be tied to an earlier payment is not an
innocent gap, so the default path decides."""
import re

from ..model import Proof
from .util import amt_eq, fmt_gbp, norm_name

TYPES = ("venue_email", "credit_note", "cancellation_confirmation", "support_chat")
TM02_MIN_RECEIVED_GBP = 2000.0   # POLICY.md TM02 (all four SOPs): fires only on £2,000.00 or more received


def _day(t):
    return t["booked_at"][:10]


def _ref(s):
    return " ".join((s or "").upper().split())


def _has_ref(ref, s):
    return not ref or re.search(r"(?<![A-Z0-9])" + re.escape(ref) + r"(?![A-Z0-9])", _ref(s)) is not None


def _same_party(a, b):
    return bool(norm_name(a)) and norm_name(a) == norm_name(b)


def _original(case, ret, direction, ok):
    """Latest earlier txn in `direction` that `ok` accepts, at least as large as the return."""
    cands = [t for t in case.txns if t["direction"] == direction and t["booked_at"] < ret["booked_at"]
             and t["transaction_id"] != ret["transaction_id"]
             and (t["amount_gbp"] > ret["amount_gbp"] or amt_eq(t["amount_gbp"], ret["amount_gbp"])) and ok(t)]
    return max(cands, key=lambda t: (t["booked_at"], t["transaction_id"])) if cands else None


def _counterparty_refund(case, d, party, amount, ref):
    """venue_email / credit_note: credit in from `party` == amount, refunding an earlier payment out to `party`."""
    if not party or amount is None:
        return None
    for ret in case.txns:
        if (ret["direction"] == "in" and _same_party(ret.get("counterparty_name"), party)
                and amt_eq(ret["amount_gbp"], amount) and d.dated <= _day(ret)
                and _has_ref(ref, ret.get("reference"))):
            orig = _original(case, ret, "out", lambda t: _same_party(t.get("counterparty_name"), party)
                             and _has_ref(ref, t.get("reference")))
            if orig:
                return ret, orig, None
    return None


def _venue(case, d):
    f = d.fields
    return _counterparty_refund(case, d, f.get("venue_name"), f.get("refund_amount"), _ref(f.get("booking_ref")))


def _credit_note(case, d):
    f = d.fields
    if not _same_party(f.get("buyer_name"), case.customer.get("name")):
        return None
    return _counterparty_refund(case, d, f.get("seller_name") or f.get("doc_issuer"), f.get("total_amount"),
                                _ref(f.get("booking_ref")))


def _cancellation(case, d):
    f = d.fields
    acct = f"{f.get('refund_sort_code', '')} {f.get('refund_account_number', '')}"
    if not (f.get("within_cancellation_period") is True
            and f.get("amount") is not None and f.get("refund_sort_code") and f.get("refund_account_number")):
        return None
    # POLICY.md: an account counts as the customer's own only when a document on file shows they hold it.
    gap = None if f.get("refund_in_own_name") is True else "RFI_COUNTERPARTY_DETAILS"
    name = case.customer.get("name")
    own = lambda t: _same_party(t.get("counterparty_name"), name) and " ".join(
        (t.get("counterparty_account") or "").split()) == acct
    for ret in case.txns:
        if ret["direction"] == "out" and own(ret) and amt_eq(ret["amount_gbp"], f["amount"]) and d.dated <= _day(ret):
            orig = _original(case, ret, "in", lambda t: own(t) and (
                not f.get("subscription_received") or _day(t) == f["subscription_received"]))
            if orig:
                return ret, orig, gap
    return None


def _redeposit(case, d):
    f = d.fields
    month, amount = f.get("stated_withdrawal_month"), f.get("stated_amount")
    if "cash_redeposit" not in (f.get("stated_explanations") or []) or not month or amount is None:
        return None
    for ret in case.txns:
        if (ret["direction"] == "in" and ret.get("channel") == "cash_deposit" and amt_eq(ret["amount_gbp"], amount)
                and d.dated >= _day(ret)):
            orig = _original(case, ret, "out", lambda t: t.get("channel") == "cash_withdrawal"
                             and t.get("account_id") == ret.get("account_id") and _day(t)[:7] == month
                             and amt_eq(t["amount_gbp"], ret["amount_gbp"]))
            return (ret, orig, None) if orig else (ret, None, "RFI_SOURCE_OF_FUNDS")
    return None


MATCHERS = {"venue_email": _venue, "credit_note": _credit_note, "cancellation_confirmation": _cancellation,
            "support_chat": _redeposit}


def refund_pairs(case):
    """[(doc, return_txn, original_txn, gap)] for every refund the documents on file tie to an earlier payment, in
    document order. gap is None when every fact is proven, else the RFI code naming the one fact still missing."""
    out, refunded = [], {}
    for d in case.docs_by_window_end():
        m = MATCHERS.get(d.type)
        hit = m(case, d) if m else None
        if not hit:
            continue
        ret, orig = hit[0], hit[1]
        if orig:
            total = refunded.get(orig["transaction_id"], 0.0) + ret["amount_gbp"]
            if total > orig["amount_gbp"] and not amt_eq(total, orig["amount_gbp"]):
                continue
            refunded[orig["transaction_id"]] = total
        out.append((d,) + hit)
    return out


def _covered(pairs):
    return {t["transaction_id"] for _, r, o, _ in pairs
            for t in (r, o) if t and (t is r or amt_eq(t["amount_gbp"], r["amount_gbp"]))}


def _explained(alert, txs, covered):
    rule, m = alert["rule_id"], alert.get("measured") or {}
    rest = [t for t in txs if t["transaction_id"] not in covered]
    if len(rest) == len(txs):
        return False
    cov_in = sum(t["amount_gbp"] for t in txs if t["transaction_id"] in covered and t["direction"] == "in")
    if rule == "TM01":
        base, lim = m.get("baseline_gbp"), m.get("limit_multiple")
        return bool(base and lim and m.get("received_gbp") is not None) and m["received_gbp"] - cov_in <= base * lim
    if rule == "TM06":
        return m.get("limit_gbp") is not None and sum(t["amount_gbp"] for t in rest) < m["limit_gbp"]
    if rule == "TM02":
        return m.get("received_gbp") is not None and m["received_gbp"] - cov_in < TM02_MIN_RECEIVED_GBP
    if rule in ("TM05", "TM08"):
        return not rest
    return False


def check_refund_of_earlier_payment(case, alert, policy):
    txs = case.alert_txns.get(alert["alert_id"], [])
    if not txs or not any(d.type in TYPES for d in case.docs):
        return None
    pairs = refund_pairs(case)
    complete = [p for p in pairs if p[3] is None]
    if complete and _explained(alert, txs, _covered(complete)):
        used, passed, code = complete, True, "REFUND_OF_EARLIER_PAYMENT"
    elif pairs and _explained(alert, txs, _covered(pairs)):
        used = [p for p in pairs if p[3] is not None]
        passed, code = False, used[0][3]
    else:
        return None
    ids = {t["transaction_id"] for t in txs}
    d, ret, orig, _ = next((p for p in used if _covered([p]) & ids), used[0])
    facts = {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "amount": fmt_gbp(ret["amount_gbp"]),
             "txn_id": ret["transaction_id"], "txn_date": _day(ret), "counterparty": ret.get("counterparty_name"),
             "rule": alert["rule_id"]}
    if orig:
        facts.update({"original_txn_id": orig["transaction_id"], "original_txn_date": _day(orig),
                      "original_amount": fmt_gbp(orig["amount_gbp"])})
    evidence = [d.doc_id, ret["transaction_id"]] + ([orig["transaction_id"]] if orig else [])
    return Proof(alert["alert_id"], passed, code, evidence, facts, "refund_of_earlier_payment")
