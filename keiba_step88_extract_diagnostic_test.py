"""Step88: diagnose original extract() on one JRA race; read-only, manual only."""
import json
import re
import traceback
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from keiba_jra_result_fetch import extract

URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96'
OUT = Path('keiba_step88_extract_diagnostic_result.json')


def inspect_table(html):
    soup = BeautifulSoup(html, 'html.parser')
    tables = [t for t in soup.find_all('table') if all(
        x in ' '.join(th.get_text(' ', strip=True) for th in t.find_all('th'))
        for x in ('着順', '馬名', 'コーナー'))]
    if len(tables) != 1:
        raise ValueError(f'Expected unique results table, got {len(tables)}')
    rows = []
    for tr in tables[0].find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue
        vals = [c.get_text(' ', strip=True) for c in cells]
        if len(vals) < 10 or not re.fullmatch(r'\d+', vals[2]):
            raise ValueError(f'Unexpected row structure: {vals[:4]}')
        corners = [int(v) for v in vals[9].split()] if re.fullmatch(r'\d+(?:\s+\d+)*', vals[9]) else []
        rows.append({'number': int(vals[2]), 'name': vals[3], 'finish_text': vals[0],
                     'finish': int(vals[0]) if re.fullmatch(r'\d+', vals[0]) else None,
                     'corners': corners})
    return rows


def main():
    result = {'status': 'ERROR', 'source': URL, 'existing_files_modified': False,
              'prediction_tested': False, 'collect_tested': False,
              'full_backtest_tested': False, 'blogger_posted': False}
    try:
        response = requests.get(URL, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        rows = inspect_table(response.text)
        finishers = [h for h in rows if h['finish'] is not None]
        dnf = [h for h in rows if h['finish'] is None]
        if len(rows) != 15 or len(finishers) != 14 or len(dnf) != 1 or dnf[0]['number'] != 12 or dnf[0]['finish_text'] not in ('中止', '競走中止'):
            raise AssertionError('Live JRA data differs from the established 15/14/12 contract')
        if len({h['number'] for h in rows}) != 15:
            raise AssertionError('Horse numbers are not unique')
        outcome = {'status': 'NOT_RUN'}
        try:
            original = extract(URL, response.text)
            outcome = {'status': 'RETURNED', 'field_size': original.get('field_size'),
                       'horse_count': len(original.get('horses', [])),
                       'numbers': [h['number'] for h in original.get('horses', [])]}
        except Exception as exc:
            outcome = {'status': 'RAISED', 'exception_type': type(exc).__name__,
                       'message': str(exc), 'traceback': traceback.format_exc()}
        result.update({'entrants': len(rows), 'numeric_finishes': len(finishers),
                       'nonfinishers': [{'number': h['number'], 'name': h['name'], 'status': h['finish_text']} for h in dnf],
                       'max_corner_position': max((max(h['corners']) for h in rows if h['corners']), default=None),
                       'original_extract': outcome})
        if outcome['status'] == 'RAISED':
            result['status'] = 'PASS_ORIGINAL_EXTRACT_FAILURE_REPRODUCED'
        else:
            result['status'] = 'UNEXPECTED_ORIGINAL_EXTRACT_RETURNED'
            raise AssertionError('Original extract returned; inspect output instead of assuming the known failure')
        print('RESULT', result['status'])
        print('Entrants:', len(rows), 'Numeric finishes:', len(finishers))
        print('Original extract:', outcome['status'], outcome.get('exception_type'), outcome.get('message'))
        print('Max corner:', result['max_corner_position'])
    except Exception as exc:
        if result['status'] == 'ERROR':
            result['error'] = f'{type(exc).__name__}: {exc}'
            result['traceback'] = traceback.format_exc()
        raise
    finally:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
