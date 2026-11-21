"""Entry logic behind system/run. Robustness contract (T-PERF):
  - malformed rows never crash the run: unreadable lines and rows missing the keys the loader needs are dropped;
  - every case in cases.jsonl with a case_id gets exactly one line, in file order (first row wins on duplicates);
  - a case whose data is incomplete (missing file, dropped row, unresolved alert, transaction or held payment,
    unreadable document) may not CLOSE: when unsure, do not close. Cosmetic noise (UTF-8 BOM, CRLF, blank lines)
    is not incompleteness."""
import builtins, io, json, os, sys, time
from . import loader
from .loader import load
from . import policy as policies
from .decide import decide, safe_escalate
from .explain import explain

FILES = ("cases.jsonl", "alerts.jsonl", "customers.jsonl", "transactions.jsonl", "evidence.jsonl",
         "previous_reviews.jsonl", "internal_list.jsonl")
# keys loader.load indexes directly, per file; a row without them (as strings) is dropped
REQUIRED = {"cases.jsonl": ("case_id", "customer_id"), "alerts.jsonl": ("alert_id",),
            "customers.jsonl": ("customer_id",), "transactions.jsonl": ("transaction_id", "customer_id", "booked_at"),
            "evidence.jsonl": ("doc_id", "customer_id", "type", "file"), "previous_reviews.jsonl": ("customer_id",),
            "internal_list.jsonl": ("entry_id",)}
LISTS = {"cases.jsonl": ("alert_ids",), "alerts.jsonl": ("transaction_ids",)}   # must be lists of strings if present
STRINGS = {"evidence.jsonl": ("dated", "title")}                                # must be strings if present
NULLABLE = {"cases.jsonl": ("held_payment_id",)}                                 # must be strings or null if present


def _rows(path):
    """Every JSON object in a .jsonl file, skipping lines that do not parse. BOM, CRLF and blank lines are noise."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines = f.read().splitlines()
    out = []
    for l in lines:
        l = l.strip().lstrip("﻿")
        if not l:
            continue
        try:
            r = json.loads(l)
        except ValueError:
            r = None
        out.append(r if isinstance(r, dict) else None)
    return out


def _valid(name, r):
    if r is None:
        return False
    if not all(isinstance(r.get(k), str) and r.get(k) for k in REQUIRED.get(name, ())):
        return False
    for k in LISTS.get(name, ()):
        if k in r and not (isinstance(r[k], list) and all(isinstance(x, str) for x in r[k])):
            return False
    if not all(r[k] is None or isinstance(r[k], str) for k in NULLABLE.get(name, ()) if k in r):
        return False
    return all(isinstance(r[k], str) for k in STRINGS.get(name, ()) if k in r)


class _Tolerant:
    """Swaps loader's file readers for tolerant ones while load() runs, and records what had to be dropped."""

    def __init__(self, folder):
        self.folder = os.path.abspath(folder)
        self.missing = [n for n in FILES if not os.path.isfile(os.path.join(self.folder, n))]
        self.dropped_all = False        # a dropped row that may affect any case (no customer, or the internal list)
        self.dropped_cust = set()       # customers with a dropped row

    def _jsonl(self, path):
        name, keep = os.path.basename(path), []
        for r in _rows(path):
            if name == "cases.jsonl" and r is not None and "alert_ids" not in r:
                r = {**r, "alert_ids": []}
            if _valid(name, r):
                keep.append(r)
            elif name not in ("cases.jsonl", "alerts.jsonl", "customers.jsonl"):
                # dropped cases/alerts/customers show up per case (no case, unresolved alert, empty customer)
                cid = r.get("customer_id") if r else None
                if name == "internal_list.jsonl" or not isinstance(cid, str):
                    self.dropped_all = True
                else:
                    self.dropped_cust.add(cid)
        return keep

    @staticmethod
    def _open(path, *a, **kw):
        kw["errors"] = "replace"
        try:
            return builtins.open(path, *a, **kw)
        except (OSError, ValueError):
            return io.StringIO("")

    def __enter__(self):
        self._saved = loader._jsonl
        loader._jsonl, loader.open = self._jsonl, self._open
        return self

    def __exit__(self, *exc):
        loader._jsonl = self._saved
        del loader.open
        return False

    def incomplete(self, c):
        """Why this case may not CLOSE, or '' if its data is complete."""
        if self.missing:
            return "missing " + ",".join(self.missing)
        if self.dropped_all:
            return "malformed rows dropped"
        if c.raw["customer_id"] in self.dropped_cust:
            return "malformed rows dropped for customer"
        if not c.customer:
            return "customer record missing"
        if len(c.alerts) != len(c.raw["alert_ids"]) or not c.alerts:
            return "alert records missing"
        if any(len(c.alert_txns[a["alert_id"]]) != len(a.get("transaction_ids", [])) for a in c.alerts):
            return "alert transactions missing"
        if c.raw.get("held_payment_id") and c.held_payment is None:
            return "held payment missing"
        if any(not d.text or "�" in d.text for d in c.docs):
            return "document text missing or unreadable"
        return ""


def _case_rows(folder):
    """(case_id, alert_ids) for every distinct case_id in cases.jsonl, in file order."""
    seen, out = set(), []
    for r in _rows(os.path.join(os.path.abspath(folder), "cases.jsonl")):
        cid = r.get("case_id") if r else None
        if not isinstance(cid, str) or not cid or cid in seen:
            continue
        seen.add(cid)
        al = r.get("alert_ids")
        out.append({"case_id": cid, "alert_ids": [a for a in al if isinstance(a, str)] if isinstance(al, list) else []})
    return out


def _decide(c, pol, why_incomplete):
    try:
        d = decide(c, pol)                 # 2a rules, 2b edge, 3 arbiter
        if d.verdict == "CLOSE" and why_incomplete:
            return safe_escalate(c.raw, why_incomplete)
        try:
            d.reason = explain(c, d)       # 4 explainer (LLM optional, template fallback)
        except Exception:
            from .reasons import render
            d.reason = render(c, d.verdict, d.reason_code, d.facts)
        return d
    except Exception as e:  # never drop a case
        return safe_escalate(c.raw, e)


def main(argv):
    if len(argv) != 2:
        print("usage: system/run <institution_folder> <output_file>", file=sys.stderr)
        return 2
    folder, out_path = argv
    t0 = time.time()
    inst = os.path.basename(os.path.abspath(folder).rstrip("/"))
    rows = _case_rows(folder)
    by_id = {}
    try:
        pol = policies.get(inst)
        with _Tolerant(folder) as tol:
            inst, cases = load(folder)
        for c in cases:
            if c.case_id not in by_id:
                by_id[c.case_id] = _decide(c, pol, tol.incomplete(c))
        if tol.missing:
            print(f"[fincrime] {inst}: missing {', '.join(tol.missing)}; no case may close", file=sys.stderr)
    except Exception as e:  # unknown institution or unloadable folder: every case still gets a line
        print(f"[fincrime] {inst}: could not load ({type(e).__name__}); escalating every case", file=sys.stderr)
        by_id = {r["case_id"]: safe_escalate(r, e) for r in rows}
    lines = []
    for r in rows:
        d = by_id.get(r["case_id"]) or safe_escalate(r, "case could not be loaded")
        d.alert_ids = r["alert_ids"]       # the line covers every alert in the case
        lines.append(json.dumps(d.to_json(), ensure_ascii=False, sort_keys=False))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in lines))
    print(f"[fincrime] {inst}: {len(lines)} cases in {time.time()-t0:.1f}s -> {out_path}", file=sys.stderr)
    return 0
