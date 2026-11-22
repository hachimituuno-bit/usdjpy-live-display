"""Weekend race posting: JST 01:00 initial, 02:00-06:00 publication-gated retries.

Manual dispatch intentionally bypasses all publication/duplicate/state restrictions.
Automatic SMTP retries require a complete, successful Blogger public-feed check.
"""
import json
import re
import unicodedata
import os
import subprocess
import time
import requests
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path('keiba_new/posting_state.json')
ARCHIVE = Path('keiba_new/pending_articles')
JST = ZoneInfo('Asia/Tokyo')
UTC = timezone.utc
BLOG_FEED = 'https://hachinosuke7.blogspot.com/feeds/posts/default'


def expected_title(day):
    d = date.fromisoformat(day)
    return f'【新方式】{d.year}年{d.month}月{d.day}日 中央競馬予想｜馬番一覧・取得状況'


def title_matches(actual, day):
    """Accept Blogger typography changes, but require this exact race date and topic."""
    def normalize(value):
        value = unicodedata.normalize('NFKC', value or '')
        return re.sub(r'[\s\W_]+', '', value, flags=re.UNICODE)
    return normalize(actual) == normalize(expected_title(day))


def public_post(day, earliest=None):
    """Check all available public-feed pages, never infer absence from a partial feed."""
    try:
        start = 1
        while True:
            response = requests.get(BLOG_FEED, params={
                'alt': 'json', 'max-results': 150, 'start-index': start, '_': int(time.time())
            }, timeout=30)
            response.raise_for_status()
            feed = response.json().get('feed', {})
            entries = feed.get('entry', [])
            if isinstance(entries, dict):
                entries = [entries]
            total = int(feed.get('openSearch$totalResults', {}).get('$t', len(entries)))
            for entry in entries:
                if not title_matches(entry.get('title', {}).get('$t', ''), day):
                    continue
                # A matching article is published regardless of when its email was sent.
                # In particular, a manually posted article should stop automatic retries.
                for link in entry.get('link', []):
                    if (link.get('rel') == 'alternate' and
                            link.get('href', '').startswith('https://hachinosuke7.blogspot.com/')):
                        return link['href'], True
            if not entries and total > 0:
                raise RuntimeError('Blogger feed returned an empty page before all posts were checked')
            start += len(entries)
            if start > total:
                return None, True
            if not entries or start > 10000:
                raise RuntimeError('Blogger feed incomplete or exceeds safe pagination limit')
    except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print('PUBLICATION CHECK UNAVAILABLE:', type(exc).__name__, str(exc), flush=True)
        return None, False


def persist_pre_send(state, article=None):
    """Durably push uncertainty marker before attempting SMTP.

    Fail closed: do not send if the remote state cannot be confirmed saved.
    """
    save(state)
    def checked(*cmd):
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('Cannot persist pre-send state: ' + ' '.join(cmd) + ': ' + result.stderr[-600:])
    checked('git', 'config', 'user.name', 'github-actions[bot]')
    checked('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    checked('git', 'add', '-f', str(STATE))
    if article is not None:
        checked('git', 'add', '-f', str(article))
    checked('git', 'commit', '-m', 'Persist keiba delivery uncertainty before SMTP')
    for attempt in range(5):
        result = subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=False,
                                capture_output=True, text=True)
        if result.returncode == 0:
            print('PRE-SEND STATE DURABLY SAVED', flush=True)
            return
        # Never rebase and retry after a failed push here: a conflict could
        # overwrite someone else's state, and SMTP must not run unprotected.
        time.sleep(2)
    raise RuntimeError('Cannot verify remote pre-send state; SMTP deliberately NOT attempted')


def load():
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text(encoding='utf-8'))


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def run(cmd):
    print('Running:', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, check=False).returncode


