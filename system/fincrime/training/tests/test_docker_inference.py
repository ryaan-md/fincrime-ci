import json
import tempfile
import math
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from fincrime.model import Case, EdgeSignal, Flag, Proof
from fincrime.training.bert.infer import fuse


class FusionTests(unittest.TestCase):
    def setUp(self):
        os.environ['FINCRIME_LLM'] = '0'
        self.case = Case('bank', {'case_id':'test', 'customer_id':'person', 'alert_ids':['alert']},
                         {'name':'Test'}, [{'alert_id':'alert','rule_id':'TM01'}], [], {'alert':[]}, [], [], {})
        self.policy = SimpleNamespace(EDGE_DOWNGRADE_RFI='RFI_PURPOSE_DOCUMENTS', RFI_BY_RULE={'TM01':'RFI_PURPOSE_DOCUMENTS'})
        self.rules = {'flags':[], 'proofs':{'alert':Proof('alert',True,'WITHIN_PROFILE')}, 'trace':[]}

    def test_agreement_preserves_proven_close(self):
        d, audit = fuse(self.case,self.policy,self.rules,EdgeSignal(),[.9,.05,.05])
        self.assertEqual(d.verdict,'CLOSE')
        self.assertEqual(audit['bert_verdict'],'CLOSE')

    def test_bert_disagreement_adds_caution(self):
        for p, verdict in [([.1,.8,.1],'RFI'),([.1,.1,.8],'ESCALATE')]:
            with self.subTest(verdict=verdict):
                d,_=fuse(self.case,self.policy,self.rules,EdgeSignal(),p)
                self.assertEqual(d.verdict,verdict)

    def test_classifier_cannot_override_flag_or_create_close(self):
        self.rules['flags']=[Flag('ESC_MULE_ACTIVITY')]
        self.assertEqual(fuse(self.case,self.policy,self.rules,EdgeSignal(),[1,0,0])[0].verdict,'ESCALATE')
        self.rules['flags']=[];self.rules['proofs']={}
        self.assertNotEqual(fuse(self.case,self.policy,self.rules,EdgeSignal(),[1,0,0])[0].verdict,'CLOSE')

    def test_edge_and_incomplete_data_prevent_close(self):
        self.assertEqual(fuse(self.case,self.policy,self.rules,EdgeSignal(.8,True),[1,0,0])[0].verdict,'RFI')
        self.assertEqual(fuse(self.case,self.policy,self.rules,EdgeSignal(),[1,0,0],'missing evidence')[0].verdict,'ESCALATE')

    def test_invalid_probabilities_rejected(self):
        for values in ([math.nan,0,1],[.3,.3,.3],[-.1,.1,1],[1,0]):
            with self.assertRaises(ValueError):fuse(self.case,self.policy,self.rules,EdgeSignal(),values)

class RawInputTests(unittest.TestCase):
    def test_unseen_raw_ids_need_no_labels_or_saved_splits(self):
        from fincrime.training.tests.fixtures import fixture
        from fincrime.training.bert.hybrid_data import model_inputs
        scaler = json.loads((Path(__file__).resolve().parents[1] / 'bert/checkpoint/preprocessing.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            folder, _ = fixture(Path(temp), count=2)
            with model_inputs(folder, scaler) as iterator:
                rows = list(iterator)
            self.assertEqual([key for key, _ in rows], ['case-0', 'case-1'])
            for _, row in rows:
                self.assertEqual(len(row['numeric']), 130)
                self.assertTrue(all(math.isfinite(x) for x in row['numeric']))
                self.assertNotIn('label', row)
                self.assertNotIn('split', row)
                for identifier in ('case-0', 'case-1', 'customer-0', 'customer-1', 'transaction-0', 'account-0'):
                    self.assertNotIn(identifier, row['text'])
                self.assertIn('[ID_', row['text'])

if __name__=='__main__':unittest.main()
