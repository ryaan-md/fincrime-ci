"""T-PRS-CONTRACT: contract-document parser. parse(doc) -> extra Doc.fields (merged by loader.py).

Regex only, no LLM. Values are copied as written (names keep "Ltd"/"Limited", case, etc.); checkers normalise.
A key is emitted only when the document states it unambiguously; a missing key means "not shown".
Amounts are floats rounded to the penny, dates are ISO strings, counts are ints.

Keys, every type:
  counterparty_name       the party named in the title ("... with X" / "... from X")

consulting_contract:
  client_name, supplier_name, scope_description, fee_amount

supply_contract (prose):
  supplier_name           the title counterparty, confirmed in the body
  supplier_location       place after the supplier ("based in X" / "of X")
  buyer_name              the one other company named in the body
  goods                   [str] goods listed ("apricots, figs, and dates" -> ["apricots", "figs", "dates"])
  term_months             int
  delivery_frequency      "quarterly" | "monthly" | "weekly" | "annual"
  pricing_currency        ISO code ("USD", "EUR", "GBP")
  payment_on_shipment     True when payment falls due upon shipment / dispatch

contract_schedule:
  project_name, supplier_name, supplier_country
  stages                  [{"stage": int, "work": str, "due": ISO, "amount": float}] in table order
  stage_count             int
  schedule_total          float, sum of stage amounts (only when every stage amount parsed)

loan_agreement:
  borrower_name, lender_name
  principal_amount        float ("Principal Amount", "Total Amount of Credit", "Credit Amount", prose "total amount of credit ... is £X")
  term_months             int
  loan_purpose            str ("Purchase of a used car")

factoring_agreement (prose):
  financier_name          the title counterparty, confirmed in the body
  client_name             the one other company named in the body
  advance_rate_pct        float (85% -> 85.0)
  financier_collects      True when the financier is responsible for direct collection of the invoices
  advance_to_client_account  True when advances are paid into the client's account at this firm

booking_confirmation:
  booking_reference, lead_traveller, trip_description, destination, departure_date
  traveller_count         int
  price_per_person, total_amount
  payment_due_date, payee_name, payee_sort_code, payee_account_number, payment_reference
  traveller_names         [str] the numbered traveller list, in order
"""
import re

TYPES = ("consulting_contract", "supply_contract", "contract_schedule", "loan_agreement", "factoring_agreement",
         "booking_confirmation")

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_MON = {m: i for i, m in enumerate(MONTHS, 1)}
_MON.update({m[:3]: i for i, m in enumerate(MONTHS, 1)})
_MON["sept"] = 9
DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(sorted(_MON, key=len, reverse=True))
                     + r")\.?,?\s+(\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MONEY_RE = re.compile(r"£\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?![\d,]*\d)")
LINE_RE = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?\*\*([^*\n]+?):?\*\*:?[ \t]*(.*?)[ \t]*$", re.M)
TITLE_PARTY_RE = re.compile(r"\b(?:with|from)\s+(.+?)\s*$", re.I)
SUFFIX = r"(?:Ltd|Limited|Co|Company|PLC|plc|LLP|LLC|Inc|Srl|SRL|SARL|SpA|GmbH|AG|BV|SA|SAS)"
COMPANY_RE = re.compile(r"(?<![\w'])((?:[A-Z][\w'&.-]*\s+){1,6}?" + SUFFIX + r")\.?(?![\w-])")
CURRENCIES = (("us dollar", "USD"), ("euro", "EUR"), ("sterling", "GBP"), ("pound", "GBP"))


def _ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _norm_name(s):
    s = _ws(s).lower().replace("limited", "ltd").replace("company", "co")
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def money(s):
    """First £ amount in s as a float (to the penny), else None. '£1,250' == '£1,250.00' == '£ 1250'."""
    m = MONEY_RE.search(s or "")
    if not m:
        return None
    pence = (m.group(2) or "0").ljust(2, "0")
    return round(int(m.group(1).replace(",", "")) + int(pence) / 100, 2)


def iso_date(s):
    m = DATE_RE.search(s or "")
    if m:
        d, mo, y = int(m.group(1)), _MON[m.group(2).lower()], int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= d <= 31 else None
    m = ISO_RE.search(s or "")
    return "-".join(m.groups()) if m else None


def _months(s):
    m = re.search(r"\b(\d{1,3})[\s-]*months?\b", s or "", re.I)
    return int(m.group(1)) if m else None


