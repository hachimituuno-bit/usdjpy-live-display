"""Manual-only retrospective surface-penalty sensitivity test; never publishes.

Compares 0%, 5%, 10%, 15%, 20% mismatched-surface penalties while
holding all other experimental settings fixed. No odds/popularity inputs.
"""
import argparse
import json
from datetime import date
from pathlib import Path

import requests

from keiba_jra_result_fetch import extract
from keiba_historical_backtest import historical_entries, metrics
from keiba_next_collect import collect
from keiba_compare_test import experimental_rank

PENALTIES = (0, 5, 10, 15, 20)
METRIC_KEYS = ('top1_win', 'top1_top3', 'top3_top3_horses', 'top3_slots', 'winner_in_top3')


def rank_with_surface_penalty(entries, history, surface, distance, penalty_percent):
    race_day = date.fromisoformat(entries['race_date'])
    by_id = {h['identity_candidate']: h for h in history['horses']}
    ranked = []
    for e in entries['horses']:
        runs = sorted((r for r in by_id[e['identity_candidate']]['runs']
                       if date.fromisoformat(r['date']) < race_day),
                      key=lambda r: r['date'], reverse=True)[:5]
        weighted = total = 0.0
        completed = 0
        for i, r in enumerate(runs):
            if r.get('finish_status') != 'finished':
                continue
            finish, field = r['finish'], r['field_size']
            if type(finish) is not int or type(field) is not int or not (1 <= finish <= field):
                raise ValueError('Invalid historical finish')
            past_distance = r.get('distance_m')
            if type(past_distance) is not int:
                raise ValueError('Missing historical distance')
            base = .85 * (finish <= 3) + .15 * (1 if field == 1 else (field - finish) / (field - 1))
            sf = 1.0 if r.get('surface') == surface else 1.0 - penalty_percent / 100
            df = 1.0 - .2 * min(abs(past_distance - distance) / 800, 1)
            w = .8 ** i
            weighted += w * base * sf * df
            total += w
            completed += 1
        score = None
        if total:
            raw = 100 * weighted / total
            score = round((completed * raw + (5 - completed) * 50) / 5, 2)
        ranked.append({'number': e['number'], 'name': e['name'], 'score': score,
                       'completed_runs': completed})
    ranked.sort(key=lambda x: (x['score'] is None, -(x['score'] or 0),
                               -x['completed_runs'], x['number']))
    for i, horse in enumerate(ranked, 1):
        horse['rank'] = i
    return ranked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--races-file', default='keiba_races.json')
    parser.add_argument('--output', default='keiba_surface_sensitivity_results.json')
    args = parser.parse_args()
    races = json.loads(Path(args.races_file).read_text(encoding='utf-8'))
    if not isinstance(races, list) or not races:
        raise ValueError('Nonempty race list required')
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    results = []
    for race in races:
        print('TESTING', race['id'], flush=True)
        try:
            if race.get('surface') not in ('turf', 'dirt') or type(race.get('distance_m')) is not int:
                raise ValueError('Race needs explicit surface and distance_m')
            response = session.get(race['url'], timeout=25)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            actual = extract(race['url'], response.text)
            if actual['date'] != race['date']:
                raise ValueError('Race date mismatch')
            entries = historical_entries(response.text, race['url'], actual)
            history = collect(entries, session=session, delay=0.4)
            truth = {h['number']: h['finish'] for h in actual['horses']}
            tests = {}
            for p in PENALTIES:
                ranking = rank_with_surface_penalty(entries, history, race['surface'], race['distance_m'], p)
                rows = [{'rank': h['rank'], 'number': h['number'], 'name': h['name'],
                         'actual_finish': truth[h['number']]} for h in ranking]
                tests[str(p)] = {'metrics': metrics(rows), 'top3': rows[:3]}
            # At 20%, reproduce the existing experimental rank exactly.
            reference = experimental_rank(entries, history, race['surface'], race['distance_m'])
            checked = rank_with_surface_penalty(entries, history, race['surface'], race['distance_m'], 20)
            if [(h['number'], h['score']) for h in checked] != [(h['number'], h['score']) for h in reference]:
                raise ValueError('20% reference check failed')
            results.append({'race_id': race['id'], 'status': 'SUCCESS', 'tests': tests})
            print('SUCCESS', race['id'], flush=True)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            results.append({'race_id': race.get('id'), 'status': 'FAILED', 'reason': str(exc)})
            print('FAILED', race.get('id'), str(exc), flush=True)
    succeeded = [r for r in results if r['status'] == 'SUCCESS']
    totals = {str(p): {key: sum(r['tests'][str(p)]['metrics'][key] for r in succeeded)
                       for key in METRIC_KEYS} for p in PENALTIES}
    output = {'posting': False, 'races_requested': len(races),
              'races_succeeded': len(succeeded), 'races_failed': len(races) - len(succeeded),
              'surface_penalty_percent': list(PENALTIES),
              'fixed_distance_penalty_max_percent': 20,
              'totals': totals, 'races': results,
              'warning': 'Retrospective, small-sample sensitivity analysis; do not select a production parameter solely from these results.'}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('TOTALS', json.dumps(totals, ensure_ascii=False), flush=True)
    print('NO POSTING', flush=True)
    if len(succeeded) != len(races):
        raise SystemExit('Incomplete: inspect failed races')


if __name__ == '__main__':
    main()
