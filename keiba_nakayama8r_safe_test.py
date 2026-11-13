"""Isolated read-only diagnostic for Nakayama 2026-09-27 8R.
No changes to existing modules, no posting, no prediction execution.
"""
import inspect
import json
import re
import textwrap
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import keiba_jra_result_fetch as original

URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96'
OUT = Path('keiba_nakayama8r_safe_test.json')


def main():
    report = {'url': URL, 'posting': False, 'status': 'NOT_VALIDATED'}
    try:
        response = requests.get(URL, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        table = next((t for t in soup.find_all('table') if all(
            x in ' '.join(h.get_text(' ', strip=True) for h in t.find_all('th'))
            for x in ('着順', '馬名', 'コーナー'))), None)
        if table is None:
            raise ValueError('結果表が見つかりません')
        entries = []
        for tr in table.find_all('tr'):
            vals = [td.get_text(' ', strip=True) for td in tr.find_all('td', recursive=False)]
            if len(vals) >= 10:
                entries.append({'finish_text': vals[0], 'number': vals[2],
                                'horse': vals[3], 'corners': vals[9]})
        finished = [e for e in entries if re.fullmatch(r'\d+', e['finish_text'])]
        others = [e for e in entries if not re.fullmatch(r'\d+', e['finish_text'])]
        report.update({'finished_count': len(finished), 'non_numeric_finish_rows': others})
        print('FINISHED', len(finished), 'OTHER ROWS', json.dumps(others, ensure_ascii=False))
        # Only accept explicit evidence of a starter without a numeric finish.
        starters_without_finish = [e for e in others if any(
            term in e['finish_text'] for term in ('中止', '失格'))]
        if not starters_without_finish:
            report['status'] = 'NEEDS_MANUAL_REVIEW'
            print('STOP: No explicit non-finish starter row; no inferred field size.')
            return
        expected_field = len(finished) + len(starters_without_finish)
        if expected_field != 15:
            raise ValueError(f'想定外の出走頭数: {expected_field}')
        # Execute a modified copy of the function in memory only; never write source files.
        source = textwrap.dedent(inspect.getsource(original.extract))
        old = '    field = len(rows)'
        if source.count(old) != 1:
            raise ValueError('元の関数が変更されています。安全のため停止')
        source = source.replace(old, '    field = ' + str(expected_field), 1)
        ns = dict(vars(original))
        exec(compile(source, '<in-memory-safe-test>', 'exec'), ns)
        parsed = ns['extract'](URL, response.text)
        if parsed['field_size'] != expected_field or len(parsed['horses']) != len(finished):
            raise ValueError('解析結果の整合性がありません')
        report.update({'status': 'PASS', 'field_size': parsed['field_size'],
                       'parsed_finished_horses': len(parsed['horses']),
                       'missing_finish_starters': starters_without_finish})
        print('PASS', json.dumps({k: report[k] for k in ('field_size', 'parsed_finished_horses')}, ensure_ascii=False))
    except Exception as exc:
        report.update({'status': 'ERROR', 'error': f'{type(exc).__name__}: {exc}'})
        print('ERROR', report['error'])
    finally:
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('READ ONLY / NO BLOGGER POST /', OUT)


if __name__ == '__main__':
    main()
