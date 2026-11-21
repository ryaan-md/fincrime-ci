"""Red-flag scanners. Each: fn(case, policy) -> [Flag]. ANY flag => ESCALATE, whatever the documents say.
Register in SCANNERS. Highest severity flag supplies the reason code."""
from .fraud_report import scan_fraud_report
from .listed_party import scan_listed_party
from .structuring_language import scan_structuring_language
from .high_risk_country import scan_high_risk_country
from .account_misuse import scan_account_misuse
from .invoice_mismatch import scan_invoice_mismatch
from .mule import scan_pass_through, scan_commission_pool, scan_third_party_funding, scan_dissolved_payer
from .bank_cash_story import scan_conflicting_cash_story
from .early_redemption import scan_early_redemption
from .payments_fx_invoice_buyer import scan_invoice_buyer

SCANNERS = [scan_fraud_report, scan_listed_party, scan_structuring_language, scan_high_risk_country,
            scan_account_misuse, scan_invoice_mismatch, scan_pass_through, scan_commission_pool,
            scan_third_party_funding, scan_dissolved_payer, scan_early_redemption, scan_conflicting_cash_story,
            scan_invoice_buyer]
