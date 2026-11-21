"""Anonymized inputs from the user's versioned preprocessing snapshot."""
import argparse,collections,hashlib,json,re
from pathlib import Path

ID_PATTERN=re.compile(r'\b(?:[A-Z]{2,8}-[A-Z]{0,4}\d+[A-Z0-9-]*|[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){10,30})\b',re.I)
ID_FIELDS={'id','key','case_key','customer_key','group_id','counterparty_account','account_number','account','iban','sort_code','company_number','registration_number','national_id','passport_number','phone','email','file'}
NAME_FIELDS={'name','counterparty_name','employer'}
LABEL_ID=re.compile(r'(?:\bid\b|\bidentifier\b|\baccount(?: number)?\b|\bsort code\b|\biban\b|\bcompany number\b|\bregistration\b|\binvoice (?:number|no|reference)\b|\breference\b)',re.I)

def rows(path):
 with Path(path).open() as f:
  for line in f:yield json.loads(line)
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def compact(x):return json.dumps(x,ensure_ascii=False,separators=(',',':'))


def anonymize(record):
 """Keep within-case equality links, reset aliases for each case, preserve amounts/dates."""
 ids={};names={}
 def register(value,kind):
  if not isinstance(value,str) or not value.strip():return
  value=value.strip().casefold();mapping=names if kind=='ENTITY' else ids
  if value not in mapping:mapping[value]=f'[{kind}_{len(mapping)+1}]'
 def collect(x,key=''):
  if isinstance(x,dict):
   for k,v in x.items():collect(v,k)
  elif isinstance(x,list):
   for v in x:collect(v,key)
  elif isinstance(x,str):
   if key in ID_FIELDS or key.endswith(('_id','_ids')):register(x,'ID')
   if key in NAME_FIELDS:register(x,'ENTITY')
   for match in ID_PATTERN.finditer(x):register(match.group(),'ID')
   for line in x.splitlines():
    m=re.match(r'\s*\*\*([^*]+):\*\*\s*(.+)',line)
    if m and LABEL_ID.search(m[1]) and any(c.isdigit() for c in m[2]):register(m[2],'ID')
 collect(record)
 # Match the original values in one pass; replacements cannot be replaced again.
 mapping={**names,**ids};keys=sorted(mapping,key=lambda x:(-len(x),x))
 pattern=re.compile('|'.join(r'(?<![\w])'+re.escape(k)+r'(?![\w])' for k in keys),re.I) if keys else None
 def replace(x):
  if isinstance(x,dict):return {k:replace(v) for k,v in x.items()}
  if isinstance(x,list):return [replace(v) for v in x]
  if isinstance(x,str):return pattern.sub(lambda m:mapping[m[0].casefold()],x) if pattern else x
  return x
 result=replace(record);text=compact(result)
 if ID_PATTERN.search(text):raise ValueError('Identifier pattern survived anonymization')
 if pattern and pattern.search(text):raise ValueError('Known identifier or entity survived anonymization')
 return result,{'id_aliases':len(ids),'entity_aliases':len(names)}


def table(rs):
 keys=sorted({k for r in rs for k in r})
 return {'columns':keys,'rows':[[r.get(k) for k in keys] for r in rs],
         'present':[[k for k in keys if k in r] for r in rs]} if any(set(r)!=set(keys) for r in rs) else {'columns':keys,'rows':[[r[k] for k in keys] for r in rs]}


def load_indices(source):
 docs={r['key']:r for r in rows(source/'documents.jsonl')}
 txs={r['key']:r['normalized'] for r in rows(source/'transactions_records.jsonl')}
 lists={r['key']:r['normalized'] for r in rows(source/'internal_list_records.jsonl')}
 features={r['case_key']:r for r in rows(source/'case_features.jsonl')}
 return docs,txs,lists,features


def case_input(b,docs,txs,lists,features):
 alerts=[dict(a['normalized']) for a in b['alerts']]
 list_keys=set(b['internal_list_candidate_keys']);alert_text=compact(alerts)
 list_keys.update(k for k in lists if k.startswith(b['institution']+':') and k.split(':',1)[1] in alert_text)
 # Target annotation, checker, candidate and execution results never enter inputs.
 record=dict(institution=b['institution'],case=b['case']['normalized'],customer=b['customer']['normalized'],
  alerts=alerts,features={k:v for k,v in features[b['case_key']].items() if k!='case_key'},
  documents=[{'metadata':docs[k]['normalized'],'text':docs[k]['normalized_text']} for k in b['document_keys']],
  transactions=[txs[k] for k in b['transaction_keys']],previous_reviews=[r['normalized'] for r in b['previous_reviews']],internal_list=[lists[k] for k in sorted(list_keys)])
 # Eliminate current technical rule IDs; meaningful rule names and measured values stay.
 for alert in record['alerts']:alert.pop('rule_id',None)
 record['features']={k:v for k,v in record['features'].items() if not k.startswith('alert_count_TM')}
 anon,stats=anonymize(record);anon['transactions']=table(anon['transactions']);text=compact(anon)
 return dict(row_id=hashlib.sha256(b['case_key'].encode()).hexdigest()[:16], institution=b['institution'], text=text), stats


def prepare(source,output,labels_path):
 source=Path(source);out=Path(output);out.mkdir(parents=True,exist_ok=False)
 manifest=json.loads((source/'preprocessing_manifest.json').read_text())
 for name,expected in manifest['output_sha256'].items():
  if digest(source/name)!=expected:raise ValueError('Preprocessed artifact hash mismatch: '+name)
 indices=load_indices(source)
 splits={r['case_key']:r['split'] for s in ('train','validation','test') for r in rows(source/(s+'_index.jsonl'))}
 labels={r['case_key']:r for r in rows(labels_path)}
 counts=collections.Counter();audit=[];handles={s:(out/(s+'.jsonl')).open('w') for s in ('train','validation','test')}
 try:
  for b in rows(source/'case_bundles.jsonl'):
   key=b['case_key'];label=labels[key];split=splits[key]
   if label['split']!=split:raise ValueError('Preprocessing/label split mismatch: '+key)
   verdict=label['annotation']['verdict']
   if verdict is None:counts['withheld:'+split]+=1;continue
   row,stats=case_input(b,*indices)
   rid=row['row_id'];text=row['text']
   handles[split].write(compact(dict(row_id=rid,split=split,institution=b['institution'],label=verdict,text=text))+'\n')
   audit.append(dict(row_id=rid,**stats));counts[split+':'+verdict]+=1
 finally:
  for h in handles.values():h.close()
 result=dict(source=str(source.resolve()),source_manifest_sha256=digest(source/'preprocessing_manifest.json'),labels_sha256=digest(labels_path),counts=dict(counts),
  inputs='Normalized preprocessing records, full normalized document text and numeric case features; target is verdict only.',
  anonymization='Within-case identifier and entity aliases, reset per case; rule IDs removed but rule names retained. Audit row hashes and split labels never enter model text.',
  source_files={p.name:digest(p) for p in source.glob('*.jsonl')},outputs={p.name:digest(p) for p in out.glob('*.jsonl')},audit=audit)
 (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(counts=dict(counts),source=result['source'])),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser()
 p.add_argument('--source',required=True,help='Prepared dataset directory')
 p.add_argument('--labels',required=True,help='Versioned training annotations, never needed for inference')
 p.add_argument('--output',default='dist/bert-anonymized-v3')
 a=p.parse_args();prepare(a.source,a.output,a.labels)
