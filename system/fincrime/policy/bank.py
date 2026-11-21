"""bank POLICY.md encoded. Owner: ticket T-POL-bank. Source: hackathon/institutions/bank/POLICY.md."""
from .common import *  # noqa
from .common import RFI_BY_RULE as _COMMON_RFI_BY_RULE

NAME = "bank"
# "What a case is": one customer over the review window, every alert in that window, one verdict.
CASE_UNIT = "customer_window"
# "Policy settings": cash reporting threshold.
CASH_REPORTING_THRESHOLD_GBP = 10000.0

# "Rules this institution runs" table. Individuals vs businesses are split where the SOP splits them.
LIMITS = {
    # TM01: money in over 30 days > 3x declared monthly income (individual) / > 2x monthly turnover (business).
    "TM01": {"window_days": 30, "multiple_individual": 3.0, "multiple_business": 2.0},
    # TM02: over 10 days, >= 80% of money received (>= £2,000.00) paid out within 48h. Card spend is not "paid out".
    "TM02": {"window_days": 10, "min_received_gbp": 2000.0, "out_share": 0.80, "out_within_hours": 48,
             "card_counts_as_out": False},
    # TM03: 3+ cash deposits, each £9,000.00-£9,999.99, within 10 days.
    "TM03": {"window_days": 10, "min_count": 3, "band_gbp": (9000.0, 9999.99)},
    # TM04: more than 6 distinct transfer counterparties (in or out) within 30 days.
    "TM04": {"window_days": 30, "max_counterparties": 6},
    # TM05: one cash deposit/withdrawal >= £10,000.00 (individual) / >= £25,000.00 (business).
    "TM05": {"cash_individual_gbp": 10000.0, "cash_business_gbp": 25000.0},
    # TM06: >= £5,000.00 within 30 days to/from an undeclared higher-risk country.
    "TM06": {"window_days": 30, "min_gbp": 5000.0},
    # TM07: >= £5,000.00 moves after 180 days with no activity.
    "TM07": {"dormant_days": 180, "min_gbp": 5000.0},
    # TM22: payer, recipient or company officer matches the internal list on name/address/DOB/identifier.
    # "A match on one detail alone does not prove it is the same party."
    "TM22": {"single_detail_proves_match": False},
}

# Default RFI code per rule when the alert is unexplained and nothing is suspicious ("Verdicts": RFI when there is
# no suspicion and one document or answer would settle it). Rules left out default to ESCALATE.
RFI_BY_RULE = dict(_COMMON_RFI_BY_RULE)
# TM05: a large cash movement with no red flag is settled by proof of where the cash came from (e.g. a sale
# receipt). TM03 (sub-threshold cash) is deliberately NOT given a default: it is the structuring rule, and
# "Once there is suspicion, escalate. Do not send the customer an RFI" (tipping off).
RFI_BY_RULE["TM05"] = "RFI_SOURCE_OF_FUNDS"
# Not defaulted to RFI: TM02 (rapid movement, money already moved on), TM04 (many-to-one) and TM06 (undeclared
# higher-risk corridor) all describe ESC_UNEXPLAINED_FUNDS / ESC_HIGH_RISK_CORRIDOR when unexplained.

# ESC_UNEXPLAINED_FUNDS "The money has no credible explanation and was moved on": conflicting stories for the cash
# in two chats, and the cash paid out again within TM02's 48 hours, worth TM02's 80% (flags/bank_cash_story.py).
CONFLICTING_CASH_STORY = {"within_hours": LIMITS["TM02"]["out_within_hours"], "min_share": LIMITS["TM02"]["out_share"]}
