"""investment POLICY.md encoded. Owner: ticket T-POL-investment. Source: hackathon/institutions/investment/POLICY.md.
A UK broker and fund platform; every customer is an individual (docs/POLICY_TABLE.md). Section names in the comments
are the POLICY.md headings. Rules TM03, TM04 and TM05 are not run here."""
from .common import *  # noqa
from .common import RFI_BY_RULE as _COMMON_RFI_BY_RULE

NAME = "investment"
# "What a case is": one customer over the review window, all alerts in it, one verdict.
CASE_UNIT = "customer_window"

# "Rules this institution runs": the limit each rule fires on. Amounts in pounds sterling ("Policy settings").
LIMITS = {
    # Money received over 30 days > 3x declared monthly income (individual) / > 2x monthly turnover (business).
    "TM01": {"window_days": 30, "individual_multiple": 3.0, "business_multiple": 2.0},
    # Over 10 days: >= 80% of deposits (>= £2,000.00) withdrawn within 10 days and < 20% of it invested.
    "TM02": {"window_days": 10, "min_deposits_gbp": 2000.0, "withdrawn_share": 0.80, "max_invested_share": 0.20},
    # >= £5,000.00 within 30 days to or from a higher-risk country the customer did not declare.
    "TM06": {"window_days": 30, "min_gbp": 5000.0},
    # >= £5,000.00 moves after 180 days with no activity.
    "TM07": {"dormant_days": 180, "min_gbp": 5000.0},
    # A deposit >= £1,000.00 from, or a withdrawal to, an account whose holder name is not the customer's.
    "TM08": {"min_gbp": 1000.0},
    # Fund units sold back within 90 days of being bought (investment only).
    "TM10": {"max_holding_days": 90},
    # A payer, recipient or company officer matches the internal list by name, address, DOB or identifier.
    # "A match on one detail alone does not prove it is the same party."
    "TM22": {"single_detail_proves_match": False},
}

# "Policy settings": cash reporting threshold.
CASH_REPORTING_THRESHOLD_GBP = 10000.0
# "Policy settings": higher-risk countries as of 1 October 2026. Same list as common.HIGH_RISK_COUNTRIES.
HIGH_RISK_COUNTRIES_AS_OF = "2026-10-01"
# "Policy settings": own-account transfers explaining activity at >= 5x declared income -> RFI for an updated
# expected income (RFI_UPDATED_PROFILE); below 5x -> close. An account elsewhere in the customer's name counts as
# their own only when a document on file shows the customer holds it.
OWN_ACCOUNT_RFI_MULTIPLE = 5.0
# "Policy settings": round amounts, shared device/IP/contact details, a same-day new recipient, crypto or gambling
# counterparties add context only. None of them may raise an alert or decide a verdict on its own.
CONTEXT_ONLY_SIGNALS = ("round_amount", "shared_device", "shared_ip", "shared_contact", "same_day_new_recipient",
                        "crypto_counterparty", "gambling_counterparty")
# "Policy settings": an earlier review covers only what it describes, never new or larger activity.
PREVIOUS_REVIEW_COVERS_NEW_ACTIVITY = False

# "Verdicts" + "Policy settings": the RFI to send when a rule's alert is unexplained and nothing is suspicious.
# Rules absent here default to ESCALATE (decide.py step I).
RFI_BY_RULE = dict(_COMMON_RFI_BY_RULE)
RFI_BY_RULE.update({
    # TM01: more money in than the declared income supports -> ask where it came from.
    "TM01": "RFI_SOURCE_OF_FUNDS",
    # TM07: a dormant account moving money again -> ask where the money came from.
    "TM07": "RFI_SOURCE_OF_FUNDS",
    # TM08 (investment override of common): "Customers fund their account by bank transfer" (intro), so an
    # unexplained deposit from an account not in the customer's name is a funding question -> proof of where the
    # money came from, e.g. a statement showing the customer holds the account ("Policy settings", own accounts).
    "TM08": "RFI_SOURCE_OF_FUNDS",
    # TM22 internal list: "If nothing on file settles it either way and nothing else is concerning, send an RFI."
    "TM22": "RFI_COUNTERPARTY_DETAILS",
})
# Deliberately NOT given an RFI default, so an unexplained alert escalates:
# - TM02 (rapid in/out, little invested) and TM10 (units sold back within 90 days) describe money moved through
#   the account. "Once there is suspicion, escalate. Do not send the customer an RFI at that point."
# - TM06 (undeclared higher-risk country) unexplained -> ESC_HIGH_RISK_CORRIDOR is the SOP's escalate code.
