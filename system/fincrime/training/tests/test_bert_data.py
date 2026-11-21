import unittest,json
from fincrime.training.bert.data import anonymize
class BertDataTests(unittest.TestCase):
 def test_ids_replaced_consistently(self):
  x={'case_id':'BK-CS123','customer_id':'BK-C234','account_id':'BK-A991','name':'taylor smith','amount_gbp':'1250.00','dated':'2026-01-01','text':'Taylor Smith paid BK-C234 from BK-A991 on 2026-01-01.'}
  a,_=anonymize(x);s=json.dumps(a)
  self.assertNotIn('BK-',s);self.assertNotIn('Smith',s);self.assertIn(a['customer_id'],a['text']);self.assertIn(a['name'],a['text']);self.assertEqual(a['amount_gbp'],'1250.00');self.assertEqual(a['dated'],'2026-01-01')
 def test_aliases_reset_and_keep_mismatch(self):
  a,_=anonymize({'customer_id':'BK-C123','text':'BK-C999 is different from BK-C123'})
  b,_=anonymize({'customer_id':'EM-C444','text':'EM-C555 is different from EM-C444'})
  self.assertEqual(a,b);self.assertIn('[ID_2]',a['text'])
 def test_document_account_number(self):
  a,_=anonymize({'text':'**Account number:** 12345678\nTransfer to 12345678 for £450.00'})
  self.assertNotIn('12345678',a['text']);self.assertIn('450.00',a['text'])
if __name__=='__main__':unittest.main()
