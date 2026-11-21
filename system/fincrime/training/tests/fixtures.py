import json
from fincrime.training.preprocessing.prepare import TABLES

def fixture(root, inst='bank', count=10):
    folder = root / inst
    (folder / 'evidence').mkdir(parents=True)
    (folder / 'POLICY.md').write_text('Example test policy\n', encoding='utf-8')
    tables = {name: [] for name in TABLES}
    for n in range(count):
        c, cs, a, tx, doc = [f'{prefix}{n}' for prefix in ('customer-', 'case-', 'alert-', 'transaction-', 'doc-')]
        tables['customers'].append({'customer_id': c, 'account_ids': [f'account-{n}'], 'kind': 'individual',
            'name': 'Example PERSON', 'risk_rating': 'Low', 'declared': {'monthly_income_gbp': 100,
            'annual_turnover_gbp': None, 'expected_countries': ['United Kingdom']}})
        tables['cases'].append({'case_id': cs, 'institution': inst, 'customer_id': c, 'alert_ids': [a],
            'held_payment_id': None, 'opened_at': '2026-02-01T00:00:00Z',
            'review_window': {'from': '2026-01-01', 'to': '2026-01-31'}})
        tables['alerts'].append({'alert_id': a, 'case_id': cs, 'customer_id': c, 'rule_id': 'TM01',
            'transaction_ids': [tx], 'measured': {'total_gbp': 10.10}})
        tables['transactions'].append({'transaction_id': tx, 'customer_id': c, 'account_id': f'account-{n}',
            'booked_at': '2026-01-02T01:00:00Z', 'amount_gbp': 10.10, 'amount': 10.10,
            'direction': 'in', 'counterparty_name': 'Example LTD.', 'counterparty_country': ' United Kingdom ',
            'counterparty_account': 'MERCHANT', 'channel': 'Bank Transfer'})
        tables['evidence'].append({'doc_id': doc, 'customer_id': c, 'type': 'support_chat',
            'file': f'evidence/{doc}.md', 'dated': '2026-01-02', 'title': 'Chat'})
        (folder / f'evidence/{doc}.md').write_bytes(b'# Chat\r\n\r\n**Amount:** \xc2\xa310.10\r\nOriginal  spacing.\r\n')
        tables['previous_reviews'].append({'review_id': f'review-{n}', 'customer_id': c,
            'verdict': 'CLOSE', 'reviewed_on': '2025-01-01', 'note': 'Earlier activity', 'rule_id': 'TM01'})
    save(folder, tables)
    return folder, tables


def save(folder, tables):
    for table, rows in tables.items():
        (folder / (table + '.jsonl')).write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')


def lines(path):
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]


