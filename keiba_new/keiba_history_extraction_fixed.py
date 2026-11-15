"""独立したYahoo!競馬過去走抽出テスト。本番コードは変更しない。"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_URL = 'https://sports.yahoo.co.jp/keiba/race/denma/2605050311?detail=1'
VENUES = '札幌 函館 福島 新潟 東京 中山 中京 京都 阪神 小倉'.split()


def extract(html):
    soup = BeautifulSoup(html, 'html.parser')
    names = soup.select_one('table.hr-tableScrollFix')
    forms = soup.select_one('table.hr-tableScroll')
    if not names or not forms:
        raise ValueError('Yahoo出馬表の対応する2テーブルが見つかりません。HTMLを保存して構造を確認してください。')
    name_rows = names.select('tr')[1:]
    form_rows = forms.select('tr')[1:]
    if not name_rows or len(name_rows) != len(form_rows):
        raise ValueError('出走馬と過去走の行数が一致しません。')
    result = []
    for row, form in zip(name_rows, form_rows):
        num = row.select_one('.hr-denma__number')
        name = row.select_one('a[href^="/keiba/directory/horse/"]')
        if not num or not name:
            raise ValueError('馬番または馬名が取得できません。')
        runs = []
        for cell in form.select('td.hr-denma__third, td.hr-tableScroll__data--race'):
            date_node = cell.select_one('p.hr-denma__date span')
            finish_node = cell.select_one('.hr-denma__arrival')
            if not date_node or not finish_node:
                continue
            date_str = date_node.get_text(' ', strip=True)
            finish_str = finish_node.get_text(' ', strip=True)
            raw = cell.get_text(' ', strip=True)
            if not re.fullmatch(r'20\d{2}/\d{1,2}/\d{1,2}', date_str):
                continue
            field = re.search(r'(?<!\d)(\d{1,2})頭', raw)
            # 芝・ダート表記を厳密に確認。障害・不明は推測しない。
            course = re.search(r'(芝|ダート)(?:[・\s]*(?:右|左|直線|内|外))*[・\s]*(\d{3,4})m', raw)
            venue = re.search(r'20\d{2}/\d{1,2}/\d{1,2}\s*(' + '|'.join(VENUES) + r')', raw)
            surface = course.group(1) if course else None
            distance = int(course.group(2)) if course else None
            runs.append({
                'date': date_str, 'finish': int(finish_str) if finish_str.isdigit() else None,
                'field_size': int(field.group(1)) if field else None,
                'venue': venue.group(1) if venue else None,
                'surface': surface, 'distance_m': distance,
                'raw_text': raw,
            })
        result.append({'horse_number': int(num.get_text(strip=True)),
                       'horse_name': name.get_text(' ', strip=True), 'runs': runs[:5]})
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default=DEFAULT_URL)
    p.add_argument('--html', help='ローカルHTMLで試す場合')
    p.add_argument('--output', default='keiba_history_extraction_test.json')
    a = p.parse_args()
    if a.html:
        html = Path(a.html).read_text(encoding='utf-8')
    else:
        parsed = urlparse(a.url)
        if parsed.scheme != 'https' or parsed.hostname != 'sports.yahoo.co.jp' or not re.fullmatch(r'/keiba/race/denma/\d{10}', parsed.path):
            p.error('sports.yahoo.co.jp の出馬表URLだけを指定してください。')
        r = requests.get(a.url, timeout=25, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'ja-JP,ja;q=0.9'})
        r.raise_for_status()
        r.encoding = r.apparent_encoding
        html = r.text
    data = extract(html)
    Path(a.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    runs = [run for horse in data for run in horse['runs']]
    complete = sum(run['surface'] is not None and run['distance_m'] is not None and run['venue'] is not None for run in runs)
    print(f'出走馬: {len(data)}頭 / 過去走: {len(runs)}件 / 距離・芝ダート・競馬場の3項目取得: {complete}件')
    print(f'結果: {a.output}')
    if complete != len(runs):
        print('注意: 未取得項目があります。推測で補完せず、JSONのraw_textを確認してください。')


if __name__ == '__main__':
    main()
