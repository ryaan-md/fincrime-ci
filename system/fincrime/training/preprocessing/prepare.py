#!/usr/bin/env python3
"""Offline, standard-library dataset preparation. No labels or model calls."""
import argparse
import collections
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

VERSION = '1'
TABLES = {'cases': 'case_id', 'alerts': 'alert_id', 'customers': 'customer_id',
          'transactions': 'transaction_id', 'evidence': 'doc_id',
          'previous_reviews': 'review_id', 'internal_list': 'entry_id'}
INSTITUTIONS = ('bank', 'emoney', 'investment', 'payments_fx')
SPLITS = ('train', 'validation', 'test')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def key(inst, identifier):
    return inst + ':' + identifier


def norm_text(value):
    if value is None:
        return None
    return ' '.join(unicodedata.normalize('NFKC', str(value)).casefold().split())


def norm_name(value):
    value = norm_text(value)
    return re.sub(r'[^\w\s]', '', value) if value is not None else None


def norm_account(value):
    return re.sub(r'\s+', '', str(value)).upper() if value else None


def decimal_text(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(',', '').replace('£', '').strip())
        if not number.is_finite():
            return None
        result = format(number, 'f')
        return result.rstrip('0').rstrip('.') if '.' in result else result
    except (InvalidOperation, ValueError):
        return None


