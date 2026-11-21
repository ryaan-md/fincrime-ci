"""T-FLG-MULE: the account moves other people's money, or passes money through to linked parties.

POLICY.md reason codes (all four institutions):
  ESC_MULE_ACTIVITY        "The account receives and passes on other people's money for someone else"
  ESC_PASS_THROUGH         "Money passes through with no credible purpose, often to linked parties"
  ESC_THIRD_PARTY_FUNDING  "Unrelated third parties fund the account or receive money from it"
  ESC_UNEXPLAINED_FUNDS    "The money has no credible explanation and was moved on"

Four scanners (the fraud report one lives in fraud_report.py):
1. Pass-through: money paid out in the review window to a company that was incorporated within a year before the
   payment and is run by the customer's own director or owner (same officer or PSC name on both register extracts).
2. Commission pool: a payment file whose many payees share only a few addresses, and the account pays them.
   Unrelated people do not live together in threes and tens; the payees are fronts for someone else.
3. Third-party funding: the customer says in a chat that they invest for other people, that others invest through
   their account, or that they pass the money on, and money from or to someone other than the customer moved in the
   window.
4. Dissolved payer: money received from a company that the register shows was dissolved before the payment.

Strict on substance: a link needs the same person on both registers, the payee must actually be paid, the company
must be new at the payment date. Tolerant on noise: "Ltd"/"Limited", case, punctuation and whitespace."""
import re
from ..model import Flag
from ..checks.util import norm_name
from ..loader import parse_dates

REGISTER_TYPES = ("company_register_extract", "significant_control_register")
PERSON_COLS = ("officer", "person", "person with significant control", "name", "director")
PAYEE_COLS = ("payee", "contractor", "name", "recipient")
NEW_COMPANY_DAYS = 365
MIN_POOL_PAYEES = 7          # TM04: "more than 6 different people"
MIN_PER_ADDRESS = 3          # an address shared by 3 or more payees
MAX_ADDRESS_SHARE = 1 / 3    # distinct addresses at most a third of the payees
NOT_PAID_OUT = ("card", "fund_subscription")

THIRD_PARTY_RE = [re.compile(p) for p in (
    r"\binvest(?:s|ed|ing)?\s+(?:it\s+|money\s+|funds\s+)?(?:for|on behalf of)\s+(?:them|others|other people|"
    r"a few people|some people|people|friends|relatives|my (?:friends?|relatives|cousins?|"
    r"brothers?|sisters?|uncles?|aunts?|parents|mates?))\b",
    r"\binvest(?:s|ed|ing)?\s+through\s+(?:mine|my account|me)\b",
    r"\b(?:sending|passing|send|pass) (?:the|their|this|that) money on\b",
)]


def _iso(text):
    d = parse_dates(text or "")
    return d[0] if d else None


