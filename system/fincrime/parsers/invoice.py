"""T-PRS-INVOICE: structured fields for invoice-family documents.

Types: invoice, supplier_invoice, dealer_invoice, carrier_invoice, freight_invoices, purchase_order,
credit_note, remittance_advice.

parse(doc) returns these keys (only the ones found; snake_case, so they never clash with the raw
lower-cased `**Field:**` keys the loader already puts in Doc.fields):

  doc_issuer          str    party named in the title ("... from X" / "... issued by X")
  invoice_ref         str    the invoice's own reference from the title, e.g. "QX/1234", "INV 40001"
  po_ref              str    purchase-order reference "PO <digits>", from title, goods line or PO body
  booking_ref         str    "BOOKING <digits>" on a credit note
  pro_forma           bool   True for a pro forma invoice (title says so)
  seller_name         str    who is paid: Supplier / Carrier line, PO addressee (only when it is an
                             organisation name, not "Sales Team"), else the issuer (not for PO / remittance)
  seller_location     str    place after the seller's name, as written ("X Ltd, Narnia" -> "Narnia";
                             "X Co, Erbil, Narnia" -> "Erbil, Narnia")
  seller_city         str    first part of a two-part place with no digits ("X Co, Erbil, Narnia" -> "Erbil")
  seller_country      str    last part of that two-part place ("X Co, Erbil, Narnia" -> "Narnia")
  seller_note         str    trailing lower-case clause on the seller line, as written
                             ("X Co, Narnia, the client's supplier before the pause")
  buyer_name          str    who pays: Bill to / Buyer / Customer / PO issuing company
  client_name         str    remittance advice: the finance company's client (who receives the advance;
                             the finance company itself is doc_issuer)
  item_description    str    Description / Goods / Service / Vehicle / Credit for / PO "order for ..."
  vehicle_registration str   registration mark on a dealer invoice, upper-cased, single-spaced
  total_amount        float  the document's headline amount (amount due, total, price, refund,
                             order value, financed total; freight table: sum of the lines)
  paid_amount         float  amount stated as paid ("Paid by bank transfer")
  advance_amount      float  invoice-finance advance paid (remittance advice)
  advance_rate        float  advance as a fraction ("Advance at 85%" -> 0.85)
  invoice_count       int    number of invoices financed (remittance advice)
  pay_to_name         str    payee name on the "Pay to" line
  pay_to_account      str    payee account on the "Pay to" line ("00-11-22 33445566", "IBAN ending 0042")
  payment_terms       str    Terms line verbatim
  instalments         int    number of instalments named in the terms ("four instalments" -> 4)
  line_items          list   table rows: [{"ref": str, "party": str, "amount": float}] in file order

Amounts tolerate "£1,250", "£1250.00", "£ 1,250.00" and "GBP 1,250.00". Names are kept as written
(stripped); checkers compare them with checks.util.norm_name.
"""
import re

TYPES = ("invoice", "supplier_invoice", "dealer_invoice", "carrier_invoice", "freight_invoices",
         "purchase_order", "credit_note", "remittance_advice")

AMOUNT_RE = re.compile(r"(?:£|\bGBP)\s?(\d[\d,]*(?:\.\d{1,2})?)", re.I)
TITLE_RE = re.compile(r"^#\s*(.+?)\s+(?:from|issued by)\s+(.+?)\s*$", re.I | re.M)
PO_RE = re.compile(r"\bPO[\s#:-]*(\d+)\b")
BOOKING_RE = re.compile(r"\bBOOKING[\s#:-]*(\d+)\b", re.I)
REG_RE = re.compile(r"\bregistration\s+([A-Z]{2}\d{2}\s?[A-Z]{3})\b", re.I)
DEAR_RE = re.compile(r"^\s*Dear\s+(.+?)(?:\s+team)?\s*,\s*$", re.I | re.M)
ORG_RE = re.compile(r"\b(?:ltd|limited|plc|llp|llc|inc|co|gmbh|bv|sa|sal|srl|fze|sp\. z o\.o\.|pty)\.?\s*$", re.I)
PO_DESC_RES = (re.compile(r"\bfor\s+the\s+supply\s+of\s+([^.,£\n]+)", re.I),
               re.compile(r"\border\s+for\s+(?:the\s+|our\s+)?([^.,£\n]+)", re.I))
