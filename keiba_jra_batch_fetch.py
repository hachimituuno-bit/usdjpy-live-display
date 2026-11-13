"""Fetch verified historical JRA results. Read-only; no publishing.

The built-in RACES list contains the two previously tested races.
To expand, add verified official race URLs to keiba_races.json and run
this script with --races-file keiba_races.json. The historical backtester
also supports --races-file with the same JSON format.
"""
import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from keiba_jra_result_fetch import extract

RACES = [
    {"id": "tokyo_20261108_10r", "url": "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1005202605021020261108/4B", "date": "2026-11-08", "surface": "dirt", "distance_m": 1600},
    {"id": "tokyo_20261108_11r", "url": "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1005202605021120261108/00", "date": "2026-11-08", "surface": "turf", "distance_m": 2500},
]


def load_races(path=None):
    if path is None:
        races = RACES
    else:
        races = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(races, dict):
            races = races.get('races')
    if not isinstance(races, list) or not races:
        raise ValueError('Race list must be a non-empty JSON array')
    seen = set()
    for race in races:
        if not isinstance(race, dict) or not all(k in race for k in ('id', 'url', 'date', 'surface', 'distance_m')):
            raise ValueError('Each race needs id, url, date, surface, distance_m')
        rid = race['id']
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', rid) or rid in seen:
            raise ValueError(f'Invalid or duplicate race id: {rid!r}')
        seen.add(rid)
        parsed = urlparse(race['url'])
        if (parsed.scheme != 'https' or parsed.hostname != 'www.jra.go.jp'
                or parsed.path != '/JRADB/accessS.html'
                or not parse_qs(parsed.query).get('CNAME')):
            raise ValueError(f'{rid}: not an official JRA result URL')
        date.fromisoformat(race['date'])
        if race['surface'] not in ('turf', 'dirt') or not isinstance(race['distance_m'], int) or not 800 <= race['distance_m'] <= 4000:
            raise ValueError(f'{rid}: invalid surface or distance')
    return races


def fetch_race(session, race):
    response = session.get(race['url'], timeout=25)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    result = extract(race['url'], response.text)
    for key in ('date', 'surface', 'distance_m'):
        if result[key] != race[key]:
            raise ValueError(f"{race['id']}: {key} mismatch: expected {race[key]}, got {result[key]}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='keiba_jra_results')
    parser.add_argument('--races-file', help='JSON file containing verified JRA result URLs')
    parser.add_argument('--delay', type=float, default=2.0)
    args = parser.parse_args()
    if args.delay < 0.5:
        parser.error('--delay must be >= 0.5 seconds')
    races = load_races(args.races_file)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    summary = []
    for index, race in enumerate(races):
        print(f"Fetching: {race['id']} ({index + 1}/{len(races)})", flush=True)
        try:
            result = fetch_race(session, race)
            target = out / f"{race['id']}.json"
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            summary.append({'race_id': race['id'], 'status': 'SUCCESS', 'date': result['date'],
                            'surface': result['surface'], 'distance_m': result['distance_m'],
                            'field_size': result['field_size'], 'file': target.name})
            print(f"OK: {race['id']} / {result['field_size']} horses", flush=True)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            summary.append({'race_id': race['id'], 'status': 'FAILED', 'reason': str(exc)})
            print(f"FAILED: {race['id']} / {type(exc).__name__}: {exc}", flush=True)
        if index < len(races) - 1:
            time.sleep(args.delay)
    succeeded = sum(x['status'] == 'SUCCESS' for x in summary)
    (out / 'summary.json').write_text(json.dumps({'race_count': succeeded, 'races_requested': len(races),
        'races_failed': len(races) - succeeded, 'races': summary, 'posting': False}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'COMPLETE: {succeeded}/{len(races)} races / no posting', flush=True)
    return 0 if succeeded == len(races) else 1


if __name__ == '__main__':
    raise SystemExit(main())
