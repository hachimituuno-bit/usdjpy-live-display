"""JRA pre-race form ranking prioritising a top-three finish.

Read-only: no Blogger posting, betting, odds, or popularity inputs.
Scores are heuristic ranking scores, NOT calibrated probabilities.
"""
import argparse
import json
from datetime import date, datetime
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rank(entries, history):
    race_date = date.fromisoformat(entries['race_date'])
    if entries.get('posting') not in (None, False):
        raise ValueError('Posting must be disabled')
    if not isinstance(entries.get('horses'), list) or len(entries['horses']) < 2:
        raise ValueError('Official entries are missing or incomplete')
    if history.get('posting') is not False or not isinstance(history.get('horses'), list):
        raise ValueError('Invalid history payload')
    if date.fromisoformat(history['target_date']) > race_date:
        raise ValueError('History snapshot is after race date')

    by_id = {}
    for horse in history['horses']:
        cid = horse.get('identity_candidate')
        if not cid or cid in by_id:
            raise ValueError('Missing or duplicate history identity')
        by_id[cid] = horse

    seen_numbers, seen_ids, ranked = set(), set(), []
    for entry in entries['horses']:
        number, name, cid = entry['number'], entry['name'], entry['identity_candidate']
        if type(number) is not int or number < 1 or not name or not cid:
            raise ValueError('Invalid entrant')
        if number in seen_numbers or cid in seen_ids:
            raise ValueError('Duplicate entrant')
        seen_numbers.add(number)
        seen_ids.add(cid)
        horse = by_id.get(cid)
        if horse is None or horse.get('name') != name:
            raise ValueError(f'{name}: matching horse history not available; do not guess')
        runs = horse.get('runs', [])
        if not isinstance(runs, list):
            raise ValueError(f'{name}: invalid history')

        eligible = sorted(
            (r for r in runs if date.fromisoformat(r['date']) < race_date),
            key=lambda r: r['date'], reverse=True
        )[:5]
        if not eligible:
            raise ValueError(f'{name}: no verified pre-race historical runs')

        weighted_top3 = weighted_finish = weight_sum = 0.0
        valid_count = top3_count = 0
        for i, run in enumerate(eligible):
            finish, field = run.get('finish'), run.get('field_size')
            status = run.get('finish_status', 'finished' if type(finish) is int else None)
            if status in ('excluded', 'scratched', 'did_not_finish', 'disqualified'):
                if finish is not None:
                    raise ValueError(f'{name}: invalid non-finish')
                continue
            if status != 'finished' or type(field) is not int or type(finish) is not int or not (1 <= finish <= field):
                raise ValueError(f'{name}: invalid finish data')

            weight = 0.8 ** i
            # Actual top-three finishes are the primary signal.
            weighted_top3 += (1.0 if finish <= 3 else 0.0) * weight
            # Relative finishing position only breaks ties / adds stability context.
            relative_finish = 1.0 if field == 1 else (field - finish) / (field - 1)
            weighted_finish += relative_finish * weight
            weight_sum += weight
            valid_count += 1
            top3_count += int(finish <= 3)

        if weight_sum:
            top3_form = weighted_top3 / weight_sum
            finish_form = weighted_finish / weight_sum
            # 85% top-three record, 15% relative finish. Heuristic, not a probability.
            score = round(100.0 * (0.85 * top3_form + 0.15 * finish_form), 2)
        else:
            top3_form = None
            score = None
        ranked.append({
            'horse_number': number, 'horse_name': name, 'identity_candidate': cid,
            'form_score': score, 'completed_runs': valid_count,
            'top3_finishes': top3_count,
            'recent_top3_rate': round(top3_form, 4) if top3_form is not None else None,
            'identity_verified': bool(horse.get('identity_verified', False)),
        })

    ranked.sort(key=lambda h: (
        h['form_score'] is None, -(h['form_score'] or 0),
        -(h['completed_runs']), h['horse_number']
    ))
    for i, h in enumerate(ranked, 1):
        h['rank'] = i
    return {
        'race_date': race_date.isoformat(), 'venue': entries['venue'],
        'race_number': entries['race_number'], 'race_name': entries['race_name'],
        'generated_at': datetime.now().astimezone().isoformat(), 'posting': False,
        'method': 'Top-three form priority: 85% recency-weighted top-3 finishes + 15% relative finish; weights 0.8^index; no odds',
        'warning': 'Heuristic form score, NOT calibrated top-3 probability; race-specific conditions not adjusted; verify entrants and identities',
        'horses': ranked,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--entries', required=True, help='Official confirmed race entries JSON')
    p.add_argument('--history', required=True, help='Historical runs JSON for the same horses')
    p.add_argument('--output', default='keiba_next_race_ranking.json')
    a = p.parse_args()
    result = rank(read_json(a.entries), read_json(a.history))
    Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"{result['race_date']} {result['venue']} {result['race_number']}R {result['race_name']}")
    for h in result['horses']:
        print(f"{h['rank']:>2}. {h['horse_number']:>2} {h['horse_name']} score={h['form_score']} runs={h['completed_runs']} top3={h['top3_finishes']}")
    print('COMPLETE / NO POSTING / TOP-THREE FORM PRIORITY')


if __name__ == '__main__':
    main()
