
"""Fetch authorized JRA historical results as JSON.

Standalone test tool. No mail, Blogger, or prediction execution.
Requires: pip install requests beautifulsoup4
"""

import argparse
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


DATE_PATTERN = re.compile(
    r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日'
)
COURSE_PATTERN = re.compile(
    r'コース\s*[:：]\s*([\d,，]+)\s*(?:メートル|m|M)'
    r'\s*[（(]\s*(芝|ダート)'
)


def check_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != 'https'
        or parsed.hostname != 'www.jra.go.jp'
        or parsed.path != '/JRADB/accessS.html'
        or not parse_qs(parsed.query).get('CNAME')
    ):
        raise ValueError(
            'JRA公式の個別レース結果URLが必要です'
        )


def parse_date(text):
    matches = DATE_PATTERN.findall(text)
    values = set()

    for year, month, day in matches:
        try:
            value = date(
                int(year), int(month), int(day)
            ).isoformat()
        except ValueError as exc:
            raise ValueError(
                'ページ内の日付が不正です'
            ) from exc
        values.add(value)

    return values


def extract_metadata(soup):
    # Keep date and course tied to the race information.
    # Avoid blindly using navigation or unrelated page dates.
    candidates = []

    for element in soup.find_all(
        ['h1', 'h2', 'h3', 'section', 'div', 'table']
    ):
        text = unicodedata.normalize('NFKC', element.get_text(' ', strip=True))

        if len(text) > 3000:
            continue

        dates = parse_date(text)
        courses = COURSE_PATTERN.findall(text)

        if len(dates) == 1 and len(set(courses)) == 1:
            candidates.append((
                len(text),
                next(iter(dates)),
                courses[0]
            ))

    if not candidates:
        raise ValueError(
            '開催日とコース情報を同じレース情報領域'
            'から特定できません'
        )

    # Prefer the smallest containing HTML region.
    candidates.sort(key=lambda item: item[0])
    shortest = candidates[0][0]

    best = [
        item for item in candidates
        if item[0] == shortest
    ]

    unique = {
        (item[1], item[2]) for item in best
    }

    if len(unique) != 1:
        raise ValueError(
            '開催日またはコース情報が複数あります'
        )

    race_date, course = next(iter(unique))
    distance_text, surface_ja = course

    distance_m = int(
        distance_text.replace(',', '').replace('，', '')
    )

    if not 800 <= distance_m <= 4000:
        raise ValueError('距離が対応範囲外です')

    surface = (
        'turf' if surface_ja == '芝' else 'dirt'
    )

    return race_date, surface, distance_m


def extract(url, html):
    check_url(url)

    soup = BeautifulSoup(html, 'html.parser')
    page_text = soup.get_text(' ', strip=True)

    if (
        'パラメータエラー' in page_text
        or 'エラー013' in page_text
    ):
        raise ValueError(
            'JRAがパラメータエラーを返しました'
        )

    race_date, surface, distance_m = (
        extract_metadata(soup)
    )

    result_table = None

    for table in soup.find_all('table'):
        headers = ' '.join(
            th.get_text(' ', strip=True)
            for th in table.find_all('th')
        )

        if all(
            word in headers
            for word in ('着順', '馬名', 'コーナー')
        ):
            result_table = table
            break

    if result_table is None:
        raise ValueError(
            'レース結果表が見つかりません'
        )

    rows = []
    for tr in result_table.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if not cells:
            continue
        vals = [cell.get_text(' ', strip=True) for cell in cells]
        if len(vals) < 10:
            # Header/subheading rows are not runners.
            continue
        status = vals[0]
        if not (re.fullmatch(r'\d+', status) or status in ('中止', '失格', '除外', '取消')):
            continue
        # 取消/除外 are non-starters and must not count in the starting field.
        if status in ('除外', '取消'):
            continue
        if not re.fullmatch(r'\d+', vals[2]):
            raise ValueError('Invalid horse number')
        number = int(vals[2])
        name = vals[3]
        if not name:
            raise ValueError('Missing horse name')
        corners_text = vals[9]
        if corners_text and not re.fullmatch(r'\d+(?:\s+\d+)*', corners_text):
            raise ValueError(f'Invalid corner positions for {number}: {corners_text!r}')
        corners = [int(n) for n in corners_text.split()] if corners_text else []
        finish = int(status) if re.fullmatch(r'\d+', status) else None
        if finish is not None and not corners:
            raise ValueError('Finished horse has no corner positions')
        rows.append({'number': number, 'name': name, 'finish': finish,
                     'finish_status': 'finished' if finish is not None else status,
                     'corner_positions': corners})
    field = len(rows)
    if not 2 <= field <= 30:
        raise ValueError('Starting field size outside supported range')
    if len({row['number'] for row in rows}) != field:
        raise ValueError('Duplicate horse number')
    ordered = sorted(row['finish'] for row in rows if row['finish'] is not None)
    if not ordered or ordered[0] != 1:
        raise ValueError('Cannot identify first place')
    # Allow official dead heats; do not fabricate a finish for non-finishers.
    expected = 1
    i = 0
    while i < len(ordered):
        observed = ordered[i]
        if observed != expected:
            raise ValueError(f'Invalid official finish sequence: {ordered}')
        tied = ordered.count(observed)
        expected += tied
        i += tied
    for row in rows:
        if not all(1 <= pos <= field for pos in row['corner_positions']):
            raise ValueError('コーナー順位が出走頭数の範囲外です')
        row['field_size'] = field
    return {'source_url': url, 'date': race_date, 'surface': surface,
            'distance_m': distance_m, 'metadata_source': 'jra_page_parsed',
            'field_size': field, 'horses': rows,
            'note': 'Official post-race results for retrospective evaluation only; no posting'}


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--url', required=True
    )
    parser.add_argument(
        '--output',
        default='jra_result_test.json'
    )
    parser.add_argument(
        '--race-date'
    )
    parser.add_argument(
        '--surface',
        choices=('turf', 'dirt')
    )
    parser.add_argument(
        '--distance-m',
        type=int
    )

    args = parser.parse_args()

    check_url(args.url)

    response = requests.get(
        args.url,
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

    data = extract(
        args.url, response.text
    )

    if (
        args.race_date is not None
        and args.race_date != data['date']
    ):
        parser.error(
            '開催日が指定値と一致しません'
        )

    if (
        args.surface is not None
        and args.surface != data['surface']
    ):
        parser.error(
            '馬場が指定値と一致しません'
        )

    if (
        args.distance_m is not None
        and args.distance_m != data['distance_m']
    ):
        parser.error(
            '距離が指定値と一致しません'
        )

    output = Path(args.output)

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
        f'{data["field_size"]} horses / '
        f'{data["date"]} / '
        f'{data["surface"]} / '
        f'{data["distance_m"]}m / '
        'no posting'
    )


if __name__ == '__main__':
    main()