def parse_day(value):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def normalise(obj, field=''):
    if isinstance(obj, dict):
        return {k: normalise(v, k) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [normalise(v, {'expected_countries': 'expected_country', 'account_ids': 'account_id', 'expected_channels': 'channel'}.get(field, field)) for v in obj]
    if obj is None:
        return None
    if field in ('amount', 'amount_gbp') or field.endswith('_gbp'):
        return decimal_text(obj)
    if field in ('account_id', 'counterparty_account'):
        return norm_account(obj)
    if field in ('name', 'counterparty_name'):
        return norm_name(obj)
    if field.endswith('_at'):
        try:
            stamp = datetime.fromisoformat(str(obj).replace('Z', '+00:00'))
            return stamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if stamp.tzinfo else None
        except ValueError:
            return None
    if field in ('dated', 'from', 'to', 'date_of_birth', 'incorporated_on', 'onboarded_on', 'reviewed_on', 'added_on'):
        return parse_day(obj)
    if isinstance(obj, str) and (field in ('direction', 'channel', 'kind', 'risk_rating', 'currency') or 'country' in field or field == 'expected_country'):
        return norm_text(obj)
    if isinstance(obj, Decimal):
        return decimal_text(obj)
    return obj


class InputError(ValueError):
    pass


class Dataset:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.tables = {}
        self.documents = {}
        self.policies = {}
        self.hashes = {}
        self.issues = []

    def issue(self, severity, code, record, detail):
        self.issues.append(dict(severity=severity, code=code, record=record, detail=detail))

    def read(self, path):
        data = path.read_bytes()
        self.hashes[path.relative_to(self.root).as_posix()] = digest(data)
        return data.decode('utf-8')

    def load(self):
        folders = [self.root] if (self.root / 'cases.jsonl').is_file() else [self.root / i for i in INSTITUTIONS if (self.root / i).is_dir()]
        if not folders:
            raise InputError('No institution folders found')
        for folder in folders:
            inst = folder.name
            if inst not in INSTITUTIONS:
                raise InputError('Unknown institution: ' + inst)
            self.policies[inst] = self.read(folder / 'POLICY.md')
            tables = self.tables[inst] = {}
            for table, id_field in TABLES.items():
                rows = tables[table] = {}
                for line_number, line in enumerate(self.read(folder / (table + '.jsonl')).splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
                        exact = json.loads(line, parse_float=Decimal)
                        identifier = raw.get(id_field) if isinstance(raw, dict) else None
                        if not isinstance(identifier, str) or not identifier:
                            raise ValueError('missing string ' + id_field)
                    except (ValueError, TypeError) as exc:
                        raise InputError(f'{inst}/{table}:{line_number}: {exc}') from exc
                    if identifier in rows:
                        raise InputError(f'Duplicate {inst}/{table} ID: {identifier}')
                    rows[identifier] = {'key': key(inst, identifier), 'raw': raw, 'raw_json': line, 'normalized': normalise(exact)}
            self.load_documents(inst, folder)
            self.validate(inst)
        errors = [i for i in self.issues if i['severity'] == 'error']
        if errors:
            raise InputError(f'{len(errors)} integrity errors; first: {canonical(errors[0])}')
        return self

    def load_documents(self, inst, folder):
        for identifier, record in sorted(self.tables[inst]['evidence'].items()):
            relative = record['raw'].get('file')
            if not isinstance(relative, str):
                self.issue('error', 'document_path_missing', record['key'], '')
                continue
            path = (folder / relative).resolve()
            if not path.is_relative_to(folder.resolve()):
                self.issue('error', 'unsafe_document_path', record['key'], relative)
                continue
            if not path.is_file():
                self.issue('error', 'document_missing', record['key'], relative)
                continue
            text = self.read(path)
            fields = []
            for line_number, line in enumerate(text.splitlines(), 1):
                match = re.match(r'^\s*\*\*([^*]+):\*\*\s*(.*)$', line)
                if match:
                    fields.append({'line': line_number, 'raw_label': match[1], 'raw_value': match[2],
                                   'normalized_label': norm_text(match[1])})
            self.documents[record['key']] = dict(record, raw_text=text,
                normalized_text=text.replace('\r\n', '\n').replace('\r', '\n'),
                sha256=digest(text.encode('utf-8')), markdown_fields=fields)

    def validate(self, inst):
        t = self.tables[inst]
        for table in ('cases', 'alerts', 'transactions', 'evidence', 'previous_reviews'):
            for record in t[table].values():
                r = record['raw']
                if r.get('customer_id') not in t['customers']:
                    self.issue('error', 'customer_missing', record['key'], str(r.get('customer_id')))
        for record in t['cases'].values():
            r = record['raw']; window = r.get('review_window', {})
            lo, hi = parse_day(window.get('from')), parse_day(window.get('to'))
            if not lo or not hi or lo > hi:
                self.issue('error', 'invalid_window', record['key'], str(window))
            if r.get('institution') != inst:
                self.issue('error', 'institution_mismatch', record['key'], str(r.get('institution')))
            ids = r.get('alert_ids')
            if not isinstance(ids, list) or not ids or any(not isinstance(a, str) for a in ids):
                self.issue('error', 'invalid_alert_ids', record['key'], str(ids)); continue
            if len(ids) != len(set(ids)):
                self.issue('error', 'duplicate_case_alert', record['key'], '')
            for aid in ids:
                alert = t['alerts'].get(aid, {}).get('raw', {})
                if alert.get('case_id') != r['case_id'] or alert.get('customer_id') != r['customer_id']:
                    self.issue('error', 'alert_join_mismatch', record['key'], aid)
            held = r.get('held_payment_id')
            if held and t['transactions'].get(held, {}).get('raw', {}).get('customer_id') != r['customer_id']:
                self.issue('error', 'held_payment_mismatch', record['key'], held)
        for record in t['alerts'].values():
            r = record['raw']; case = t['cases'].get(r.get('case_id'), {}).get('raw', {})
            if r['alert_id'] not in case.get('alert_ids', []):
                self.issue('error', 'unlisted_alert', record['key'], str(r.get('case_id')))
            ids = r.get('transaction_ids')
            if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
                self.issue('error', 'invalid_transaction_ids', record['key'], str(ids)); continue
            for tid in ids:
                if t['transactions'].get(tid, {}).get('raw', {}).get('customer_id') != r['customer_id']:
                    self.issue('error', 'transaction_join_mismatch', record['key'], tid)
        for record in t['transactions'].values():
            r = record['normalized']
            if not r.get('booked_at') or r.get('amount_gbp') is None or r.get('direction') not in ('in', 'out'):
                self.issue('error', 'invalid_transaction', record['key'], 'date, amount_gbp or direction')
            if not r.get('account_id'):
                self.issue('error', 'missing_account', record['key'], '')
        for table, field in [('evidence', 'dated'), ('previous_reviews', 'reviewed_on')]:
            for record in t[table].values():
                if record['normalized'].get(field) is None:
                    self.issue('warning', 'invalid_or_missing_date', record['key'], field)


def grouped_index(rows, field):
    result = collections.defaultdict(list)
    for record in sorted(rows.values(), key=lambda r: r['key']):
        result[record['raw'].get(field)].append(record)
    return result


def case_features(inst, case, customer, alerts, txns, docs, reviews):
    lo, hi = case['normalized']['review_window']['from'], case['normalized']['review_window']['to']
    current = [r['normalized'] for r in txns if lo <= parse_day(r['normalized']['booked_at']) <= hi]
    by_direction = {d: [r for r in current if r['direction'] == d] for d in ('in', 'out')}
    result = {'institution': inst, 'customer_kind': customer['normalized'].get('kind'),
              'risk_rating': customer['normalized'].get('risk_rating'),
              'alert_count': len(alerts), 'transaction_count': len(current),
              'historical_transaction_count': sum(parse_day(r['normalized']['booked_at']) < lo for r in txns),
              'post_window_transaction_count': sum(parse_day(r['normalized']['booked_at']) > hi for r in txns),
              'document_count': len(docs), 'previous_review_count': len(reviews),
              'held_payment_present': bool(case['raw'].get('held_payment_id')),
              'active_days': len({parse_day(r['booked_at']) for r in current})}
    for direction, rows in by_direction.items():
        amounts = [Decimal(r['amount_gbp']) for r in rows]
        parties = {r.get('counterparty_account') or r.get('counterparty_name') for r in rows}
        parties.discard(None); parties.discard('')
        result[direction + '_count'] = len(rows)
        result[direction + '_total_gbp'] = decimal_text(sum(amounts, Decimal(0)))
        result[direction + '_max_gbp'] = decimal_text(max(amounts)) if amounts else None
        result[direction + '_counterparty_count'] = len(parties)
        result[direction + '_missing_counterparty_count'] = sum(not (r.get('counterparty_account') or r.get('counterparty_name')) for r in rows)
    declared = customer['normalized'].get('declared') or {}
    monthly = None
    denominator_source = None
    candidates = [('monthly_income_gbp', 1)] if customer['normalized'].get('kind') == 'individual' else [('monthly_turnover_gbp', 1), ('annual_turnover_gbp', 12)]
    if inst == 'payments_fx':
        candidates = [('annual_turnover_gbp', 12)]
    for field, divisor in candidates:
        value = decimal_text(declared.get(field))
        if value is not None and Decimal(value) > 0:
            monthly = Decimal(value) / divisor; denominator_source = field; break
    result['declared_monthly_basis_gbp'] = decimal_text(monthly)
    result['declared_basis_source'] = denominator_source
    result['declared_basis_missing'] = monthly is None
    result['inflow_to_declared_ratio'] = decimal_text(Decimal(result['in_total_gbp']) / monthly) if monthly else None
    result['outflow_to_inflow_ratio'] = decimal_text(Decimal(result['out_total_gbp']) / Decimal(result['in_total_gbp'])) if Decimal(result['in_total_gbp']) > 0 else None
    result['profile_alert_threshold_ratio'] = '3' if customer['normalized'].get('kind') == 'individual' else '2'
    result['inflow_to_profile_threshold_ratio'] = decimal_text(Decimal(result['inflow_to_declared_ratio']) / Decimal(result['profile_alert_threshold_ratio'])) if monthly else None
    amounts = sorted(Decimal(r['amount_gbp']) for r in current)
    n = len(amounts)
    result['median_transaction_gbp'] = decimal_text((amounts[(n - 1) // 2] + amounts[n // 2]) / 2) if n else None
    result['mean_transaction_gbp'] = decimal_text(sum(amounts) / n) if n else None
    channels = collections.Counter(r.get('channel') for r in current)
    result['missing_channel_count'] = channels.get(None, 0) + channels.get('', 0)
    for channel in ('card', 'faster_payment', 'swift', 'direct_debit', 'bacs', 'wallet_transfer', 'cash_withdrawal', 'fund_subscription', 'standing_order', 'cash_deposit', 'cash_topup', 'fund_redemption'):
        # Exact normalised source category only; no semantic channel guesses.
        result['channel_count_' + channel] = channels[channel]
        result['channel_total_gbp_' + channel] = decimal_text(sum((Decimal(r['amount_gbp']) for r in current if r.get('channel') == channel), Decimal(0)))
    historical = [parse_day(r['normalized']['booked_at']) for r in txns if parse_day(r['normalized']['booked_at']) < lo]
    result['days_since_last_observed_prior_transaction'] = (date.fromisoformat(lo) - date.fromisoformat(max(historical))).days if historical else None
    result['prior_history_absent'] = not historical
    result['counterparty_country_count'] = len({r['counterparty_country'] for r in current if r.get('counterparty_country')})
    result['missing_counterparty_country_count'] = sum(not r.get('counterparty_country') for r in current)
    expected = set(declared.get('expected_countries') or [])
    result['undeclared_country_transaction_count'] = sum(bool(r.get('counterparty_country')) and r['counterparty_country'] not in expected for r in current) if expected else None
    result['documents_after_window_count'] = sum(bool(r['normalized'].get('dated')) and r['normalized']['dated'] > hi for r in docs)
    result['reviews_after_window_count'] = sum(bool(r['normalized'].get('reviewed_on')) and r['normalized']['reviewed_on'] > hi for r in reviews)
    result['document_missing_date_count'] = sum(r['normalized'].get('dated') is None for r in docs)
    for rule in ('TM01', 'TM02', 'TM03', 'TM04', 'TM05', 'TM06', 'TM07', 'TM08', 'TM10', 'TM22'):
        result['alert_count_' + rule] = sum(a['raw'].get('rule_id') == rule for a in alerts)
    # Fixed categories; no vocabulary is fitted from validation/test records.
    for kind in ('support_chat', 'invoice', 'bank_statement', 'contact_note', 'sending_bank_fraud_report', 'due_diligence_summary'):
        result['document_count_' + kind] = sum(d['raw'].get('type') == kind for d in docs)
    return result


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, node):
        self.parent.setdefault(node, node)
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != node:
            node, self.parent[node] = self.parent[node], root
        return root

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        self.parent[max(a, b)] = min(a, b)


def split_cases(dataset, bundles, ratios, seed):
    uf = UnionFind(); owners = collections.defaultdict(set)
    for inst, tables in sorted(dataset.tables.items()):
        for cid, record in sorted(tables['customers'].items()):
            customer = key(inst, cid); uf.find(customer)
            for account in record['raw'].get('account_ids') or []:
                if norm_account(account):
                    owners[norm_account(account)].add(customer)
        for record in tables['transactions'].values():
            r = record['normalized']; owners[r['account_id']].add(key(inst, r['customer_id']))
    for customers in owners.values():
        members = sorted(customers)
        for member in members[1:]:
            uf.union(members[0], member)
    # Connect transactions to another known account owner. Shared merchants alone do not link customers.
    for inst, tables in sorted(dataset.tables.items()):
        for record in tables['transactions'].values():
            r = record['normalized']
            for owner in sorted(owners.get(r.get('counterparty_account'), ())):
                uf.union(key(inst, r['customer_id']), owner)
    groups = collections.defaultdict(list)
    for bundle in bundles:
        groups[uf.find(bundle['customer_key'])].append(bundle)
    totals = collections.Counter(b['institution'] for b in bundles)
    assigned = {s: collections.Counter() for s in SPLITS}
    assignments = {}; group_rows = []
    order = sorted(groups, key=lambda g: (-len(groups[g]), digest((seed + '\0' + g).encode())))
    for root in order:
        members = sorted(groups[root], key=lambda b: b['case_key'])
        counts = collections.Counter(b['institution'] for b in members)
        def cost(split):
            ratio = ratios[SPLITS.index(split)]
            if ratio == 0:
                return Decimal('Infinity')
            return sum(((Decimal(assigned[split][i] + count) - Decimal(totals[i]) * ratio) ** 2 -
                        (Decimal(assigned[split][i]) - Decimal(totals[i]) * ratio) ** 2) /
                       (Decimal(totals[i]) * ratio) for i, count in counts.items())
        chosen = min(SPLITS, key=lambda s: (cost(s), SPLITS.index(s)))
        assigned[chosen].update(counts)
        gid = digest(canonical(sorted(m['case_key'] for m in members)).encode())
        group_rows.append({'group_id': gid, 'split': chosen, 'case_keys': [m['case_key'] for m in members],
                           'customer_keys': sorted({m['customer_key'] for m in members})})
        for member in members:
            assignments[member['case_key']] = {'split': chosen, 'group_id': gid}
    return {'seed': seed, 'ratios': dict(zip(SPLITS, map(str, ratios))), 'assignments': assignments,
            'groups': sorted(group_rows, key=lambda g: g['group_id']),
            'counts': {s: dict(sorted(assigned[s].items())) for s in SPLITS},
            'largest_group_cases': max(map(len, groups.values()), default=0)}


def prepare(source, output, ratios=(Decimal('.7'), Decimal('.15'), Decimal('.15')), seed='fincrime-v1'):
    source, output = Path(source).resolve(), Path(output).resolve()
    ratios = tuple(Decimal(str(r)) for r in ratios)
    if len(ratios) != 3 or any(not r.is_finite() or r < 0 for r in ratios) or sum(ratios) != 1:
        raise InputError('Three finite nonnegative ratios must sum to 1')
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        raise InputError('Input and output directories must not overlap')
    if output.exists() and any(output.iterdir()):
        raise InputError('Output must be absent or empty; use a new directory')
    dataset = Dataset(source)
    try:
        dataset.load()
    except InputError:
        output.mkdir(parents=True, exist_ok=True)
        (output / 'quality_report.json').write_text(canonical({'status': 'failed', 'issues': sorted(dataset.issues, key=canonical)}) + '\n', encoding='utf-8')
        raise
    bundles = []; feature_rows = []; links = []
    with localcontext() as context:
        context.prec = 28
        for inst, tables in sorted(dataset.tables.items()):
            transactions = grouped_index(tables['transactions'], 'customer_id')
            documents = grouped_index(tables['evidence'], 'customer_id')
            reviews = grouped_index(tables['previous_reviews'], 'customer_id')
            # Candidates by exact normalised name, including all non-empty names. These are not matches.
            list_names = collections.defaultdict(list)
            for entry in tables['internal_list'].values():
                name = norm_name(entry['raw'].get('name'))
                if name:
                    list_names[name].append(entry['key'])
            for cid, case in sorted(tables['cases'].items()):
                raw = case['raw']; customer_id = raw['customer_id']; customer = tables['customers'][customer_id]
                alerts = [tables['alerts'][aid] for aid in sorted(raw['alert_ids'])]
                txns = sorted(transactions[customer_id], key=lambda r: (r['normalized']['booked_at'], r['key']))
                docs = documents[customer_id]; prev = reviews[customer_id]
                lo, hi = case['normalized']['review_window']['from'], case['normalized']['review_window']['to']
                referenced = {tid for a in alerts for tid in a['raw']['transaction_ids']}
                time_views = {'review_window': [], 'historical': [], 'after_window': [], 'alert_linked': []}
                candidates = set()
                for txn in txns:
                    day = parse_day(txn['normalized']['booked_at'])
                    view = 'historical' if day < lo else 'after_window' if day > hi else 'review_window'
                    time_views[view].append(txn['key'])
                    if txn['raw']['transaction_id'] in referenced:
                        time_views['alert_linked'].append(txn['key'])
                    candidates.update(list_names.get(norm_name(txn['raw'].get('counterparty_name')), ()))
                for doc in docs:
                    links.append({'case_key': case['key'], 'document_key': doc['key']})
                    if doc['normalized'].get('dated') and doc['normalized']['dated'] > hi:
                        dataset.issue('warning', 'document_after_window', case['key'], doc['key'])
                if parse_day(raw.get('opened_at')) and parse_day(raw['opened_at']) < hi:
                    dataset.issue('warning', 'opened_before_window_end', case['key'], raw['opened_at'])
                bundles.append({'case_key': case['key'], 'institution': inst, 'customer_key': customer['key'],
                    'case': case, 'customer': customer, 'alerts': alerts,
                    'transaction_keys': [r['key'] for r in txns], 'time_views': time_views,
                    'document_keys': [d['key'] for d in docs], 'previous_reviews': prev,
                    'held_payment_key': key(inst, raw['held_payment_id']) if raw.get('held_payment_id') else None,
                    'internal_list_candidate_keys': sorted(candidates),
                    'policy_key': inst, 'policy_sha256': digest(dataset.policies[inst].encode())})
                feature_rows.append(dict(case_features(inst, case, customer, alerts, txns, docs, prev), case_key=case['key']))
    with localcontext() as context:
        context.prec = 28
        splits = split_cases(dataset, bundles, ratios, seed)
    output.mkdir(parents=True, exist_ok=True)
    output_hashes = {}
    def write(name, text):
        data = text.encode('utf-8'); (output / name).write_bytes(data); output_hashes[name] = digest(data)
    def write_json(name, value):
        write(name, canonical(value) + '\n')
    def write_lines(name, values):
        checksum = hashlib.sha256()
        with (output / name).open('wb') as stream:
            for value in values:
                data = (canonical(value) + '\n').encode('utf-8')
                stream.write(data)
                checksum.update(data)
        output_hashes[name] = checksum.hexdigest()
    write_lines('case_bundles.jsonl', sorted(bundles, key=lambda b: b['case_key']))
    write_lines('documents.jsonl', [dataset.documents[k] for k in sorted(dataset.documents)])
    write_lines('case_documents.jsonl', sorted(links, key=lambda l: (l['case_key'], l['document_key'])))
    for table in TABLES:
        write_lines(table + '_records.jsonl', [r for inst, tables in sorted(dataset.tables.items()) for _, r in sorted(tables[table].items())])
    write_lines('policies.jsonl', [{'institution': i, 'raw_text': p, 'sha256': digest(p.encode())} for i, p in sorted(dataset.policies.items())])
    feature_rows.sort(key=lambda r: r['case_key'])
    write_lines('alert_features.jsonl', [
        {'alert_key': record['key'], 'case_key': key(inst, record['raw']['case_id']),
         'rule_id': record['raw'].get('rule_id'), 'measured_raw': record['raw'].get('measured'),
         'measured_normalized': record['normalized'].get('measured'),
         'transaction_keys': sorted({key(inst, tid) for tid in record['raw']['transaction_ids']})}
        for inst, tables in sorted(dataset.tables.items()) for _, record in sorted(tables['alerts'].items())])
    write_lines('case_features.jsonl', feature_rows)
    buf = io.StringIO(newline=''); columns = sorted({k for row in feature_rows for k in row})
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator='\n'); writer.writeheader(); writer.writerows(feature_rows)
    write('case_features.csv', buf.getvalue())
    write_json('splits.json', splits)
    for split in SPLITS:
        write_lines(split + '_index.jsonl', [{'case_key': k, **v} for k, v in sorted(splits['assignments'].items()) if v['split'] == split])
    schema = {c: {'role': 'join_key_not_feature' if c == 'case_key' else 'feature',
                  'representation': sorted({type(r[c]).__name__ for r in feature_rows if r[c] is not None}),
                  'nullable': any(r[c] is None for r in feature_rows)} for c in columns}
    write_json('feature_schema.json', {'version': VERSION, 'columns': schema,
        'money_and_ratios': 'Exact decimal strings; null is unknown, never implicitly zero.',
        'csv_null': 'Empty cell; JSONL is authoritative for types.'})
    write_json('quality_report.json', {'status': 'ok', 'counts': {i: {t: len(r) for t, r in ts.items()} for i, ts in sorted(dataset.tables.items())},
        'issues': sorted(dataset.issues, key=canonical), 'case_count': len(bundles),
        'document_count': len(dataset.documents), 'largest_split_group': splits['largest_group_cases']})
    write_json('preprocessing_manifest.json', {'schema_version': VERSION, 'source_files': dict(sorted(dataset.hashes.items())),
        'code_sha256': digest(Path(__file__).read_bytes()), 'python_version': sys.version.split()[0],
        'unicode_version': unicodedata.unidata_version, 'decimal_precision': 28,
        'split_seed': seed, 'ratios': list(map(str, ratios)), 'output_sha256': dict(sorted(output_hashes.items())),
        'current_case_labels_included': False})
    return {'cases': len(bundles), 'documents': len(dataset.documents), 'splits': splits['counts'], 'warnings': len(dataset.issues)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='institutions/ or one institution folder')
    parser.add_argument('--output', required=True, type=Path, help='new or empty output directory outside input')
    parser.add_argument('--ratios', nargs=3, default=['0.7', '0.15', '0.15'])
    parser.add_argument('--seed', default='fincrime-v1', help='stable hash salt; no random generator')
    args = parser.parse_args()
    try:
        print(canonical(prepare(args.input, args.output, args.ratios, args.seed)))
    except (InputError, OSError, InvalidOperation) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
