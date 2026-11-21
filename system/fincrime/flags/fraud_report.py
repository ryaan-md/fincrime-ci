from ..model import Flag

def scan_fraud_report(case, policy):
    return [Flag("ESC_MULE_ACTIVITY", [d.doc_id], {"doc_id": d.doc_id, "doc_title": d.title}, "fraud_report", 5)
            for d in case.docs if d.type == "sending_bank_fraud_report"]
