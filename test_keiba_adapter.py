import unittest
import json
import copy
from pathlib import Path
from keiba_convert import convert, InputError
from keiba_predict import validate, render

EX = Path(__file__).resolve().parent.parent / 'examples/raw_synthetic_for_adapter.json'

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(EX.read_text(encoding='utf-8'))
    def test_end_to_end_demo(self):
        obj = convert(self.raw, '2026-11-07', demo=True)
        venue, races = validate(obj, '2026-11-07', demo=True)
        self.assertEqual(venue, '架空東京')
        self.assertEqual([r['race_number'] for r in races], [10,11,12])
        self.assertIn('公開禁止', render(obj, venue, races, demo=True))
        self.assertTrue(all(0 <= h['recent_form_rating'] <= 100 for race in races for h in race['horses']))
    def test_reject_demo_as_real(self):
        with self.assertRaises(InputError):
            convert(self.raw, '2026-11-07', demo=False)
    def test_reject_future_result(self):
        raw = copy.deepcopy(self.raw)
        raw['races'][0]['horses'][0]['recent_runs'][0]['date'] = '2026-11-07'
        with self.assertRaises(InputError):
            convert(raw, '2026-11-07', demo=True)
    def test_reject_missing_history(self):
        raw = copy.deepcopy(self.raw)
        raw['races'][0]['horses'][0]['recent_runs'] = []
        with self.assertRaises(InputError):
            convert(raw, '2026-11-07', demo=True)
    def test_reject_fake_permission(self):
        raw = copy.deepcopy(self.raw)
        raw['synthetic_demo'] = False
        with self.assertRaises(InputError):
            convert(raw, '2026-11-07', demo=False)
    def test_reject_wrong_date(self):
        with self.assertRaises(InputError):
            convert(self.raw, '2026-11-08', demo=True)

if __name__ == '__main__':
    unittest.main()
