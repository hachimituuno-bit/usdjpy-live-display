"""Step84: JRA live-page entrant contract check; manual, read-only, no scoring/posting."""
import json
import re
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from keiba_historical_backtest import historical_entries

URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96'
OUT = Path('keiba_step84_live_entries_result.json')


def inspect(html):
    soup = BeautifulSoup(html, 'html.parser')
    tables = [t for t in soup.find_all('table') if all(
        term in ' '.join(h.get_text(' ', strip=True) for h in t.find_all('th'))
        for term in ('着順', '馬名', 'コーナー'))]
    if len(tables) != 1:
        raise ValueError(f'Result table count: {len(tables)}')
    rows = []
    for tr in tables[0].find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue
        vals = [c.get_text(' ', strip=True) for c in cells]
        if len(vals) < 10 or not re.fullmatch(r'\d+', vals[2]):
            raise ValueError(f'Unexpected horse row: {vals[:4]}')
        name_cell = cells[3]
        links = []
        for a in name_cell.find_all('a', href=True):
            link = urljoin(URL, a['href'])
            parsed = urlparse(link)
            if (parsed.scheme == 'https' and parsed.hostname == 'www.jra.go.jp'
                    and parsed.path == '/JRADB/accessU.html'
                    and len(parse_qs(parsed.query).get('CNAME', [])) == 1):
                links.append(link)
        if len(links) != 1:
            raise ValueError(f'Horse {vals[2]}: expected 1 official profile URL, got {len(links)}')
        rows.append({'number': int(vals[2]), 'name': vals[3],
                     'finish_text': vals[0],
                     'finish': int(vals[0]) if re.fullmatch(r'\d+', vals[0]) else None,
                     'horse_url': links[0],
                     'identity_candidate': parse_qs(urlparse(links[0]).query)['CNAME'][0]})
    numbers = [h['number'] for h in rows]
    if len(rows) != 15 or len(set(numbers)) != 15:
        raise ValueError(f'Expected 15 unique entrants, got {len(rows)} rows, {len(set(numbers))} numbers')
    finishers = [h for h in rows if h['finish'] is not None]
    nonfinishers = [h for h in rows if h['finish'] is None]
    if len(finishers) != 14 or [(h['number'], h['finish_text']) for h in nonfinishers] not in (
            [(12, '中止')], [(12, '競走中止')]):
        raise ValueError(f'Unexpected finish status: {[(h["number"], h["finish_text"]) for h in nonfinishers]}')
    # Only the original function is called, and only to establish its existing behavior.
    # Its actual argument is an independent 15-entrant contract, not a modified original parser.
    actual = {'date': '2026-09-27', 'field_size': 15,
              'horses': [{'number': h['number'], 'name': h['name']} for h in rows]}
    original_outcome = None
    try:
        original = historical_entries(html, URL, actual)
        original_outcome = {'status': 'ACCEPTED', 'count': len(original['horses'])}
    except ValueError as exc:
        original_outcome = {'status': 'REJECTED', 'error': str(exc)}
    if original_outcome['status'] != 'REJECTED' or original_outcome['error'] != 'Entrants do not match official result table':
        raise ValueError(f'Original behavior differs from expected: {original_outcome}')
    return {'status': 'PASS_LIVE_ENTRIES_CONTRACT_ONLY', 'race_url': URL,
            'full_entrant_count': len(rows), 'numeric_finish_count': len(finishers),
            'nonfinishers': [{'number': h['number'], 'name': h['name'], 'status': h['finish_text']} for h in nonfinishers],
            'profile_url_count': len(rows), 'identity_candidate_count': len({h['identity_candidate'] for h in rows}),
            'all_numbers': sorted(numbers), 'original_historical_entries': original_outcome,
            'ranking_tested': False, 'scoring_tested': False, 'full_backtest_tested': False,
            'blogger_posted': False, 'existing_files_modified': False}


def main():
    result = {'status': 'ERROR', 'ranking_tested': False, 'scoring_tested': False,
              'full_backtest_tested': False, 'blogger_posted': False, 'existing_files_modified': False}
    try:
        response = requests.get(URL, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        result = inspect(response.text)
        print('RESULT', result['status'])
        print('Full entrants:', result['full_entrant_count'], 'Numeric finishes:', result['numeric_finish_count'])
        print('Nonfinishers:', result['nonfinishers'])
        print('Original historical_entries:', result['original_historical_entries'])
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['traceback'] = traceback.format_exc()
        print(result['traceback'])
        raise
    finally:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
