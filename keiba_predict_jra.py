"""Manual-only JRA historical-form ranking (not a validated win probability).

Uses the existing multi-horse collector, or a previously collected JSON file.
Does not publish, email, trade, or modify other automations.
"""
import argparse
import json
import math
import subprocess
import sys
from datetime import date
from pathlib import Path


def evaluate(data):
    target = date.fromisoformat(data['target_date'])
    horses = data['horses']
    if len(horses) != 11 or data.get('horse_count') != 11:
        raise ValueError('Expected exactly 11 horses')
    if data.get('posting') is not False:
        raise ValueError('Input must have posting=false')
    names, ids = set(), set()
    ranked = []
    for horse in horses:
        name = horse['name']
        cid = horse['identity_candidate']
        if name in names or cid in ids:
            raise ValueError('Duplicate horse name or identity candidate')
        names.add(name); ids.add(cid)
        runs = horse['runs']
        if not 1 <= len(runs) <= 5:
            raise ValueError(f'{name}: invalid run count')
        last_date = None
        weighted, weight_sum, excluded = 0.0, 0.0, 0
        for index, run in enumerate(runs):
            run_date = date.fromisoformat(run['date'])
            if run_date >= target or (last_date and run_date > last_date):
                raise ValueError(f'{name}: invalid date order or future result')
            last_date = run_date
            field = run['field_size']
            finish = run['finish']
            status = run.get('finish_status', 'finished' if isinstance(finish, int) else None)
            if not isinstance(field, int) or field < 1:
                raise ValueError(f'{name}: invalid field size')
            if status == 'finished':
                if not isinstance(finish, int) or not 1 <= finish <= field:
                    raise ValueError(f'{name}: invalid finish')
                # 100 points for winning, 0 for last place (single-run field=1 -> 100)
                value = 100.0 if field == 1 else 100.0 * (field - finish) / (field - 1)
                weight = 0.8 ** index
                weighted += value * weight
                weight_sum += weight
            elif status in ('excluded', 'scratched', 'did_not_finish', 'disqualified'):
                if finish is not None:
                    raise ValueError(f'{name}: special status must have null finish')
                excluded += 1
            else:
                raise ValueError(f'{name}: unsupported finish status {status}')
        score = round(weighted / weight_sum, 2) if weight_sum else None
        ranked.append({'horse_name': name, 'horse_number': horse['number'],
                       'form_score': score, 'completed_runs': len(runs) - excluded,
                       'special_status_runs': excluded, 'identity_verified': False})
    ranked.sort(key=lambda h: (h['form_score'] is None, -(h['form_score'] or 0), h['horse_number']))
    for n, horse in enumerate(ranked, 1):
        horse['rank'] = n
    return {'target_date': target.isoformat(), 'horse_count': len(ranked),
            'method': 'recency-weighted normalized finish percentile; weights 0.8^index',
            'meaning': 'Historical finish ranking only; NOT calibrated odds or win probability',
            'limitations': ['Horse IDs and JRA source rows not independently verified',
                            'Does not use odds, pace, corners, class, jockey or track suitability',
                            'Target race results may be included if target_date is after that race'],
            'posting': False, 'horses': ranked}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='keiba_multi_horse_test.json')
    parser.add_argument('--fetch', action='store_true', help='Run existing multi-horse collector first')
    parser.add_argument('--target-date', default='2026-11-10')
    parser.add_argument('--output', default='keiba_form_ranking.json')
    args = parser.parse_args()
    if args.fetch:
        subprocess.run([sys.executable, 'keiba_jra_multi_horse_test.py',
                        '--target-date', args.target_date, '--max-runs', '5',
                        '--output', args.input], check=True)
    data = json.loads(Path(args.input).read_text(encoding='utf-8'))
    if data['target_date'] != args.target_date:
        raise ValueError('Input target date mismatch')
    result = evaluate(data)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Historical form ranking (NOT a verified prediction):')
    for h in result['horses']:
        print(f"{h['rank']:>2}. {h['horse_name']}: {h['form_score']} / 100 ({h['completed_runs']} completed)")
    print('COMPLETE: 11 horses / no posting')


if __name__ == '__main__':
    main()
