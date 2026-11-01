"""Free panel comic maker: public Blogger novel -> Pillow comic -> Blogger post.
Run in GitHub Actions as `prepare` then `publish` (image committed between steps).
No changes to daily_content.py or its state.
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

JST = timezone(timedelta(hours=9))
BASE = 'https://hachinosuke7.blogspot.com'
STATE = Path('new_story_manga_state.json')
MANIFEST = Path('manga_manifest.json')
OUT = Path('manga_output')
OUT.mkdir(exist_ok=True)
FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
LABEL = '連載小説'
TITLE_EP = re.compile(r'第\s*(\d{1,2})\s*話')
TIMEOUT = 35


def read_state():
    source = STATE
    if not source.exists():
        return {'mode': 'backfill', 'processed': {}}
    data = json.loads(source.read_text(encoding='utf-8'))
    data.setdefault('processed', {})
    return data


def save_state(s):
    from new_mail_gateway import is_preview
    if is_preview():
        print('PREVIEW: マンガの投稿履歴は変更しません')
        return
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def feed(label, limit=100):
    # Mail2Blogger does not support category labels. Search full public feed instead.
    url = f'{BASE}/feeds/posts/default'
    r = requests.get(url, params={'alt': 'json', 'max-results': limit}, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        obj = r.json()['feed']
    except (ValueError, KeyError) as e:
        raise RuntimeError('Bloggerの公開フィードを読めません。サイトフィードを「完全」にしてください。') from e
    return obj.get('entry', [])


def parse_entry(e):
    links = [x.get('href') for x in e.get('link', []) if x.get('rel') == 'alternate']
    if not links:
        return None
    title = e.get('title', {}).get('$t', '')
    match = TITLE_EP.search(title)
    if not match or 'マンガ版' in title:
        return None
    pub = e.get('published', {}).get('$t', '')
    try:
        day = datetime.fromisoformat(pub.replace('Z', '+00:00')).astimezone(JST).date().isoformat()
    except ValueError:
        return None
    raw = e.get('content', {}).get('$t', '')
    soup = BeautifulSoup(raw, 'html.parser')
    for node in soup(['script', 'style']):
        node.decompose()
    # Do not include backlink text / continuation notes in the comic.
    paragraphs = [p.get_text(' ', strip=True) for p in soup.find_all('p')]
    paragraphs = [p for p in paragraphs if p and not p.startswith(('前回（', '次回：', '―― 全30話', '※'))]
    body = '\n'.join(paragraphs) if paragraphs else soup.get_text('\n', strip=True)
    body = body[:18000]
    return {'url': links[0], 'title': title, 'episode': int(match.group(1)), 'day': day, 'body': body}


def story_list():
    stories = [parse_entry(x) for x in feed(LABEL)]
    return [x for x in stories if x and x['body']]


def already_published():
    # Rechecking Blogger protects against duplicate posts if a run stopped after publishing.
    published = set()
    for e in feed('連載マンガ'):
        title = e.get('title', {}).get('$t', '')
        if not title.startswith('【マンガ版】'):
            continue
        raw = e.get('content', {}).get('$t', '')
        match = re.search(r'data-original-story="([^"]+)"', raw) or re.search(r'原作URL[:：]\s*(https?://[^\s<]+)', raw)
        if match:
            published.add(html.unescape(match.group(1)))
        else:
            # Earlier, unmarked posts are not safely identifiable; do not guess.
            pass
    return published


def series_key(title):
    # 同じシリーズなら「第1話」「第2話」「第3話」の前は共通になる。
    # 空白・全角空白の違いも吸収する。
    title = re.sub(r'[\s\u3000]+', ' ', title).strip()
    title = re.sub(r'第\s*\d+\s*話.*$', '', title).strip()
    return title


def choose_story(state, stories, posted):
    done = set(state.get('processed', {})) | posted
    if state.get('mode') == 'backfill':
        # Never accidentally use a later series: all first three must share same title prefix.
        eps = {}
        for s in sorted(stories, key=lambda x: x['day']):
            if s['episode'] in (1, 2, 3):
                prefix = series_key(s['title'])
                eps.setdefault(prefix, {})[s['episode']] = s
        valid = [group for group in eps.values() if set(group) == {1, 2, 3}]
        if not valid:
            raise RuntimeError('同じシリーズの第1〜3話が揃っていません。誤投稿を避けるため停止します。')
        trio = min(valid, key=lambda group: group[1]['day'])
        for n in (1, 2, 3):
            s = trio[n]
            if s['url'] not in done:
                return s
        state['mode'] = 'daily'
        save_state(state)
        return None
    if state.get('mode') != 'daily':
        raise RuntimeError('不明なモードです')
    yesterday = (datetime.now(JST).date() - timedelta(days=1)).isoformat()
    candidates = [s for s in stories if s['day'] == yesterday and s['url'] not in done]
    if not candidates:
        print('前日の未マンガ化小説はありません')
        return None
    # Multiple stories on a date are suspicious: never silently discard one.
    if len(candidates) > 1:
        raise RuntimeError('前日の未マンガ化小説が複数あります。確認が必要です。')
    return candidates[0]


def wrap(draw, text, font, width, max_lines):
    lines, current = [], ''
    for c in text.replace('\n', ' '):
        if draw.textlength(current + c, font=font) <= width:
            current += c
        else:
            if current:
                lines.append(current)
            current = c
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + '…'
    return lines


def draw_text(draw, text, xy, font, width, count=5, leading=13):
    x, y = xy
    for line in wrap(draw, text, font, width, count):
        draw.text((x, y), line, font=font, fill='black')
        y += font.size + leading


def summarize_panels(story):
    raw = re.split(r'(?<=[。！？])\s*|\n+', story['body'])
    bits = [x.strip() for x in raw if len(x.strip()) >= 5]
    if not bits:
        raise RuntimeError('小説の文章を抽出できません')
    # Divide the plot in temporal order, take short excerpts only. No fabricated dialogue.
    panels = []
    for i in range(4):
        idx = min(len(bits) - 1, (i * len(bits)) // 4)
        seg = bits[idx]
        dialogue = re.search(r'「([^」]{1,55})」', seg)
        panels.append({'caption': seg[:74], 'dialogue': dialogue.group(1) if dialogue else ''})
    return panels


def make_image(story):
    if not Path(FONT).exists():
        raise RuntimeError('日本語フォントがありません。workflowのfonts-noto-cjk導入を確認してください')
    title_font = ImageFont.truetype(FONT, 37)
    small = ImageFont.truetype(FONT, 25)
    bodyfont = ImageFont.truetype(FONT, 30)
    panels = summarize_panels(story)
    image = Image.new('RGB', (1000, 1800), 'white')
    d = ImageDraw.Draw(image)
    d.rectangle((25, 25, 975, 145), outline='black', width=4)
    draw_text(d, '【マンガ風ダイジェスト】' + story['title'], (50, 40), title_font, 900, 2)
    for i, p in enumerate(panels):
        x1, y1, x2, y2 = 30, 170 + i * 395, 970, 545 + i * 395
        d.rectangle((x1, y1, x2, y2), outline='black', width=5)
        d.rectangle((x1 + 20, y1 + 20, x1 + 140, y1 + 63), fill='black')
        d.text((x1 + 36, y1 + 23), f'{i+1}コマ', font=small, fill='white')
        # Very simple, deliberately non-specific character silhouette.
        cx, cy = x1 + 160 + (i % 2) * 50, y1 + 225
        d.ellipse((cx-48, cy-93, cx+48, cy+3), outline='black', width=6)
        d.line([(cx, cy+4), (cx, cy+100)], fill='black', width=7)
        d.line([(cx, cy+31), (cx-75, cy+67)], fill='black', width=6)
        d.line([(cx, cy+31), (cx+75, cy+46)], fill='black', width=6)
        d.line([(cx, cy+100), (cx-57, cy+143)], fill='black', width=6)
        d.line([(cx, cy+100), (cx+57, cy+143)], fill='black', width=6)
        bx1, by1, bx2, by2 = x1+300, y1+85, x2-22, y1+290
        d.rounded_rectangle((bx1, by1, bx2, by2), radius=25, outline='black', width=4)
        d.polygon([(bx1+45, by2), (bx1+90, by2), (bx1+40, by2+35)], fill='white')
        d.line([(bx1+45, by2), (bx1+40, by2+35), (bx1+90, by2)], fill='black', width=3)
        text = p['dialogue'] if p['dialogue'] else p['caption']
        draw_text(d, text, (bx1+20, by1+20), bodyfont, bx2-bx1-40, 4)
        d.rectangle((x1+280, y2-72, x2-22, y2-20), fill='#f4f4f4')
        draw_text(d, p['caption'], (x1+292, y2-64), small, x2-x1-340, 1)
    draw_text(d, '※ 原作の一部を抜粋した簡易マンガです。全文は元の小説をご覧ください。', (40, 1760), small, 920, 1)
    name = f"s{re.sub(r'[^0-9]', '', story['day'])}_ep{story['episode']}_{abs(__import__('zlib').crc32(story['url'].encode())):08x}.png"
    path = OUT / name
    image.save(path, optimize=True)
    return path


def prepare():
    state = read_state()
    stories = story_list()
    posted = already_published()
    story = choose_story(state, stories, posted)
    if story is None:
        MANIFEST.unlink(missing_ok=True)
        print('NO_WORK')
        return
    path = make_image(story)
    manifest = {'story': story, 'image_path': path.as_posix()}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    print('READY', story['title'], path)


def publish():
    if not MANIFEST.exists():
        print('NO_WORK')
        return
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    story = manifest['story']
    state = read_state()
    if story['url'] in state['processed'] or story['url'] in already_published():
        state['processed'][story['url']] = {'title': '既存のマンガ投稿を検出'}
        save_state(state)
        MANIFEST.unlink()
        print('ALREADY_POSTED')
        return
    from new_mail_gateway import publish as mail_publish
    # Mail2Blogger supports image attachments. No public GitHub repository or raw URL is required.
    image_path = manifest['image_path']
    body = (
        f'<p>原作小説の内容を4コマ風にまとめました。画像はメール添付です。</p>'
        f'<p>原作：{html.escape(story["title"])}</p>'
        f'<p>原作URL: {html.escape(story["url"])}</p>'
        '<p>※小説本文からの抜粋による簡易マンガです。</p>'
    )
    post_title = '【マンガ版】' + story['title']
    result, _ = mail_publish(post_title, body, ['連載マンガ'],
                             is_draft=False, attachment_path=image_path)
    state['processed'][story['url']] = {
        'title': post_title, 'post_id': str(result['id']), 'day': story['day']
    }
    if state.get('mode') == 'backfill':
        # Only mark daily after ALL three initial episodes have been posted.
        stories = story_list()
        done = set(state['processed'])
        groups = {}
        for s in stories:
            if s['episode'] in (1, 2, 3):
                prefix = series_key(s['title'])
                groups.setdefault(prefix, {})[s['episode']] = s
        if any(set(g) == {1, 2, 3} and all(g[i]['url'] in done for i in (1, 2, 3)) for g in groups.values()):
            state['mode'] = 'daily'
    save_state(state)
    MANIFEST.unlink()
    print('PREVIEW' if os.getenv('DRY_RUN', 'true') != 'false' else 'SMTP_ACCEPTED (not published verification)', post_title)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'publish'])
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare()
    else:
        publish()
