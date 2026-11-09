"""One-off historical JRA URL Context experiment; never publishes or emails."""
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TARGET = 'https://app.jra.jp/JRADB/accessD.html?CNAME=sw01ddd1008202605021220261108%2F9E'
EXPECTED_DATE = '2026-11-08'
EXPECTED_VENUE = '京都'
EXPECTED_RACE = 12
EXPECTED_COUNT = 18
MODEL = 'gemini-3.5-flash-lite'

def check_record(payload):
    if not isinstance(payload, dict):
        raise ValueError('JSON object required')
    if (payload.get('date') != EXPECTED_DATE or
        payload.get('venue') not in ('京都', '京都競馬場') or
        type(payload.get('race_number')) is not int or
        payload.get('race_number') != EXPECTED_RACE):
        raise ValueError('Date/venue/race do not match known historical example')
    horses = payload.get('horses')
    if not isinstance(horses, list) or len(horses) != EXPECTED_COUNT:
        raise ValueError('Expected exactly 18 horses')
    numbers, names = set(), set()
    for h in horses:
        if not isinstance(h, dict):
            raise ValueError('Horse must be object')
        n, name, odd = h.get('number'), h.get('name'), h.get('win_odds')
        if type(n) is not int or n < 1 or n > 18 or n in numbers:
            raise ValueError('Invalid/duplicate horse number')
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('Empty/duplicate horse name')
        if type(odd) not in (float, int) or not (1.01 <= odd <= 1000):
            raise ValueError('Missing/invalid single-win odds')
        numbers.add(n); names.add(name)
    if numbers != set(range(1, 19)):
        raise ValueError('Missing horse numbers')
    return horses

def main():
    key = os.environ.get('KEIBA_GEMINI_API_KEY', '')
    if not key:
        raise SystemExit('ERROR: KEIBA_GEMINI_API_KEY missing')
    prompt = (
        '指定されたURLをURL Context機能で実際に取得してください。Google検索は使わないでください。'
        '取得できなければ推測や記憶で回答しないでください。'
        '対象URL: ' + TARGET + '\n'
        '出馬表の本文から2026-11-08京都競馬場12Rの全出走馬の馬番・馬名・単勝オッズを抽出。'
        '単勝と複勝を混同しない。結果はJSONだけを出す。'
        '形式: {"date":"2026-11-08","venue":"京都","race_number":12,'
        '"horses":[{"number":1,"name":"馬名","win_odds":10.0}]}。'
        '不明な値を補完せず、全18頭の単勝が確認できなければ '
        '{"error":"incomplete"} だけ返してください。'
    )
    body = {'contents': [{'parts': [{'text': prompt}]}],
            'tools': [{'urlContext': {}}],
            'generationConfig': {'responseMimeType': 'application/json'}}
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent'
    req = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
                  headers={'Content-Type': 'application/json', 'x-goog-api-key': key}, method='POST')
    try:
        with urlopen(req, timeout=90) as response:
            api = json.load(response)
    except HTTPError as exc:
        raise SystemExit(f'ERROR: Gemini API HTTP {exc.code}') from None
    except (URLError, TimeoutError) as exc:
        raise SystemExit(f'ERROR: Gemini API network error: {type(exc).__name__}') from None
    try:
        candidate = api['candidates'][0]
        urls = candidate.get('urlContextMetadata', {}).get('urlMetadata', [])
        success = any(str(u.get('urlRetrievalStatus', '')) == 'URL_RETRIEVAL_STATUS_SUCCESS' and
                      u.get('retrievedUrl', '').startswith('https://app.jra.jp/JRADB/accessD.html') for u in urls)
        if not success:
            raise ValueError('URL Context retrieval not confirmed')
        output = ''.join(p.get('text', '') for p in candidate['content']['parts'] if 'text' in p)
        parsed = json.loads(output)
        horses = check_record(parsed)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f'BLOCKED: extraction not verified ({exc})') from None
    selected = [h for h in horses if 5.0 <= h['win_odds'] <= 20.0]
    # Deliberately no automatic public post, no mailing, no exposure of key.
    file = Path('keiba_gemini_review/2026-11-08_kyoto12_test.json')
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({'source_url': TARGET, 'historical_test_only': True,
                                'data': parsed, 'mid_odds': selected}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'REVIEW_READY: {file} / horses={len(horses)} / 5-20x={len(selected)} / email=off / post=off')
    print('CAUTION: Valid shape does not prove correctness. Compare all 18 entries with JRA source manually.')

if __name__ == '__main__':
    main()
