"""Bank: the customer gives conflicting stories for where the money came from, and the cash is moved straight on.

POLICY.md (bank) ESC_UNEXPLAINED_FUNDS: "The money has no credible explanation and was moved on."
"Once there is suspicion, escalate. Do not send the customer an RFI at that point."

POLICY.md says context alone never raises an alert, so two independent signals are both required:
1. Narrative: two support chats on file by the window end each state where the money came from (a gift, a sale,
   a loan, ...), and the stories do not overlap. Two different answers to the same question are not a credible
   explanation. One story, or the same story told twice, does not fire: that is an RFI for proof of the story.
   Purposes ("family support", a hospital bill) say where money went, not where it came from, so they are ignored.
2. Structure: cash paid in during the review window, and transfers out (card spend excluded, as in TM02) within
   policy.CONFLICTING_CASH_STORY["within_hours"] of a cash deposit, worth at least ["min_share"] of the cash paid in.

Runs only where the policy module sets CONFLICTING_CASH_STORY (bank)."""
from datetime import datetime, timedelta
from ..model import Flag

# parsers/notes.py stated_explanations categories that say where money came from.
SOURCE_STORIES = ("cash_redeposit", "friends_money", "gift", "loan_from_friend", "payments_job", "sale_proceeds")
NOT_MOVED_ON = ("card",)


def _at(t):
    return datetime.fromisoformat(t["booked_at"][:19])


def _stories(doc):
    return sorted({s for s in (doc.fields.get("stated_explanations") or []) if s in SOURCE_STORIES})


def scan_conflicting_cash_story(case, policy):
    cfg = getattr(policy, "CONFLICTING_CASH_STORY", None)
    if not cfg:
        return []
    chats = sorted((d for d in case.docs_by_window_end() if d.type == "support_chat" and _stories(d)),
                   key=lambda d: (d.dated, d.doc_id))
    pair = next(((a, b) for i, a in enumerate(chats) for b in chats[i + 1:]
                 if not set(_stories(a)) & set(_stories(b))), None)
    if not pair:
        return []
    w = case.window
    in_window = [t for t in case.txns if t.get("booked_at") and w["from"] <= t["booked_at"][:10] <= w["to"]]
    cash = [t for t in in_window if t.get("direction") == "in" and t.get("channel") == "cash_deposit"]
    cash_in = round(sum(t.get("amount_gbp") or 0 for t in cash), 2)
    if not cash_in:
        return []
    within = timedelta(hours=cfg["within_hours"])
    moved = [t for t in in_window if t.get("direction") == "out" and t.get("channel") not in NOT_MOVED_ON
             and any(timedelta(0) <= _at(t) - _at(c) <= within for c in cash)]
    moved_out = round(sum(t.get("amount_gbp") or 0 for t in moved), 2)
    if moved_out < cfg["min_share"] * cash_in:
        return []
    a, b = pair
    facts = {"doc_id": a.doc_id, "doc_title": a.title, "other_doc_id": b.doc_id, "story": ", ".join(_stories(a)),
             "other_story": ", ".join(_stories(b)), "cash_in": f"£{cash_in:,.2f}", "moved_out": f"£{moved_out:,.2f}",
             "variant": "conflicting_cash_story"}
    ev = [a.doc_id, b.doc_id] + [t["transaction_id"] for t in cash + moved]
    return [Flag("ESC_UNEXPLAINED_FUNDS", ev, facts, "bank_conflicting_cash_story", 2)]
