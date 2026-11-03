"""One-time guarded sync of a manually published chapter 13 (no Gemini/email).
Default is read-only.  Requires the existing CONTENT_STATE_KEY secret.
"""
import argparse
import copy
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from new_encrypted_state import load, save

SERIES = '刻限の偽証：忘却の島と消えた13時'
BLOG = 'https://hachinosuke7.blogspot.com'
JST = timezone(timedelta(hours=9))

# Based only on the text of chapter 13 supplied by the author.
RECAP_13 = (
    '第13話：市長の「刺客」という警告を受けた九条蓮は水瀬凪を案じて図書館へ急ぐ。'
    '作業服の男2人が凪を連れ去ろうとしており、蓮は格闘して救出する。'
    '凪が触れた郷土史書架の仕掛けが作動し、図書館の設計図にない地下階段が開く。'
    '地下には機械的な排気音と薬品のにおいがあり、蓮と凪はそこへ入る。'
    'コンクリートの壁には「13時を殺せ」「私は忘れたくない」と刻まれ、'
    '妹・栞の筆跡の「お兄ちゃん、助けて」も見つかる。蓮の首筋の痣が脈打つ。'
    '第14話は2人が地下へ入った直後から続ける。'
)


def get_public_chapter():
    response = requests.get(BLOG + '/feeds/posts/default',
                            params={'alt': 'json', 'max-results': 150}, timeout=30)
    response.raise_for_status()
    entries = response.json().get('feed', {}).get('entry', [])
    if len(entries) >= 150:
        raise RuntimeError('公開フィードが150件に達しています。確認が不完全なため停止。')
    matches = []
    for entry in entries:
        title = entry.get('title', {}).get('$t', '')
        if not title.startswith('【' + SERIES + '】') or not re.search(r'第\s*13\s*話', title):
            continue
        links = [link.get('href', '') for link in entry.get('link', []) if link.get('rel') == 'alternate']
        links = [url for url in links if urlparse(url).scheme == 'https'
                 and urlparse(url).hostname == 'hachinosuke7.blogspot.com']
        if len(links) != 1:
            raise RuntimeError('第13話の公開URLが確定できません。')
        matches.append((title, links[0], entry.get('published', {}).get('$t', '')))
    if len(matches) != 1:
        raise RuntimeError(f'第13話の公開件数が1件ではありません（{len(matches)}件）。状態は変更しません。')
    return matches[0]


def make_updated_state(state, chapter):
    title, url, published_at = chapter
    series = state.get('series')
    if not isinstance(series, dict) or series.get('series_title') != SERIES:
        raise RuntimeError('現在のシリーズが一致しません。')
    if type(state.get('episode')) is not int or state['episode'] != 13:
        raise RuntimeError(f'次回話数が13ではありません（{state.get("episode")}）。重複更新を防ぎます。')
    if state.get('pending_story_publication'):
        raise RuntimeError('未確認送信の記録があります。自動変更しません。')
    try:
        publication_date = datetime.fromisoformat(published_at.replace('Z', '+00:00')).astimezone(JST).date().isoformat()
    except (ValueError, TypeError):
        raise RuntimeError('公開日時が検証できません。') from None
    history = state.get('published', [])
    if not isinstance(history, list):
        raise RuntimeError('履歴の形式が不正です。')
    if any(isinstance(row, dict) and row.get('kind') == 'story'
           and int(row.get('episode') or 0) == 13
           and int(row.get('series') or 0) == int(state.get('series_number') or 0)
           for row in history):
        raise RuntimeError('第13話の履歴がすでにあります。二重記録を防ぐため停止。')
    updated = copy.deepcopy(state)
    updated['episode'] = 14
    updated['last_story_date'] = publication_date
    updated['series']['previous_url'] = url
    updated['series']['recap'] = RECAP_13
    updated.setdefault('published', []).append({
        'kind': 'story', 'date': publication_date,
        'post_id': '', 'url': url,
        'series': state.get('series_number'), 'episode': 13,
        'title': title, 'source': 'manual_blogger_publication_verified',
    })
    updated['published'] = updated['published'][-120:]
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    before = load()
    chapter = get_public_chapter()
    after = make_updated_state(before, chapter)
    print('公開確認した第13話:', chapter[0])
    print('公開URL:', chapter[1])
    print('修正前の次回話数:', before['episode'])
    print('修正後の次回話数:', after['episode'])
    print('第13話本文に基づく引継ぎメモ:', len(RECAP_13), '文字')
    if not args.apply:
        print('DRY RUN: GitHubの状態を変更していません。')
        return
    fresh = load()
    if fresh != before or get_public_chapter() != chapter:
        raise RuntimeError('公開記事または保存状態が変化したため停止しました。')
    save(after)
    print('APPLIED: 次回話数を14にし、公開第13話のURLとあらすじを記録しました。')


if __name__ == '__main__':
    main()
