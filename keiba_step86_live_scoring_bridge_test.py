"""Step86: live JRA entrant -> synthetic ranking -> existing metrics interface.
Manual diagnostic only. No collect(), rank(), betting or posting.
"""
import json
import traceback
from pathlib import Path

import requests

from keiba_step84_live_entries_test import URL, inspect
from keiba_historical_backtest import metrics

OUT = Path('keiba_step86_live_scoring_bridge_result.json')


def verify_bridge(html):
    # Step84 validates 15 distinct numbers, 14 numeric finishes, and #12 DNF.
    observed = inspect(html)
    if observed['status'] != 'PASS_LIVE_ENTRIES_CONTRACT_ONLY':
        raise AssertionError('Step84 entrant contract failed')
    # Independently recover actual finish values, not guessed finishing order.
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(html, 'html.parser')
    tables = [t for t in soup.find_all('table') if all(
        term in ' '.join(th.get_text(' ', strip=True) for th in t.find_all('th'))
        for term in ('着順', '馬名', 'コーナー'))]
    if len(tables) != 1:
        raise AssertionError('Ambiguous JRA results table')
    truth = {}
    for tr in tables[0].find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue
        if len(cells) < 10:
            raise AssertionError('Unexpected row structure')
        number_text = cells[2].get_text(' ', strip=True)
        finish_text = cells[0].get_text(' ', strip=True)
        if not re.fullmatch(r'\d+', number_text):
            raise AssertionError(f'Unexpected horse number: {number_text!r}')
        number = int(number_text)
        if number in truth:
            raise AssertionError('Duplicate horse number')
        truth[number] = int(finish_text) if re.fullmatch(r'\d+', finish_text) else None
    if set(truth) != set(observed['all_numbers']) or truth.get(12) is not None:
        raise AssertionError('Entrant/finish mapping mismatch')
    finishes = sorted(v for v in truth.values() if v is not None)
    if finishes != list(range(1, 15)):
        raise AssertionError(f'Numeric finish ranks not 1..14: {finishes}')

    # The ordering below is intentionally artificial. It does NOT use or test the model.
    numbers = sorted(truth)
    comparisons = [{'rank': i, 'number': n, 'actual_finish': truth[n]}
                   for i, n in enumerate(numbers, start=1)]
    scored = metrics(comparisons)
    if scored['field_size'] != 15 or scored['top3_slots'] != 3:
        raise AssertionError('Existing metrics rejected 15 entrants')

    # Rank DNF first, keep all other ranks unique and complete.
    dnf_first_numbers = [12] + [n for n in numbers if n != 12]
    dnf_first = [{'rank': i, 'number': n, 'actual_finish': truth[n]}
                 for i, n in enumerate(dnf_first_numbers, start=1)]
    dnf_scored = metrics(dnf_first)
    if dnf_scored['field_size'] != 15 or dnf_scored['top1_win'] != 0 or dnf_scored['top1_top3'] != 0:
        raise AssertionError('DNF incorrectly scored as hit')
    return {
        'status': 'PASS_LIVE_SCORING_BRIDGE_ONLY',
        'race_url': URL, 'entrants': len(truth), 'numeric_finishes': len(finishes),
        'dnf_number': 12, 'dnf_finish_value': None,
        'existing_metrics_accepts_15': True, 'dnf_rank1_not_a_hit': True,
        'ranking_source': 'SYNTHETIC_ORDER_NOT_MODEL_PREDICTION',
        'existing_extract_tested': False, 'collect_tested': False,
        'rank_model_tested': False, 'full_backtest_tested': False,
        'blogger_posted': False, 'existing_files_modified': False,
    }


def main():
    result = {'status': 'ERROR', 'blogger_posted': False, 'existing_files_modified': False}
    try:
        response = requests.get(URL, timeout=25, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        result = verify_bridge(response.text)
        print('RESULT', result['status'])
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['traceback'] = traceback.format_exc()
        print(result['traceback'])
        raise
    finally:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
