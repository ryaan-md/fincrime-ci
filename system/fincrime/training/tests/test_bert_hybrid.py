import types,unittest
try:
 import torch
 from torch import nn
 from fincrime.training.bert.model import CaseClassifier
except ImportError:
 torch=None;nn=None;CaseClassifier=None
class Encoder(nn.Module if nn else object):
 def __init__(self):
  super().__init__();self.config=types.SimpleNamespace(hidden_size=4);self.embedding=nn.Embedding(10,4)
 def forward(self,input_ids,attention_mask):return types.SimpleNamespace(last_hidden_state=self.embedding(input_ids))
@unittest.skipIf(torch is None, 'Optional PyTorch training dependencies are not installed')
class HybridTests(unittest.TestCase):
 def test_accumulation_matches_batch_and_both_branches_learn(self):
  torch.manual_seed(42);m=CaseClassifier(Encoder(),2).eval()
  x=torch.tensor([[1,2],[3,4],[5,6],[7,8]]);mask=torch.ones_like(x);n=torch.tensor([[.2,.4],[-.3,.5]]);y=torch.tensor([0,2])
  loss=nn.functional.cross_entropy(m(x,mask,[2,2],2,n),y);loss.backward()
  expected={k:p.grad.clone() for k,p in m.named_parameters()}
  self.assertGreater(expected['numeric_net.0.weight'].abs().sum().item(),0)
  self.assertGreater(expected['encoder.embedding.weight'].abs().sum().item(),0)
  m.zero_grad()
  for i in range(2):
   logits=m(x[2*i:2*i+2],mask[2*i:2*i+2],[2],1,n[i:i+1]);(nn.functional.cross_entropy(logits,y[i:i+1])/2).backward()
  for k,p in m.named_parameters():self.assertTrue(torch.allclose(p.grad,expected[k],atol=1e-6),k)
if __name__=='__main__':unittest.main()
