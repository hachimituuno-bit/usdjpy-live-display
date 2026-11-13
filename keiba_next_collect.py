"""Collect pre-race JRA horse histories from operator-confirmed entrants.

No entry-list discovery, no publishing, no betting. Never treat URL matching
as verified identity: independent confirmation remains necessary.
"""
import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from keiba_jra_past_runs_test import extract_runs, validate_horse_url


def validate_entries(data):
    if not isinstance(data, dict) or data.get('posting') not in (None, False):
        raise ValueError('Invalid entries or posting enabled')
    day = date.fromisoformat(data['race_date'])
    horses = data.get('horses')
    if not isinstance(horses, list) or len(horses) < 2:
        raise ValueError('Confirmed entries for at least two horses required')
    if not all(data.get(k) for k in ('venue', 'race_number', 'race_name')):
        raise ValueError('Race metadata missing')
    numbers, names, ids = set(), set(), set()
    for h in horses:
        num, name, url = h.get('number'), h.get('name'), h.get('horse_url')
        if type(num) is not int or num < 1 or not isinstance(name, str) or not name.strip():
            raise ValueError('Invalid horse number/name')
        if not isinstance(url, str):
            raise ValueError(f'{name}: JRA horse_url missing')
        validate_horse_url(url)
        cid = parse_qs(urlparse(url).query)['CNAME'][0]
        if num in numbers or name in names or cid in ids:
            raise ValueError('Duplicate horse number/name/profile')
        numbers.add(num); names.add(name); ids.add(cid)
        if h.get('identity_candidate') not in (None, cid):
            raise ValueError(f'{name}: profile URL and identity_candidate disagree')
    return day



def official_profile_name(soup):
    """Read the Japanese horse name from the JRA profile's main heading."""
    candidates = []
    for heading in soup.select("h1 .txt"):
        labels = heading.select(".opt")
        if len(labels) != 1 or labels[0].get_text(" ", strip=True) != "競走馬情報":
            continue
        # The name is a direct text node, not the English name or section label.
        direct_text = [str(node).strip() for node in heading.contents
                       if isinstance(node, str) and str(node).strip()]
        if len(direct_text) != 1:
            raise ValueError("Ambiguous JRA profile heading horse name")
        candidates.append(direct_text[0])
    if len(candidates) != 1:
        raise ValueError(f"Expected one official JRA horse-name heading; found {len(candidates)}")
    return candidates[0]


def collect(entries, max_runs=5, session=None, delay=0.5):
    day = validate_entries(entries)
    if not 1 <= max_runs <= 5:
        raise ValueError('max_runs must be 1..5')
    session = session or requests.Session()
    result = {'target_date': day.isoformat(), 'posting': False, 'horses': [],
              'identity_note': 'JRA profile heading must exactly match entry name; independent identity verification is not performed.'}
    for index, horse in enumerate(entries['horses']):
        url = horse['horse_url']
        response = session.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0 (compatible; read-only-research/1.0)'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        profile_name = official_profile_name(soup)
        if profile_name != horse['name']:
            raise ValueError(
                f"JRA horse identity mismatch: entry={horse['name']!r}, "
                f"profile={profile_name!r}, URL={url}"
            )
        runs, available = extract_runs(soup, day, max_runs, horse_name=horse['name'])
        cid = parse_qs(urlparse(url).query)['CNAME'][0]
        result['horses'].append({'name': horse['name'], 'identity_candidate': cid,
                                 'identity_verified': False, 'horse_url': url,
                                 'available_runs': available, 'runs': runs})
        print(f"{index+1}/{len(entries['horses'])} {horse['name']}: {len(runs)} runs", flush=True)
        if index + 1 < len(entries['horses']) and delay:
            time.sleep(delay)
    result['horse_count'] = len(result['horses'])
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--entries', required=True)
    p.add_argument('--output', default='keiba_next_history.json')
    p.add_argument('--max-runs', type=int, default=5)
    a = p.parse_args()
    entries = json.loads(Path(a.entries).read_text(encoding='utf-8'))
    history = collect(entries, a.max_runs)
    Path(a.output).write_text(json.dumps(history, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('COMPLETE / NO POSTING / IDENTITY NOT INDEPENDENTLY VERIFIED')

if __name__ == '__main__':
    main()
