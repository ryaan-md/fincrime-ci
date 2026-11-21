"""payments_fx POLICY.md encoded. Owner: ticket T-POL-payments_fx. Source: hackathon/institutions/payments_fx/POLICY.md.
Section names in comments refer to that file. Cross-checked against docs/BRIEF_DIGEST.md and docs/POLICY_TABLE.md."""
from .common import *  # noqa
from .common import RFI_BY_RULE as _COMMON_RFI_BY_RULE

NAME = "payments_fx"

# "Rules this institution runs" table. Amounts in pounds; windows in days unless named otherwise.
LIMITS = {
    # TM01: money received over 30 days > 2x the monthly share (annual / 12) of declared expected payment volume.
    "TM01": {"window_days": 30, "multiple": 2.0, "profile_field": "expected_annual_volume", "months_per_year": 12},
    # TM02: over 10 days, >=80% of money from anyone other than the client (>= £2,000) paid out within 48 hours.
    # Client's own funding is not counted; card spending is not counted as paid out.
    "TM02": {"window_days": 10, "min_third_party_in_gbp": 2000.00, "out_share": 0.80, "out_within_hours": 48,
             "third_party_only": True, "card_spend_counts": False},
    # TM03: >=3 payments to the SAME beneficiary, each £9,000.00-£9,999.99, within 10 days (not cash deposits).
    "TM03": {"window_days": 10, "min_count": 3, "band_low_gbp": 9000.00, "band_high_gbp": 9999.99, "same_beneficiary": True},
    # TM04: client pays MORE THAN 25 different beneficiaries within 30 days (one-to-many).
    "TM04": {"window_days": 30, "max_beneficiaries": 25},
    # TM06: >= £5,000 within 30 days to/from a higher-risk country the customer did not declare.
    "TM06": {"window_days": 30, "min_gbp": 5000.00},
    # TM07: >= £5,000 moves after 180 days with no activity.
    "TM07": {"dormant_days": 180, "min_gbp": 5000.00},
    # TM08: funding >= £1,000 from an account whose holder name is not the client's (funding only, not payouts).
    "TM08": {"min_gbp": 1000.00, "direction": "in"},
    # TM22: payer, recipient or company officer matches the internal list by name, address, DOB or identifier.
    # "A match on one detail alone does not prove it is the same party."
    "TM22": {"match_fields": ("name", "address", "date_of_birth", "identifier"), "single_detail_is_proof": False,
             "parties": ("payer", "recipient", "company_officer")},
}

# "Policy settings".
CURRENCY = "GBP"                       # amounts in other currencies are also given in pounds
CASH_REPORTING_THRESHOLD_GBP = 10000.00
HIGH_RISK_COUNTRIES_AS_OF = "2026-10-01"   # list itself is shared: common.HIGH_RISK_COUNTRIES
# Payments of £10,000 or more to anyone other than the client need an invoice or contract on file before release.
# Transfers to accounts in the client's own name do not.
INVOICE_REQUIRED_GBP = 10000.0
INVOICE_REQUIRED_DOC_KINDS = ("invoice", "contract")
INVOICE_EXEMPT_OWN_NAME = True
# ...and the invoice must be the client's: one billed to another buyer does not support the client's payment
# ("ESC_INVOICE_MISMATCH: The invoices do not support the payments"; flags/payments_fx_invoice_buyer.py).
INVOICE_BUYER_MUST_BE_CLIENT = True
# Own-account transfers: only when a document on file shows the client holds the account; >=5x -> RFI, below -> close.
OWN_ACCOUNT_NEEDS_HOLDER_DOC = True     # multiple itself: common.OWN_ACCOUNT_RFI_MULTIPLE
# Context only, never an alert on its own: round amounts, shared device/IP/contact, same-day payee, crypto/gambling.
CONTEXT_ONLY_SIGNALS = ("round_amount", "shared_device", "shared_ip", "shared_contact", "payee_added_same_day",
                        "crypto_counterparty", "gambling_counterparty")
# An earlier review covers only what it describes; it does not explain new or larger activity.
EARLIER_REVIEW_COVERS_LARGER = False

# "What a case is": one held outgoing payment with every alert on it; for money coming in, one client over the window.
CASE_UNIT = "held_payment_or_customer_window"

# Default RFI per rule when the alert is unexplained and nothing is suspicious (arbiter step H).
# TM01, TM07: unexplained money received over the window / dormant reactivation; RFI_PURPOSE_DOCUMENTS asks for
#   documents showing what the payments were for (provisional labels: bare TM01 cases are RFI_PURPOSE_DOCUMENTS).
# TM08: third-party funding with nothing on file; ask for the documents behind it (e.g. a financing agreement).
# TM22: "If nothing on file settles it either way and nothing else is concerning, send an RFI" (common default).
# TM02, TM03, TM06 stay unlisted -> ESCALATE: unexplained rapid movement, sub-threshold runs and higher-risk
#   corridors are escalation typologies here ("the activity is unexplained").
# TM04 stays unlisted on measurement, not typology: bare TM04 cases are labelled RFI_BUSINESS_RECORDS, but the
#   TM04+TM08 mule cluster has no red-flag scanner yet, so an RFI default there costs 3 each versus 2 each saved
#   (make eval payments_fx cost 1358 without it, 1386 with it).
RFI_BY_RULE = {
    **_COMMON_RFI_BY_RULE,
    "TM01": "RFI_PURPOSE_DOCUMENTS",
    "TM07": "RFI_PURPOSE_DOCUMENTS",
    "TM08": "RFI_PURPOSE_DOCUMENTS",
}
