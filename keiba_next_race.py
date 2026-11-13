"""JRA pre-race historical form ranking. Manual, no posting, no betting.

Inputs:
  entries JSON: {"race_date":"2026-11-14","venue":"東京","race_number":11,
                 "race_name":"武蔵野ステークス","horses":[{"number":1,"name":"...","identity_candidate":"..."},...]}
  history JSON: existing keiba_multi_horse_test.json shape, but collected for matching entrants.

The program never assumes a previous race's entrants are the next race's entrants.
"""
import argparse
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse


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
    seen_numbers, seen_ids = set(), set()
    ranked = []
    for entry in entries['horses']:
        number, name = entry['number'], entry['name']
        cid = entry.get('identity_candidate')
        if cid is None and entry.get('horse_url'):
            parsed = urlparse(entry['horse_url'])
            if parsed.scheme != 'https' or parsed.hostname != 'www.jra.go.jp' or parsed.path != '/JRADB/accessU.html':
                raise ValueError(f'{name}: invalid JRA horse URL')
            candidates = parse_qs(parsed.query).get('CNAME', [])
            if len(candidates) != 1:
                raise ValueError(f'{name}: invalid JRA CNAME')
            cid = candidates[0]
        if type(number) is not int or number < 1 or not name or not cid:
            raise ValueError('Invalid entrant')
        if number in seen_numbers or cid in seen_ids:
            raise ValueError('Duplicate entrant')
        seen_numbers.add(number); seen_ids.add(cid)
        horse = by_id.get(cid)
        if horse is None or horse.get('name') != name:
            raise ValueError(f'{name}: matching horse history not available; do not guess')
        runs = horse.get('runs', [])
        if not isinstance(runs, list):
            raise ValueError(f'{name}: invalid history')
        # Sort by actual date, never rely on source ordering.
        eligible = sorted((r for r in runs if date.fromisoformat(r['date']) < race_date),
                          key=lambda r: r['date'], reverse=True)[:5]
        if not eligible:
            raise ValueError(f'{name}: no verified pre-race historical runs')
        weighted = weight_sum = 0.0
        valid_count = 0
        for i, run in enumerate(eligible):
            finish, field = run.get('finish'), run.get('field_size')
            status = run.get('finish_status', 'finished' if type(finish) is int else None)
            if status in ('excluded', 'scratched', 'did_not_finish', 'disqualified'):
                if finish is not None:
                    raise ValueError(f'{name}: invalid non-finish')
                continue
            if status != 'finished' or type(field) is not int or type(finish) is not int or not (1 <= finish <= field):
                raise ValueError(f'{name}: invalid finish data')
            value = 100.0 if field == 1 else 100.0 * (field - finish) / (field - 1)
            weight = 0.8 ** i
            weighted += value * weight
            weight_sum += weight
            valid_count += 1
        score = round(weighted / weight_sum, 2) if weight_sum else None
        ranked.append({'horse_number': number, 'horse_name': name, 'identity_candidate': cid,
                       'form_score': score, 'completed_runs': valid_count,
                       'identity_verified': bool(horse.get('identity_verified', False))})
    ranked.sort(key=lambda h: (h['form_score'] is None, -(h['form_score'] or 0), h['horse_number']))
    # Competition ranking: equal displayed scores share a rank (1, 1, 3).
    # Horse number is only a stable display order within a tie.
    previous_score = object()
    current_rank = None
    for i, h in enumerate(ranked, 1):
        score = h['form_score']
        if score is None:
            h['rank'] = None
            continue
        if score != previous_score:
            current_rank = i
            previous_score = score
        h['rank'] = current_rank
    return {'race_date': race_date.isoformat(), 'venue': entries['venue'],
            'race_number': entries['race_number'], 'race_name': entries['race_name'],
            'generated_at': datetime.now().astimezone().isoformat(), 'posting': False,
            'method': 'Historical normalized finish, recency weight 0.8^index; not win probability',
            'warning': 'Entrants and horse IDs must be verified against official race card; no odds or course adjustment',
            'horses': ranked}


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
        rank_label = str(h['rank']) if h['rank'] is not None else '-'
        print(f"{rank_label:>2}. {h['horse_number']:>2} {h['horse_name']} score={h['form_score']} runs={h['completed_runs']}")
    print('COMPLETE / NO POSTING / HISTORICAL FORM ONLY')


if __name__ == '__main__':
    main()