def labelled(text):
    """{label (lower, single-spaced): value} for every **Label:** line; never reads across a line break."""
    out = {}
    for k, v in LINE_RE.findall(text):
        k = _ws(k).lower().rstrip(":")
        out.setdefault(k, _ws(v))
    return out


def _first(lab, *keys):
    for k in keys:
        if lab.get(k):
            return lab[k]
    return None


def _body(doc):
    """Text after the header block (title, Document ID, Dated)."""
    lines = [l for l in doc.text.splitlines()
             if not re.match(r"^\s*(#|\*\*(document id|dated):?\*\*)", l, re.I)]
    return "\n".join(lines)


def _companies(text):
    seen, out = set(), []
    for m in COMPANY_RE.finditer(text):
        name = _ws(m.group(1))
        if _norm_name(name) not in seen:
            seen.add(_norm_name(name))
            out.append(name)
    return out


def _title_party(doc):
    m = TITLE_PARTY_RE.search(_ws(doc.title))
    return m.group(1).rstrip(".") if m else None


def _named_pair(doc, body):
    """(title party as written in the body, the single other company) for prose two-party agreements."""
    party = _title_party(doc)
    names = _companies(body)
    mine = [n for n in names if party and _norm_name(n) == _norm_name(party)]
    other = [n for n in names if not party or _norm_name(n) != _norm_name(party)]
    return (mine[0] if mine else None), (other[0] if len(other) == 1 else None)


def _consulting(doc, lab, body):
    return {"client_name": _first(lab, "client"), "supplier_name": _first(lab, "supplier", "consultant"),
            "scope_description": _first(lab, "scope", "scope of work", "services"),
            "fee_amount": money(_first(lab, "fee", "fees", "contract value") or "")}


def _supply(doc, lab, body):
    flat = _ws(body)
    supplier, buyer = _named_pair(doc, flat)
    out = {"supplier_name": supplier, "buyer_name": buyer, "term_months": _months(flat)}
    if supplier:
        m = re.search(re.escape(supplier) + r",?\s+(?:based in|of)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)", flat)
        out["supplier_location"] = m.group(1) if m else None
    m = (re.search(r"\bspecifically\s+((?:[a-z]+,\s+)+(?:and\s+)?[a-z]+)\b", flat)
         or re.search(r"\bdried\s+((?:[a-z]+,\s+)+(?:and\s+)?[a-z]+)\b", flat))
    if m:
        out["goods"] = [g for g in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1)) if g]
    m = re.search(r"\b(quarterly|monthly|weekly|annual)\b", flat, re.I)
    out["delivery_frequency"] = m.group(1).lower() if m else None
    m = re.search(r"\bpric\w*[^.]*?\bin\s+([a-z ]+)", flat, re.I)
    if m:
        cur = m.group(1).lower()
        out["pricing_currency"] = next((c for w, c in CURRENCIES if w in cur), None)
    if re.search(r"\b(?:upon|on|at the time of)\s+(?:the\s+|each\s+)?(?:shipment|dispatch)\b", flat, re.I):
        out["payment_on_shipment"] = True
    return out


