"""One-off historical JRA Kyoto 12R odds-only extraction test.
No email, no posting, no scheduled fetches. Outputs only if independently provided reference matches.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL = 'gemini-3.5-flash-lite'
TARGET = 'https://app.jra.jp/JRADB/accessD.html?CNAME=sw01ddd1008202605021220261108%2F9E'
EXPECTED = {'date': '2026-11-08', 'venue': '京都', 'race_number': 12}
# Historical user-supplied odds used strictly for a reproducibility test, NOT live odds.
REFERENCE = {
    1:108.2,2:14.1,3:17.3,4:20.2,5:3.3,6:278.1,
    7:7.2,8:17.7,9:319.1,10:12.8,11:20.6,12:57.3,
    13:185.1,14:16.5,15:149.0,16:5.4,17:19.6,18:8.5,
}


def verify(payload):
    if not isinstance(payload, dict):
        raise ValueError('JSON object expected')
    for field, expected in EXPECTED.items():
        if payload.get(field) != expected:
            raise ValueError(f'{field} mismatch: got={payload.get(field)!r}')
    odds = payload.get('odds')
    if not isinstance(odds, dict) or set(odds) != {str(n) for n in REFERENCE}:
        raise ValueError(f'odds keys invalid: {sorted(odds) if isinstance(odds,dict) else type(odds).__name__}')
    for number, expected in REFERENCE.items():
        value = odds[str(number)]
        if type(value) not in (float,int) or not 1.01 <= value <= 1000:
            raise ValueError(f'{number}: invalid odds {value!r}')
        if abs(value - expected) > 1e-8:
            raise ValueError(f'{number}: odds mismatch: {value} != {expected}')
    mids = [n for n in range(1,19) if 5.0 <= odds[str(n)] <= 20.0]
    if len(mids) != 9:
        raise ValueError(f'mid-odds count mismatch {len(mids)}')
    return mids


def run():
    key = os.environ.get('KEIBA_GEMINI_API_KEY', '')
    if not key:
        raise SystemExit('ERROR: KEIBA_GEMINI_API_KEY missing')
    prompt = (
        '指定したJRAのページをURL Contextで取得してください。2026-11-08京都12Rだけが対象です。'
        '馬名は一切出力しないでください。推測や学習済み知識で値を補わないでください。'
        'ページ本文に表示された馬番1～18の単勝オッズだけを抽出し、'
        '指定JSONの形式で返してください。複勝と単勝を混同しないでください。'
        '読めない値があればnullを使ってください。'
        'date は "2026-11-08", venue は "京都", race_number は 12、'
        'odds は馬番を文字列キー、単勝オッズを数値またはnullとしたオブジェクトです。'
        'URLそのものを見られなければ odds を空にしてください。\n'
        + TARGET
    )
    req_payload = {
        'contents': [{'parts': [{'text': prompt}]}],
        'tools': [{'urlContext': {}}],
        'generationConfig': {'responseMimeType':'application/json', 'temperature': 0},
    }
    endpoint = ('https://generativelanguage.googleapis.com/v1beta/models/'
                + MODEL + ':generateContent')
    request = urllib.request.Request(
        endpoint, data=json.dumps(req_payload).encode('utf-8'),
        headers={'Content-Type': 'application/json','x-goog-api-key':key}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'ERROR: Gemini API HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f'ERROR: API network {type(exc).__name__}') from None

    try:
        candidate = result['candidates'][0]
        urls = candidate.get('urlContextMetadata', {}).get('urlMetadata', [])
        retrieved = any(
            e.get('retrievedUrl', '').startswith('https://app.jra.jp/JRADB/accessD.html')
            and e.get('urlRetrievalStatus') == 'URL_RETRIEVAL_STATUS_SUCCESS'
            for e in urls)
        if not retrieved:
            raise ValueError('URL Context retrieval not verified')
        content = ''.join(p.get('text','') for p in candidate['content']['parts'])
        extracted = json.loads(content)
        mids = verify(extracted)
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f'BLOCKED: {exc}') from None

    out = Path('keiba_gemini_review/2026-11-08_kyoto12_odds_only.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    output = {
        'historical_test_only':True, 'source_url':TARGET,
        'verified_against_user_provided_historical_odds':True,
        'date':EXPECTED['date'], 'venue':EXPECTED['venue'],
        'race_number':EXPECTED['race_number'], 'odds':extracted['odds'],
        'mid_odds_numbers':mids,
    }
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'ODDS_MATCH_OK: 18/18 / mid_odds={len(mids)} / {out} / mail=off / post=off')

if __name__ == '__main__':
    run()
