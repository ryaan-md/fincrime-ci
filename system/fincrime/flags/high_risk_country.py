"""TM06: ESC_HIGH_RISK_CORRIDOR when payments to an undeclared higher-risk country have no credible explanation.

POLICY.md (all four institutions) code table: ESC_HIGH_RISK_CORRIDOR = "Unexplained payments to or from a higher-risk
country". TM06 itself only says the country is higher risk and undeclared; that alone is not suspicion (a hospital bill,
a refund or a documented supply contract closes it). So this fires only when the file shows the explanation fails,
with two independent signals each time:

1. Fan-out under a vague claim (individual customers). The money the TM06 alert fired on goes OUT to the country:
   a. to two or more different people, none sharing the customer's surname and none named in any document on file
      (so a "family" claim is not borne out), and
   b. it was funded by third parties: two or more payers in the review window who are not the customer, the employer
      or anyone sharing the customer's surname, paying in at least half of what went out to the country.
2. Services, not goods (business customers). Payments out to the country go to a supplier whose invoices on file
   bill only services (consultancy, market access, advisory, ...) although the customer declares a goods-trading
   business, AND a staff note records that the goods come from a different country or that the customer could not
   explain why the money goes to this one.

Context only, never decisive (POLICY.md): round amounts, crypto or gambling counterparties, shared devices. Not used.
Not covered here: higher-risk money coming IN from third parties for whom the customer invests (that is third-party
funding, T-FLG-MULE)."""
import re
from ..model import Flag
from ..checks.util import norm_name

SEVERITY = 3
SERVICE_RE = re.compile(r"\b(consult\w*|advis\w*|advice|market access|services?|management fees?|commission|"
                        r"brokerage|introduc\w*|facilitation|liaison)\b", re.I)
GOODS_BUSINESS_RE = re.compile(r"\b(imports?|importer|buys|stock|exports?|wholesal\w*|retail\w*|manufactur\w*|"
                               r"suppl(?:y|ies|ier)|sells|trader|trading in)\b", re.I)
DESC_COLS = ("goods", "description", "item", "items", "services", "details", "for")
ORIGIN_RE = re.compile(r"\b(?:originat\w*|sourced|imported|shipped|arriving|coming|bought|purchased|made|manufactured)"
                       r"\s+(?:in|from)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)")
EVASIVE_RE = re.compile(r"\b(unable|unwilling|evasive|declined|refused|could not|couldn't|cannot|did not|failed to|"
                        r"no (?:clear |credible )?(?:explanation|reason|rationale|justification))\b", re.I)
WHY_RE = re.compile(r"\b(explain\w*|explanation\w*|justif\w*|rationale|reason\w*|clarif\w*|why)\b", re.I)


def _countries(alert):
    raw = (alert.get("measured") or {}).get("countries") or ""
    return [c.strip() for c in re.split(r",|;| and ", raw) if c.strip()]


def _surname(name):
    toks = norm_name(name).split()
    return toks[-1] if toks else ""


def _in_window(t, w):
    return w["from"] <= t["booked_at"][:10] <= w["to"]


def _outs(case, alert, countries):
    """Outgoing payments the alert fired on, to the alert's country where the row says so; stable order."""
    want = {c.lower() for c in countries}
    txs = case.alert_txns.get(alert["alert_id"], []) or ([case.held_payment] if case.held_payment else [])
    out = [t for t in txs if t and t.get("direction") == "out" and
           (not t.get("counterparty_country") or t["counterparty_country"].lower() in want)]
    return sorted(out, key=lambda t: (t["booked_at"], t["transaction_id"]))


def _customer_claim(chats):
    """First thing the customer says in the chat, for the reason template."""
    for d in chats:
        for line in d.text.splitlines():
            if line.strip().lower().startswith("**customer:**"):
                return line.strip()[len("**customer:**"):].strip()
    return ""


# ---------- 1. fan-out to unrelated people, funded by third parties ----------

