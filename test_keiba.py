import copy
import json
import tempfile
import unittest
from pathlib import Path
from keiba_predict import validate, render, DataError, main

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads((ROOT / 'examples/demo_synthetic.json').read_text(encoding='utf-8'))

class KeibaTests(unittest.TestCase):
    def test_selects_g2_over_g3(self):
        venue, races = validate(SAMPLE, '2026-11-07', demo=True)
        self.assertEqual(venue, '架空東京')
        self.assertEqual([r['race_number'] for r in races], [10,11,12])
        self.assertIn('公開禁止', render(SAMPLE, venue, races, demo=True))

    def test_rejects_unlicensed_real_data(self):
        payload=copy.deepcopy(SAMPLE)
        payload['synthetic_demo']=False
        with self.assertRaises(DataError):
            validate(payload, '2026-11-07')

    def test_rejects_results_after_start(self):
        payload=copy.deepcopy(SAMPLE)
        payload['data_asof']='2026-11-07T17:00:00+09:00'
        with self.assertRaises(DataError):
            validate(payload, '2026-11-07', demo=True)

    def test_rejects_duplicate_runner(self):
        payload=copy.deepcopy(SAMPLE)
        payload['races'][0]['horses'][1]['number']=1
        with self.assertRaises(DataError):
            validate(payload, '2026-11-07', demo=True)

    def test_rejects_missing_12r(self):
        payload=copy.deepcopy(SAMPLE)
        payload['races']=[r for r in payload['races'] if r['race_number']!=12]
        with self.assertRaises(DataError):
            validate(payload, '2026-11-07', demo=True)

    def test_rejects_no_mid_odds(self):
        payload=copy.deepcopy(SAMPLE)
        for r in payload['races']:
            for h in r['horses']:
                h['win_odds']=30.0
        with self.assertRaises(DataError):
            validate(payload, '2026-11-07', demo=True)

    def test_missing_real_file_skips_without_output(self):
        with tempfile.TemporaryDirectory() as d:
            rv = main(['--date','2026-11-07','--data-dir',d,'--out-dir',str(Path(d)/'out')])
            self.assertEqual(rv,0)
            self.assertFalse((Path(d)/'out').exists())

if __name__=='__main__':
    unittest.main()
