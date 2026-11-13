"""Manual, read-only comparison of existing JRA ranking and experimental condition-aware ranking.
No publishing. Experimental parameters are hypotheses, not validated improvements.
Run from the existing repository root, with its normal dependencies installed.
"""
import argparse
import json
from pathlib import Path
from datetime import date
import requests
from bs4 import BeautifulSoup
from keiba_jra_result_fetch import extract
from keiba_historical_backtest import historical_entries, metrics
from keiba_next_collect import collect
from keiba_next_race import rank


def experimental_rank(entries, history, target_surface, target_distance):
    race_day = date.fromisoformat(entries['race_date'])
    by_id = {h['identity_candidate']: h for h in history['horses']}
    ranked = []
    for e in entries['horses']:
        h = by_id[e['identity_candidate']]
        runs = sorted((r for r in h['runs'] if date.fromisoformat(r['date']) < race_day),
                      key=lambda r: r['date'], reverse=True)[:5]
        total = weighted = completed = 0.0
        for i, r in enumerate(runs):
            if r.get('finish_status') != 'finished':
                continue
            finish, field = r['finish'], r['field_size']
            if type(finish) is not int or type(field) is not int or not (1 <= finish <= field):
                raise ValueError('Invalid historical finish')
            base = 0.85 * (finish <= 3) + 0.15 * (1 if field == 1 else (field - finish) / (field - 1))
            # Explicit test assumptions: 20% penalty for mismatched surface,
            # up to 20% penalty for distances differing by >= 800m.
            surface_factor = 1.0 if r.get('surface') == target_surface else 0.8
            distance = r.get('distance_m')
            if type(distance) is not int:
                raise ValueError('Missing historical distance')
            distance_factor = 1.0 - 0.2 * min(abs(distance - target_distance) / 800, 1)
            w = 0.8 ** i
            weighted += w * base * surface_factor * distance_factor
            total += w
            completed += 1
        if not total:
            score = None
        else:
            # Shrink small samples towards a neutral 50-point baseline.
            # This is a test assumption, not a statistically calibrated prior.
            raw = 100 * weighted / total
            score = round((completed * raw + (5 - completed) * 50) / 5, 2)
        ranked.append({'number': e['number'], 'name': e['name'], 'score': score, 'completed_runs': int(completed)})
    ranked.sort(key=lambda x: (x['score'] is None, -(x['score'] or 0), -x['completed_runs'], x['number']))
    for i, h in enumerate(ranked, 1):
        h['rank'] = i
    return ranked


def evaluate(ranking, actual):
    truth = {h['number']: h['finish'] for h in actual['horses']}
    comparisons = [{'rank': h['rank'], 'number': h['number'], 'name': h['name'],
                    'actual_finish': truth[h['number']]} for h in ranking]
    return metrics(comparisons), comparisons


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--races-file', default='keiba_races.json')
    p.add_argument('--output', default='keiba_comparison_results.json')
    args = p.parse_args()
    races = json.loads(Path(args.races_file).read_text(encoding='utf-8'))
    if not isinstance(races, list) or not races:
        raise ValueError('Nonempty race list required')
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    results = []
    for race in races:
        if race.get('surface') not in ('turf', 'dirt') or type(race.get('distance_m')) is not int:
            raise ValueError(f"Race {race.get('id')} needs explicit surface and distance_m")
        print('COMPARING', race['id'], flush=True)
        try:
            response = session.get(race['url'], timeout=25)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            actual = extract(race['url'], response.text)
            if actual['date'] != race['date']:
                raise ValueError('Race date mismatch')
            entries = historical_entries(response.text, race['url'], actual)
            history = collect(entries, session=session, delay=0.4)
            original = rank(entries, history)['horses']
            baseline = [{'rank': h['rank'], 'number': h['horse_number'], 'name': h['horse_name']} for h in original]
            improved = experimental_rank(entries, history, race['surface'], race['distance_m'])
            base_metrics, base_rows = evaluate(baseline, actual)
            test_metrics, test_rows = evaluate(improved, actual)
            results.append({'race_id': race['id'], 'status': 'SUCCESS',
                            'baseline': {'metrics': base_metrics, 'ranking': base_rows},
                            'experimental': {'metrics': test_metrics, 'ranking': test_rows},
                            'experimental_scores': improved})
            print('SUCCESS', race['id'], flush=True)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            results.append({'race_id': race.get('id'), 'status': 'FAILED', 'reason': str(exc)})
            print('FAILED', race.get('id'), str(exc), flush=True)
    successful = [r for r in results if r['status'] == 'SUCCESS']
    totals = {}
    for label in ('baseline', 'experimental'):
        totals[label] = {key: sum(r[label]['metrics'][key] for r in successful)
                         for key in ('top1_win', 'top1_top3', 'top3_top3_horses', 'top3_slots', 'winner_in_top3')}
    output = {'posting': False, 'races_requested': len(races), 'races_succeeded': len(successful),
              'races_failed': len(races) - len(successful), 'totals': totals, 'races': results,
              'warning': 'Retrospective entrant selection. Experimental coefficients are unvalidated and this sample is too small to establish improvement.'}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('TOTALS', json.dumps(totals, ensure_ascii=False), flush=True)
    if len(successful) != len(races):
        raise SystemExit('Comparison incomplete; inspect failed races')


if __name__ == '__main__':
    main()
