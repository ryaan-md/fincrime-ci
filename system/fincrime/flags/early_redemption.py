"""TM10: ESC_EARLY_REDEMPTION when fund units were bought and quickly sold back to move money on to someone else.

POLICY.md code table: ESC_EARLY_REDEMPTION = "Fund units are bought and quickly sold back to move money". TM10 itself
("units sold back within 90 days") is not suspicion: a redundancy that forces a sale closes it, and an own-funded
redemption at a loss paid back to the customer's own account is at most an RFI (ambiguous cluster #1, open with the
policy judge). So this fires only when the money MOVES ON, with two independent signals:

  required  the redemption proceeds go out to someone else: a payment of at least half the redeemed amount, made
            within 10 days of the redemption, to a payee whose name is not the customer's; and
  plus one of
    - quick round trip: the units were held 30 days or less;
    - third-party funding: someone other than the customer paid in at least half the purchase price in the 30 days
      before the units were bought;
    - the customer says in a chat or note that they invest or hold money for other people.

Context only, never decisive (POLICY.md): round amounts, crypto or gambling counterparties, shared devices. Not used."""
import datetime
import re
from ..model import Flag
from ..checks.util import norm_name

SEVERITY = 3
PAYOUT_DAYS = 10
QUICK_DAYS = 30
FUNDING_DAYS = 30
FOR_OTHERS_RE = re.compile(r"\b(?:invest|investing|hold|holding|manage|managing|trade|trading)\b[^.?!]{0,30}"
                           r"\b(?:for|on behalf of)\s+(?:a few |some |several |other |my )?"
                           r"(?:people|friends|others|family|relatives|clients|colleagues|cousins)\b", re.I)
BOUGHT_RE = re.compile(r"bought on (\d{1,2} [A-Za-z]+ \d{4})")


def _day(iso):
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return datetime.date(y, m, d).toordinal()


def _redemption(case, alert, redeemed):
    txs = [t for t in case.alert_txns.get(alert["alert_id"], []) if t.get("direction") == "in"]
    red = [t for t in txs if t.get("channel") == "fund_redemption"] or \
          [t for t in txs if redeemed is not None and abs(t["amount_gbp"] - redeemed) <= 0.01]
    return sorted(red, key=lambda t: (t["booked_at"], t["transaction_id"]))[-1] if red else None


def _subscription(case, alert, bought):
    txs = [t for t in case.alert_txns.get(alert["alert_id"], []) if t.get("direction") == "out"
           and (t.get("channel") == "fund_subscription" or (bought is not None and abs(t["amount_gbp"] - bought) <= 0.01))]
    exact = [t for t in txs if bought is not None and abs(t["amount_gbp"] - bought) <= 0.01]
    pick = exact or txs
    return sorted(pick, key=lambda t: (t["booked_at"], t["transaction_id"]))[-1] if pick else None


def scan_early_redemption(case, policy):
    out = []
    cust = norm_name(case.customer.get("name"))
    for a in case.alerts:
        if a.get("rule_id") != "TM10":
            continue
        m = a.get("measured") or {}
        redeemed, bought, held = m.get("redeemed_gbp"), m.get("bought_gbp"), m.get("held_days")
        red = _redemption(case, a, redeemed)
        if red is None:
            continue
        redeemed = redeemed if redeemed is not None else red["amount_gbp"]
        r_day = _day(red["booked_at"])
        payouts = [t for t in case.txns if t.get("direction") == "out" and t.get("channel") != "fund_subscription"
                   and 0 <= _day(t["booked_at"]) - r_day <= PAYOUT_DAYS
                   and norm_name(t.get("counterparty_name")) not in ("", cust)
                   and t["amount_gbp"] >= 0.5 * redeemed]
        if not payouts:
            continue
        signals = []
        if held is not None and held <= QUICK_DAYS:
            signals.append("quick_round_trip")
        sub = _subscription(case, a, bought)
        funders = []
        if sub is not None:
            s_day = _day(sub["booked_at"])
            funders = [t for t in case.txns if t.get("direction") == "in" and t.get("channel") != "fund_redemption"
                       and 0 <= s_day - _day(t["booked_at"]) <= FUNDING_DAYS
                       and norm_name(t.get("counterparty_name")) not in ("", cust)]
            if sum(t["amount_gbp"] for t in funders) < 0.5 * (bought if bought is not None else sub["amount_gbp"]):
                funders = []
        if funders:
            signals.append("third_party_funded")
        said = [d for d in case.docs if d.type in ("support_chat", "staff_note", "contact_note", "customer_email")
                and d.dated <= case.window["to"] and FOR_OTHERS_RE.search(d.text)]
        if said:
            signals.append("invests_for_others")
        if not signals:
            continue
        pay = payouts[0]
        quote = FOR_OTHERS_RE.search(said[0].text).group(0) if said else ""
        ev = [d.doc_id for d in said] + [pay["transaction_id"], red["transaction_id"]] + \
             ([sub["transaction_id"]] if sub else []) + [t["transaction_id"] for t in funders]
        facts = {"variant": "+".join(["third_party_payout"] + signals), "fund": m.get("fund"), "held_days": held,
                 "bought_gbp": bought, "redeemed_gbp": redeemed, "redeemed_on": red["booked_at"][:10],
                 "bought_on": sub["booked_at"][:10] if sub else None,
                 "payout_to": pay.get("counterparty_name"), "payout_gbp": pay["amount_gbp"],
                 "payout_on": pay["booked_at"][:10], "payout_txn": pay["transaction_id"],
                 "funded_by": sorted({t["counterparty_name"] for t in funders}),
                 "funded_gbp": round(sum(t["amount_gbp"] for t in funders), 2), "customer_said": quote,
                 "doc_id": said[0].doc_id if said else None, "doc_title": said[0].title if said else None}
        out.append(Flag("ESC_EARLY_REDEMPTION", ev, facts, "early_redemption", SEVERITY))
    return out
