"""Fetch one authorized JRA result page and export strict historical race JSON.

Standalone: does not import or modify the prediction/posting workflow.
Requires: pip install requests beautifulsoup4
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


def extract(url: str, html: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'www.jra.go.jp' or parsed.path != '/JRADB/accessS.html':
        raise ValueError('JRA公式の個別レース結果URLを指定してください')
    if not parse_qs(parsed.query).get('CNAME'):
        raise ValueError('CNAMEがありません')
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True)
    if 'パラメータエラー' in text or 'エラー013' in text:
        raise ValueError('JRAがパラメータエラーを返しました')
    # Avoid guessing the page layout: find the actual finish-order table by headers.
    result_table = None
    for table in soup.find_all('table'):
        heads = ' '.join(x.get_text(' ', strip=True) for x in table.find_all('th'))
        if all(x in heads for x in ('着順', '馬名', 'コーナー')):
            result_table = table
            break
    if result_table is None:
        raise ValueError('着順・馬名・コーナー通過順位の表が見つかりません')

    rows = []
    for tr in result_table.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue
        vals = [c.get_text(' ', strip=True) for c in cells]
        if not re.fullmatch(r'\d+', vals[0]):
            continue  # non-finishers are not silently assigned a finish
        if len(vals) < 10:
            raise ValueError('結果表の列数が想定外です')
        finish = int(vals[0])
        number = int(vals[2]) if re.fullmatch(r'\d+', vals[2]) else None
        name = vals[3]
        # JRA table: finish, frame, number, name, sex/age, weight, jockey,
        # time, margin, corner positions, ...
        corners_text = vals[9]
        if not number or not name or not re.fullmatch(r'\d+(?:\s+\d+)*', corners_text):
            raise ValueError(f'馬番・馬名・コーナー順位の解釈に失敗: {name!r} {corners_text!r}')
        corners = [int(n) for n in corners_text.split()]
        rows.append({'number': number, 'name': name, 'finish': finish, 'corner_positions': corners})
    if len(rows) < 2:
        raise ValueError('有効な着順データが不足しています')
    field = len(rows)
    if len({r['number'] for r in rows}) != field or len({r['finish'] for r in rows}) != field:
        raise ValueError('馬番または着順が重複しています')
    if sorted(r['finish'] for r in rows) != list(range(1, field + 1)):
        raise ValueError('着順に欠落があります。中止・失格などは現試作版では未対応です')
    for r in rows:
        if not all(1 <= p <= field for p in r['corner_positions']):
            raise ValueError('コーナー順位が出走頭数の範囲外です')
        r['field_size'] = field
    # Date and surface/distance are not guessed from CNAME or free text.
    return {'source_url': url, 'field_size': field, 'horses': rows,
            'note': '過去走データの一部。開催日・芝ダート・距離は別途検証が必要。'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True)
    p.add_argument('--output', default='jra_result_test.json')
    a = p.parse_args()
    parsed = urlparse(a.url)
    if parsed.scheme != 'https' or parsed.hostname != 'www.jra.go.jp' or parsed.path != '/JRADB/accessS.html':
        p.error('JRA公式のレース結果URLのみ対応')
    response = requests.get(a.url, timeout=20, headers={'User-Agent': 'Mozilla/5.0 (compatible; authorized-research/1.0)'})
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    data = extract(a.url, response.text)
    output = Path(a.output)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'OK: {output} / {data["field_size"]} horses / no posting')


if __name__ == '__main__':
    main()
