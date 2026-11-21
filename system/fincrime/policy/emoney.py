"""emoney POLICY.md encoded. Owner: ticket T-POL-emoney. Source: hackathon/institutions/emoney/POLICY.md.
Section names in comments refer to that file. Constants only; no dev IDs, names or amounts."""
from .common import *  # noqa
from .common import RFI_BY_RULE as _COMMON_RFI_BY_RULE

NAME = "emoney"

# "What a case is": one customer over the review window, every alert in that window, one verdict.
CASE_UNIT = "customer_window"
# Intro paragraph: app-based accounts for individuals and small businesses.
CUSTOMER_TYPES = ("individual", "business")

# "Rules this institution runs". Amounts in pounds (Policy settings: "Currency: pounds sterling").
RULES_RUN = ("TM01", "TM02", "TM03", "TM04", "TM05", "TM06", "TM07", "TM22")
LIMITS = {
    # TM01: money received over 30 days > 3x declared monthly income (individual) / > 2x monthly turnover (business).
    "TM01": {"window_days": 30, "multiple_individual": 3.0, "multiple_business": 2.0},
    # TM02: over 10 days, >= 80% of money received (>= 2,000.00) paid out within 48h. Card spend is not paid out.
    "TM02": {"window_days": 10, "min_received": 2000.00, "out_share": 0.80, "out_within_hours": 48,
             "card_spend_counts": False},
    # TM03: >= 4 cash top-ups, each 800.00-999.99, within 10 days. Top-ups of 1,000.00+ need extra checks.
    "TM03": {"window_days": 10, "min_count": 4, "band_low": 800.00, "band_high": 999.99,
             "extra_checks_from": 1000.00},
    # TM04: more than 6 distinct people/companies pay in, or are paid, by bank transfer within 30 days.
    "TM04": {"window_days": 30, "max_counterparties": 6, "channel": "bank_transfer"},
    # TM05: one cash top-up >= 1,000.00 (individual) or >= 5,000.00 (business).
    "TM05": {"cash_individual": 1000.00, "cash_business": 5000.00},
    # TM06: >= 5,000.00 within 30 days to/from a higher-risk country the customer did not declare.
    "TM06": {"window_days": 30, "min_amount": 5000.00},
    # TM07: >= 5,000.00 moves after 180 days with no activity.
    "TM07": {"dormant_days": 180, "min_amount": 5000.00},
    # TM22: payer, recipient or company officer matches the internal list by name, address, DOB or identifier.
    # "A match on one detail alone does not prove it is the same party."
    "TM22": {"match_fields": ("name", "address", "date_of_birth", "identifier"), "single_detail_proves": False},
}

# "Policy settings".
CASH_REPORTING_THRESHOLD = 10000.00
HIGH_RISK_COUNTRIES_AS_OF = "2026-10-01"   # list itself is shared: common.HIGH_RISK_COUNTRIES
# Own account elsewhere counts only when a document on file shows the customer holds it;
# >= 5x declared income/volume -> RFI_UPDATED_PROFILE, below -> close (common.OWN_ACCOUNT_RFI_MULTIPLE).
OWN_ACCOUNT_NEEDS_HOLDER_DOC = True
# Context only, never an alert or a verdict on its own.
CONTEXT_ONLY_SIGNALS = ("round_amounts", "shared_device", "shared_ip", "shared_contact_details",
                        "recipient_added_same_day", "crypto_counterparty", "gambling_counterparty")
# An earlier review covers only what it describes; it does not explain new or larger activity.
PREVIOUS_REVIEW_COVERS_ONLY_DESCRIBED = True

# Default RFI code per rule when the alert is unexplained and nothing is suspicious ("Verdicts": RFI when one
# specific document or answer would settle it). Rules left out escalate (decide.py step I).
RFI_BY_RULE = dict(_COMMON_RFI_BY_RULE)
RFI_BY_RULE.pop("TM08", None)   # TM08 is not in "Rules this institution runs".
# TM01 (no explaining doc) and TM07 (dormant reactivation) -> RFI_SOURCE_OF_FUNDS; TM22 unresolved list match
# -> RFI_COUNTERPARTY_DETAILS ("If nothing on file settles it either way and nothing else is concerning, send an RFI").
# TM02/TM04 stay unlisted (ESCALATE) for now. Bare TM02 and TM02+TM04 (CLUSTER_SWEEP emoney #11, #14) are provisionally
# RFI / RFI_PURPOSE_DOCUMENTS, but the TM02 escalations (#17 dissolved payer, #21/#27 shared device) have no red-flag
# scanner yet, so an RFI default would turn them into RFIs (cost 3 each) and lower the verdict score. Revisit
# once those flags land.
# TM03 (repeated just-below-threshold top-ups), TM05 (large cash) and TM06 (undeclared higher-risk country) stay
# unlisted: unexplained, they are the activity the rule exists to catch, so the default is ESCALATE.
