"""CONTRACT (frozen; change only via the integrator). See docs/CONTRACTS.md."""
from dataclasses import dataclass, field
from typing import Optional

VERDICTS = ("CLOSE", "RFI", "ESCALATE")
CODES = {
    "CLOSE": ["COLLECTION_DOCUMENTED", "BUSINESS_RECORDS_RECONCILE", "DOCUMENTED_EVENT", "INVOICE_MATCHES",
              "OWN_ACCOUNT", "GROUP_COMPANY", "THIRD_PARTY_DOCUMENTED", "REFUND_OF_EARLIER_PAYMENT",
              "RETURNING_CUSTOMER", "LIST_MATCH_DIFFERENT_PARTY", "WITHIN_PROFILE"],
    "RFI": ["RFI_PURPOSE_DOCUMENTS", "RFI_BUSINESS_RECORDS", "RFI_SOURCE_OF_FUNDS", "RFI_COUNTERPARTY_DETAILS",
            "RFI_UPDATED_PROFILE"],
    "ESCALATE": ["ESC_MULE_ACTIVITY", "ESC_STRUCTURING", "ESC_UNEXPLAINED_FUNDS", "ESC_PASS_THROUGH",
                 "ESC_INVOICE_MISMATCH", "ESC_HIGH_RISK_CORRIDOR", "ESC_ACCOUNT_MISUSE", "ESC_THIRD_PARTY_FUNDING",
                 "ESC_EARLY_REDEMPTION", "ESC_LISTED_PARTY"],
}
CODE_TO_VERDICT = {c: v for v, cs in CODES.items() for c in cs}


@dataclass
class Doc:
    doc_id: str
    type: str
    title: str
    dated: str            # ISO date
    text: str             # raw markdown
    fields: dict          # parsed **Field:** lines (lower-cased keys)
    amounts: list         # every £ amount found, as float
    dates: list           # every date found, ISO


@dataclass
class Case:
    institution: str      # bank | emoney | investment | payments_fx
    raw: dict             # cases.jsonl row
    customer: dict
    alerts: list          # alerts.jsonl rows
    txns: list            # all transactions for the customer, sorted by booked_at
    alert_txns: dict      # alert_id -> [txn rows]
    docs: list            # [Doc] for the customer
    prev_reviews: list
    list_entries: dict    # entry_id -> internal_list row (whole list)
    held_payment: Optional[dict] = None

    @property
    def case_id(self): return self.raw["case_id"]
    @property
    def window(self): return self.raw["review_window"]
    def docs_of(self, *types): return [d for d in self.docs if d.type in types]
    def docs_by_window_end(self): return [d for d in self.docs if d.dated <= self.window["to"]]


@dataclass
class Proof:
    """Output of a CLOSE checker. passed=True means this alert is fully explained."""
    alert_id: str
    passed: bool
    code: str                         # CLOSE code if passed, else the RFI code describing what is missing
    evidence: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)   # slot values for the reason template
    checker: str = ""


@dataclass
class Flag:
    """Output of a red-flag scanner. Any flag => ESCALATE."""
    code: str                         # ESC_* code
    evidence: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    scanner: str = ""
    severity: int = 1                 # higher wins when several flags fire


@dataclass
class EdgeSignal:
    """Output of the edge-case detector (edge.py). Unsupervised: 'how unlike a normal case is this?'"""
    score: float = 0.0                # 0 = typical, 1 = highly unusual
    unusual: bool = False             # score >= policy.EDGE_THRESHOLD
    suspicious: bool = False          # an unusual feature that itself points to crime (-> ESCALATE, not RFI)
    features: list = field(default_factory=list)   # human-readable names of the unusual features


@dataclass
class Decision:
    """Arbiter output. `reason` is filled by explain.py AFTER the verdict is fixed."""
    case_id: str
    alert_ids: list
    verdict: str
    reason_code: str
    reason: str
    evidence: list
    trace: list = field(default_factory=list)   # decision path, debug only, never written to output
    facts: dict = field(default_factory=dict)   # grounded facts bundle for the explainer

    def to_json(self):
        return {"case_id": self.case_id, "alert_ids": self.alert_ids, "verdict": self.verdict,
                "reason_code": self.reason_code, "reason": self.reason, "evidence": self.evidence}
