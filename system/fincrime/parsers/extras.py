"""T-PRS-EXTRAS: generic overparse for EVERY doc type, run by loader.py after the typed T-PRS-* parsers.

parse(doc) -> {"_extras": {...}} merged into Doc.fields. The one namespaced key can never collide with a raw
**Label:** key (labels never start with "_") or a typed snake_case key. Nothing is interpreted: labels and cells
are kept as written, in document order, duplicates included, so no fact a document states is dropped. Only
shape-certain tokens are typed. There is no semantic labelling (payee, own account, ...); that stays in the
typed parsers. Lists hold only what the document states; empty lists are left out.

_extras
  headings     list  [str]                 markdown "# ..." heading text, in order
  labels       list  [[label, value]]      every "**Label:** value", "**Label**: value" and plain "Label: value"
                                           line; label lower-cased and whitespace-collapsed, value as written.
                                           A bold line with no value ("**Receipt of Sale**", a letterhead) and a
                                           plain label with an empty value ("Signed:") keep value "".
  tables       list  [{"header": [str], "rows": [[str]]}]   every markdown table; header cells lower-cased,
                                           row cells as written, separator rows dropped, ragged rows kept as is
  amounts      list  [[float, currency, context]]   every £/€/$/GBP/EUR/USD amount (penny-rounded); currency
                                           is "GBP" | "EUR" | "USD"; context = up to 6 preceding words
  dates        list  [[iso, context]]      every "d Month yyyy" (ordinal suffix allowed) and ISO yyyy-mm-dd date
  accounts     list  [[kind, value]]       kind: "sort_account" ("12-34-56 12345678") | "ending" (3-4 digits
                                           after "ending" / "ending in") | "iban" (spaces removed)
  refs         list  [str]                 reference-shaped tokens: CLAIM n, BOOKING n, INV n, PO n, C-nnn, AB/nnn
  orgs         list  [str]                 capitalised names ending in a company suffix, as written
  countries    list  [str]                 names from the fixed COUNTRIES list, as written
  percents     list  [float]
  placeholders list  [str]                 unfilled-form markers: "[Blank]"-style brackets, "____" lines
  sentences    list  [str]                 prose sentences outside labels, tables and headings

refs, orgs and countries are de-duplicated keeping the first occurrence; the other lists keep every occurrence.

ML use (T-EDGE): derive numeric aggregates only (counts, matches). Never use a label, org, country or cell
string as a feature name or model parameter: labels include letterhead names and edge_model.json is leak-scanned.
"""
import bisect, re

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
CURRENCY = {"£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR", "$": "USD", "usd": "USD"}
AMOUNT_RE = re.compile(r"(£|€|\$|\bGBP|\bEUR|\bUSD)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?![\d,]*\d)", re.I)
DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)? (" + "|".join(MONTHS) + r"),? (\d{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
LIST_PREFIX = r"^\s*(?:[-*+]|\d{1,2}[.)])?\s*"
BOLD_RE = re.compile(LIST_PREFIX + r"\*\*([^*\n]+?)\*\*\s*:?\s*(.*?)\s*$")
PLAIN_RE = re.compile(LIST_PREFIX + r"([A-Z][A-Za-z /&()'-]{0,40}?)\s*:(?:\s+(.*?))?\s*$")
SEP_CELL_RE = re.compile(r":?-+:?")
SORT_ACC_RE = re.compile(r"\b(\d{2}-\d{2}-\d{2})\s+(\d{6,10})\b")
ENDING_RE = re.compile(r"\bending(?: in)?\s+(\d{3,4})\b", re.I)
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?)\b")
REF_RE = re.compile(r"\b(CLAIM \d+|BOOKING \d+|INV[ -]?\d+|PO \d+|C-\d{3,}|[A-Z]{2,4}/\d{3,})\b")
SUFFIX = (r"(?:Ltd|Limited|plc|PLC|LLP|LLC|Inc|Co|Company|GmbH|AG|SARL|SAL|SAS|Srl|SRL|SpA|FZE|FZCO|BV|NV|SA|PJSC"
          r"|Pty|Sp\. z o\.o\.)")
ORG_RE = re.compile(r"(?<![\w'])((?:[A-Z][\w&'.-]*\s+){1,5}?" + SUFFIX + r")(?![\w-])")
COUNTRIES = ("Afghanistan", "Albania", "Algeria", "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan",
             "Bahrain", "Bangladesh", "Belarus", "Belgium", "Bolivia", "Bosnia and Herzegovina", "Brazil", "Bulgaria",
             "Cambodia", "Canada", "Chile", "China", "Colombia", "Croatia", "Cuba", "Cyprus", "Czech Republic",
             "Denmark", "Egypt", "Estonia", "Ethiopia", "Finland", "France", "Georgia", "Germany", "Ghana", "Greece",
             "Haiti", "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel",
             "Italy", "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya", "Kuwait", "Latvia", "Lebanon", "Libya",
             "Lithuania", "Luxembourg", "Malaysia", "Mali", "Malta", "Mexico", "Moldova", "Morocco", "Myanmar",
             "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Nigeria", "North Korea", "Norway", "Oman",
             "Pakistan", "Panama", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia",
             "Saudi Arabia", "Senegal", "Serbia", "Singapore", "Slovakia", "Slovenia", "Somalia", "South Africa",
             "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Sweden", "Switzerland", "Syria", "Taiwan",
             "Tanzania", "Thailand", "Tunisia", "Turkey", "Uganda", "Ukraine", "United Arab Emirates",
             "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Venezuela", "Vietnam", "Yemen", "Zimbabwe")
COUNTRY_RE = re.compile(r"\b(" + "|".join(sorted(COUNTRIES, key=len, reverse=True)) + r")\b")
PCT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?%")
PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,30}\](?!\()|_{3,}")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(£])")


