"""THIRD_PARTY_DOCUMENTED: a documented relationship explains a third-party payer (TM08 funding).
POLICY.md (investment, payments_fx): CLOSE `THIRD_PARTY_DOCUMENTED` "A documented relationship explains a third-party
payer". Two documented relationships:
  gift letter         giver == payer, recipient == customer, family relationship stated, amount == credit (penny),
                      outright gift (not repayable, giver keeps no interest)
  invoice finance     factoring agreement: financier == payer, client == customer, advance paid to the client;
                      remittance advice from the financier: client == customer, advance == credit (penny),
                      advance == rate x financed invoices, rate == the agreement's rate
Every document is dated on/before the credit and on file by the window end.
TM08 passes when every alert credit is explained. TM01 / TM02 pass when the explained credits bring the alert back
under its own limit (the documented money is what tripped it). A payer-matching letter or agreement that fails
another fact -> Proof(passed=False): RFI_SOURCE_OF_FUNDS (gift) or RFI_PURPOSE_DOCUMENTS (invoice finance)."""
import re
from ..model import Proof
from .util import fmt_gbp, norm_name

APPLIES_TO = {"TM01", "TM02", "TM08"}
FAMILY = {"child", "son", "daughter", "parent", "mother", "father", "grandchild", "grandson", "granddaughter",
          "grandparent", "grandmother", "grandfather", "sibling", "brother", "sister", "spouse", "wife", "husband",
          "partner", "relative", "niece", "nephew", "aunt", "uncle", "cousin"}
NOT_REPAYABLE_RE = re.compile(
    r"\bnon-repayable\b|\bnot repayable\b|\b(?:does|do|will) not (?:require|need) repayment\b"
    r"|\bnot (?:need|have|be required|required) to be repaid\b|\bno part of [\w ]{1,30}? is required to be repaid\b"
    r"|\bnot subject to (?:any )?(?:requirement for )?repayment(?: obligation| terms)?\b"
    r"|\b(?:no|without any) (?:requirement|expectation|obligation)(?: or (?:requirement|expectation))? (?:of|for) "
    r"(?:(?:this amount|these funds) to be repaid|repayment)\b|\bno repayment is expected\b|\bexpect no repayment\b",
    re.I)
COND_RE = re.compile(r"\b(?:unless|except|until|provided that|if|in the event|should|must|later than|on demand"
                     r"|before|after|while|when|once|within|yet|for (?:\w+ )?(?:years?|months?)"
                     r"|at (?:this|the present) time|for now)\b", re.I)


def _pennies(x):
    return None if x is None else round(float(x) * 100)


def _same(a, b):
    """Equal to the penny (amt_eq's tolerance would let a 1p difference through)."""
    return a is not None and b is not None and _pennies(a) == _pennies(b)


def _not_repayable(d):
    """Parser flag, else a local reading: every sentence about repayment is an unconditional not-repayable phrase."""
    if d.fields.get("gift_not_repayable"):
        return True
    if re.search(r"\bloan\b", d.text, re.I):
        return False
    sents = [s for s in re.split(r"(?<=[.!?])\s+", d.text) if re.search(r"repa(?:y|id)", s, re.I)]
    return bool(sents) and all(not COND_RE.search(s) and NOT_REPAYABLE_RE.search(s)
                               and not re.search(r"repa(?:y|id)", NOT_REPAYABLE_RE.sub("", s), re.I) for s in sents)


def _on_file(d, t, case):
    return d.dated and d.dated <= t["booked_at"][:10] and d.dated <= case.window["to"]


def _gift(case, t, cust, used):
    """-> (proof facts, None) when a gift letter explains credit t; (None, rfi) when it only partly does."""
    rfi = None
    for d in case.docs_of("gift_letter"):
        f = d.fields
        if norm_name(f.get("giver_name")) != norm_name(t["counterparty_name"]):
            continue
        ok = (d.doc_id not in used and norm_name(f.get("recipient_name")) == cust
              and (f.get("relationship") or "").lower() in FAMILY
              and _same(f.get("amount"), t["amount_gbp"]) and _not_repayable(d) and f.get("giver_no_interest")
              and _on_file(d, t, case))
        if ok:
            return {"doc_id": d.doc_id, "doc_title": d.title, "doc_date": d.dated, "relationship": f["relationship"],
                    "payer": t["counterparty_name"]}, [d.doc_id], None
        rfi = "RFI_SOURCE_OF_FUNDS"
    return None, [], rfi


