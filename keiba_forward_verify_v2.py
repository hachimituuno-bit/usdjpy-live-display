#!/usr/bin/env python3
"""Independent, local-only forward prediction snapshot and result verification v2.
No posting, no production edits. Dependencies are read from the same directory.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
COEFFICIENTS = (0.0, 0.5, 1.0, 1.5, 2.0)
JST = timezone(timedelta(hours=9))
VENUES = '札幌 函館 福島 新潟 東京 中山 中京 京都 阪神 小倉'.split()


def modules():
    import importlib.util
    def load(filename, name):
        path = ROOT / filename
        if not path.is_file():
            raise FileNotFoundError(f'同じフォルダに必要なファイルがありません: {filename}')
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return (load('keiba_batch_compare_v4.py', 'forward_v4'),
            load('keiba_history_extraction_fixed.py', 'forward_extractor'),
            load('keiba_backtest.py', 'forward_scoring'))


def fetch(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def prediction(horses, scoring, venue, surface, distance, cutoff):
    scores = {str(c): [] for c in COEFFICIENTS}
    old = []
    for horse in horses:
        number = horse['horse_number']
        name = horse['horse_name']
        runs = horse['runs']
        if not runs:
            raise ValueError(f'{number}番 {name}: 過去走なし（全頭比較できないため保存中止）')
        for run in runs:
            run_date = date.fromisoformat(run['date'].replace('/', '-'))
            if run_date >= cutoff:
                raise ValueError(f'{number}番 {name}: レース当日以降の過去走が含まれています')
        flat = [r for r in runs if '障害' not in r.get('raw_text', '')]
        flat.sort(key=lambda r: date.fromisoformat(r['date'].replace('/', '-')), reverse=True)
        flat = flat[:5]
        if not flat:
            raise ValueError(f'{number}番 {name}: 平地過去走なし（保存中止）')
        if any(scoring.run_value(r) is None for r in flat):
            raise ValueError(f'{number}番 {name}: 過去走評価不可（保存中止）')
        base = scoring.score(flat)
        if base is None:
            raise ValueError(f'{number}番 {name}: 旧方式評価不可')
        old.append({'number': number, 'name': name, 'score': round(100 * base, 2), 'runs': len(flat)})
        for coef in COEFFICIENTS:
            # score() already multiplies weights by condition() when passed a callback.
            # To avoid its built-in 1+2*fit multiplier, compute weighted average explicitly.
            numerator = denominator = 0.0
            for i, run in enumerate(flat):
                fit = scoring.similarity(run, venue, surface, distance)
                w = (0.8 ** i) * (1 + coef * (0.5 if fit is None else fit))
                numerator += w * scoring.run_value(run)
                denominator += w
            value = numerator / denominator
            scores[str(coef)].append({'number': number, 'name': name, 'score': value})
    old.sort(key=lambda h: (-h['score'], -h['runs'], h['number']))
    for key in scores:
        scores[key].sort(key=lambda h: (-h['score'], h['number']))
    return old, scores


def race_start(race_day, hhmm):
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', hhmm):
        raise ValueError('発走時刻はHH:MM形式で指定してください')
    return datetime.fromisoformat(f'{race_day}T{hhmm}:00').replace(tzinfo=JST)


def capture(args):
    cutoff = date.fromisoformat(args.date)
    start = race_start(args.date, args.start)
    if datetime.now(JST) >= start:
        raise ValueError('発走予定時刻以降の新規保存は禁止です')
    now = datetime.now(JST)
    if cutoff < now.date():
        raise ValueError('過去日の新規スナップショットは作れません（事前予想のみ）')
    if not re.fullmatch(r'\d{10}', args.race_id):
        raise ValueError('race-idは10桁の数字です')
    if args.venue not in VENUES or args.surface not in ('芝', 'ダート') or not 800 <= args.distance <= 4000:
        raise ValueError('競馬場・馬場・距離を確認してください')
    out = Path(args.output).resolve()
    if out.exists():
        raise FileExistsError('保存済みファイルは上書きしません: ' + str(out))
    if out.suffix.lower() != '.json':
        raise ValueError('出力ファイルは.jsonを指定してください')
    _, extractor, scoring = modules()
    session = requests.Session()
    url = f'https://sports.yahoo.co.jp/keiba/race/denma/{args.race_id}?detail=1'
    html = fetch(session, url)
    horses = extractor.extract(html)
    if not horses:
        raise ValueError('出走馬が取得できません')
    numbers = [h['horse_number'] for h in horses]
    if len(set(numbers)) != len(numbers):
        raise ValueError('馬番が重複しています')
    old, scores = prediction(horses, scoring, args.venue, args.surface, args.distance, cutoff)
    if datetime.now(JST) >= start:
        raise ValueError('データ取得中に発走予定時刻を過ぎました。保存しません')
    payload = {
        'scheduled_start_jst': start.isoformat(),
        'schema': 'keiba-forward-v1', 'captured_at_jst': now.isoformat(),
        'race_id': args.race_id, 'race_date': args.date, 'venue': args.venue,
        'surface': args.surface, 'distance_m': args.distance,
        'source_url': url, 'source_html_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
        'horse_count': len(horses), 'horses_and_runs': horses,
        'old_ranking': old, 'coefficient_rankings': scores,
        'warning': '出馬表の開催条件・発走時刻は外部照合が必要。実際の発走前に保存したか確認してください。'
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print('保存:', out)
    print('取得時刻（JST）:', now.isoformat(), '/ 出走馬:', len(horses))
    print('旧方式 上位3:', [(h['number'], h['name']) for h in old[:3]])
    for coef in COEFFICIENTS:
        print(f'係数{coef:.1f} 上位3:', [(h['number'], h['name']) for h in scores[str(coef)][:3]])
    print('注意: レース前に保存したことと、レース条件・発走時刻を必ず確認してください。')


def verify(args):
    path = Path(args.snapshot)
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get('schema') != 'keiba-forward-v1':
        raise ValueError('未対応のスナップショットです')
    cutoff = date.fromisoformat(payload['race_date'])
    start_text = payload.get('scheduled_start_jst')
    if start_text:
        start = datetime.fromisoformat(start_text)
        if start.tzinfo is None or start.utcoffset() != timedelta(hours=9) or start.date() != cutoff:
            raise ValueError('保存された発走時刻が不正です')
    elif args.start:
        start = race_start(payload['race_date'], args.start)
    else:
        raise ValueError('旧形式の保存データには発走時刻がありません。公式発走時刻を確認し --start HH:MM を指定してください')
    if datetime.now(JST) < start + timedelta(minutes=45):
        raise ValueError('発走予定時刻から45分経過するまで照合禁止です（結果確定を保証するものではありません）')
    captured = datetime.fromisoformat(payload['captured_at_jst'])
    if captured.tzinfo is None or captured >= start:
        raise ValueError('発走前の保存と確認できないため照合中止')
    v4, _, _ = modules()
    rid = payload['race_id']
    session = requests.Session()
    result = v4.result_rows(fetch(session, f'https://sports.yahoo.co.jp/keiba/race/result/{rid}'))
    if len(result) < 2 or sorted(f for f, _ in result.values()) != list(range(1, len(result) + 1)):
        raise ValueError('結果に着順欠落・同着などがあり、自動判定を中止しました')
    snapshot_names = {h['horse_number']: h['horse_name'] for h in payload['horses_and_runs']}
    for number, (_, name) in result.items():
        if number not in snapshot_names or snapshot_names[number] != name:
            raise ValueError(f'{number}番: スナップショットと結果の馬名・馬番が一致しません')
    print('注意: 発走時刻経過と結果ページの着順検査を行いましたが、公式確定の保証はありません')
    print('照合:', rid, payload['venue'], payload['surface'], str(payload['distance_m']) + 'm')
    print('事前保存日時(JST):', payload['captured_at_jst'])
    print('保存ファイルSHA256:', hashlib.sha256(raw).hexdigest())
    rankings = [('旧方式', payload['old_ranking'])] + [(f'係数{c:.1f}', payload['coefficient_rankings'][str(c)]) for c in COEFFICIENTS]
    for label, ranking in rankings:
        top = ranking[:3]
        if len(top) < 3:
            raise ValueError('保存された予想頭数が3未満です')
        finishes = [result.get(h['number'], (None,))[0] for h in top]
        if any(f is None for f in finishes):
            raise ValueError('予想馬の一部に結果がありません（取消等）。自動判定を中止しました')
        print(f'{label}: 上位3={[(h["number"], f) for h, f in zip(top, finishes)]} / '
              f'予想1位3着内={int(finishes[0] <= 3)} / 勝利={int(finishes[0] == 1)} / '
              f'上位3頭的中={sum(f <= 3 for f in finishes)}/3')
    print('照合のみ。予想スナップショットは変更していません。投稿なし。')


def main():
    parser = argparse.ArgumentParser(description='独立型・事前予想保存と事後照合（投稿なし）')
    sub = parser.add_subparsers(dest='action', required=True)
    c = sub.add_parser('capture', help='レース前の予想を新規JSONに保存（上書き禁止）')
    c.add_argument('--race-id', required=True)
    c.add_argument('--date', required=True, help='YYYY-MM-DD')
    c.add_argument('--venue', required=True, choices=VENUES)
    c.add_argument('--surface', required=True, choices=['芝', 'ダート'])
    c.add_argument('--distance', required=True, type=int, help='距離m')
    c.add_argument('--output', required=True, help='保存先.json')
    c.add_argument('--start', required=True, help='公式発走予定時刻 JST HH:MM')
    v = sub.add_parser('verify', help='保存済みJSONとレース結果を比較')
    v.add_argument('--snapshot', required=True)
    v.add_argument('--start', help='旧形式JSONのみ必須: 公式発走予定時刻 JST HH:MM')
    args = parser.parse_args()
    try:
        {'capture': capture, 'verify': verify}[args.action](args)
    except Exception as exc:
        print('エラー:', exc, file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