def _money(m):
    return round(int(m.group(2).replace(",", "")) + int((m.group(3) or "0").ljust(2, "0")) / 100, 2)


def _ctx(flat, starts, pos):
    """Up to 6 words before pos on the same source line (a table cell's context is the cells before it)."""
    return " ".join(flat[starts[bisect.bisect_right(starts, pos) - 1]:pos].split()[-6:])


def _iso(m):
    return f"{int(m.group(3)):04d}-{MONTHS.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"


def _in_order(hits):
    """(position, item) pairs from several regexes -> items in document order (ties keep regex order)."""
    return [item for _, item in sorted(hits, key=lambda h: h[0])]


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _label(s):
    return " ".join(s.split()).rstrip(":").strip().lower()


def tables(text):
    """Every markdown table: [{"header": [lower-cased cell], "rows": [[cell as written]]}]. A non-table line
    (blank included) ends a table; separator rows are dropped; the first row is the header."""
    out, cur = [], None
    for line in (text or "").splitlines():
        s = line.strip()
        if not (s.startswith("|") and len(s) > 1):
            cur = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(SEP_CELL_RE.fullmatch(c) for c in cells if c) and any(cells):
            continue
        if cur is None:
            cur = {"header": [_label(c) for c in cells], "rows": []}
            out.append(cur)
        else:
            cur["rows"].append(cells)
    return out


def labels(text):
    """Every labelled line as [label, value], document order, duplicates kept. Returns (labels, headings, prose)
    so the caller sees which lines were neither labels, tables nor headings."""
    labs, heads, prose = [], [], []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("|"):
            continue
        if s.startswith("#"):
            h = s.lstrip("#").strip()
            if h:
                heads.append(h)
            continue
        m = BOLD_RE.match(s) or PLAIN_RE.match(s)
        if m:
            labs.append([_label(m.group(1)), (m.group(2) or "").strip()])
        else:
            prose.append(re.sub(LIST_PREFIX, "", s.replace("**", "")))
    return labs, heads, prose


def parse(doc):
    text = doc.text or ""
    labs, heads, prose = labels(text)
    segs = [" ".join(re.sub(r"\*\*|\|", " ", line).split()) for line in text.splitlines()]
    segs = [x for x in segs if x]
    flat, starts = " ".join(segs), [0]
    for x in segs[:-1]:
        starts.append(starts[-1] + len(x) + 1)
    ctx = lambda m: _ctx(flat, starts, m.start())
    ex = {
        "headings": heads,
        "labels": labs,
        "tables": tables(text),
        "amounts": [[_money(m), CURRENCY[m.group(1).lower()], ctx(m)] for m in AMOUNT_RE.finditer(flat)],
        "dates": _in_order([(m.start(), [_iso(m), ctx(m)]) for m in DATE_RE.finditer(flat)]
                           + [(m.start(), ["-".join(m.groups()), ctx(m)]) for m in ISO_RE.finditer(flat)]),
        "accounts": _in_order([(m.start(), ["sort_account", f"{m.group(1)} {m.group(2)}"])
                               for m in SORT_ACC_RE.finditer(flat)]
                              + [(m.start(), ["ending", m.group(1)]) for m in ENDING_RE.finditer(flat)]
                              + [(m.start(), ["iban", m.group(1).replace(" ", "")]) for m in IBAN_RE.finditer(flat)]),
        "refs": _uniq(r for r in REF_RE.findall(flat) if r != doc.doc_id),
        "orgs": _uniq(o for x in segs for o in ORG_RE.findall(x)),
        "countries": _uniq(COUNTRY_RE.findall(flat)),
        "percents": [float(p) for p in PCT_RE.findall(flat)],
        "placeholders": PLACEHOLDER_RE.findall(flat),
        "sentences": [x.strip() for line in prose for x in SENTENCE_RE.split(line) if x.strip()],
    }
    return {"_extras": {k: v for k, v in ex.items() if v}}
