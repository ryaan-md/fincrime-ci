import concurrent.futures,hashlib,json,os,time
from pathlib import Path
from urllib.request import Request,urlopen
repo='answerdotai/ModernBERT-base'
meta=json.load(urlopen(f'https://huggingface.co/api/models/{repo}?blobs=true',timeout=30));revision=meta['sha']
entry=next(r for r in meta['siblings'] if r['rfilename']=='model.safetensors');size=entry.get('size',entry.get('lfs',{}).get('size'))
if size is None:size=598635032
out=Path('dist/models/modernbert-base');out.mkdir(parents=True,exist_ok=True)
part=out/'model.parallel.partial';fd=os.open(part,os.O_CREAT|os.O_RDWR,0o600);os.ftruncate(fd,size)
chunk=4*1024*1024

def fetch(i):
 start=i*chunk;end=min(size,start+chunk)-1
 for attempt in range(5):
  try:
   url=f'https://huggingface.co/{repo}/resolve/{revision}/model.safetensors?download=true&part={i}'
   with urlopen(Request(url,headers={'Range':f'bytes={start}-{end}'}),timeout=90) as r:
    if r.status!=206 or r.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':raise ValueError('Range not honored')
    data=r.read()
   if len(data)!=end-start+1:raise ValueError('Short range')
   os.pwrite(fd,data,start);return len(data)
  except Exception:
   if attempt==4:raise
   time.sleep(2)
try:
 pending=[i for i in range((size+chunk-1)//chunk) if not any(os.pread(fd,min(chunk,size-i*chunk),i*chunk))]
 done=size-sum(min(chunk,size-i*chunk) for i in pending)
 print(json.dumps(dict(resumed_bytes=done,remaining_ranges=len(pending))),flush=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
  for f in concurrent.futures.as_completed([pool.submit(fetch,i) for i in pending]):
   done+=f.result();print(json.dumps(dict(downloaded=done,total=size)),flush=True)
finally:os.close(fd)
h=hashlib.sha256(part.read_bytes()).hexdigest();expected=entry.get('lfs',{}).get('sha256')
if expected and h!=expected:raise ValueError('Weight hash mismatch')
part.replace(out/'model.safetensors')
(out/'source.json').write_text(json.dumps(dict(repo=repo,revision=revision,sha256=h),indent=2));print('Complete',flush=True)
