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
from new_encrypted_state import load, save

BLOG = 'https://hachinosuke7.blogspot.com'
SERIES = '刻限の偽証：忘却の島と消えた13時'
JST = timezone(timedelta(hours=9))


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
        # Published title and permalink are adequate for this guarded rollback.
        # Do not fetch an article page: Google may redirect to a 429 challenge.
        chapters[number] = {'url': urls[0], 'title': title,
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
    # Repeated SMTP acceptance can produce multiple false-positive history rows.
    # Only remove records with synthetic SMTP IDs. Refuse if any record looks
    # like a real Blogger publication or references a public permalink.
    for row in bogus:
        post_id = str(row.get('post_id', ''))
        if not post_id.startswith('smtp-'):
            raise RuntimeError('第13話の履歴にSMTP仮ID以外があります。変更しません。')
        if row.get('url') or row.get('blogger_url'):
            raise RuntimeError('第13話の履歴に公開URLがあります。変更しません。')
        if row.get('title') and not re.search(r'第\s*13\s*話', str(row['title'])):
            raise RuntimeError('第13話の履歴タイトルが一致しません。変更しません。')
    if bogus:
        print(f'第13話のSMTP仮ID履歴 {len(bogus)} 件を検出。'
              '公開URLのない仮記録のみ修復対象とします。')

    after = copy.deepcopy(before)
    after['episode'] = 13
    bogus_ids = {id(row) for row in bogus}
    after['published'] = [copy.deepcopy(row) for row in history if id(row) not in bogus_ids]
    after['series']['previous_url'] = chapters[12]['url']
    # The current recap may contain events from unpublished chapter 13.
    # Never use that text or invent a recap from unavailable page content.
    # Provide verified public chapter references plus the original 30-episode
    # outline already retained in the state.
    after['series']['recap'] = (
        '第12話まで公開済み。第13話は未公開。未公開第13話の出来事は確定していない。'
        '第13話は保存済みの全30話構成に沿って、第12話の直後から作成すること。'
        '以下の公開済み記事を参照できない場合、本文の細部を創作して既成事実にしない。\n'
        + '\n'.join(f'第{n}話: {chapters[n]["title"]} {chapters[n]["url"]}'
                    for n in (11, 12))
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
    print('第12話URL・公開日時を公開フィードから取得。継続メモは第11/12話のタイトルとURLのみで再設定')
    print('注意: 本文は取得しません。第13話の詳細な連続性は保証できません。')
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
