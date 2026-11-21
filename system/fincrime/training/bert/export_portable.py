"""Export the trained NF4 base plus selected LoRA delta into portable FP32 weights."""
import argparse, copy, hashlib, json, shutil
from pathlib import Path
import torch
from transformers import AutoModel, AutoTokenizer
from bitsandbytes.functional import dequantize_4bit
from fincrime.training.bert.train import load_model, collate, tokenize
from fincrime.training.bert.model import CaseClassifier


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',default='dist/models/modernbert-base')
    p.add_argument('--checkpoint',default=str(Path(__file__).with_name('checkpoint')))
    p.add_argument('--output',default='dist/portable-hybrid')
    p.add_argument('--validation',default='dist/bert-hybrid-data/validation.jsonl')
    a=p.parse_args(); out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    cp=Path(a.checkpoint);scaler=json.loads((cp/'preprocessing.json').read_text())
    torch.set_num_threads(4)
    model=load_model(a.base,scaler['numeric_dim'])
    model.encoder.load_adapter(cp/'adapter',adapter_name='selected',is_trainable=False)
    model.encoder.set_adapter('selected')
    state=torch.load(cp/'classifier.pt',map_location='mps',weights_only=True)
    for name in ('pool_gate','numeric_net','head'):getattr(model,name).load_state_dict(state[name])
    model.eval();tok=AutoTokenizer.from_pretrained(a.base,local_files_only=True)
    samples=[]
    for line in Path(a.validation).open():
        r=json.loads(line)
        if sum(x['label']==r['label'] for x in samples)<4:samples.append(r)
        if len(samples)==12:break
    encoded=tokenize(samples,tok,256);reference=[]
    with torch.inference_mode():
        for row in encoded:
            x,_=collate([row],tok.pad_token_id);reference.append(model(**x).cpu())
            del x;torch.mps.empty_cache()
    original=model.encoder.get_base_model()
    config=copy.deepcopy(original.config)
    if hasattr(config,'quantization_config'):del config.quantization_config
    config._attn_implementation='sdpa'
    portable=AutoModel.from_config(config).float().eval()
    with torch.no_grad():
        for name,param in portable.named_parameters():
            parent,field=name.rsplit('.',1);source=original.get_submodule(parent)
            base=source.get_base_layer() if hasattr(source,'get_base_layer') else source
            value=getattr(base,field)
            if field=='weight' and getattr(value,'quant_state',None) is not None:
                value=dequantize_4bit(value.data,value.quant_state)
            value=value.detach().to('cpu',dtype=torch.float32)
            if field=='weight' and hasattr(source,'get_delta_weight'):
                value=value+source.get_delta_weight('selected').detach().cpu().float()
            param.copy_(value)
    portable.save_pretrained(out/'encoder',safe_serialization=True);tok.save_pretrained(out/'encoder')
    cpu=CaseClassifier(portable,scaler['numeric_dim']).eval()
    for name in ('pool_gate','numeric_net','head'):getattr(cpu,name).load_state_dict({k:v.cpu() for k,v in state[name].items()})
    torch.save({name:getattr(cpu,name).state_dict() for name in ('pool_gate','numeric_net','head')},out/'classifier.pt')
    for name in ('preprocessing.json','labels.json','training.json','base-source.json'):
        shutil.copy2(cp/name,out/name)
    for name in ('LICENSE','README.md'):shutil.copy2(Path(a.base)/name,out/('BASE_'+name))
    actual=[]
    with torch.inference_mode():
        for row in encoded:
            x,_=collate([row],tok.pad_token_id)
            x={k:v.cpu() if isinstance(v,torch.Tensor) else v for k,v in x.items()}
            actual.append(cpu(**x))
    ref=torch.cat(reference);got=torch.cat(actual)
    result={'cases':12,'max_probability_difference':(ref.softmax(-1)-got.softmax(-1)).abs().max().item(),'matching_predictions':int((ref.argmax(-1)==got.argmax(-1)).sum()),'export':'dequantized NF4 base + selected LoRA delta, FP32 CPU'}
    (out/'parity.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    if result['matching_predictions']!=12 or result['max_probability_difference']>.02:raise RuntimeError('Portable parity check failed')
    checks={str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob('*') if f.is_file()}
    (out/'checksums.json').write_text(json.dumps(checks,indent=2))

if __name__=='__main__':main()
