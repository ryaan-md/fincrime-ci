"""T-PRS-BUSINESS: business-records parser (per-day / per-outlet cash totals to reconcile against deposits).

parse(doc) -> dict merged into Doc.fields. Keys are only present when extracted. Amounts are float GBP,
dates are ISO strings, row lists keep document order. Header names are matched loosely (case, spacing,
column order), so cosmetic variants parse the same; a cell that is not a clean amount/date is left None.

cash_collection_reports
  business_name          str   operator in the title ("... from <name>")
  collections            list  {site, date, notes, coins, total, safe_bag} per emptied machine bag
  collections_total_gbp  float sum of row totals
cash_reconciliation
  event_name             str   event in the title ("... for <event>")
  reconciliation_points  list  {point, sales, counted, banked}
  reconciliation_total_gbp float sum of counted cash
takings_book
  takings_days           list  {date, market, takings, topped_up}
  takings_total_gbp      float sum of takings
  topped_up_total_gbp    float sum of cash topped up
ticket_report
  event_name             str   event the report covers
  event_dates            list  ISO dates the event ran (sorted, unique)
  event_start, event_end str   first / last event date
management_accounts
  period_end             str   ISO year-end from the title ("... for the year to <date>")
  turnover_gbp, cash_sales_gbp, card_sales_gbp, cost_of_sales_gbp, net_profit_gbp   float
cash_handling_procedure
  business_name          str   operator in the title ("Cash handling procedure, <name>")
  procedure_sites        list  {site, collection_day, bank_branch, banked_next_morning}
  sites_combined         bool  False when the text says the sites' cash is never combined

Collections, reconciliation and takings also get
  daily_totals           list  {date, location, amount, cap, ref}: one row per cash total that should
                               match a deposit. amount = collection total / counted cash / cash topped up;
                               date = emptied / banked / takings date; cap = that day's takings (takings_book
                               only, the top-up must not exceed it), ref = safe bag (collections only).
"""
import re

TYPES = ("cash_collection_reports", "cash_reconciliation", "takings_book", "ticket_report",
         "management_accounts", "cash_handling_procedure")

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
_MON = {m[:3]: i for i, m in enumerate(MONTHS, 1)}
DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
AMOUNT_RE = re.compile(r"^(?:£|gbp)?\s*(-?\d{1,3}(?:,\d{3})+|-?\d+)(?:\.(\d{1,2}))?\s*(?:gbp)?$", re.I)
ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$", re.M)
SEP_RE = re.compile(r"^[\s:|-]+$")
DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
SITE_RE = re.compile(r"^\s*(?P<site>[^:\n]+?)\s*:\s*machines are emptied every (?P<day>" + DAYS + r")\b"
                     r"(?P<rest>[^\n]*)$", re.I | re.M)
BRANCH_RE = re.compile(r"\bpay it in (?P<when>the next morning )?at (?P<branch>.+?) branch\b", re.I)