PO_DESC_TAIL = re.compile(r"\s+(?:(?:(?:we|as|recently)\s+)*discussed|at\s+a\b|to\s+be\b|under\b).*$", re.I)
COUNT_RE = re.compile(r"^\s*(\d+)\b")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
NUM_WORDS = {w: i for i, w in enumerate(["one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                         "ten", "eleven", "twelve"], 1)}
INSTALMENTS_RE = re.compile(r"\b(\d+|" + "|".join(NUM_WORDS) + r")\s+instal?ments?\b", re.I)

BUYER_KEYS = ("bill to", "buyer", "customer", "company")
SELLER_KEYS = ("supplier", "carrier", "seller", "vendor")
DESC_KEYS = ("description", "goods", "service", "vehicle", "credit for", "item", "items")
AMOUNT_KEYS = ("amount due", "total", "amount", "price", "refund", "total order value", "order value",
               "invoice total", "total due")


def amount(s):
    m = AMOUNT_RE.search(s or "")
    if not m:
        return None
    try:
        return round(float(m.group(1).replace(",", "")), 2)
    except ValueError:
        return None


def _first(fields, keys):
    for k in keys:
        v = fields.get(k)
        if v:
            return v
    return None


def _split_place(s):
    """Seller line -> {seller_name, seller_location, seller_city, seller_country, seller_note} (stated parts only).

    The name ends at the first comma after a company suffix (else the first comma). Trailing parts that start
    lower-case are a note ("the client's supplier before the pause"); the rest is the place, and a two-part place
    with no digits (no street or postcode) is "City, Country". 'Testmoor Trading Co, Erbil, Narnia' -> name
    'Testmoor Trading Co', location 'Erbil, Narnia', city 'Erbil', country 'Narnia'."""
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 2 or not all(parts) or any(AMOUNT_RE.search(p) for p in parts[1:]):
        return {"seller_name": s.strip()}
    cut = next((i + 1 for i, p in enumerate(parts[:-1]) if ORG_RE.search(p)), 1)
    rest = parts[cut:]
    notes = 0
    while notes < len(rest) and rest[len(rest) - 1 - notes][:1].islower():
        notes += 1
    place, note = rest[:len(rest) - notes], rest[len(rest) - notes:]
    out = {"seller_name": ", ".join(parts[:cut])}
    if place:
        out["seller_location"] = ", ".join(place)
    if len(place) == 2 and not any(ch.isdigit() for p in place for ch in p):
        out["seller_city"], out["seller_country"] = place
    if note:
        out["seller_note"] = ", ".join(note)
    return out


def _title(doc):
    m = TITLE_RE.search(doc.text) or TITLE_RE.search("# " + (doc.title or ""))
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def _table(text):
    """Markdown table rows as [{header_lower: cell}] in file order."""
    rows, header = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def _line_items(text):
    out = []
    for row in _table(text):
        amt_col = next((h for h in row if "amount" in h or "total" in h or "value" in h), None)
        ref_col = next((h for h in row if h != amt_col and ("invoice" in h or "ref" in h or h == "no")), None)
        party_col = next((h for h in row if h not in (amt_col, ref_col)), None)
        a = amount(row.get(amt_col, "")) if amt_col else None
        if a is None:
            continue
        first = next(iter(row.values()), "")
        if first.lower().startswith("total"):
            continue
        out.append({"ref": row.get(ref_col, "") if ref_col else "",
                    "party": row.get(party_col, "") if party_col else "", "amount": a})
    return out


def parse(doc):
    f, text, out = doc.fields, doc.text, {}
    kind, issuer = _title(doc)
    if issuer:
        out["doc_issuer"] = issuer
    if kind:
        k = kind.lower()
        if "pro forma" in k:
            out["pro_forma"] = True
        m = re.match(r"invoice\s+(?!for\b)(\S.*)$", kind, re.I)
        if m:
            out["invoice_ref"] = m.group(1).strip()

    po = PO_RE.search(f.get("order reference", "")) or PO_RE.search(kind or "") or PO_RE.search(text)
    if po:
        out["po_ref"] = "PO " + po.group(1)
    bk = BOOKING_RE.search(f.get("credit for", "") or text)
    if bk:
        out["booking_ref"] = "BOOKING " + bk.group(1)

    seller = _first(f, SELLER_KEYS)
    if doc.type == "purchase_order":
        dear = DEAR_RE.search(text)
        if not seller and dear and ORG_RE.search(dear.group(1)):
            seller = dear.group(1)
    if seller:
        out.update(_split_place(seller))
    elif issuer and doc.type not in ("purchase_order", "remittance_advice"):
        out["seller_name"] = issuer

    buyer = _first(f, BUYER_KEYS) or (issuer if doc.type == "purchase_order" else None)
    if buyer:
        out["buyer_name"] = buyer.strip()

    desc = _first(f, DESC_KEYS)
    if not desc and doc.type == "purchase_order":
        m = next((m for m in (r.search(text) for r in PO_DESC_RES) if m), None)
        desc = PO_DESC_TAIL.sub("", m.group(1)) if m else None
    if desc:
        out["item_description"] = desc.strip()
    reg = REG_RE.search(f.get("vehicle", ""))
    if reg:
        out["vehicle_registration"] = re.sub(r"\s+", " ", reg.group(1).upper())

    total = amount(_first(f, AMOUNT_KEYS))
    if total is None and doc.type == "remittance_advice":
        total = amount(f.get("invoices financed", ""))
    items = _line_items(text)
    if items:
        out["line_items"] = items
        if total is None:
            total = round(sum(i["amount"] for i in items), 2)
    if total is None and doc.type == "purchase_order":
        amts = [a for a in (amount(m.group(0)) for m in AMOUNT_RE.finditer(text)) if a is not None]
        total = amts[0] if len(set(amts)) == 1 else None
    if total is not None:
        out["total_amount"] = total
    for key, v in sorted(f.items()):
        if key.startswith("paid by") and amount(v) is not None:
            out["paid_amount"] = amount(v)
        if key.startswith("advance") and amount(v) is not None:
            out["advance_amount"] = amount(v)
            pct = PCT_RE.search(key) or PCT_RE.search(AMOUNT_RE.sub("", v))
            if pct:
                out["advance_rate"] = round(float(pct.group(1)) / 100, 4)
    if doc.type == "remittance_advice" and f.get("client"):
        out["client_name"] = f["client"].strip()
    cnt = COUNT_RE.match(f.get("invoices financed", ""))
    if cnt:
        out["invoice_count"] = int(cnt.group(1))

    pay = f.get("pay to")
    if pay:
        name, sep, acct = pay.partition(",")
        if sep and re.search(r"\d", acct):
            out["pay_to_name"], out["pay_to_account"] = name.strip(), re.sub(r"^\s*account\s+", "", acct.strip(), flags=re.I)
        elif re.search(r"\d", pay):
            out["pay_to_account"] = pay.strip()
        else:
            out["pay_to_name"] = pay.strip()
    terms = f.get("terms")
    if terms:
        out["payment_terms"] = terms
        m = INSTALMENTS_RE.search(terms)
        if m:
            w = m.group(1).lower()
            out["instalments"] = int(w) if w.isdigit() else NUM_WORDS[w]
    return out
