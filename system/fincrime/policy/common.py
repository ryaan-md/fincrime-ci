"""Settings shared by all four SOPs (see each POLICY.md 'policy settings')."""
HIGH_RISK_COUNTRIES = {"Afghanistan", "Belarus", "DR Congo", "Democratic Republic of the Congo", "Haiti", "Iran",
                       "Iraq", "Lebanon", "Libya", "Mali", "Myanmar", "North Korea", "Russia", "Somalia",
                       "South Sudan", "Syria", "Venezuela", "Yemen"}
OWN_ACCOUNT_RFI_MULTIPLE = 5.0     # own-account transfers pushing activity to >=5x declared -> RFI_UPDATED_PROFILE
AMOUNT_TOLERANCE = 0               # pence; amounts must match exactly to the penny (checks.util.amt_eq)

# Decisive document TYPE -> CLOSE code it can support (generic types, never IDs).
EXPLAINING_DOCS = {
    "vehicle_sale_receipt": "DOCUMENTED_EVENT", "sale_receipt": "DOCUMENTED_EVENT",
    "solicitor_letter": "DOCUMENTED_EVENT", "insurer_letter": "DOCUMENTED_EVENT",
    "settlement_letter": "DOCUMENTED_EVENT", "completion_statement": "DOCUMENTED_EVENT",
    "redundancy_letter": "DOCUMENTED_EVENT",
    "booking_confirmation": "COLLECTION_DOCUMENTED",
    "dealer_invoice": "INVOICE_MATCHES", "supplier_invoice": "INVOICE_MATCHES", "contract_schedule": "INVOICE_MATCHES",
    "carrier_invoice": "INVOICE_MATCHES",
    "venue_email": "REFUND_OF_EARLIER_PAYMENT", "credit_note": "REFUND_OF_EARLIER_PAYMENT",
    "cancellation_confirmation": "REFUND_OF_EARLIER_PAYMENT",
}

# Default RFI code when an alert of this rule is unexplained and there is NO suspicion (arbiter step H: used only
# when EVERY unexplained alert's rule is listed). This is the fallback for cases that DO have documents on file that
# did not settle the alert. Rules not listed default to ESCALATE (cost matrix: escalating a true RFI costs 2,
# RFI-ing a true ESC costs 3). Cases with nothing on file are handled by RFI_WHEN_NOTHING_ON_FILE below.
RFI_BY_RULE = {
    "TM01": "RFI_SOURCE_OF_FUNDS",
    "TM07": "RFI_SOURCE_OF_FUNDS",
    "TM08": "RFI_COUNTERPARTY_DETAILS",
    "TM22": "RFI_COUNTERPARTY_DETAILS",   # list match, nothing settles it either way -> RFI (SOP)
}

# POLICY.md reason-code table: what each RFI asks the customer for (reason text must say what to ask for).
RFI_ASK = {
    "RFI_PURPOSE_DOCUMENTS": "documents showing what the payments were for",
    "RFI_BUSINESS_RECORDS": "the business records behind the activity",
    "RFI_SOURCE_OF_FUNDS": "proof of where the money came from",
    "RFI_COUNTERPARTY_DETAILS": "who the counterparty is and what the payment was for",
    "RFI_UPDATED_PROFILE": "an updated expected income or volume",
}

# NOTHING ON FILE -> RFI (checks/missing_document.py, arbiter step E). Applies only when every document on file is a
# standard onboarding/filler type, so no document explains the alert and none contradicts it; red flags still win
# first (step A). POLICY.md: "RFI: there is no suspicion yet, and one specific document or answer from the customer
# would settle the case. The reason must say what to ask for."
# The code is chosen from WHAT is missing, per institution. Order matters: the first listed rule present in the case
# picks the code for the whole case (list match > money out > money in). A case with any rule NOT listed for its
# institution is left alone (falls through to RFI_BY_RULE / ESCALATE).
STANDARD_DOC_TYPES = ("due_diligence_summary", "proof_of_address", "contact_note")   # every customer has these
OWN_ONLY_DOC_TYPES = ("company_register_extract",)   # standard only when it names the customer (own register)
RFI_WHEN_NOTHING_ON_FILE = {
    # Individuals (bank also has businesses). In: ask where the money came from. Out/through: ask what it paid for.
    "bank": (("TM22", "RFI_COUNTERPARTY_DETAILS"),   # who is the matched counterparty
             ("TM02", "RFI_PURPOSE_DOCUMENTS"),      # money in and straight out (loan -> dealer, trip collection)
             ("TM04", "RFI_PURPOSE_DOCUMENTS"),      # many payers -> one booking
             ("TM01", "RFI_SOURCE_OF_FUNDS"),        # large inflow (estate, sale)
             ("TM07", "RFI_SOURCE_OF_FUNDS")),       # dormant account receives funds
    "emoney": (("TM22", "RFI_COUNTERPARTY_DETAILS"),
               ("TM02", "RFI_PURPOSE_DOCUMENTS"),    # deposit returned and paid on (tenancy holding deposit)
               ("TM04", "RFI_PURPOSE_DOCUMENTS"),
               ("TM01", "RFI_SOURCE_OF_FUNDS"),      # settlement / large inflow
               ("TM07", "RFI_SOURCE_OF_FUNDS")),
    "investment": (("TM22", "RFI_COUNTERPARTY_DETAILS"),
                   ("TM02", "RFI_PURPOSE_DOCUMENTS"),    # subscription withdrawn (ISA cancellation refund)
                   ("TM10", "RFI_PURPOSE_DOCUMENTS"),    # units sold back early: ask why
                   ("TM01", "RFI_SOURCE_OF_FUNDS"),
                   ("TM07", "RFI_SOURCE_OF_FUNDS"),
                   ("TM08", "RFI_SOURCE_OF_FUNDS")),     # third-party deposit (sale proceeds, ISA top-up): whose money
    # Businesses; the case is usually a HELD OUTGOING payment, and >= £10k to a third party needs an invoice or
    # contract on file before release (POLICY.md) -> ask for purpose documents, or business records for volume patterns.
    "payments_fx": (("TM22", "RFI_COUNTERPARTY_DETAILS"),
                    ("TM04", "RFI_BUSINESS_RECORDS"),    # payments to many beneficiaries (monthly fee runs)
                    ("TM02", "RFI_BUSINESS_RECORDS"),    # third-party money in and out (freight collections)
                    ("TM03", "RFI_PURPOSE_DOCUMENTS"),   # stage payments to one beneficiary: ask for the contract
                    ("TM01", "RFI_PURPOSE_DOCUMENTS"),   # held payment above profile: ask for the invoice/contract
                    ("TM07", "RFI_PURPOSE_DOCUMENTS"),
                    ("TM08", "RFI_PURPOSE_DOCUMENTS")),  # third-party funding (invoice-finance advance)
}
# Rules that get the nothing-on-file RFI ONLY when they are the sole rule in the case (policy rulings 1 and 4 cover the
# bare single-rule clusters). Next to another pattern, e.g. third-party money with an early sale or with stage payments
# below £10k, the case is left alone and escalates (RFI-ing a true ESC costs 3, escalating a true RFI costs 2).
RFI_WHEN_NOTHING_ON_FILE_SOLO_ONLY = {
    "investment": ("TM10",),
    "payments_fx": ("TM03",),
}

# Edge-case detector threshold (0..1). 1.01 = disabled until T-EDGE tunes it with `make mutate`.
EDGE_THRESHOLD = 1.01
# RFI code used when the arbiter downgrades a rules-CLOSE because the case is unusual.
EDGE_DOWNGRADE_RFI = "RFI_PURPOSE_DOCUMENTS"
