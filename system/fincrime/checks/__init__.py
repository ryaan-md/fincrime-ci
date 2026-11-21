"""CLOSE proof checkers. Each: fn(case, alert, policy) -> Proof | None.
Return Proof(passed=True, code=<CLOSE code>) only when EVERY substantive fact is verified.
Return Proof(passed=False, code=<RFI code>) when the explanation exists but is incomplete (e.g. 5x own-account).
Return None when the checker does not apply. Register new checkers in CHECKERS (order = priority)."""
from .business_records_reconcile import check_business_records
from .collection_documented import check_collection_documented
from .documented_amount import check_documented_amount
from .documented_event import check_documented_event, defer_to_events
from .invoice_matches import check_invoice_matches
from .own_account import check_own_account
from .group_company import check_group_company
from .missing_document import check_missing_document
from .refund_of_earlier_payment import check_refund_of_earlier_payment
from .third_party_documented import check_third_party_documented
from .within_profile import check_within_profile
from .list_match_different_party import check_list_match_different_party

# check_missing_document never passes; keep it LAST so a real proof always gets the chance to run first.
CHECKERS = [check_collection_documented, check_own_account, check_group_company, check_invoice_matches,
            check_third_party_documented, check_business_records, check_refund_of_earlier_payment,
            check_documented_event, defer_to_events(check_documented_amount), check_within_profile,
            check_list_match_different_party, check_missing_document]
