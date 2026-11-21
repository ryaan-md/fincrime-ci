"""Shared text/numeric classification architecture; no training-only dependencies."""
import torch
from torch import nn

class CaseClassifier(nn.Module):
 def __init__(self,encoder,numeric_dim=0):
  super().__init__();self.encoder=encoder;h=encoder.config.hidden_size
  self.pool_gate=nn.Linear(h,1);nn.init.zeros_(self.pool_gate.weight);nn.init.zeros_(self.pool_gate.bias)
  self.numeric_net=nn.Sequential(nn.Linear(numeric_dim,128),nn.GELU(),nn.Dropout(.1),nn.Linear(128,128),nn.GELU()) if numeric_dim else None
  self.head=nn.Sequential(nn.Linear(h+(128 if numeric_dim else 0),256),nn.GELU(),nn.Dropout(.1),nn.Linear(256,3)) if numeric_dim else nn.Sequential(nn.Dropout(.1),nn.Linear(h,3))
 def forward(self,input_ids,attention_mask,sizes,ncases,numeric=None):
  h=self.encoder(input_ids=input_ids,attention_mask=attention_mask).last_hidden_state.float()
  mask=attention_mask.unsqueeze(-1);chunks=(h*mask).sum(1)/mask.sum(1).clamp_min(1)
  pooled=[];start=0
  for size in sizes:
   values=chunks[start:start+size];start+=size;weights=torch.softmax(self.pool_gate(values).squeeze(-1),dim=0)
   pooled.append((values*weights.unsqueeze(-1)).sum(0))
  combined=torch.stack(pooled)
  if self.numeric_net is not None:combined=torch.cat([combined,self.numeric_net(numeric)],dim=-1)
  return self.head(combined)

