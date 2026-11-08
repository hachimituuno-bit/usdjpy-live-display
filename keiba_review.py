"""Offline prediction audit: synthetic/demo or operator-supplied authorized results only.

Reports one hypothetical 100-yen win bet on each chosen 5x-20x longshot.
No network, no publishing, no actual bets. The payoff MUST be actual JRA/NAR
100-yen payout, not pre-race indicative odds.
"""
import argparse
import csv
import html
import json
import math
from datetime import date, datetime
from pathlib import Path

from keiba_predict import validate, picks_for, DataError


def read_results(path, day, demo):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError('結果CSVが空です')
    required = {'date', 'venue', 'race_number', 'winning_number', 'win_payout_100', 'synthetic_demo', 'result_source'}
    if not required.issubset(rows[0]):
        raise ValueError('結果CSVの列が不足しています: ' + ', '.join(sorted(required - set(rows[0]))))
    found = {}
    for row in rows:
        if row['date'] != day:
            raise ValueError('結果の日付が一致しません')
        if row['synthetic_demo'].strip().lower() not in ('true', 'false'):
            raise ValueError('synthetic_demoはtrueまたはfalseを入力してください')
        if (row['synthetic_demo'].strip().lower() == 'true') != demo:
            raise ValueError('結果データのデモ区分が一致しません')
        venue = row['venue'].strip()
        if not venue or not row['result_source'].strip():
            raise ValueError('競馬場・結果出典が空です')
        try:
            rn = int(row['race_number'])
            winner = int(row['winning_number'])
            payout = int(row['win_payout_100'])
        except ValueError as exc:
            raise ValueError('レース番号・勝馬番号・単勝払戻額には整数が必要です') from exc
        if rn not in (10, 11, 12) or not 1 <= winner <= 30 or payout < 100 or payout > 1_000_000:
            raise ValueError('レース番号・勝馬番号・単勝払戻額が範囲外です')
        key = (venue, rn)
        if key in found:
            raise ValueError(f'重複する結果があります: {venue} {rn}R')
        found[key] = {'winner': winner, 'payout': payout, 'source': row['result_source'].strip()}
    return found


def evaluate(payload, day, result_rows, *, demo):
    venue, races = validate(payload, day, demo=demo)
    records = []
    for race in races:
        number = race['race_number']
        key = (venue, number)
        if key not in result_rows:
            raise ValueError(f'結果が不足しています: {venue} {number}R。未確定なら実行しないでください')
        result = result_rows[key]
        if result['winner'] not in {h['number'] for h in race['horses']}:
            raise ValueError(f'{venue} {number}R: 勝馬番号が出走表に存在しません')
        pick = picks_for(race)[0]
        hit = pick['number'] == result['winner']
        records.append({'race': number, 'horse': pick['name'], 'number': pick['number'],
                        'predicted_odds': pick['win_odds'], 'winner': result['winner'],
                        'return_yen': result['payout'] if hit else 0, 'hit': hit,
                        'source': result['source']})
    stake = 100 * len(records)
    return {'date': day, 'venue': venue, 'demo': demo, 'races': records,
            'count': len(records), 'hits': sum(x['hit'] for x in records),
            'stake_yen': stake, 'return_yen': sum(x['return_yen'] for x in records),
            'profit_yen': sum(x['return_yen'] for x in records) - stake,
            'hit_rate_pct': round(100 * sum(x['hit'] for x in records) / len(records), 1),
            'return_rate_pct': round(100 * sum(x['return_yen'] for x in records) / stake, 1)}


