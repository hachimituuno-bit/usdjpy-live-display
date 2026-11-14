"""V4: 本番旧方式の丸め・同点順位規則を反映した独立検証用バッチ比較。既存ファイルを変更せず、Blogger/GitHubに接続しない。
必要: requests, beautifulsoup4。CSV: race_id,race_date,venue,surface,distance_m
注意: 現在の出馬表が当時の公開状態を再現している保証はない。未来情報混入の監査が別途必要。
"""
import argparse
import csv
import importlib.util
import re
import time
from datetime import date
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
VENUES = '札幌 函館 福島 新潟 東京 中山 中京 京都 阪神 小倉'.split()

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f'必要なファイルがありません: {path}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def get(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text

def result_rows(html):
    soup = BeautifulSoup(html, 'html.parser')
    candidates = []
    for table in soup.select('table'):
        heading = ' '.join(h.get_text(' ', strip=True) for h in table.select('th'))
        if not all(x in heading for x in ('着順', '馬番', '馬名')):
            continue
        rows = {}
        for tr in table.select('tr'):
            tds = tr.find_all('td', recursive=False)
            if len(tds) < 4:
                continue
            ft, nt = tds[0].get_text(' ', strip=True), tds[2].get_text(' ', strip=True)
            if not ft.isdigit() or not nt.isdigit():
                continue
            number = int(nt)
            link = tds[3].select_one('a[href*="/horse/"]')
            name = link.get_text(' ', strip=True) if link else tds[3].get_text(' ', strip=True).split()[0]
            if not name or number in rows:
                raise ValueError('結果の馬名欠落・馬番重複')
            rows[number] = (int(ft), name)
        if rows:
            candidates.append(rows)
    if len(candidates) != 1:
        raise ValueError(f'確定着順テーブルが一意に特定できません: {len(candidates)}件')
    return candidates[0]

def evaluate(spec, session, extractor, scoring):
    rid = spec['race_id'].strip()
    if not re.fullmatch(r'\d{10}', rid):
        raise ValueError('レースIDが不正')
    cutoff = date.fromisoformat(spec['race_date'].strip())
    venue, surface, distance = spec['venue'].strip(), spec['surface'].strip(), int(spec['distance_m'])
    if venue not in VENUES or surface not in ('芝','ダート') or not 800 <= distance <= 4000:
        raise ValueError('レース条件が不正')
    horses = extractor.extract(get(session, f'https://sports.yahoo.co.jp/keiba/race/denma/{rid}?detail=1'))
    results = result_rows(get(session, f'https://sports.yahoo.co.jp/keiba/race/result/{rid}'))
    by_number = {h['horse_number']: h for h in horses}
    if len(by_number) != len(horses):
        raise ValueError('出馬表の馬番重複')
    if not set(results).issubset(by_number):
        raise ValueError('結果に出馬表にない馬番がある')
    if len(results) < 2 or sorted(f for f, _ in results.values()) != list(range(1,len(results)+1)):
        raise ValueError('結果の着順に欠落・同着などがある')
    removed = set(by_number) - set(results)
    scored = []
    obstacle_count = 0
    for number, (finish, result_name) in results.items():
        horse = by_number[number]
        if horse['horse_name'] != result_name:
            raise ValueError(f'{number}番の馬名不一致')
        runs = horse['runs']
        for run in runs:
            if date.fromisoformat(run['date'].replace('/','-')) >= cutoff:
                raise ValueError('レース当日以降の過去走が混入')
        obstacle_count += sum('障害' in r.get('raw_text','') for r in runs)
        flat = [r for r in runs if '障害' not in r.get('raw_text','')]
        flat.sort(key=lambda r: date.fromisoformat(r['date'].replace('/','-')), reverse=True)
        flat = flat[:5]
        if not flat:
            raise ValueError(f'{horse["horse_name"]}: 平地の過去走なし（新馬など）')
        for r in flat:
            if scoring.run_value(r) is None:
                raise ValueError(f'{horse["horse_name"]}: 平地の着順・頭数が不明')
            if r.get('venue') not in VENUES or r.get('surface') not in ('芝','ダート') or type(r.get('distance_m')) is not int:
                raise ValueError(f'{horse["horse_name"]}: 平地の適性データ不明')
        old = scoring.score(flat)
        new = scoring.score(flat, lambda r: scoring.similarity(r, venue, surface, distance))
        if old is None or new is None:
            raise ValueError(f'{horse["horse_name"]}: 採点不可')
        scored.append({'number':number, 'finish':finish, 'old':round(100.0 * old, 2), 'new':new, 'completed_runs':len(flat)})
    outcomes = {}
    for method in ('old','new'):
        ranked = sorted(scored, key=(lambda h:(-h['old'],-h['completed_runs'],h['number'])) if method == 'old' else (lambda h:(-h['new'],h['number'])))
        outcomes[method] = {'top1_in3':int(ranked[0]['finish']<=3), 'top1_win':int(ranked[0]['finish']==1), 'top3_hits':sum(h['finish']<=3 for h in ranked[:3]), 'top3':[(h['number'],h['finish']) for h in ranked[:3]]}
    return outcomes, len(removed), obstacle_count

