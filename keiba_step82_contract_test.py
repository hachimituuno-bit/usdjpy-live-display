"""Offline contract test for 15 starters / 14 finishes / one DNF.
Does not change existing files, fetch races, score predictions, or post.
"""
import ast
import json
from pathlib import Path
from bs4 import BeautifulSoup

SOURCE = Path('keiba_historical_backtest.py')
OUTPUT = Path('keiba_step82_contract_result.json')


def load_functions(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'historical_entries', 'metrics'}]
    if {node.name for node in selected} != {'historical_entries', 'metrics'}:
        raise AssertionError('Required functions missing in existing backtest')
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    import re
    from urllib.parse import urljoin, urlparse, parse_qs
    namespace = {'BeautifulSoup': BeautifulSoup, 're': re, 'urljoin': urljoin, 'urlparse': urlparse, 'parse_qs': parse_qs}
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace['historical_entries'], namespace['metrics']


def fixture():
    # Synthetic input: tests code contract, NOT the live JRA page or actual race outcome.
    trs = []
    for number in range(1, 16):
        finish = '中止' if number == 12 else str(number if number < 12 else number - 1)
        cells = [finish, '1', str(number),
                 f'<a href="/JRADB/accessU.html?CNAME=horse{number:02d}">テスト馬{number:02d}</a>']
        cells.extend(['-'] * 6)
        trs.append('<tr>' + ''.join(f'<td>{value}</td>' for value in cells) + '</tr>')
    html = '<table><tr><th>着順</th><th>馬名</th><th>コーナー</th></tr>' + ''.join(trs) + '</table>'
    actual = {'date': '2026-09-27', 'field_size': 15,
              'horses': [{'number': i, 'name': f'テスト馬{i:02d}', 'finish': None if i == 12 else (i if i < 12 else i-1)} for i in range(1, 16)]}
    return html, actual


def safe_entries_fixture(html, actual, url):
    """Test-only mapping; NOT a patch for the existing parser."""
    from urllib.parse import urljoin, urlparse, parse_qs
    soup = BeautifulSoup(html, 'html.parser')
    horses = []
    for tr in soup.find('table').find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) < 10:
            continue
        number = int(cells[2].get_text(' ', strip=True))
        link = cells[3].find('a', href=True)
        if link is None:
            raise AssertionError('Missing profile URL')
        horse_url = urljoin(url, link['href'])
        identity = parse_qs(urlparse(horse_url).query)['CNAME'][0]
        horses.append({'number': number, 'name': cells[3].get_text(' ', strip=True),
                       'horse_url': horse_url, 'identity_candidate': identity})
    if len(horses) != actual['field_size'] or len({h['number'] for h in horses}) != len(horses):
        raise AssertionError('Test-only mapping lost entrants')
    return horses


def main():
    old_entries, metrics = load_functions(SOURCE)
    url = 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=synthetic'
    html, actual = fixture()
    old_error = None
    try:
        old_entries(html, url, actual)
    except ValueError as exc:
        old_error = str(exc)
    if old_error != 'Entrants do not match official result table':
        raise AssertionError(f'Unexpected existing-parser behavior: {old_error!r}')

    entries = safe_entries_fixture(html, actual, url)
    truth = {h['number']: h['finish'] for h in actual['horses']}
    comparisons = [{'rank': i, 'number': h['number'], 'name': h['name'],
                    'actual_finish': truth[h['number']]} for i, h in enumerate(entries, 1)]
    result_metrics = metrics(comparisons)
    if len(comparisons) != 15 or comparisons[11]['actual_finish'] is not None:
        raise AssertionError('DNF or starter mapping wrong')
    if result_metrics['field_size'] != 15 or result_metrics['top3_slots'] != 3:
        raise AssertionError('Existing metrics rejected complete field')
    # DNF must never count as a top-3 finish, including if ranked first.
    dnf_first = [dict(h, rank=(1 if h['number'] == 12 else (h['rank'] + 1 if h['rank'] < 12 else h['rank']))) for h in comparisons]
    dnf_metrics = metrics(dnf_first)
    if dnf_metrics['top1_win'] != 0 or dnf_metrics['top1_top3'] != 0:
        raise AssertionError('DNF incorrectly scored as hit')
    report = {'status': 'PASS_OFFLINE_CONTRACT_ONLY', 'fixture': 'SYNTHETIC_NOT_LIVE_JRA',
              'starters': 15, 'numeric_finishes': 14, 'dnf_number': 12,
              'existing_historical_entries_error': old_error,
              'test_only_adapter_retains_all': True,
              'existing_metrics_accepts_15': True, 'dnf_not_counted_as_hit': True,
              'actual_full_backtest_compatibility': 'NOT_TESTED',
              'network_access': False, 'posting': False}
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('RESULT', report['status'])
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
