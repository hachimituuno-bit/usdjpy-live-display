"""Historical JRA backtest: use post-race result page for entrants only;
score strictly with runs before the race, compare with withheld actual results.
Read-only; no posting. This is NOT a live prediction.
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
            if p.hostname == 'www.jra.go.jp' and p.path == '/JRADB/accessU.html' and parse_qs(p.query).get('CNAME'):
                links.append(u)
        if len(links) != 1:
            raise ValueError(f'{name}: horse profile URL not unique')
        horses.append({'number': number, 'name': name, 'horse_url': links[0]})
    actual_horses = {(h['number'], h['name']) for h in actual['horses']}
    if len(horses) != actual['field_size'] or {(h['number'], h['name']) for h in horses} != actual_horses:
        raise ValueError('Entrants do not match official result table')
    return {'race_date': actual['date'], 'venue': 'historical_JRA', 'race_number': 1,
            'race_name': 'historical_backtest', 'posting': False, 'horses': horses,
            'warning': 'Retrospective entrants sourced from result page, NOT a pre-race card'}

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
            report = {'race_id': race['id'], 'race_date': actual['date'],
                      'backtest': True, 'posting': False,
                      'leakage_policy': 'Only horse runs dated BEFORE the target race; target result used solely for evaluation',
                      'warning': 'Retrospective entrant selection; no evidence of real-world predictive performance',
                      'ranking_vs_actual': comparisons}
            (OUT / f"{race['id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            summaries.append({'race_id': race['id'], 'status': 'SUCCESS', 'horses': len(comparisons)})
            print('BACKTEST SUCCESS', race['id'], flush=True)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            summaries.append({'race_id': race['id'], 'status': 'FAILED', 'reason': str(exc)})
            print('BACKTEST FAILED', race['id'], type(exc).__name__, str(exc), flush=True)
    (OUT/'summary.json').write_text(json.dumps({'posting': False, 'races': summaries}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if not any(x['status'] == 'SUCCESS' for x in summaries):
        raise SystemExit('BACKTEST FAILED: no historical race could be processed')

if __name__ == '__main__':
    main()
