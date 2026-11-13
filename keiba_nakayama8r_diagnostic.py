"""Read-only diagnostic of JRA result parsing for 2026-09-27 Nakayama 8R.

No posting, no changes to existing modules, no odds/popularity processing.
"""
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from keiba_jra_result_fetch import extract

RACE_ID = 'nakayama_20260927_08r'
RACE_URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96'
OUTPUT = Path('keiba_nakayama8r_diagnostic.json')


def main():
    report = {'race_id': RACE_ID, 'url': RACE_URL, 'posting': False,
              'purpose': 'Inspect raw official result rows and isolate result-parser failure'}
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    try:
        response = session.get(RACE_URL, timeout=25)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = []
        for table in soup.find_all('table'):
            headers = ' '.join(th.get_text(' ', strip=True) for th in table.find_all('th'))
            if all(term in headers for term in ('着順', '馬名', 'コーナー')):
                tables.append(table)
        report['matching_result_tables'] = len(tables)
        report['tables'] = []
        for table in tables:
            rows = []
            for row in table.find_all('tr'):
                cells = row.find_all('td', recursive=False)
                if len(cells) < 10:
                    continue
                rank = cells[0].get_text(' ', strip=True)
                if not re.fullmatch(r'\d+', rank):
                    continue
                rows.append({'rank': rank, 'horse_number': cells[2].get_text(' ', strip=True),
                             'horse_name': cells[3].get_text(' ', strip=True),
                             'cells': [c.get_text(' ', strip=True) for c in cells]})
            report['tables'].append({'row_count': len(rows), 'rows': rows})
        try:
            result = extract(RACE_URL, response.text)
            report['extract_status'] = 'SUCCESS'
            report['extract_date'] = result.get('date')
            report['extract_field_size'] = result.get('field_size')
        except Exception as exc:
            report['extract_status'] = 'FAILED'
            report['extract_error_type'] = type(exc).__name__
            report['extract_error'] = str(exc)
        print('RESULT', report['extract_status'], report.get('extract_error', ''), flush=True)
        print('MATCHING TABLES', report['matching_result_tables'], flush=True)
        for table in report['tables']:
            print('TABLE ROWS', table['row_count'], flush=True)
            for row in table['rows']:
                print('ROW', json.dumps(row, ensure_ascii=False), flush=True)
    except Exception as exc:
        report['fetch_or_diagnostic_error'] = f'{type(exc).__name__}: {exc}'
        print('DIAGNOSTIC ERROR', report['fetch_or_diagnostic_error'], flush=True)
    finally:
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('NO POSTING / OUTPUT', OUTPUT, flush=True)


if __name__ == '__main__':
    main()