def _key(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def amount(s):
    """'£1,250' / '£1,250.00' / 'GBP 1250.5' -> float; anything else -> None."""
    m = AMOUNT_RE.match((s or "").strip())
    if not m:
        return None
    return round(float(m.group(1).replace(",", "") + "." + (m.group(2) or "0")), 2)


def date(s):
    """First '9 August 2026' / '9 Aug 2026' / '2026-08-09' in s -> ISO; else None."""
    s = s or ""
    hits = []
    for m in DATE_RE.finditer(s):
        mo = _MON.get(m.group(2).lower()[:3])
        if mo and (len(m.group(2)) == 3 or m.group(2).lower() in MONTHS) and 1 <= int(m.group(1)) <= 31:
            hits.append((m.start(), f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(1)):02d}"))
    hits += [(m.start(), m.group(0)) for m in ISO_RE.finditer(s)]
    return min(hits)[1] if hits else None


def all_dates(s):
    out = []
    for m in list(DATE_RE.finditer(s or "")) + list(ISO_RE.finditer(s or "")):
        d = date(m.group(0))
        if d:
            out.append(d)
    return sorted(set(out))


def _tables(text):
    blocks, cur = [], []
    for line in (text or "").splitlines():
        m = ROW_RE.match(line)
        if m:
            cur.append([c.strip() for c in m.group(1).split("|")])
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def table(text, columns):
    """Rows of the first markdown table whose header holds every wanted column.
    columns: {out_key: (header alias, ...)} matched on _key(). Returns [{out_key: raw cell}]."""
    for block in _tables(text):
        hk = [_key(h) for h in block[0]]
        idx = {}
        for out, aliases in columns.items():
            hit = [j for j, h in enumerate(hk) if h in aliases]
            if hit:
                idx[out] = hit[0]
        if len(idx) != len(columns):
            continue
        body = []
        for r in block[1:]:
            if all(SEP_RE.match(c or "-") for c in r):
                continue
            if [_key(c) for c in r] == hk:
                continue
            body.append({out: (r[j] if j < len(r) else "") for out, j in idx.items()})
        return body
    return []


def _title(doc):
    if doc.title:
        return doc.title.strip()
    m = re.search(r"^#\s+(.+?)\s*$", doc.text or "", re.M)
    return m.group(1) if m else ""


def _after(title, pattern):
    m = re.search(pattern, title, re.I)
    return m.group(1).strip() if m else None


def _sum(rows, k):
    vals = [r[k] for r in rows]
    return round(sum(vals), 2) if vals and None not in vals else None


def _put(out, k, v):
    if v is not None and v != []:
        out[k] = v


def _collections(doc, out):
    _put(out, "business_name", _after(_title(doc), r"\bfrom\s+(.+)$"))
    rows = [{"site": r["site"], "date": date(r["date"]), "notes": amount(r["notes"]), "coins": amount(r["coins"]),
             "total": amount(r["total"]), "safe_bag": r["safe_bag"] or None}
            for r in table(doc.text, {"site": ("site",), "date": ("emptied",),
                                      "notes": ("notes",), "coins": ("coins",), "total": ("total",),
                                      "safe_bag": ("safe bag",)})]
    _put(out, "collections", rows)
    _put(out, "collections_total_gbp", _sum(rows, "total"))
    _put(out, "daily_totals", [{"date": r["date"], "location": r["site"], "amount": r["total"], "cap": None,
                                "ref": r["safe_bag"]} for r in rows])


def _reconciliation(doc, out):
    _put(out, "event_name", _after(_title(doc), r"\bfor\s+(.+)$"))
    rows = [{"point": r["point"], "sales": r["sales"] or None, "counted": amount(r["counted"]),
             "banked": date(r["banked"])}
            for r in table(doc.text, {"point": ("point",),
                                      "sales": ("tickets and sales",),
                                      "counted": ("cash counted",),
                                      "banked": ("banked",)})]
    _put(out, "reconciliation_points", rows)
    _put(out, "reconciliation_total_gbp", _sum(rows, "counted"))
    _put(out, "daily_totals", [{"date": r["banked"], "location": r["point"], "amount": r["counted"], "cap": None,
                                "ref": None} for r in rows])


def _takings(doc, out):
    rows = [{"date": date(r["date"]), "market": r["market"], "takings": amount(r["takings"]),
             "topped_up": amount(r["topped_up"])}
            for r in table(doc.text, {"date": ("date",), "market": ("market",),
                                      "takings": ("takings",),
                                      "topped_up": ("cash topped up",)})]
    _put(out, "takings_days", rows)
    _put(out, "takings_total_gbp", _sum(rows, "takings"))
    _put(out, "topped_up_total_gbp", _sum(rows, "topped_up"))
    _put(out, "daily_totals", [{"date": r["date"], "location": r["market"], "amount": r["topped_up"],
                                "cap": r["takings"], "ref": None} for r in rows])


def _tickets(doc, out):
    _put(out, "event_name", _after(_title(doc), r"\breport\s+for\s+(.+)$"))
    m = re.search(r"\bran on\s+(.+?)(?:\.\s|\.$|$)", doc.text or "", re.I | re.M)
    days = all_dates(m.group(1)) if m else []
    _put(out, "event_dates", days)
    if days:
        out["event_start"], out["event_end"] = days[0], days[-1]


MA_FIELDS = {"turnover_gbp": ("turnover",), "cash_sales_gbp": ("cash sales",),
             "card_sales_gbp": ("card sales",), "cost_of_sales_gbp": ("cost of sales",),
             "net_profit_gbp": ("net profit",)}


def _accounts(doc, out):
    _put(out, "period_end", date(_after(_title(doc), r"\byear\s+(?:to|ended|ending)\s+(.+)$")))
    f = {_key(k): v for k, v in sorted((doc.fields or {}).items())}
    for k, aliases in MA_FIELDS.items():
        for a in aliases:
            if a in f:
                _put(out, k, amount(f[a]))
                break


def _procedure(doc, out):
    _put(out, "business_name", _after(_title(doc), r"\bprocedure\s*,\s*(.+)$"))
    sites = []
    for m in SITE_RE.finditer(doc.text or ""):
        b = BRANCH_RE.search(m.group("rest"))
        sites.append({"site": m.group("site").strip(), "collection_day": m.group("day").capitalize(),
                      "bank_branch": b.group("branch").strip() if b else None,
                      "banked_next_morning": bool(b and b.group("when"))})
    _put(out, "procedure_sites", sites)
    if re.search(r"\bnever\s+combined\b", doc.text or "", re.I):
        out["sites_combined"] = False


HANDLERS = {"cash_collection_reports": _collections, "cash_reconciliation": _reconciliation,
            "takings_book": _takings, "ticket_report": _tickets, "management_accounts": _accounts,
            "cash_handling_procedure": _procedure}


def parse(doc):
    out = {}
    h = HANDLERS.get(doc.type)
    if h:
        h(doc, out)
    return out
