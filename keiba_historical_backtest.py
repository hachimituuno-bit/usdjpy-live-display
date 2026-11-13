"""Historical JRA backtest: use post-race result page for entrants only;
score strictly with runs before the race, compare with withheld actual results.
Read-only; no posting. This is NOT a live prediction.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
from keiba_jra_result_fetch import extract
from keiba_next_collect import collect
from keiba_next_race import rank
from keiba_jra_batch_fetch import RACES

OUT = Path('keiba_backtest_results')

def historical_entries(html, url, actual):
    soup = BeautifulSoup(html, 'html.parser')
    tables = []
    for table in soup.find_all('table'):
        headers = ' '.join(th.get_text(' ', strip=True) for th in table.find_all('th'))
        if all(word in headers for word in ('着順', '馬名', 'コーナー')):
            tables.append(table)
    if len(tables) != 1:
        raise ValueError('Cannot identify unique result table')
    horses = []
    for tr in tables[0].find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) < 10 or not re.fullmatch(r'\d+', cells[0].get_text(' ', strip=True)):
            continue
        number = int(cells[2].get_text(' ', strip=True))
        name_cell = cells[3]
        name = name_cell.get_text(' ', strip=True)
        links = []
        for a in name_cell.find_all('a', href=True):
            u = urljoin(url, a['href'])
            p = urlparse(u)
            if p.hostname == 'www.jra.go.jp' and p.path == '/JRADB/accessU.html' and parse_qs(p.query).get('CNAME'):
                links.append(u)
        if len(links) != 1:
            raise ValueError(f'{name}: horse profile URL not unique')
        # The ranking module matches entrants to histories by JRA CNAME.
        # Derive it from the same validated official profile URL as collect().
        identity_candidate = parse_qs(urlparse(links[0]).query)['CNAME'][0]
        horses.append({'number': number, 'name': name,
                       'horse_url': links[0],
                       'identity_candidate': identity_candidate})
    actual_horses = {(h['number'], h['name']) for h in actual['horses']}
    if len(horses) != actual['field_size'] or {(h['number'], h['name']) for h in horses} != actual_horses:
        raise ValueError('Entrants do not match official result table')
    return {'race_date': actual['date'], 'venue': 'historical_JRA', 'race_number': 1,
            'race_name': 'historical_backtest', 'posting': False, 'horses': horses,
            'warning': 'Retrospective entrants sourced from result page, NOT a pre-race card'}

def load_races(path):
    if path is None:
        races = RACES
    else:
        races = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(races, dict):
            races = races.get('races')
    if not isinstance(races, list) or not races:
        raise ValueError('Race list must be a nonempty JSON array')
    seen = set()
    for race in races:
        if not isinstance(race, dict) or not all(race.get(k) for k in ('id', 'url', 'date')):
            raise ValueError('Every race requires id, url and date')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', str(race['id'])):
            raise ValueError('Unsafe race id')
        if race['id'] in seen:
            raise ValueError('Duplicate race id')
        seen.add(race['id'])
        parsed = urlparse(race['url'])
        if parsed.scheme != 'https' or parsed.hostname != 'www.jra.go.jp':
            raise ValueError('Race URL must be on https://www.jra.go.jp/')
    return races


def metrics(comparisons):
    if not comparisons:
        raise ValueError('Empty ranking')
    ranks = [int(h['rank']) for h in comparisons]
    if sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise ValueError('Ranks are not a complete unique ordering')
    by_rank = sorted(comparisons, key=lambda h: int(h['rank']))
    # Scratches, disqualifications and missing finishes are not treated as a win/place.
    def placed(h, limit):
        value = h['actual_finish']
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= limit
    top = by_rank[:3]
    return {
        'top1_win': int(placed(by_rank[0], 1)),
        'top1_top3': int(placed(by_rank[0], 3)),
        'top3_top3_horses': sum(placed(h, 3) for h in top),
        'top3_slots': len(top),
        'winner_in_top3': int(any(placed(h, 1) for h in top)),
        'field_size': len(by_rank),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--races-file', help='Optional JSON list of official historical JRA race IDs, dates and URLs')
    args = parser.parse_args()
    races = load_races(args.races_file)
    OUT.mkdir(exist_ok=True)
    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0'})
    summaries = []
    for race in races:
        print('BACKTEST', race['id'], flush=True)
        try:
            r = sess.get(race['url'], timeout=25)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            actual = extract(race['url'], r.text)
            if actual['date'] != race['date']:
                raise ValueError('Race date mismatch')
            entries = historical_entries(r.text, race['url'], actual)
            history = collect(entries, session=sess, delay=0.4)
            ranking = rank(entries, history)
            truth = {h['number']: h['finish'] for h in actual['horses']}
            comparisons = [{'rank': h['rank'], 'number': h['horse_number'],
                            'name': h['horse_name'], 'form_score': h['form_score'],
                            'actual_finish': truth[h['horse_number']]}
                           for h in ranking['horses']]
            if len(comparisons) != actual['field_size'] or len({h['number'] for h in comparisons}) != len(comparisons):
                raise ValueError('Ranking does not cover all unique entrants')
            race_metrics = metrics(comparisons)
            report = {'race_id': race['id'], 'race_date': actual['date'],
                      'backtest': True, 'posting': False,
                      'leakage_policy': 'Only horse runs dated BEFORE the target race; target result used solely for evaluation',
                      'warning': 'Retrospective entrant selection; no evidence of real-world predictive performance',
                      'metrics': race_metrics, 'ranking_vs_actual': comparisons}
            (OUT / f"{race['id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            summaries.append({'race_id': race['id'], 'status': 'SUCCESS', 'horses': len(comparisons), 'metrics': race_metrics})
            print('BACKTEST SUCCESS', race['id'], flush=True)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            summaries.append({'race_id': race['id'], 'status': 'FAILED', 'reason': str(exc)})
            print('BACKTEST FAILED', race['id'], type(exc).__name__, str(exc), flush=True)
    successful = [x['metrics'] for x in summaries if x['status'] == 'SUCCESS']
    total = {key: sum(m[key] for m in successful) for key in ('top1_win', 'top1_top3', 'top3_top3_horses', 'top3_slots', 'winner_in_top3')}
    n = len(successful)
    total.update({'races_requested': len(races), 'races_succeeded': n,
                  'races_failed': len(races)-n,
                  'top1_win_rate': total['top1_win']/n if n else None,
                  'top1_top3_rate': total['top1_top3']/n if n else None,
                  'top3_horse_top3_rate': total['top3_top3_horses']/total['top3_slots'] if total['top3_slots'] else None,
                  'winner_in_top3_rate': total['winner_in_top3']/n if n else None})
    (OUT/'summary.json').write_text(json.dumps({'posting': False, 'races': summaries, 'metrics': total}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('BACKTEST METRICS', json.dumps(total, ensure_ascii=False), flush=True)
    if n != len(races):
        raise SystemExit(f'BACKTEST INCOMPLETE: {n}/{len(races)} races succeeded')


if __name__ == '__main__':
    main()
