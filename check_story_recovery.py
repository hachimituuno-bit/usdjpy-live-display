"""Read-only audit of Mail2Blogger story progress. No Gemini or email usage.

Run from the repository root after configuring CONTENT_STATE_KEY.
"""
import json
import sys
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from new_encrypted_state import load

BLOG = 'https://hachinosuke7.blogspot.com'
SERIES = '刻限の偽証：忘却の島と消えた13時'


def get_posts():
    r = requests.get(BLOG + '/feeds/posts/default', params={'alt':'json','max-results':150}, timeout=30)
    r.raise_for_status()
    feed = r.json()['feed']
    posts = {}
    for item in feed.get('entry', []):
        title = item.get('title', {}).get('$t','')
        if not title.startswith('【' + SERIES + '】'):
            continue
        import re
        m = re.search(r'第\s*(\d+)\s*話', title)
        if not m:
            continue
        n = int(m.group(1))
        urls = [x.get('href','') for x in item.get('link',[]) if x.get('rel')=='alternate']
        if urls and urlparse(urls[0]).netloc != 'hachinosuke7.blogspot.com':
            continue
        posts.setdefault(n, []).append({'title':title,'url':urls[0] if urls else ''})
    return posts


def main():
    state = load()
    series = state.get('series') or {}
    posts = get_posts()
    print('=== READ ONLY: no email, no Gemini, no state changes ===')
    print('stored_series:', series.get('series_title','(missing)'))
    print('stored_episode_next:', state.get('episode','(missing)'))
    pending = state.get('pending_story_publication') or {}
    print('pending_story:', bool(pending))
    if pending:
        print('pending_episode:', pending.get('episode'))
        print('pending_title:', pending.get('title'))
    print('public_chapters:', ', '.join(map(str, sorted(posts))))
    print('public_chapter_13:', 'YES' if 13 in posts else 'NO (not in the latest 150 feed items)')
    print('public_chapter_12:', 'YES' if 12 in posts else 'NO (not in the latest 150 feed items)')
    if (series.get('series_title') == SERIES and int(state.get('episode',0)) == 14
            and not pending and set(posts) == set(range(1,13))):
        print('ASSESSMENT: specific episode-13 rollback conditions appear satisfied.')
        print('NEXT: manual recovery can be considered after checking full published content.')
    elif pending:
        print('ASSESSMENT: pending publication; never re-send without resolving it.')
    else:
        print('ASSESSMENT: manual inspection needed; no automatic rollback recommendation.')


if __name__ == '__main__':
    main()
