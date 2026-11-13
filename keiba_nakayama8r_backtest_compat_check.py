"""Read-only compatibility check: V4 parser counts vs existing backtest entrants."""
import json
import re
import traceback
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96'
OUT = Path('keiba_nakayama8r_backtest_compat_check.json')


def main():
    result = {'status': 'NOT_TESTED', 'race_url': URL, 'posting': False, 'source_modified': False}
    try:
        response = requests.get(URL, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = [t for t in soup.find_all('table') if all(
            x in ' '.join(th.get_text(' ', strip=True) for th in t.find_all('th'))
            for x in ('着順', '馬名', 'コーナー'))]
        if len(tables) != 1:
            raise ValueError(f'Expected one result table; found {len(tables)}')
        rows = []
        for tr in tables[0].find_all('tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) < 10:
                continue
            rows.append({'finish_text': cells[0].get_text(' ', strip=True),
                         'number': cells[2].get_text(' ', strip=True),
                         'name': cells[3].get_text(' ', strip=True)})
        numeric = [r for r in rows if re.fullmatch(r'\d+', r['finish_text'])]
        non_numeric = [r for r in rows if not re.fullmatch(r'\d+', r['finish_text'])]
        if not rows or len({r['number'] for r in rows}) != len(rows):
            raise ValueError('Empty/duplicate horse numbers: cannot interpret starters safely')
        result.update({'result_table_rows': len(rows), 'numeric_finish_rows': len(numeric),
                       'non_numeric_finish_rows': non_numeric,
                       'existing_historical_entries_count': len(numeric),
                       'v4_expected_field_size': len(rows),
                       'count_mismatch': len(rows) != len(numeric)})
        if len(rows) != 15 or len(numeric) != 14 or len(non_numeric) != 1 or non_numeric[0]['number'] != '12':
            raise ValueError('Race structure differs from the previously verified 15/14/1 case')
        result['status'] = 'CONFIRMED_INCOMPATIBILITY' if result['count_mismatch'] else 'COUNTS_MATCH'
        print('RESULT', result['status'])
        print('Starters:', len(rows), 'Numeric finishes:', len(numeric))
        print('Excluded by current historical_entries filter:', json.dumps(non_numeric, ensure_ascii=False))
        print('No scoring or prediction performed. No source changes. No posting.')
    except Exception as exc:
        result['status'] = 'ERROR'
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['traceback'] = traceback.format_exc()
        print(result['traceback'])
        raise
    finally:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