def main():
    now = datetime.now(UTC)
    local = now.astimezone(JST)
    manual = os.getenv('EVENT_NAME') == 'workflow_dispatch'
    publish = (os.getenv('PUBLISH') or 'true').lower() == 'true'
    requested = (os.getenv('RACE_DATE') or '').strip()
    if requested:
        date.fromisoformat(requested)

    if manual:
        # Explicit operator request: NO feed check, duplicate suppression,
        # persisted-state gate, or schedule/date restriction. Preview is send-free.
        day = requested or local.date().isoformat()
        print('MANUAL UNRESTRICTED:', day, 'publish=', publish, flush=True)
        if run([sys.executable, 'keiba_new/keiba_new_all_races.py', '--date', day]) != 0:
            sys.exit(1)
        if run([sys.executable, 'keiba_new/keiba_new_blog_post.py']) != 0:
            sys.exit(1)
        if publish:
            sys.exit(run([sys.executable, 'keiba_new/keiba_new_blog_post.py', '--send']))
        print('MANUAL PREVIEW: no SMTP send.', flush=True)
        return

    # UTC cron is only the trigger; enforce the actual JST window as well.
    if local.weekday() not in (5, 6) or not (1 <= local.hour <= 6):
        print('Outside automatic weekend 01:00-06:59 JST window; no action.', flush=True)
        return

    day = local.date().isoformat()
    state = load()
    item = state.setdefault(day, {'status': 'pending'})
    if item.get('status') == 'published':
        print('ALREADY VERIFIED PUBLISHED:', day, item.get('public_url', ''), flush=True)
        return

    # A successful full public-feed scan is the only basis for an automatic
    # resend. If the feed is unreachable, we cannot conclude it is unpublished.
    url, verified = public_post(day)
    if url:
        item['status'] = 'published'
        item['public_url'] = url
        item['published_verified_utc'] = datetime.now(UTC).isoformat()
        item.pop('last_error', None)
        item.pop('next_attempt_utc', None)
        save(state)
        print('Blogger publication VERIFIED:', day, url, flush=True)
        return
    if not verified:
        item['last_error'] = 'Blogger feed unavailable; automatic SMTP deferred'
        save(state)
        raise RuntimeError(item['last_error'])

    # Confirmed absent in public feed: retry even if a previous SMTP call was
    # accepted. A delayed Blogger publication can still produce a duplicate.
    archive = ARCHIVE / (day + '.json')
    item['attempts'] = item.get('attempts', 0) + 1
    item['last_attempt_utc'] = now.isoformat()
    item['status'] = 'preparing'
    save(state)

    if archive.exists():
        try:
            archived = json.loads(archive.read_text(encoding='utf-8'))
            if (archived.get('title') != expected_title(day) or
                    not isinstance(archived.get('body'), str) or
                    not archived['body'].strip()):
                raise ValueError('Archived title/date/body invalid')
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            item['status'] = 'failed'
            item['last_error'] = f'Archived article invalid: {exc}'
            save(state)
            raise RuntimeError(item['last_error']) from exc
        print('Reusing saved article for', day, flush=True)
    else:
        if run([sys.executable, 'keiba_new/keiba_new_all_races.py', '--date', day]) != 0:
            item['status'] = 'failed'
            item['last_error'] = 'Prediction generation failed'
            save(state)
            raise RuntimeError(item['last_error'])
        if run([sys.executable, 'keiba_new/keiba_new_blog_post.py']) != 0:
            item['status'] = 'failed'
            item['last_error'] = 'Article rendering failed'
            save(state)
            raise RuntimeError(item['last_error'])
        summary = json.loads(Path('keiba_new_results/run_summary.json').read_text(encoding='utf-8'))
        if summary.get('date') != day or not summary.get('predicted'):
            item['status'] = 'failed'
            item['last_error'] = 'No valid predictions for race date'
            save(state)
            raise RuntimeError(item['last_error'])
        title = Path('keiba_new_results/blog_article_title.txt').read_text(encoding='utf-8')
        body = Path('keiba_new_results/blog_article.html').read_text(encoding='utf-8')
        if title != expected_title(day):
            raise RuntimeError('Article title/date mismatch; refusing automatic send')
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        archive.write_text(json.dumps({'title': title, 'body': body}, ensure_ascii=False), encoding='utf-8')

    # Persist the attempt and archived article before SMTP. This records what
    # was attempted, but does not block later scheduled retries if still absent.
    item['status'] = 'delivery_uncertain'
    item.setdefault('first_send_utc', datetime.now(UTC).isoformat())
    persist_pre_send(state, archive)
    code = run([sys.executable, 'keiba_new/keiba_new_blog_post.py', '--send', '--archived-article', str(archive)])
    if code == 0:
        item['status'] = 'awaiting_publication'
        item['smtp_accepted_utc'] = datetime.now(UTC).isoformat()
        item['last_error'] = 'SMTP accepted; awaiting Blogger publication verification'
        save(state)
        print('SMTP accepted; will check publication at next scheduled run.', flush=True)
        return
    item['status'] = 'delivery_uncertain'
    item['last_error'] = 'SMTP failed; delivery uncertain; will recheck public feed next run'
    save(state)
    raise RuntimeError(item['last_error'])


if __name__ == '__main__':
    main()
