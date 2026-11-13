"""Isolated Nakayama 8R parser validation; no writes to existing code, no posting."""
import inspect
import json
import re
import textwrap
import traceback
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import keiba_jra_result_fetch as original

URL = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96'
OUTPUT = Path('keiba_nakayama8r_safe_test_v3.json')


def main():
    report = {'url': URL, 'status': 'NOT_TESTED', 'posting': False, 'source_files_modified': False}
    try:
        response = requests.get(URL, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        table = next((t for t in soup.find_all('table') if all(
            x in ' '.join(th.get_text(' ', strip=True) for th in t.find_all('th'))
            for x in ('着順', '馬名', 'コーナー'))), None)
        if table is None:
            raise ValueError('JRA結果表が見つかりません')
        entries = []
        for tr in table.find_all('tr'):
            cells = [td.get_text(' ', strip=True) for td in tr.find_all('td', recursive=False)]
            if len(cells) >= 10:
                entries.append({'finish': cells[0], 'number': cells[2], 'horse': cells[3], 'corners': cells[9]})
        finished = [e for e in entries if re.fullmatch(r'\d+', e['finish'])]
        non_finish = [e for e in entries if e['finish'] in ('中止', '失格')]
        unrecognized = [e for e in entries if e not in finished and e not in non_finish]
        report.update({'finished_count': len(finished), 'non_finish_starters': non_finish, 'unrecognized_rows': unrecognized})
        if unrecognized:
            raise ValueError('未対応の着順表記があります。手動確認が必要です')
        if len(finished) != 14 or len(non_finish) != 1 or non_finish[0]['number'] != '12':
            raise ValueError('診断済みのレース構成と一致しません。停止します')
        all_numbers = [e['number'] for e in finished + non_finish]
        if len(all_numbers) != len(set(all_numbers)):
            raise ValueError('実際に馬番の重複があります')
        starters = len(all_numbers)
        source = textwrap.dedent(inspect.getsource(original.extract))
        old_field = '    field = len(rows)'
        old_duplicate = "    if len({\n        row['number'] for row in rows\n    }) != field:"
        if source.count(old_field) != 1 or source.count(old_duplicate) != 1:
            raise ValueError('元コードの構造が想定と異なります。変更せず停止します')
        # Keep two counts separate: actual starters for corner validation,
        # numeric finishers for the duplicate check. Modify only an in-memory copy.
        source = source.replace(old_field, f'    field = {starters}', 1)
        source = source.replace(old_duplicate,
            "    if len({\n        row['number'] for row in rows\n    }) != len(rows):", 1)
        namespace = dict(vars(original))
        exec(compile(source, '<v3-in-memory>', 'exec'), namespace)
        parsed = namespace['extract'](URL, response.text)
        report['parsed_field_size'] = parsed.get('field_size')
        report['parsed_horses_count'] = len(parsed.get('horses', []))
        if report['parsed_field_size'] != starters or report['parsed_horses_count'] != len(finished):
            raise ValueError('解析結果の頭数が一致しません')
        report['status'] = 'PASS_PARSER_ONLY'
        print('PASS_PARSER_ONLY', json.dumps({'starters': starters, 'finished': len(finished)}, ensure_ascii=False))
        print('NOTE: downstream prediction/scoring compatibility NOT verified')
    except Exception as exc:
        report['status'] = 'ERROR'
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
        print('ERROR', report['error'])
        print(report['traceback'])
    finally:
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('NO BLOGGER POST / NO SOURCE MODIFICATION /', OUTPUT)


if __name__ == '__main__':
    main()
