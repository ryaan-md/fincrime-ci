"""ModernBERT NF4 LoRA case classifier using anonymized text and numeric preprocessing inputs."""
import argparse,collections,hashlib,json,random,time
from pathlib import Path
import torch
from torch import nn
from transformers import AutoModel,AutoTokenizer,BitsAndBytesConfig
from peft import LoraConfig,get_peft_model,prepare_model_for_kbit_training
from fincrime.training.bert.data import rows,digest
LABELS=['CLOSE','RFI','ESCALATE']

from fincrime.training.bert.model import CaseClassifier

def load_model(path,numeric_dim=0):
 config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.float16)
 encoder=AutoModel.from_pretrained(path,local_files_only=True,quantization_config=config,device_map={'':'mps'},dtype=torch.float16,attn_implementation='sdpa')
 encoder=prepare_model_for_kbit_training(encoder,use_gradient_checkpointing=False)
 depth=encoder.config.num_hidden_layers
 targets=[name for name,module in encoder.named_modules() if name.endswith(('.attn.Wqkv','.attn.Wo')) and any(f'layers.{i}.' in name for i in range(depth-8,depth))]
 if len(targets)!=16:raise ValueError(f'Expected 16 attention projections; found {targets}')
 encoder=get_peft_model(encoder,LoraConfig(r=8,lora_alpha=16,lora_dropout=.05,target_modules=targets,bias='none',task_type='FEATURE_EXTRACTION'))
 model=CaseClassifier(encoder,numeric_dim);model.pool_gate.to('mps');model.head.to('mps')
 if model.numeric_net is not None:model.numeric_net.to('mps')
 return model

def tokenize(data,tokenizer,chunk_size):
 result=[]
 for r in data:
  ids=tokenizer.encode(r['text'],add_special_tokens=False);payload=chunk_size-2;stride=payload-64
  chunks=[]
  for start in range(0,len(ids),stride):
   chunks.append([tokenizer.cls_token_id]+ids[start:start+payload]+[tokenizer.sep_token_id])
   if start+payload>=len(ids):break
  result.append(dict(row_id=r['row_id'],label=LABELS.index(r['label']),institution=r['institution'],chunks=chunks,numeric=r.get('numeric',[])))
 return result

def collate(rs,pad_id):
 chunks=[c for r in rs for c in r['chunks']];owners=[i for i,r in enumerate(rs) for c in r['chunks']];length=max(map(len,chunks))
 ids=torch.full((len(chunks),length),pad_id,dtype=torch.long);mask=torch.zeros_like(ids)
 for i,c in enumerate(chunks):ids[i,:len(c)]=torch.tensor(c);mask[i,:len(c)]=1
 return dict(input_ids=ids.to('mps'),attention_mask=mask.to('mps'),sizes=[len(r['chunks']) for r in rs],ncases=len(rs),numeric=torch.tensor([r.get('numeric',[]) for r in rs],dtype=torch.float32,device='mps')),torch.tensor([r['label'] for r in rs],device='mps')

def metrics(truth,preds,loss):
 from sklearn.metrics import accuracy_score,f1_score,confusion_matrix,classification_report
 cost=sum(0 if t==p else 10 if p==0 else 3 if t==0 or (t==2 and p==1) else 2 for t,p in zip(truth,preds))
 return dict(cases=len(truth),loss=loss,total_cost=cost,wrongful_closes=sum(p==0 and t!=0 for t,p in zip(truth,preds)),accuracy=accuracy_score(truth,preds),macro_f1=f1_score(truth,preds,labels=[0,1,2],average='macro',zero_division=0),confusion_matrix=confusion_matrix(truth,preds,labels=[0,1,2]).tolist(),per_class=classification_report(truth,preds,labels=[0,1,2],target_names=LABELS,output_dict=True,zero_division=0))

