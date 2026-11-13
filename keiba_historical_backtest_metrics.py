"""Historical JRA backtest with tie-aware accuracy metrics.

Uses post-race result pages to identify entrants, but scores only pre-race runs.
Retrospective research only. Never posts or places bets.
"""
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
            if (p.scheme == 'https' and p.hostname == 'www.jra.go.jp'
                    and p.path == '/JRADB/accessU.html'
                    and len(parse_qs(p.query).get('CNAME', [])) == 1):
                links.append(u)
        if len(links) != 1:
            raise ValueError(f'{name}: horse profile URL not unique')
        horses.append({'number': number, 'name': name, 'horse_url': links[0]})
    actual_horses = {(h['number'], h['name']) for h in actual['horses']}
    if (len(horses) != actual['field_size']
            or {(h['number'], h['name']) for h in horses} != actual_horses):
        raise ValueError('Entrants do not match official result table')
    return {'race_date': actual['date'], 'venue': 'historical_JRA', 'race_number': 1,
            'race_name': 'historical_backtest', 'posting': False, 'horses': horses,
            'warning': 'Retrospective entrants sourced from result page, NOT a pre-race card'}


def evaluate(comparisons):
    """Count unique-top-pick hits; tied top picks are reported separately.

    Top-3 selection means ranks 1 through 3 including ties. It may contain
    more than three horses; its size is always disclosed.
    """
    if not comparisons:
        raise ValueError('No comparison data')
    winners = [h for h in comparisons if h['actual_finish'] == 1]
    if len(winners) != 1:
        raise ValueError('Cannot identify exactly one winner')
    top = [h for h in comparisons if h['rank'] == 1]
    top3 = [h for h in comparisons if h['rank'] <= 3]
    if not top or not top3:
        raise ValueError('Missing predicted top ranks')
    actual_top3 = [h for h in comparisons if type(h['actual_finish']) is int and 1 <= h['actual_finish'] <= 3]
    return {
        'top_ranked_count': len(top),
        'top_ranked_names': [h['name'] for h in top],
        'unique_top_pick': len(top) == 1,
        'unique_top_pick_won': (top[0]['actual_finish'] == 1) if len(top) == 1 else None,
        'unique_top_pick_finished_top3': (top[0]['actual_finish'] in (1, 2, 3)) if len(top) == 1 else None,
        'winner_in_tied_top_rank': (winners[0]['number'] in {h['number'] for h in top}),
        'predicted_top3_selection_count': len(top3),
        'predicted_top3_selection_names': [h['name'] for h in top3],
        'actual_top3_count': len(actual_top3),
        'actual_top3_found_in_predicted_top3': sum(h['actual_finish'] in (1, 2, 3) for h in top3),
        'winner_in_predicted_top3': winners[0]['number'] in {h['number'] for h in top3},
    }


def main():
    OUT.mkdir(exist_ok=True)
    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0'})
    summaries = []
    for race in RACES:
        print('BACKTEST', race['id'], flush=True)
        try:
            r = sess.get(race['url'], timeout=25)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            actual = extract(race['url'], r.text)
            for key in ('date', 'surface', 'distance_m'):
                if actual[key] != race[key]:
                    raise ValueError(f'{race["id"]}: {key} mismatch: {actual[key]} != {race[key]}')
            entries = historical_entries(r.text, race['url'], actual)
            history = collect(entries, session=sess, delay=0.4)
            ranking = rank(entries, history)
            truth = {h['number']: h['finish'] for h in actual['horses']}
            comparisons = [{'rank': h['rank'], 'number': h['horse_number'],
                            'name': h['horse_name'], 'form_score': h['form_score'],
                            'actual_finish': truth[h['horse_number']]}
                           for h in ranking['horses']]
            metrics = evaluate(comparisons)
            report = {'race_id': race['id'], 'race_date': actual['date'],
                      'backtest': True, 'posting': False,
                      'leakage_policy': 'Only horse runs dated BEFORE target race; target result used only for evaluation',
                      'warning': 'Retrospective entrants; small sample; not real-world predictive accuracy',
                      'metrics': metrics, 'ranking_vs_actual': comparisons}
            (OUT / f"{race['id']}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            summaries.append({'race_id': race['id'], 'status': 'SUCCESS',
                              'horses': len(comparisons), 'metrics': metrics})
            print('BACKTEST SUCCESS', race['id'], flush=True)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            summaries.append({'race_id': race['id'], 'status': 'FAILED', 'reason': str(exc)})
            print('BACKTEST FAILED', race['id'], type(exc).__name__, str(exc), flush=True)
    eligible = [x['metrics'] for x in summaries if x['status'] == 'SUCCESS'
                and x['metrics']['unique_top_pick']]
    summary = {
        'posting': False,
        'races': summaries,
        'successful_races': sum(x['status'] == 'SUCCESS' for x in summaries),
        'unique_top_pick_races': len(eligible),
        'unique_top_pick_win_rate': (
            round(sum(x['unique_top_pick_won'] for x in eligible) / len(eligible), 4)
            if eligible else None),
        'unique_top_pick_top3_rate': (
            round(sum(x['unique_top_pick_finished_top3'] for x in eligible) / len(eligible), 4)
            if eligible else None),
        'warning': 'Rates exclude races tied at rank 1; inspect counts and selection sizes',
    }
    (OUT / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not any(x['status'] == 'SUCCESS' for x in summaries):
        raise SystemExit('BACKTEST FAILED: no historical race could be processed')


if __name__ == '__main__':
    main()