def _fan_out(case, alert, countries):
    if case.customer.get("kind") != "individual":
        return []
    cust = norm_name(case.customer.get("name"))
    sur = _surname(case.customer.get("name"))
    emp = norm_name(case.customer.get("employer"))
    outs = _outs(case, alert, countries)
    payees = sorted({norm_name(t.get("counterparty_name")) for t in outs} - {""})
    if len(payees) < 2 or any(p == cust or _surname(p) == sur for p in payees):
        return []
    all_text = norm_name(" ".join(d.text for d in case.docs))
    if any(p in all_text for p in payees):              # a document names a recipient: leave it to the checkers
        return []
    payers = [t for t in case.txns if t.get("direction") == "in" and _in_window(t, case.window)
              and norm_name(t.get("counterparty_name")) not in ("", cust, emp)
              and _surname(t.get("counterparty_name")) != sur]
    names = sorted({norm_name(t["counterparty_name"]) for t in payers})
    paid_in = round(sum(t["amount_gbp"] for t in payers), 2)
    paid_out = round(sum(t["amount_gbp"] for t in outs), 2)
    if len(names) < 2 or paid_in < 0.5 * paid_out:
        return []
    chats = [d for d in case.docs_of("support_chat") if any(c.lower() in d.text.lower() for c in countries)]
    ev = [d.doc_id for d in chats] + [t["transaction_id"] for t in outs] + [t["transaction_id"] for t in payers]
    facts = {"variant": "fan_out_third_party_funded", "country": ", ".join(countries),
             "recipients": [t.get("counterparty_name") for t in outs], "recipient_count": len(payees),
             "paid_out_gbp": paid_out, "third_party_payers": sorted({t["counterparty_name"] for t in payers}),
             "third_party_in_gbp": paid_in, "customer_claim": _customer_claim(chats),
             "doc_id": chats[0].doc_id if chats else None, "doc_title": chats[0].title if chats else None}
    return [Flag("ESC_HIGH_RISK_CORRIDOR", ev, facts, "high_risk_country", SEVERITY)]


# ---------- 2. invoices for services to a goods business, routing unexplained ----------

def _invoice_lines(doc):
    """Description of each billed line: table cells under a goods/description column, or a Goods/Services field."""
    lines, block = [], []
    for raw in doc.text.splitlines() + [""]:
        s = raw.strip()
        if s.startswith("|"):
            block.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if len(block) >= 2:
            head = [h.lower() for h in block[0]]
            col = next((i for i, h in enumerate(head) if h in DESC_COLS), None)
            if col is not None:
                lines += [r[col] for r in block[1:] if col < len(r) and not set(r[col]) <= set("-: ")]
        block = []
    for k in ("goods", "services", "description", "goods or services"):
        if doc.fields.get(k):
            lines.append(doc.fields[k])
    return [l for l in lines if l]


def _services_only(lines):
    return bool(lines) and all(SERVICE_RE.search(l) for l in lines)


def _note_unexplained(doc, countries):
    """(finding, origin): the note puts the goods' origin in another country, or says the routing went unexplained."""
    text = re.sub(r"\s+", " ", doc.text)
    want = {c.lower() for c in countries}
    for m in ORIGIN_RE.finditer(text):
        origin = m.group(1).strip()
        if origin.lower() not in want and origin.lower() not in ("the", "a", "an"):
            return "goods_from_other_country", origin
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if EVASIVE_RE.search(sent) and WHY_RE.search(sent):
            return "routing_unexplained", ""
    return None, ""


def _services_not_goods(case, alert, countries):
    activity = case.customer.get("business_activity") or ""
    if case.customer.get("kind") != "business" or not GOODS_BUSINESS_RE.search(activity):
        return []
    outs = _outs(case, alert, countries)
    end = case.window["to"]
    for payee in sorted({norm_name(t.get("counterparty_name")) for t in outs} - {""}):
        invs = [d for d in case.docs if "invoice" in d.type and d.dated <= end
                and payee in norm_name(d.title + " " + d.text)]
        billed = [l for d in invs for l in _invoice_lines(d)]
        if not invs or not _services_only(billed):
            continue
        for note in case.docs_of("staff_note"):
            if note.dated > end:
                continue
            finding, origin = _note_unexplained(note, countries)
            if not finding:
                continue
            paid = [t for t in outs if norm_name(t.get("counterparty_name")) == payee]
            ev = [invs[0].doc_id, note.doc_id] + [d.doc_id for d in invs[1:]] + [t["transaction_id"] for t in paid]
            facts = {"variant": "services_not_goods", "country": ", ".join(countries),
                     "payee": paid[0]["counterparty_name"], "payment_count": len(paid),
                     "paid_out_gbp": round(sum(t["amount_gbp"] for t in paid), 2),
                     "invoice_doc_id": invs[0].doc_id, "invoice_title": invs[0].title, "billed_for": billed[0],
                     "business_activity": activity, "note_doc_id": note.doc_id, "note_title": note.title,
                     "note_finding": finding, "goods_origin": origin,
                     "doc_id": invs[0].doc_id, "doc_title": invs[0].title}
            return [Flag("ESC_HIGH_RISK_CORRIDOR", ev, facts, "high_risk_country", SEVERITY)]
    return []


def scan_high_risk_country(case, policy):
    out = []
    for a in case.alerts:
        if a.get("rule_id") != "TM06":
            continue
        countries = _countries(a)
        if countries:
            out += _fan_out(case, a, countries) or _services_not_goods(case, a, countries)
    return out
