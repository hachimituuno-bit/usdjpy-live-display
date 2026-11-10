
"""Fetch one authorized JRA historical race result as JSON; no posting.

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
    if (parsed.scheme != 'https'
            or parsed.hostname != 'www.jra.go.jp'
            or parsed.path != '/JRADB/accessS.html'
            or not parse_qs(parsed.query).get('CNAME')):
        raise ValueError('JRA公式の個別レース結果URLを指定してください')

    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True)

    if 'パラメータエラー' in text or 'エラー013' in text:
        raise ValueError('JRAがパラメータエラーを返しました')

    # Identify the race-specific date heading, not navigation dates.
    heading = soup.find(
        string=re.compile(
            r'\d{4}年\s*\d{1,2}月\s*\d{1,2}日.*?発走時刻'
        )
    )
    if heading is None:
        raise ValueError('開催日と発走時刻を含むレース見出しが見つかりません')

    dates = re.findall(
        r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',
        str(heading)
    )
    if len(dates) != 1:
        raise ValueError('開催日を一意に特定できません')

    try:
        race_date = date(*map(int, dates[0])).isoformat()
    except ValueError as exc:
        raise ValueError('開催日が不正です') from exc

    # Example: コース：2,500メートル（芝・左）
    course_matches = re.findall(
        r'コース\s*[：:]\s*([\d,，]+)\s*メートル\s*[（(]\s*(芝|ダート)',
        text
    )
    if not course_matches or len(set(course_matches)) != 1:
        raise ValueError('コースの距離・芝ダートを一意に特定できません')

    distance_text, surface_ja = course_matches[0]
    distance_m = int(
        distance_text.replace(',', '').replace('，', '')
    )

    if not 800 <= distance_m <= 4000:
        raise ValueError('距離が対応範囲外です')

    surface = 'turf' if surface_ja == '芝' else 'dirt'

    # Find the finishing-order table.
    result_table = None
    for table in soup.find_all('table'):
        heads = ' '.join(
            x.get_text(' ', strip=True)
            for x in table.find_all('th')
        )
        if all(
            x in heads
            for x in ('着順', '馬名', 'コーナー')
        ):
            result_table = table
            break

    if result_table is None:
        raise ValueError(
            '着順・馬名・コーナー通過順位の表が見つかりません'
        )

    rows = []

    for tr in result_table.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue

        vals = [
            c.get_text(' ', strip=True)
            for c in cells
        ]

        if not re.fullmatch(r'\d+', vals[0]):
            continue

        if len(vals) < 10:
            raise ValueError('結果表の列数が想定外です')

        finish = int(vals[0])
        number = (
            int(vals[2])
            if re.fullmatch(r'\d+', vals[2])
            else None
        )
        name = vals[3]
        corners_text = vals[9]

        if (
            not number
            or not name
            or not re.fullmatch(
                r'\d+(?:\s+\d+)*',
                corners_text
            )
        ):
            raise ValueError(
                f'馬番・馬名・コーナー順位の解釈に失敗: '
                f'{name!r} {corners_text!r}'
            )

        corners = [
            int(n)
            for n in corners_text.split()
        ]

        rows.append({
            'number': number,
            'name': name,
            'finish': finish,
            'corner_positions': corners
        })

    field = len(rows)

    if field < 2:
        raise ValueError('有効な着順データが不足しています')

    if (
        len({r['number'] for r in rows}) != field
        or len({r['finish'] for r in rows}) != field
    ):
        raise ValueError('馬番または着順が重複しています')

    if sorted(
        r['finish'] for r in rows
    ) != list(range(1, field + 1)):
        raise ValueError(
            '着順に欠落があります。'
            '中止・失格などは現試作版では未対応です'
        )

    for r in rows:
        if not all(
            1 <= p <= field
            for p in r['corner_positions']
        ):
            raise ValueError(
                'コーナー順位が出走頭数の範囲外です'
            )
        r['field_size'] = field

    return {
        'source_url': url,
        'date': race_date,
        'surface': surface,
        'distance_m': distance_m,
        'metadata_source': 'jra_page_parsed',
        'field_size': field,
        'horses': rows,
        'note': (
            'JRA結果ページから取得した過去走データ。'
            '予想対象レースの結果には使用しない。'
        )
    }


def main():
    p = argparse.ArgumentParser()

    p.add_argument('--url', required=True)
    p.add_argument(
        '--output',
        default='jra_result_test.json'
    )

    # Optional cross-checks; no longer used as data sources.
    p.add_argument(
        '--race-date',
        help='任意の照合用開催日 YYYY-MM-DD'
    )
    p.add_argument(
        '--surface',
        choices=('turf', 'dirt'),
        help='任意の照合用馬場'
    )
    p.add_argument(
        '--distance-m',
        type=int,
        help='任意の照合用距離m'
    )

    a = p.parse_args()
    parsed = urlparse(a.url)

    if (
        parsed.scheme != 'https'
        or parsed.hostname != 'www.jra.go.jp'
        or parsed.path != '/JRADB/accessS.html'
    ):
        p.error('JRA公式のレース結果URLのみ対応')

    response = requests.get(
        a.url,
        timeout=20,
        headers={
            'User-Agent': (
                'Mozilla/5.0 '
                '(compatible; authorized-research/1.0)'
            )
        }
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding

    data = extract(a.url, response.text)

    # Reject mismatches instead of overwriting JRA page values.
    if (
        a.race_date is not None
        and a.race_date != data['date']
    ):
        p.error(
            f'開催日がJRAページと不一致: '
            f'{a.race_date} != {data["date"]}'
        )

    if (
        a.surface is not None
        and a.surface != data['surface']
    ):
        p.error(
            f'馬場がJRAページと不一致: '
            f'{a.surface} != {data["surface"]}'
        )

    if (
        a.distance_m is not None
        and a.distance_m != data['distance_m']
    ):
        p.error(
            f'距離がJRAページと不一致: '
            f'{a.distance_m} != {data["distance_m"]}'
        )

    output = Path(a.output)

    output.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ) + '\n',
        encoding='utf-8'
    )

    print(
        f'OK: {output} / '
        f'{data["field_size"]} horses / no posting'
    )


if __name__ == '__main__':
    main()