def render(report):
    e = lambda s: html.escape(str(s), quote=True)
    title = '【架空データ・テスト専用・公開禁止】' if report['demo'] else '【競馬予想の事後検証】'
    lines = ['<!doctype html>', '<html lang="ja"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>競馬予想の事後検証</title>', '<style>',
             'body{font-family:Meiryo,"Yu Gothic",sans-serif;background:#f6f7fa;color:#222;font-size:19px;line-height:1.8;margin:0}',
             'main{max-width:900px;background:white;margin:24px auto;padding:24px;border-radius:12px}',
             'h1{font-size:30px}h2{font-size:24px;border-bottom:2px solid #bbb;margin-top:32px}',
             '.metric{background:#eef3f8;padding:16px;border-radius:10px;margin:12px 0}',
             '.small{font-size:16px;color:#555}table{border-collapse:collapse;width:100%}',
             'th,td{border-bottom:1px solid #ddd;padding:12px;text-align:left}',
             '@media(max-width:600px){body{font-size:17px}main{padding:14px;margin:0}table{font-size:14px}th,td{padding:5px}}',
             '</style></head><body><main>', f'<h1>{title}</h1>',
             f'<p>{e(report["date"])} ／ {e(report["venue"])} ／ 10R・11R・12R</p>',
             '<p class="small">◎中穴注目を各レース単勝100円ずつ購入したと仮定したシミュレーションです。実際の投票は行いません。</p>',
             f'<div class="metric"><strong>的中：{report["hits"]}/{report["count"]}レース（{report["hit_rate_pct"]:.1f}％）</strong></div>',
             f'<div class="metric">仮想投資額：{report["stake_yen"]:,}円<br>払戻合計：{report["return_yen"]:,}円<br>',
             f'収支：{report["profit_yen"]:+,}円<br><strong>回収率：{report["return_rate_pct"]:.1f}％</strong></div>',
             '<h2>レース別の検証</h2>',
             '<table><thead><tr><th>レース</th><th>◎予想馬</th><th>結果</th><th>払戻</th></tr></thead><tbody>']
    for r in report['races']:
        lines.append(f'<tr><td>{r["race"]}R</td><td>{r["number"]}番 {e(r["horse"])}<br><span class="small">予想時単勝 {r["predicted_odds"]:.1f}倍</span></td>'
                     f'<td>{"的中" if r["hit"] else "不的中"}<br><span class="small">勝馬 {r["winner"]}番</span></td>'
                     f'<td>{r["return_yen"]:,}円</td></tr>')
    lines += ['</tbody></table>', '<h2>重要事項</h2>',
              '<p>払戻はレース後に確定した単勝100円当たりの払戻額で計算しています。予想時オッズは払戻計算には使用しません。</p>',
              '<p>わずか3レースでは予想精度や将来の収益性は判定できません。データ入力の正確性や出典の利用権限は別途確認が必要です。</p>',
              '<p class="small">結果出典：' + ' ／ '.join(e(r['source']) for r in report['races']) + '</p>',
              '</main></body></html>']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='オフライン競馬予想の事後検証。送信・投稿なし')
    ap.add_argument('--date', required=True)
    ap.add_argument('--predictions', required=True, help='既存のkeiba_data形式JSON')
    ap.add_argument('--results', required=True, help='確定結果CSV')
    ap.add_argument('--out-dir', default='keiba_reviews')
    ap.add_argument('--demo', action='store_true')
    args = ap.parse_args(argv)
    date.fromisoformat(args.date)
    try:
        payload = json.loads(Path(args.predictions).read_text(encoding='utf-8'))
        rows = read_results(args.results, args.date, args.demo)
        report = evaluate(payload, args.date, rows, demo=args.demo)
    except (ValueError, DataError, KeyError, TypeError, OSError) as exc:
        print('ERROR: 検証できません:', exc)
        return 1
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = '_DEMO_DO_NOT_POST' if args.demo else ''
    base = out / f'{args.date}_review{suffix}'
    base.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    base.with_suffix('.html').write_text(render(report), encoding='utf-8')
    print(f'REVIEW_READY: {base}.html / 的中 {report["hits"]}/{report["count"]} / 回収率 {report["return_rate_pct"]:.1f}% / 送信なし・公開なし')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