def _factoring(case, t, cust, used):
    payer = norm_name(t["counterparty_name"])
    aggs = [d for d in case.docs_of("factoring_agreement") if norm_name(d.fields.get("financier_name")) == payer]
    if not aggs:
        return None, [], None
    for a in aggs:
        fa = a.fields
        if not (norm_name(fa.get("client_name")) == cust and fa.get("advance_to_client_account")
                and fa.get("advance_rate_pct") and _on_file(a, t, case)):
            continue
        rate = fa["advance_rate_pct"] / 100.0
        for r in case.docs_of("remittance_advice"):
            fr = r.fields
            adv, tot = fr.get("advance_amount"), fr.get("total_amount")
            if (r.doc_id not in used and norm_name(fr.get("doc_issuer")) == payer
                    and norm_name(fr.get("client_name")) == cust
                    and _same(adv, t["amount_gbp"]) and tot is not None and _same(adv, round(tot * rate, 2))
                    and (fr.get("advance_rate") is None or abs(fr["advance_rate"] - rate) < 1e-9)
                    and _on_file(r, t, case)):
                return {"doc_id": r.doc_id, "doc_title": r.title, "doc_date": r.dated, "agreement_id": a.doc_id,
                        "agreement_title": a.title, "invoices_total": fmt_gbp(tot), "advance_rate": f"{fa['advance_rate_pct']:g}%",
                        "payer": t["counterparty_name"]}, [r.doc_id, a.doc_id], None
    return None, [], "RFI_PURPOSE_DOCUMENTS"


def _explain(case, t, cust, used):
    if t.get("direction") != "in" or not t.get("counterparty_name") or norm_name(t["counterparty_name"]) == cust:
        return None, [], None
    facts, docs, rfi = _gift(case, t, cust, used)
    if facts:
        return facts, docs, None
    facts, docs, rfi2 = _factoring(case, t, cust, used)
    return facts, docs, (None if facts else rfi or rfi2)


def check_third_party_documented(case, alert, policy):
    if alert["rule_id"] not in APPLIES_TO:
        return None
    txs = case.alert_txns.get(alert["alert_id"], [])
    cust = norm_name(case.customer.get("name"))
    if not txs or not cust:
        return None
    explained, docs, rfi, first, used = [], [], None, None, set()
    for t in sorted(txs, key=lambda t: (t["booked_at"], t["transaction_id"])):
        facts, ds, r = _explain(case, t, cust, used)
        if facts:
            explained.append(t); first = first or facts
            used.add(ds[0])
            docs += [d for d in ds if d not in docs]
        rfi = rfi or r
    m = alert.get("measured", {})
    if alert["rule_id"] == "TM08":
        third = [t for t in txs if t.get("direction") == "in" and norm_name(t.get("counterparty_name")) != cust]
        done = bool(third) and len(explained) == len(txs)
    elif not explained:
        done = False
    else:
        rest = (m.get("received_gbp") or 0) - sum(t["amount_gbp"] for t in explained)
        if alert["rule_id"] == "TM01":
            done = m.get("baseline_gbp") is not None and m.get("limit_multiple") is not None \
                and rest <= m["baseline_gbp"] * m["limit_multiple"] + policy.AMOUNT_TOLERANCE
        else:  # TM02 counts only third-party money in: every such credit must be documented
            done = rest <= policy.AMOUNT_TOLERANCE
    if not done and not rfi:
        return None
    ev = docs + [t["transaction_id"] for t in explained][:3]
    tot = sum(t["amount_gbp"] for t in explained)
    facts = {**(first or {}), "amount": fmt_gbp(tot), "txn_id": explained[0]["transaction_id"] if explained else "",
             "txn_date": explained[0]["booked_at"][:10] if explained else "", "rule": alert["rule_id"]}
    if done:
        return Proof(alert["alert_id"], True, "THIRD_PARTY_DOCUMENTED", ev, facts, "third_party_documented")
    return Proof(alert["alert_id"], False, rfi, ev or [t["transaction_id"] for t in txs][:3], facts,
                 "third_party_documented")