def _schedule(doc, lab, body):
    out = {"project_name": _first(lab, "project")}
    sup = _first(lab, "supplier", "contractor")
    if sup:
        m = re.match(r"^(.*" + SUFFIX + r")\.?,\s*([A-Z][\w ]+)$", sup)
        out["supplier_name"], out["supplier_country"] = (m.group(1), m.group(2)) if m else (sup, None)
    header, stages, ok = None, [], True
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [_ws(c) for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            continue
        row = dict(zip(header, cells))
        num = re.search(r"\d+", row.get("stage", ""))
        amt = money(row.get("amount", ""))
        ok = ok and amt is not None
        stages.append({"stage": int(num.group()) if num else None, "work": row.get("work") or None,
                       "due": iso_date(row.get("due", "")), "amount": amt})
    if stages:
        out["stages"], out["stage_count"] = stages, len(stages)
        if ok:
            out["schedule_total"] = round(sum(s["amount"] for s in stages), 2)
    return out


def _loan(doc, lab, body):
    flat = _ws(body)
    amount = money(_first(lab, "principal amount", "principal loan amount", "total amount of credit",
                          "amount of credit", "total credit amount", "credit amount", "loan amount") or "")
    if amount is None:
        m = re.search(r"\b(?:total amount of credit|amount of credit|principal|loan amount)\b[^.£]*(£\s?[\d,.]+)",
                      flat, re.I)
        amount = money(m.group(1)) if m else None
    term = _months(_first(lab, "loan term", "repayment term", "repayment period", "term") or "")
    if term is None:
        m = re.search(r"\b(?:term|period|duration)\s+of\s+(\d{1,3})\s+months?\b", flat, re.I)
        term = int(m.group(1)) if m else None
    purpose = _first(lab, "loan purpose", "purpose of loan", "stated purpose", "stated purpose of loan",
                     "intended purpose", "purpose")
    if not purpose:
        m = re.search(r"\b(?:designated|used|utili[sz]ed|intended)\b[^.]*?\bfor\s+(?:the\s+)?(.+?)\s*\.", flat, re.I)
        purpose = m.group(1) if m else None
    return {"borrower_name": _first(lab, "borrower"), "lender_name": _first(lab, "lender"),
            "principal_amount": amount, "term_months": term, "loan_purpose": purpose}


def _factoring(doc, lab, body):
    flat = _ws(body)
    financier, client = _named_pair(doc, flat)
    m = re.search(r"\b(\d{1,3}(?:\.\d+)?)\s*%", flat)
    out = {"financier_name": financier, "client_name": client, "advance_rate_pct": float(m.group(1)) if m else None}
    if financier and re.search(re.escape(financier) + r"[^.]*\bresponsib\w*\s+for\s+the\s+direct\s+collection", flat):
        out["financier_collects"] = True
    if re.search(r"\bclient\W{1,2}s\s+(?:designated\s+)?account\b", flat) or (
            client and re.search(r"\baccount\s+held\s+by\s+" + re.escape(client), flat)):
        out["advance_to_client_account"] = True
    return out


def _booking(doc, lab, body):
    trip, price, pay = _first(lab, "trip") or "", _first(lab, "price") or "", _first(lab, "payment") or ""
    out = {"booking_reference": _first(lab, "booking reference", "booking ref"),
           "lead_traveller": _first(lab, "lead traveller", "lead traveler"), "trip_description": trip or None}
    m = re.search(r"\btrip to\s+(.+?)(?:,|\s+departing\b|$)", trip, re.I)
    out["destination"] = m.group(1) if m else None
    m = re.search(r"\bdeparting\b(.*)", trip, re.I)
    out["departure_date"] = iso_date(m.group(1)) if m else None
    m = re.fullmatch(r"(\d+)(?:\s+\w+)?", _first(lab, "travellers", "travelers", "number of travellers") or "")
    out["traveller_count"] = int(m.group(1)) if m else None
    m = re.search(r"(£\s?[\d,.]+)\s*(?:per person|each|pp)\b", price, re.I)
    out["price_per_person"] = money(m.group(1)) if m else None
    m = re.search(r"(£\s?[\d,.]+)\s*(?:in total|total)\b", price, re.I)
    out["total_amount"] = money(m.group(1)) if m else None
    m = re.search(r"\bdue by\b(.*)", pay, re.I)
    out["payment_due_date"] = iso_date(m.group(1)) if m else None
    m = re.search(r"\bto\s+(.+?),\s*account\b", pay, re.I)
    out["payee_name"] = m.group(1) if m else None
    m = re.search(r"\baccount\s+(\d{2}-\d{2}-\d{2})\s+(\d{6,10})\b", pay, re.I)
    if m:
        out["payee_sort_code"], out["payee_account_number"] = m.group(1), m.group(2)
    m = re.search(r"\breference\s+([A-Za-z0-9/-]+)\s*$", pay, re.I)
    out["payment_reference"] = m.group(1) if m else None
    names = re.findall(r"^[ \t]*\d{1,3}[.)][ \t]+(.+?)[ \t]*$", body, re.M)
    if names:
        out["traveller_names"] = [_ws(n) for n in names]
    return out


_BY_TYPE = {"consulting_contract": _consulting, "supply_contract": _supply, "contract_schedule": _schedule,
            "loan_agreement": _loan, "factoring_agreement": _factoring, "booking_confirmation": _booking}


def parse(doc):
    fn = _BY_TYPE.get(doc.type)
    if fn is None:
        return {}
    out = {"counterparty_name": _title_party(doc)}
    out.update(fn(doc, labelled(doc.text), _body(doc)))
    return {k: v for k, v in out.items() if v is not None and v != "" and v != []}
