"""JRA middle-odds editorial drafts from user-supplied, authorized data only.

No scraping, paid APIs, LLM, Blogger posting, or network requests.
No prediction is a calibrated probability. For entertainment/editorial use only.
"""
from __future__ import annotations
import argparse
import html
import json
import math
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
RACES = (10, 11, 12)
GRADE_RANK = {'G1': 60, 'G2': 50, 'G3': 40, 'J-G1': 35, 'J-G2': 30, 'J-G3': 25, 'L': 20, 'OP': 10, 'OTHER': 0}
SCORE_WEIGHTS = {'recent_form_rating': .45, 'course_distance_rating': .35, 'pace_rating': .20}
ODDS_MIN, ODDS_MAX = 5.0, 20.0

class DataError(ValueError):
    pass

def _number(value, low, high, description):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise DataError(f'{description}: {value!r} は {low}〜{high} の数値である必要があります')
    return float(value)

def _timestamp(value, description):
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f'{description}: タイムゾーン付きISO8601日時が必要') from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise DataError(f'{description}: タイムゾーンが必要')
    return dt.astimezone(JST)

def _text(value, description):
    if not isinstance(value, str) or not value.strip():
        raise DataError(f'{description} が空です')
    return value.strip()

def _rating(horse):
    return sum(_number(horse.get(key), 0, 100, key) * weight for key, weight in SCORE_WEIGHTS.items())

def validate(payload, target_date, *, demo=False):
    if not isinstance(payload, dict) or payload.get('date') != target_date:
        raise DataError('日付が一致しません')
    try:
        day = date.fromisoformat(target_date)
    except ValueError as exc:
        raise DataError('日付が不正です') from exc
    if day.weekday() not in (5, 6):
        raise DataError('対象は土日開催のみです（祝日の変則開催は現バージョン非対応）')
    if bool(payload.get('synthetic_demo')) != bool(demo):
        raise DataError('実データとデモデータの実行モードが一致しません')
    if not demo:
        # This flag is an operator attestation, not automatic legal verification.
        if payload.get('reuse_permission_verified') is not True:
            raise DataError('データの自動処理・予想記事への利用許可が未確認です')
        _text(payload.get('permission_evidence'), '許可条件・根拠')
        _text(payload.get('source_name'), 'データ提供元')
        source = _text(payload.get('source_url'), '参照URL')
        if not source.startswith('https://'):
            raise DataError('source_urlにはhttps URLが必要です')
    asof = _timestamp(payload.get('data_asof'), 'data_asof')
    if asof.date().isoformat() > target_date:
        raise DataError('予想日より後の情報が混じっています')
    if not demo and asof < datetime.combine(day, datetime.min.time(), JST) - timedelta(days=2):
        raise DataError('当日の予想にはデータの基準時刻が古すぎます（2日前以降が必要）')
    records = payload.get('races')
    if not isinstance(records, list) or not records:
        raise DataError('レース情報がありません')
    race_map = {}
    for race in records:
        if not isinstance(race, dict):
            raise DataError('レース情報の形式が不正です')
        num = race.get('race_number')
        if type(num) is not int or num not in RACES:
            continue
        venue = _text(race.get('venue'), '競馬場')
        key = (venue, num)
        if key in race_map:
            raise DataError(f'{venue} {num}Rが重複しています')
        _text(race.get('race_name'), 'レース名')
        if race.get('grade') not in GRADE_RANK:
            raise DataError(f'{venue} {num}Rの格付けが不正です')
        post = _timestamp(race.get('post_time'), '発走日時')
        if post.date().isoformat() != target_date:
            raise DataError(f'{venue} {num}Rの発走日付が違います')
        if asof >= post:
            raise DataError(f'{venue} {num}R: 発走後のデータです（結果の漏洩防止）')
        if not demo and post - asof > timedelta(hours=36):
            raise DataError(f'{venue} {num}R: 発走まで36時間以上あり、オッズの鮮度を確認できません')
        horses = race.get('horses')
        if not isinstance(horses, list) or len(horses) < 5:
            raise DataError(f'{venue} {num}R: 出走馬データが不足しています')
        seen_numbers, seen_names = set(), set()
        for horse in horses:
            n = horse.get('number')
            name = _text(horse.get('name'), '馬名')
            if type(n) is not int or not 1 <= n <= 30 or n in seen_numbers or name in seen_names:
                raise DataError(f'{venue} {num}R: 馬番・馬名に重複または不正値があります')
            seen_numbers.add(n); seen_names.add(name)
            _number(horse.get('win_odds'), 1.01, 1000, '単勝オッズ')
            _rating(horse)
            if horse.get('scratched') not in (None, False):
                raise DataError(f'{venue} {num}R: 出走取消を含みます。更新済みデータが必要です')
        # Never accept arbitrary ratings without documentation of how they were derived.
        _text(race.get('ratings_method'), f'{venue} {num}R 評価値の算出根拠')
        _number(race.get('editorial_priority', 0), 0, 100, 'editorial_priority')
        race_map[key] = race
    venues = sorted({v for v, _ in race_map if all((v, n) in race_map for n in RACES)})
    if not venues:
        raise DataError('10・11・12Rがそろった競馬場がありません')
    chosen = max(venues, key=lambda v: (GRADE_RANK[race_map[v, 11]['grade']], race_map[v, 11].get('editorial_priority', 0), v))
    selected = [race_map[chosen, n] for n in RACES]
    for race in selected:
        if not any(ODDS_MIN <= h['win_odds'] <= ODDS_MAX for h in race['horses']):
            raise DataError(f'{chosen} {race["race_number"]}Rに中穴候補がいません（無理に選びません）')
    return chosen, selected