@torch.no_grad()
def evaluate(model,data,pad_id,batch=2,output=None):
 model.eval();truth=[];preds=[];loss_sum=0;records=[]
 for offset in range(0,len(data),batch):
  rs=data[offset:offset+batch];inputs,y=collate(rs,pad_id);logits=model(**inputs)
  loss_sum+=nn.functional.cross_entropy(logits,y,reduction='sum').item();prob=logits.softmax(-1).cpu().tolist();pred=logits.argmax(-1).cpu().tolist()
  truth.extend(y.cpu().tolist());preds.extend(pred)
  records.extend(dict(row_id=r['row_id'],truth=LABELS[r['label']],prediction=LABELS[p],probabilities=ps) for r,p,ps in zip(rs,pred,prob))
  if (offset//batch)%25==0:print(json.dumps(dict(event='evaluation_progress',cases=min(offset+batch,len(data)),total=len(data))),flush=True)
  del inputs,y,logits;torch.mps.empty_cache()
 result=metrics(truth,preds,loss_sum/len(data))
 if output:Path(output).write_text(json.dumps(dict(summary=result,predictions=records),indent=2))
 return result

def save(model,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=True);model.encoder.save_pretrained(out/'adapter')
 torch.save({'pool_gate':model.pool_gate.state_dict(),'head':model.head.state_dict(),'numeric_net':model.numeric_net.state_dict() if model.numeric_net is not None else None},out/'classifier.pt')

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',default='dist/bert-hybrid-data');p.add_argument('--model',default='dist/models/modernbert-base');p.add_argument('--output',default='dist/modernbert-hybrid');p.add_argument('--batch-size',type=int,default=2);p.add_argument('--accumulation',type=int,default=8);p.add_argument('--chunk-size',type=int,default=256);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--epochs',type=int,default=3);p.add_argument('--probe',action='store_true');a=p.parse_args()
 torch.manual_seed(42);random.seed(42);torch.set_num_threads(4)
 if not torch.backends.mps.is_available():raise RuntimeError('MPS GPU required')
 out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
 tok=AutoTokenizer.from_pretrained(a.model,local_files_only=True)
 cache=Path(a.data)/f'tokens-{a.chunk_size}.pt'
 key=dict(train=digest(Path(a.data)/'train.jsonl'),validation=digest(Path(a.data)/'validation.jsonl'),tokenizer=digest(Path(a.model)/'tokenizer.json'),chunk_size=a.chunk_size)
 if cache.exists():
  cached=torch.load(cache,weights_only=True)
  if cached['key']!=key:raise ValueError('Token cache provenance mismatch')
  train,valid=cached['train'],cached['validation']
 else:
  train=tokenize(list(rows(Path(a.data)/'train.jsonl')),tok,a.chunk_size);valid=tokenize(list(rows(Path(a.data)/'validation.jsonl')),tok,a.chunk_size)
  torch.save(dict(key=key,train=train,validation=valid),cache)
 subset=[]
 for institution in ('bank','emoney','investment','payments_fx'):
  for label in range(3):subset.extend(sorted([r for r in valid if r['institution']==institution and r['label']==label],key=lambda r:r['row_id'])[:10])
 model=load_model(a.model,len(train[0].get('numeric',[])));base_params=[p for n,p in model.named_parameters() if p.requires_grad and n.startswith('encoder.')];head_params=list(model.pool_gate.parameters())+list(model.head.parameters())+(list(model.numeric_net.parameters()) if model.numeric_net is not None else [])
 optimizer=torch.optim.AdamW([{'params':base_params,'lr':1e-4},{'params':head_params,'lr':1e-3}],weight_decay=.01)
 trainable=sum(p.numel() for p in model.parameters() if p.requires_grad)
 manifest=dict(model=json.loads((Path(a.model)/'source.json').read_text()),data_manifest_sha256=digest(Path(a.data)/'manifest.json'),train_cases=len(train),validation_cases=len(valid),trainable_parameters=trainable,numeric_dim=len(train[0].get('numeric',[])),physical_cases_per_batch=a.batch_size,gradient_accumulation=a.accumulation,effective_batch=a.batch_size*a.accumulation,chunk_size=a.chunk_size,chunk_overlap=64,max_chunks=max(len(r['chunks']) for r in train),mean_chunks=sum(len(r['chunks']) for r in train)/len(train),quantization='NF4 double quantization, FP16 compute',lora_rank=8,lora_alpha=16,lora_dropout=.05,adapted_layers=8,adapter_learning_rate=1e-4,head_learning_rate=1e-3,optimizer='AdamW',weight_decay=.01,seed=42,time_cap_seconds=a.seconds)
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(dict(event='configured',**manifest)),flush=True)
 order=list(train);random.Random(42).shuffle(order)
 if a.probe:
  start=time.monotonic();optimizer.zero_grad(set_to_none=True)
  for i in range(4):
   rs=order[i*a.batch_size:(i+1)*a.batch_size];inputs,y=collate(rs,tok.pad_token_id);t=time.monotonic();loss=nn.functional.cross_entropy(model(**inputs),y);(loss/a.accumulation).backward();torch.mps.synchronize()
   print(json.dumps(dict(event='probe',microbatch=i+1,cases=len(rs),chunks=inputs['input_ids'].shape[0],seconds=time.monotonic()-t,loss=loss.item(),memory_gb=torch.mps.current_allocated_memory()/1e9,driver_memory_gb=torch.mps.driver_allocated_memory()/1e9)),flush=True)
  optimizer.step();print('QLORA_PROBE_PASSED',flush=True);return
 before=evaluate(model,subset[::10],tok.pad_token_id,a.batch_size,out/'baseline.json');print(json.dumps(dict(event='baseline',accuracy=before['accuracy'],macro_f1=before['macro_f1'])),flush=True)
 start=time.monotonic();step=0;seen=0;best=-1;stale=0;durations=[];recent=[];stop=False
 optimizer.zero_grad(set_to_none=True)
 for epoch in range(a.epochs):
  random.Random(42+epoch).shuffle(order)
  for offset in range(0,len(order),a.batch_size*a.accumulation):
   if durations and time.monotonic()-start+max(durations[-5:])+15>=a.seconds:stop=True;break
   group=order[offset:offset+a.batch_size*a.accumulation];model.train();t=time.monotonic();group_loss=0
   warm=min(1.,(step+1)/10)
   optimizer.param_groups[0]['lr']=1e-4*warm;optimizer.param_groups[1]['lr']=1e-3*warm
   for m in range(0,len(group),a.batch_size):
    rs=group[m:m+a.batch_size];inputs,y=collate(rs,tok.pad_token_id);loss=nn.functional.cross_entropy(model(**inputs),y)
    (loss*len(rs)/len(group)).backward();group_loss+=loss.item()*len(rs);del inputs,y,loss
    torch.mps.synchronize();torch.mps.empty_cache()
   torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],1.)
   optimizer.step();optimizer.zero_grad(set_to_none=True);torch.mps.synchronize();torch.mps.empty_cache()
   step+=1;seen+=len(group);durations.append(time.monotonic()-t);recent.append(group_loss/len(group))
   progress=dict(event='train',step=step,epoch=epoch+1,examples_seen=seen,loss=recent[-1],elapsed=time.monotonic()-start,seconds_per_update=durations[-1],memory_gb=torch.mps.current_allocated_memory()/1e9,driver_memory_gb=torch.mps.driver_allocated_memory()/1e9)
   (out/'progress.json').write_text(json.dumps(progress,indent=2));print(json.dumps(progress),flush=True)
   if step==1 or step%5==0:save(model,out/'latest')
   if step%10==0 or offset+len(group)>=len(order):
    val=evaluate(model,subset,tok.pad_token_id,a.batch_size,out/f'validation-step-{step}.json');print(json.dumps(dict(event='validation',step=step,accuracy=val['accuracy'],macro_f1=val['macro_f1'],loss=val['loss'])),flush=True)
    if val['macro_f1']>best+.005:best=val['macro_f1'];stale=0;save(model,out/'best')
    else:stale+=1
    if stale>=3:stop=True;break
  if stop:break
 save(model,out/'final');training_seconds=time.monotonic()-start
 if (out/'best/adapter').exists():
  # Reload only the trainable adapter and classification head into the same frozen base.
  model.encoder.load_adapter(out/'best/adapter',adapter_name='evaluated_best',is_trainable=False);model.encoder.set_adapter('evaluated_best')
  state=torch.load(out/'best/classifier.pt',map_location='mps',weights_only=True);model.pool_gate.load_state_dict(state['pool_gate']);model.head.load_state_dict(state['head'])
  if model.numeric_net is not None:model.numeric_net.load_state_dict(state['numeric_net'])
 final=evaluate(model,valid,tok.pad_token_id,a.batch_size,out/'validation-600.json')
 result=dict(training_seconds=training_seconds,optimizer_steps=step,examples_seen=seen,complete_epochs=seen//len(train),validation=final,notice='Validation agreement with provisional labels; test split was not loaded.');(out/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':main()
