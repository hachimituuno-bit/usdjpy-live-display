"""競馬予想の独立バックテスト。GitHub/Bloggerへのアクセス・投稿なし。

入力: レースごとの出走馬、レース前の過去走、確定着順を含むJSON。
予想対象レースの結果を過去走へ混ぜない（date < race_date）。
同じ出走馬・同じレースで旧方式と新方式を比較する。
"""
import argparse
import json
from datetime import date
from pathlib import Path


def run_value(r):
    f, n = r.get('finish'), r.get('field_size')
    if type(f) is not int or type(n) is not int or not 1 <= f <= n:
        return None
    relative = 1.0 if n == 1 else (n - f) / (n - 1)
    return 0.85 * (f <= 3) + 0.15 * relative


def similarity(r, venue, surface, distance):
    if r.get('surface') not in ('芝', 'ダート') or type(r.get('distance_m')) is not int:
        return None
    if r['surface'] != surface:
        return 0.0
    dist = max(0.0, 1.0 - abs(r['distance_m'] - distance) / 800.0)
    return 0.75 * dist + 0.25 * (r.get('venue') == venue)


def score(runs, condition=None):
    total = weight = 0.0
    for i, r in enumerate(runs):
        v = run_value(r)
        if v is None:
            continue
        w = 0.8 ** i
        if condition is not None:
            fit = condition(r)
            w *= 1 + 2 * (0.5 if fit is None else fit)
        total += w * v
        weight += w
    return total / weight if weight else None


def evaluate_race(race):
    required = ('race_date', 'venue', 'surface', 'distance_m', 'horses')
    if any(k not in race for k in required):
        raise ValueError('レースの必須項目が不足: ' + ', '.join(k for k in required if k not in race))
    cutoff = date.fromisoformat(race['race_date'])
    if race['surface'] not in ('芝', 'ダート') or type(race['distance_m']) is not int:
        raise ValueError('レース条件が不正')
    if not isinstance(race['horses'], list) or len(race['horses']) < 2:
        raise ValueError('出走馬が不足')
    seen = set()
    scored = []
    for h in race['horses']:
        number = h['horse_number']
        if type(number) is not int or number in seen:
            raise ValueError('馬番が重複または不正')
        seen.add(number)
        finish = h.get('actual_finish')
        if type(finish) is not int or not 1 <= finish <= len(race['horses']):
            raise ValueError(f'{number}番: 確定着順が不足または不正（全出走馬が必要）')
        history = []
        for r in h.get('runs', []):
            d = date.fromisoformat(r['date'].replace('/', '-'))
            if d >= cutoff:
                raise ValueError(f'{number}番: レース当日以降の過去走が含まれています')
            history.append((d, r))
        history.sort(key=lambda x: x[0], reverse=True)
        runs = [r for _, r in history[:5]]
        scored.append({'number': number, 'finish': finish,
                       'old': score(runs),
                       'new': score(runs, lambda r: similarity(r, race['venue'], race['surface'], race['distance_m']))})
    finishes = [h['finish'] for h in scored]
    if len(set(finishes)) != len(finishes):
        raise ValueError('確定着順が重複（同着は未対応）')
    if set(finishes) != set(range(1, len(scored) + 1)):
        raise ValueError('確定着順が全頭分そろっていません')
    if any(h['old'] is None or h['new'] is None for h in scored):
        raise ValueError('過去走のない馬がいます。比較条件を統一するためスキップ')
    outcome = {}
    for method in ('old', 'new'):
        ranked = sorted(scored, key=lambda h: (-h[method], h['number']))
        outcome[method] = {'top1_in3': int(ranked[0]['finish'] <= 3),
                           'top3_hits': sum(h['finish'] <= 3 for h in ranked[:3]),
                           'top1_win': int(ranked[0]['finish'] == 1)}
    return outcome


def main():
    p = argparse.ArgumentParser(description='旧・新方式を確定着順で比較（投稿なし）')
    p.add_argument('--input', required=True, help='バックテスト用JSON。形式は --template で作成')
    p.add_argument('--template', action='store_true', help='入力形式の説明のみ表示')
    args = p.parse_args()
    if args.template:
        print('JSON形式: {"races":[{"race_date":"YYYY-MM-DD","venue":"東京","surface":"ダート","distance_m":1600,"horses":[{"horse_number":1,"actual_finish":1,"runs":[{"date":"YYYY/MM/DD","finish":2,"field_size":16,"venue":"東京","surface":"ダート","distance_m":1600}]}]}]}')
        return
    data = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('races'), list):
        p.error('入力は {"races": [...]} 形式の確定結果付きJSONが必要です。過去走抽出JSONだけでは検証できません')
    totals = {m: {'top1_in3': 0, 'top3_hits': 0, 'top1_win': 0} for m in ('old', 'new')}
    count = 0
    for i, race in enumerate(data['races'], 1):
        try:
            result = evaluate_race(race)
        except (ValueError, KeyError, TypeError) as e:
            print(f'{i}レース目を除外: {e}')
            continue
        count += 1
        for method in ('old', 'new'):
            for key in totals[method]:
                totals[method][key] += result[method][key]
    if not count:
        print('検証可能なレースは0件。確定着順とレース前の全出走馬データが必要です。')
        return
    print(f'検証レース数: {count}')
    for m, label in (('old', '旧方式'), ('new', '新方式')):
        t = totals[m]
        print(f'{label}: 1位予想の3着内率 {t["top1_in3"] / count:.1%} / 1位予想の勝率 {t["top1_win"] / count:.1%} / 上位3頭の3着内的中数 {t["top3_hits"]}/{3*count}')
    print('注意: この結果は入力したレースに限ります。係数の調整には別期間での再検証が必要です。')


if __name__ == '__main__':
    main()
