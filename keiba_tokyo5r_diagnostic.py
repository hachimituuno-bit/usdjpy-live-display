"""Manual, read-only audit of the 2026-11-07 Tokyo 5R ranking reversal.
No Blogger posting, no odds, no modification of existing files.
"""
import json
from datetime import date
from pathlib import Path

import requests
from keiba_jra_result_fetch import extract
from keiba_historical_backtest import historical_entries
from keiba_next_collect import collect
from keiba_next_race import rank
from keiba_compare_test import experimental_rank

RACE_ID = 'tokyo_20261107_05r'
TARGET_NAMES = ('タイセイフレッサ', 'ソレエスピアージャ')
OUTPUT = 'keiba_tokyo5r_diagnostic.json'


def breakdown(runs, race_day, surface, distance):
    eligible = sorted((r for r in runs if date.fromisoformat(r['date']) < race_day),
                      key=lambda r: r['date'], reverse=True)[:5]
    rows = []
    for i, r in enumerate(eligible):
        finish, field = r.get('finish'), r.get('field_size')
        status = r.get('finish_status', 'finished' if type(finish) is int else None)
        w = 0.8 ** i
        item = {'date': r['date'], 'surface': r.get('surface'), 'distance_m': r.get('distance_m'),
                'finish': finish, 'field_size': field, 'finish_status': status,
                'recency_weight': round(w, 6)}
        if status == 'finished' and type(finish) is int and type(field) is int and 1 <= finish <= field:
            base = .85 * (finish <= 3) + .15 * (1 if field == 1 else (field - finish) / (field - 1))
            sf = 1.0 if r.get('surface') == surface else .8
            d = r.get('distance_m')
            if type(d) is not int:
                raise ValueError(f'Missing distance for {r["date"]}')
            df = 1.0 - .2 * min(abs(d - distance) / 800, 1)
            item.update({'base_points': round(100 * base, 5), 'surface_factor': sf,
                         'distance_factor': round(df, 6),
                         'after_surface_points': round(100 * base * sf, 5),
                         'after_both_points': round(100 * base * sf * df, 5),
                         'weighted_base': w * base,
                         'weighted_after_surface': w * base * sf,
                         'weighted_after_both': w * base * sf * df})
        rows.append(item)
    valid = [x for x in rows if 'weighted_base' in x]
    if not valid:
        return {'runs': rows, 'scores': None}
    weight_sum = sum(x['recency_weight'] for x in valid)
    n = len(valid)
    def score(key):
        raw = 100 * sum(x[key] for x in valid) / weight_sum
        return round((n * raw + (5-n) * 50) / 5, 4)
    return {'runs': rows, 'scores': {
        'baseline_no_penalties': score('weighted_base'),
        'surface_penalty_only': score('weighted_after_surface'),
        'surface_and_distance_penalties': score('weighted_after_both'),
        'completed_runs': n}}


def main():
    races = json.loads(Path('keiba_races.json').read_text(encoding='utf-8'))
    matches = [r for r in races if r['id'] == RACE_ID]
    if len(matches) != 1:
        raise ValueError('Target race missing or duplicated')
    race = matches[0]
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    response = session.get(race['url'], timeout=25)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    actual = extract(race['url'], response.text)
    if actual['date'] != race['date']:
        raise ValueError('Race date mismatch')
    entries = historical_entries(response.text, race['url'], actual)
    history = collect(entries, session=session, delay=0.4)
    baseline = rank(entries, history)['horses']
    experimental = experimental_rank(entries, history, race['surface'], race['distance_m'])
    horses = {}
    for name in TARGET_NAMES:
        selected = [e for e in entries['horses'] if e['name'] == name]
        if len(selected) != 1:
            raise ValueError(f'Expected exactly one entry for {name}')
        entry = selected[0]
        hist = [h for h in history['horses'] if h['identity_candidate'] == entry['identity_candidate']]
        if len(hist) != 1:
            raise ValueError(f'History identity mismatch for {name}')
        b = next(x for x in baseline if x['horse_number'] == entry['number'])
        e = next(x for x in experimental if x['number'] == entry['number'])
        audit = breakdown(hist[0]['runs'], date.fromisoformat(race['date']), race['surface'], race['distance_m'])
        if audit['scores'] is not None and round(audit['scores']['surface_and_distance_penalties'], 2) != e['score']:
            raise ValueError(f'Score audit does not match for {name}: {audit["scores"]} vs {e["score"]}')
        horses[name] = {'horse_number': entry['number'], 'baseline_rank': b['rank'],
                        'baseline_score': b['form_score'], 'experimental_rank': e['rank'],
                        'experimental_score': e['score'], **audit}
    result = {'posting': False, 'race_id': RACE_ID, 'race_date': race['date'],
              'target_surface': race['surface'], 'target_distance_m': race['distance_m'],
              'horses': horses,
              'note': 'Retrospective audit. Penalty factors are unvalidated hypotheses; not calibrated probabilities.'}
    Path(OUTPUT).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, h in horses.items():
        print(name, 'baseline:', h['baseline_rank'], h['baseline_score'],
              'experimental:', h['experimental_rank'], h['experimental_score'],
              'audit:', h['scores'], flush=True)
    print('COMPLETE / NO POSTING / ' + OUTPUT, flush=True)


if __name__ == '__main__':
    main()
