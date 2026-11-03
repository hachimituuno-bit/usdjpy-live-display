"""Conservative one-time repair for a specific missed Blogger episode.

Does not send email or call Gemini. Requires CONTENT_STATE_KEY.
Default is read-only; --apply writes encrypted state after all guards succeed.
"""
import argparse
import copy
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from new_encrypted_state import load, save

BLOG = 'https://hachinosuke7.blogspot.com'
SERIES = '刻限の偽証：忘却の島と消えた13時'
JST = timezone(timedelta(hours=9))



def get_public_post_body(url):
    """Read the publicly rendered post when Blogger JSON feed omits content.

    Never use text from a whole page (navigation / sidebar / unrelated posts).
    """
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'hachinosuke7.blogspot.com':
        raise RuntimeError('公開記事URLが想定外です。安全のため停止します。')
    r = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for selector in ('.post-body.entry-content', '.post-body', 'article .entry-content'):
        candidates = soup.select(selector)
        if len(candidates) != 1:
            continue
        node = candidates[0]
        for junk in node.select('script, style, noscript'):
            junk.decompose()
        text = node.get_text('\n', strip=True)
        if len(text) >= 120:
            return text
    raise RuntimeError('記事ページから本文を確実に取り出せません。状態は変更しません。')


def public_episodes():
    response = requests.get(BLOG + '/feeds/posts/default',
                            params={'alt': 'json', 'max-results': 150}, timeout=30)
    response.raise_for_status()
    entries = response.json()['feed'].get('entry', [])
    if len(entries) >= 150:
        raise RuntimeError('公開フィードが150件の上限に達しました。完全性を確認できないため停止します。')
    chapters = {}
    for item in entries:
        title = item.get('title', {}).get('$t', '')
        if not title.startswith('【' + SERIES + '】'):
            continue
        match = re.search(r'第\s*(\d+)\s*話', title)
        if not match:
            continue
        number = int(match.group(1))
        urls = [link.get('href', '') for link in item.get('link', [])
                if link.get('rel') == 'alternate']
        urls = [u for u in urls if urlparse(u).scheme == 'https'
                and urlparse(u).hostname == 'hachinosuke7.blogspot.com']
        if len(urls) != 1 or number in chapters:
            raise RuntimeError(f'第{number}話の公開URLが不明または重複しています。')
        raw = item.get('content', {}).get('$t', '')
        body = BeautifulSoup(raw, 'html.parser').get_text('\n', strip=True)
        if number in (11, 12) and len(body) < 120:
            print(f'第{number}話: フィードに本文がないため、公開記事ページを確認します。')
            body = get_public_post_body(urls[0])
        if number in (11, 12) and len(body) < 120:
            raise RuntimeError(f'第{number}話の本文が取得できません。安全のため停止します。')
        chapters[number] = {'url': urls[0], 'title': title, 'body': body,
                            'published': item.get('published', {}).get('$t', '')}
    return chapters


def repaired_state(before, chapters):
    series = before.get('series')
    if not isinstance(series, dict) or series.get('series_title') != SERIES:
        raise RuntimeError('連載タイトルが異なります。変更しません。')
    if type(before.get('episode')) is not int or before['episode'] != 14:
        raise RuntimeError('現在の次回話数が14ではありません。変更しません。')
    if before.get('pending_story_publication'):
        raise RuntimeError('確認待ち送信があるため復旧しません。')
    if set(chapters) != set(range(1, 13)):
        raise RuntimeError(f'公開済み話数が1〜12話と一致しません: {sorted(chapters)}')
    if not isinstance(series.get('outlines'), list) or len(series['outlines']) != 30:
        raise RuntimeError('30話分の設計図が見つかりません。')
    history = before.get('published', [])
    if not isinstance(history, list):
        raise RuntimeError('published履歴の形式が不正です。')
    bogus = [row for row in history if isinstance(row, dict) and row.get('kind') == 'story'
             and row.get('episode') == 13 and row.get('series') == before.get('series_number')]
    # Do not remove unrelated posts, and never silently discard multiple records.
    if len(bogus) > 1:
        raise RuntimeError('第13話の送信記録が複数あります。手動確認が必要です。')
    for row in bogus:
        if not str(row.get('post_id', '')).startswith('smtp-'):
            raise RuntimeError('第13話の履歴がSMTP仮IDではありません。変更しません。')

    after = copy.deepcopy(before)
    after['episode'] = 13
    after['published'] = [row for row in history if row not in bogus]
    after['series']['previous_url'] = chapters[12]['url']
    # Unpublished chapter 13 may have contaminated the saved recap. Rebuild from
    # actual public chapters 11 and 12; do not ask Gemini to invent continuity.
    after['series']['recap'] = (
        '第12話まで公開済み。以下は公開済み第11話・第12話の本文。これ以降の出来事は未確定。\n\n'
        + '\n\n'.join(f'【第{n}話】\n{chapters[n]["body"][:7000]}' for n in (11, 12))
    )
    published = chapters[12]['published']
    try:
        after['last_story_date'] = datetime.fromisoformat(
            published.replace('Z', '+00:00')).astimezone(JST).date().isoformat()
    except (ValueError, TypeError) as exc:
        raise RuntimeError('第12話の公開日時を検証できません。') from exc
    return after, len(bogus)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    before = load()
    chapters = public_episodes()
    after, removed = repaired_state(before, chapters)
    print('公開済みの連載話数:', ', '.join(map(str, sorted(chapters))))
    print('修復前の次回話数:', before['episode'])
    print('修復後の次回話数:', after['episode'])
    print('SMTP仮ID付き第13話履歴の除去件数:', removed)
    print('第12話のURL、継続メモ、最終公開日を公開記事から復元予定')
    print('その他の連載構成、公開履歴、雑学履歴は維持')
    if not args.apply:
        print('DRY RUN: 状態を書き換えません。')
        return
    # Re-check stored data and public feed immediately before saving.
    fresh = load()
    if fresh != before:
        raise RuntimeError('状態が実行中に変更されました。保存を中止します。')
    fresh_chapters = public_episodes()
    if set(fresh_chapters) != set(chapters):
        raise RuntimeError('公開話数が実行中に変化しました。保存を中止します。')
    save(after)
    print('APPLIED: 暗号化状態を第13話から再開する内容へ修正しました。')
    print('注意: メール送信やBlogger公開は行っていません。')


if __name__ == '__main__':
    main()