def picks_for(race):
    ranked = sorted(race['horses'], key=lambda h: (-_rating(h), h['number']))
    mids = [h for h in ranked if ODDS_MIN <= h['win_odds'] <= ODDS_MAX]
    best = mids[0]
    others = [h for h in ranked if h is not best]
    return [best, *others[:2]]

def render(payload, venue, races, *, demo=False):
    """Render an offline HTML draft without changing scoring or selection."""
    escape = lambda value: html.escape(str(value), quote=True)
    label = '【架空データ／テスト専用・公開禁止】' if demo else '【中穴競馬予想】'

    parts = [
        '<!doctype html>',
        '<html lang="ja">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{escape(label)} {escape(payload["date"])} {escape(venue)} 10R・11R・12R</title>',
        '<style>',
        '*, *::before, *::after { box-sizing: border-box; }',
        'body { margin: 0; padding: 24px 16px; background: #f3f5f8; color: #20252c; '
        'font-family: "Yu Gothic", "Hiragino Kaku Gothic ProN", Meiryo, sans-serif; '
        'font-size: 20px; line-height: 1.8; }',
        '.page { max-width: 960px; margin: 0 auto; background: white; padding: 30px; '
        'border-radius: 14px; }',
        'h1 { font-size: 32px; line-height: 1.5; margin: 14px 0 20px; }',
        'h2 { font-size: 27px; line-height: 1.5; margin: 0 0 18px; '
        'border-bottom: 3px solid #364c66; padding-bottom: 12px; }',
        'h3 { font-size: 21px; margin: 22px 0 10px; }',
        'p { margin: 12px 0 20px; }',
        '.tag { display: inline-block; font-size: 18px; font-weight: bold; '
        'padding: 6px 12px; border-radius: 8px; background: #a62b2b; color: white; }',
        '.tag.real { background: #245786; }',
        '.meta { padding: 16px 20px; background: #edf3fa; border-radius: 10px; '
        'margin: 24px 0; overflow-wrap: anywhere; }',
        '.race { padding: 24px; margin: 30px 0; border: 1px solid #d6dce5; '
        'border-radius: 12px; }',
        '.post { font-size: 18px; color: #4c5664; font-weight: normal; }',
        '.picks { list-style: none; padding: 0; margin: 0; }',
        '.picks li { margin: 14px 0; padding: 15px 18px; background: #f6f8fb; '
        'border-left: 5px solid #7a92b0; border-radius: 6px; overflow-wrap: anywhere; }',
        '.picks strong { font-size: 22px; }',
        '.details { margin-top: 24px; padding: 18px 20px; background: #f8fafc; '
        'border-radius: 10px; overflow-wrap: anywhere; }',
        '.details p { margin: 8px 0 14px; }',
        '.details ul { margin: 4px 0 18px; padding-left: 1.5em; }',
        '.details li { margin: 5px 0; }',
        '.notice { margin-top: 28px; background: #fff7e8; padding: 18px 20px; '
        'border-radius: 10px; }',
        'a { overflow-wrap: anywhere; }',
        '@media (max-width: 600px) { body { padding: 8px; font-size: 18px; } '
        '.page { padding: 18px 14px; } .race { padding: 18px 12px; } '
        'h1 { font-size: 26px; } h2 { font-size: 23px; } '
        '.picks strong { font-size: 20px; } }',
        '</style>',
        '</head>',
        '<body><main class="page">',
        f'<div class="tag{"" if demo else " real"}">{escape(label)}</div>',
        f'<h1>{escape(payload["date"])} {escape(venue)} 10R・11R・12R</h1>',
        '<p>過去成績などから計算した独自の注目馬スコアです。'
        '勝率や収益を示すものではありません。</p>',
        '<div class="meta">',
        f'<strong>データ基準時刻：</strong>{escape(payload["data_asof"])}',
        '<p>※単勝オッズは変動します。</p>',
        '</div>',
    ]

    if not demo:
        parts.append(
            '<p><strong>データ出典：</strong>'
            f'<a href="{escape(payload["source_url"])}">'
            f'{escape(payload["source_name"])}</a></p>'
        )

    for race in races:
        parts.extend([
            '<section class="race">',
            f'<h2>{escape(venue)} {race["race_number"]}R '
            f'{escape(race["race_name"])}'
            f'<span class="post">（{escape(race["post_time"])}発走）</span></h2>',
            '<ul class="picks">',
        ])
        for mark, horse in zip(('◎ 中穴注目', '○ 対抗', '▲ 単穴'), picks_for(race)):
            parts.append(
                f'<li><strong>{mark}</strong><br>'
                f'{horse["number"]}番 {escape(horse["name"])}<br>'
                f'参考単勝：{horse["win_odds"]:.1f}倍<br>'
                f'評価：{_rating(horse):.1f}点</li>'
            )
        parts.extend([
            '</ul>',
            '<div class="details">',
            '<h3>【評価根拠】</h3>',
            f'<p>{escape(race["ratings_method"])}</p>',
            '<h3>【評価配分】</h3>',
            '<ul>',
            '<li>近走成績：45％</li>',
            '<li>コース・距離適性：35％</li>',
            '<li>展開・脚質：20％</li>',
            '</ul>',
            '<h3>【注意事項】</h3>',
            '<p>この評価は勝率ではありません。</p>',
            '</div>',
            '</section>',
        ])

    parts.extend([
        '<div class="notice"><strong>ご注意</strong>',
        '<p>5〜20倍は中穴候補を選ぶための目安で、利益を保証しません。'
        '出走取消・馬場・オッズの変更に注意してください。</p>',
        '</div>',
        '</main></body></html>',
    ])
    return '\n'.join(parts) + '\n'

def main(argv=None):
    parser = argparse.ArgumentParser(description='競馬記事下書き：無許諾データは使わず、メール送信しません')
    parser.add_argument('--date', required=True)
    parser.add_argument('--data-dir', default='keiba_data')
    parser.add_argument('--out-dir', default='keiba_drafts')
    parser.add_argument('--demo', action='store_true', help='架空データのみ。生成物は公開禁止')
    args = parser.parse_args(argv)
    date.fromisoformat(args.date)
    path = Path(args.data_dir) / f'{args.date}.json'
    if args.demo:
        path = Path('examples') / 'demo_synthetic.json'
    if not path.exists():
        print('SKIP: 許可済みレースデータがありません。記事・メールを生成しません')
        return 0
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        venue, races = validate(payload, args.date, demo=args.demo)
        article = render(payload, venue, races, demo=args.demo)
    except (DataError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print('SKIP: データ検証失敗:', exc)
        return 0
    output = Path(args.out_dir) / f'{args.date}{"_DEMO_DO_NOT_POST" if args.demo else ""}.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(article, encoding='utf-8')
    print(f'DRAFT_READY: {output} / 競馬場: {venue} / 送信なし・公開なし')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