def main():
    p = argparse.ArgumentParser(description='V4 検証専用: 本番旧方式の丸め・同点規則で新方式と比較')
    p.add_argument('--races', required=True, help='対象レースCSV')
    p.add_argument('--delay', type=float, default=2.0, help='レース間待機秒数（最低1秒）')
    args = p.parse_args()
    extractor = load(ROOT / 'keiba_history_extraction_fixed.py', 'keiba_extract_v3')
    scoring = load(ROOT / 'keiba_backtest.py', 'keiba_score_v3')
    with open(args.races, encoding='utf-8-sig', newline='') as f:
        specs = list(csv.DictReader(f))
    required = {'race_id','race_date','venue','surface','distance_m'}
    if not 1 <= len(specs) <= 100 or not required.issubset(specs[0]):
        p.error('CSVは必要列を持つ1～100レースにしてください')
    session = requests.Session()
    session.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'ja-JP,ja;q=0.9'})
    totals = {m:{'top1_in3':0,'top1_win':0,'top3_hits':0} for m in ('old','new')}
    valid = 0
    for i,spec in enumerate(specs,1):
        if i>1: time.sleep(max(1.0,args.delay))
        try:
            outcome, removed, obstacles = evaluate(spec, session, extractor, scoring)
            valid += 1
            for m in totals:
                for metric in totals[m]: totals[m][metric] += outcome[m][metric]
            print(f'{i}/{len(specs)} OK {spec["race_id"]} / 取消等{removed}頭 / 障害走{obstacles}件',flush=True)
            for m,label in (('old','旧'),('new','新')):
                print(f'  {label}: 上位3頭(馬番,実着順)={outcome[m]["top3"]}',flush=True)
        except (ValueError,KeyError,TypeError,requests.RequestException) as exc:
            print(f'{i}/{len(specs)} 除外 {spec.get("race_id","?")}: {exc}',flush=True)
    print(f'検証: 有効{valid} / 除外{len(specs)-valid}')
    if valid:
        for m,label in (('old','検証用旧'),('new','検証用新')):
            t=totals[m]
            print(f'{label}: 予想1位3着内={t["top1_in3"]}/{valid} ({t["top1_in3"]/valid:.1%}) / 勝利={t["top1_win"]}/{valid} ({t["top1_win"]/valid:.1%}) / 上位3頭的中={t["top3_hits"]}/{valid*3}')
    print('注意: 旧方式の丸め・同点規則は本番に合わせたが、障害走の扱い・取得データ・出馬表時点の一致は未監査。新馬や同着は除外。書き込み・投稿なし。')

if __name__=='__main__':
    main()
