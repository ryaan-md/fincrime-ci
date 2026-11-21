"""Slot-filled reason templates, one per reason code. Must name THIS case's documents, amounts and people.
Owner: T-RSN. Keep 2-4 sentences. Never invent facts: only use keys present in `facts`."""

RULE_NAMES = {"TM01": "activity above the declared profile", "TM02": "rapid movement of funds",
              "TM03": "repeated transactions just below a threshold", "TM04": "many-to-one or one-to-many payments",
              "TM05": "a large cash transaction", "TM06": "an undeclared higher-risk country",
              "TM07": "dormant account reactivation", "TM08": "third-party funding or payout",
              "TM10": "early fund redemption", "TM22": "an internal list match"}

def _alerts_phrase(case):
    rules = sorted({a["rule_id"] for a in case.alerts})
    return ", ".join(f"{r} ({RULE_NAMES.get(r, r)})" for r in rules)

def render(case, verdict, code, facts):
    f = dict(facts or {})
    who = case.customer.get("name", "the customer")
    al = _alerts_phrase(case)
    if code == "OWN_ACCOUNT":
        return (f"The alerts for {al} are explained by transfers between {who}'s own accounts. "
                f"Statement {f.get('doc_id')} ({f.get('doc_title')}) shows the customer holds the other account, "
                f"and activity is {f.get('multiple', 0):.1f}x the declared level, below the 5x threshold.")
    if code == "WITHIN_PROFILE" and f.get("review_id"):
        return (f"Earlier review {f.get('review_id')}, dated {f.get('review_date')}, already describes the "
                f"{f.get('amount')} from {f.get('counterparty')} that raised {al}. "
                f"The activity is within the profile reviewed for {who}.")
    if code == "WITHIN_PROFILE":
        return (f"{f.get('doc_title')} ({f.get('doc_id')}), dated {f.get('doc_date')}, declares a seasonal peak in "
                f"{', '.join(f.get('peak_months', []))} of up to {f.get('peak_figure')}. "
                f"The {f.get('amount')} that raised {al} falls in those months and within that figure, "
                f"so it is within the declared profile for {who}.")
    if code == "RFI_UPDATED_PROFILE" and f.get("peak_months"):
        return (f"{f.get('doc_title')} ({f.get('doc_id')}), dated {f.get('doc_date')}, declares a seasonal peak in "
                f"{', '.join(f.get('peak_months', []))} but gives no figure to cover the {f.get('amount')} that "
                f"raised {al}. Ask {who} for an updated expected turnover covering the peak period, "
                f"with supporting evidence.")
    if code == "RFI_UPDATED_PROFILE":
        return (f"Statement {f.get('doc_id')} shows {who} holds the other account, but the transfers take activity to "
                f"{f.get('multiple', 0):.1f}x the declared level, at or above the 5x threshold. "
                f"Ask {who} for an updated expected monthly income or volume, with supporting evidence.")
    if verdict == "CLOSE" and f.get("doc_id") and f.get("amount"):
        return (f"{f.get('doc_title')} ({f.get('doc_id')}), dated {f.get('doc_date')}, states {f.get('amount')}, "
                f"matching transaction {f.get('txn_id')} on {f.get('txn_date')} that raised {al}. "
                f"The document explains the activity for {who}.")
    if code == "ESC_LISTED_PARTY":
        return (f"{f.get('doc_title')} ({f.get('doc_id')}) gives a date of birth of {f.get('dob')}, the same as listed "
                f"party {f.get('listed_name')} ({f.get('entry_id')}). The counterparty appears to be the listed party, "
                f"so the case is escalated to the MLRO rather than raised with the customer.")
    if code == "ESC_MULE_ACTIVITY":
        return (f"{f.get('doc_title')} ({f.get('doc_id')}) reports that a sender's customer was scammed, and {who}'s "
                f"account received and passed on the funds that raised {al}. This indicates the account is moving "
                f"other people's money, so it is escalated to the MLRO.")
    if code == "ESC_STRUCTURING":
        return (f"Note {f.get('doc_id')} ({f.get('doc_title')}) records {who} asking about keeping transactions below "
                f"a limit, alongside {al}. That suggests deliberate structuring, so the case is escalated.")
    if verdict == "RFI":
        return (f"Nothing on file explains the activity behind {al} for {who}, and nothing suspicious is recorded. "
                f"Ask {who} for documents showing the source and purpose of the funds ({code}).")
    return (f"The activity behind {al} for {who} has no documented explanation on file. "
            f"It is escalated to the MLRO for review ({code}).")