def _days(a, b):
    """Whole days from ISO date a to ISO date b."""
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _tables(text):
    """Yield (header, rows) for every markdown table, cells stripped, header lower-cased."""
    block = []
    for line in (text or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if len(block) >= 2:
            rows = [r for r in block[1:] if not all(set(c) <= set("-: ") for c in r)]
            yield [h.lower() for h in block[0]], rows
        block = []


def _col(header, names):
    for i, h in enumerate(header):
        if h in names:
            return i
    return None


def _company(doc):
    """The company a register extract is about: its Company field, else the title after ' for '."""
    name = doc.fields.get("company") or (doc.title.split(" for ", 1)[1] if " for " in doc.title else "")
    return norm_name(name.split(":")[0])


def _people(doc):
    """Normalised names of every officer and person with significant control in a register extract."""
    out = set()
    for header, rows in _tables(doc.text):
        i = _col(header, PERSON_COLS)
        if i is None:
            continue
        out |= {norm_name(r[i]) for r in rows if i < len(r) and norm_name(r[i])}
    return out


def _window_txns(case):
    """Transactions booked in the review window. Rows missing a date, ID or direction are skipped, never guessed."""
    w = case.window or {}
    lo, hi = w.get("from") or "", w.get("to") or "9999"
    return [t for t in case.txns if t and t.get("booked_at") and t.get("transaction_id") and t.get("direction")
            and lo <= t["booked_at"][:10] <= hi]


def _is_customer(case, name):
    return norm_name(name) == norm_name(case.customer.get("name"))


def scan_pass_through(case, policy):
    """Payments out to a newly incorporated company that the customer's own director or owner runs."""
    regs = [d for d in case.docs if d.type in REGISTER_TYPES]
    me = norm_name(case.customer.get("name"))
    mine = set().union(*[_people(d) for d in regs if _company(d) == me]) if me else set()
    if case.customer.get("kind") == "individual" or not mine:
        mine |= {me} if me else set()
    mine.discard("")
    if not mine:
        return []
    outs = [t for t in _window_txns(case) if t["direction"] == "out" and t.get("channel") not in NOT_PAID_OUT]
    hits = []
    for d in regs:
        co = _company(d)
        inc = _iso(d.fields.get("incorporated"))
        shared = sorted(_people(d) & mine)
        if not co or co == me or not inc or not shared:
            continue
        paid = [t for t in outs if norm_name(t.get("counterparty_name")) == co and 0 <= _days(inc, t["booked_at"][:10])
                <= NEW_COMPANY_DAYS]
        if paid:
            hits.append((d, inc, shared, paid))
    if not hits:
        return []
    d, inc, shared, paid = hits[0]
    ev = [x.doc_id for x, _, _, _ in hits] + [t["transaction_id"] for _, _, _, p in hits for t in p]
    facts = {"doc_id": d.doc_id, "doc_title": d.title, "linked_company": paid[0]["counterparty_name"],
             "incorporated": inc, "linked_person": shared[0], "transaction_id": paid[0]["transaction_id"],
             "payment_date": paid[0]["booked_at"][:10], "linked_companies": len(hits),
             "payments_to_linked": sum(len(p) for _, _, _, p in hits), "variant": "new_company_same_director"}
    return [Flag("ESC_PASS_THROUGH", ev, facts, "pass_through", 4)]


def _norm_addr(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def scan_commission_pool(case, policy):
    """A payment file of many payees who share a handful of addresses, and the account pays them."""
    outs = {}
    for t in _window_txns(case):
        if t["direction"] == "out" and t.get("channel") not in NOT_PAID_OUT:
            outs.setdefault(norm_name(t.get("counterparty_name")), []).append(t)
    out = []
    for d in case.docs_of("payment_file"):
        for header, rows in _tables(d.text):
            pi, ai = _col(header, PAYEE_COLS), _col(header, ("address",))
            if pi is None or ai is None:
                continue
            rows = [r for r in rows if max(pi, ai) < len(r) and norm_name(r[pi]) and _norm_addr(r[ai])]
            by_addr = {}
            for r in rows:
                by_addr.setdefault(_norm_addr(r[ai]), set()).add(norm_name(r[pi]))
            payees = {norm_name(r[pi]) for r in rows}
            if len(payees) < MIN_POOL_PAYEES or len(by_addr) > len(payees) * MAX_ADDRESS_SHARE:
                continue
            if max(len(v) for v in by_addr.values()) < MIN_PER_ADDRESS:
                continue
            paid = sorted((t for p in sorted(payees) for t in outs.get(p, [])),
                          key=lambda t: (t["booked_at"], t["transaction_id"]))
            if len({norm_name(t["counterparty_name"]) for t in paid}) * 2 < len(payees):
                continue
            pool = [t for t in _window_txns(case) if t["direction"] == "in" and not _is_customer(case, t.get("counterparty_name"))
                    and t["booked_at"] <= paid[0]["booked_at"]]
            ev = [d.doc_id] + [t["transaction_id"] for t in pool[-1:]] + [t["transaction_id"] for t in paid]
            facts = {"doc_id": d.doc_id, "doc_title": d.title, "payees": len(payees), "addresses": len(by_addr),
                     "paid_payees": len({norm_name(t["counterparty_name"]) for t in paid}),
                     "variant": "commission_pool"}
            if pool:
                facts.update({"pool_payer": pool[-1]["counterparty_name"], "pool_txn_id": pool[-1]["transaction_id"]})
            out.append(Flag("ESC_MULE_ACTIVITY", ev, facts, "commission_pool", 4))
            break
    return out


def _customer_lines(doc):
    """What the customer said in a chat (lines starting **Customer:**), lower-cased, quotes normalised."""
    said = re.findall(r"^\s*\*\*customer:?\*\*:?\s*(.+)$", doc.text or "", re.I | re.M)
    return " ".join(said).lower().replace("’", "'")


def scan_third_party_funding(case, policy):
    """The customer says they invest or pass on other people's money, and third-party money moved in the window."""
    third = [t for t in _window_txns(case) if t.get("channel") not in NOT_PAID_OUT + ("fund_redemption", "direct_debit",
             "standing_order") and t.get("counterparty_name") and not _is_customer(case, t["counterparty_name"])]
    if not third:
        return []
    for d in case.docs_of("support_chat"):
        said = _customer_lines(d)
        m = next((m for r in THIRD_PARTY_RE for m in [r.search(said)] if m), None)
        if not m:
            continue
        tm08 = [t for a in case.alerts if a.get("rule_id") == "TM08" for t in case.alert_txns.get(a.get("alert_id"), [])
                if t and t.get("transaction_id")]
        txs = tm08 or third
        facts = {"doc_id": d.doc_id, "doc_title": d.title, "said": m.group(0), "transaction_id": txs[0]["transaction_id"],
                 "counterparty": txs[0].get("counterparty_name"), "variant": "customer_moves_others_money"}
        return [Flag("ESC_THIRD_PARTY_FUNDING", [d.doc_id] + [t["transaction_id"] for t in txs], facts,
                     "third_party_funding", 3)]
    return []


def scan_dissolved_payer(case, policy):
    """Money received from a company the register shows was dissolved before the payment."""
    ins = [t for t in _window_txns(case) if t["direction"] == "in"]
    out = []
    for d in case.docs:
        if d.type not in REGISTER_TYPES:
            continue
        status = (d.fields.get("status") or "").lower()
        gone = _iso(status) if "dissolved" in status else None
        co = _company(d)
        if not gone or not co:
            continue
        paid = [t for t in ins if norm_name(t.get("counterparty_name")) == co and t["booked_at"][:10] > gone]
        if paid:
            facts = {"doc_id": d.doc_id, "doc_title": d.title, "payer": paid[0]["counterparty_name"], "dissolved": gone,
                     "transaction_id": paid[0]["transaction_id"], "payment_date": paid[0]["booked_at"][:10],
                     "variant": "dissolved_payer"}
            out.append(Flag("ESC_UNEXPLAINED_FUNDS", [d.doc_id] + [t["transaction_id"] for t in paid], facts,
                            "dissolved_payer", 2))
    return out
