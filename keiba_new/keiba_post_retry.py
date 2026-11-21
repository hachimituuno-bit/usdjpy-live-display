"""Persistent hourly retry gate for weekend race posts (JST).

A successful SMTP send means SMTP accepted the message, NOT that Blogger published it.
Unknown SMTP outcomes cannot be retried safely without a publication verification API.
"""
import json
import os
import subprocess
import requests
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path('keiba_new/posting_state.json')
JST = ZoneInfo('Asia/Tokyo')
UTC = timezone.utc
BLOG_FEED = 'https://hachinosuke7.blogspot.com/feeds/posts/default'


def expected_title(day):
    d = date.fromisoformat(day)
    return f'【新方式】{d.year}年{d.month}月{d.day}日 中央競馬予想｜馬番一覧・取得状況'


def public_post(day, earliest=None):
    """Find exact published title; return (url, verified_feed).

    A feed outage, truncation or ambiguous result must not trigger a resend.
    """
    try:
        response = requests.get(BLOG_FEED, params={'alt': 'json', 'max-results': 150}, timeout=30)
        response.raise_for_status()
        feed = response.json().get('feed', {})
        entries = feed.get('entry', [])
        if isinstance(entries, dict):
            entries = [entries]
        total = int(feed.get('openSearch$totalResults', {}).get('$t', len(entries)))
        if total > len(entries):
            raise RuntimeError('Blogger feed is truncated')
        for entry in entries:
            if entry.get('title', {}).get('$t') != expected_title(day):
                continue
            if earliest:
                published = datetime.fromisoformat(entry['published']['$t'].replace('Z', '+00:00'))
                start = datetime.fromisoformat(earliest.replace('Z', '+00:00'))
                if published.tzinfo is None or published < start - timedelta(minutes=5):
                    continue
            for link in entry.get('link', []):
                if link.get('rel') == 'alternate' and link.get('href', '').startswith('https://hachinosuke7.blogspot.com/'):
                    return link['href'], True
        return None, True
    except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print('PUBLICATION CHECK UNAVAILABLE:', type(exc).__name__, str(exc), flush=True)
        return None, False


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
    state = load()
    manual = os.getenv('EVENT_NAME') == 'workflow_dispatch'
    publish = (os.getenv('PUBLISH') or 'true').lower() == 'true'
    requested = (os.getenv('RACE_DATE') or '').strip()
    if requested:
        date.fromisoformat(requested)
    if manual:
        # A manual dispatch always attempts the requested operation, even if
        # the same race date was already sent. Do not mutate automatic state.
        day = requested or local.date().isoformat()
        print('MANUAL: duplicate suppression disabled:', day, flush=True)
        if run([sys.executable, 'keiba_new/keiba_new_all_races.py', '--date', day]) != 0:
            sys.exit(1)
        if run([sys.executable, 'keiba_new/keiba_new_blog_post.py']) != 0:
            sys.exit(1)
        if publish:
            sys.exit(run([sys.executable, 'keiba_new/keiba_new_blog_post.py', '--send']))
        print('MANUAL PREVIEW: no SMTP send.')
        return
    else:
        # Saturday/Sunday 01:00 JST starts that day's attempt.
        if local.weekday() in (5, 6) and local.hour >= 1:
            day = local.date().isoformat()
            state.setdefault(day, {'status': 'pending'})
    save(state)
    due = []
    for day, item in sorted(state.items()):
        status = item.get('status')
        if status == 'published':
            continue
        if status not in ('pending', 'failed', 'smtp_accepted', 'awaiting_publication', 'delivery_uncertain'):
            continue
        next_at = item.get('next_attempt_utc')
        if next_at and now < datetime.fromisoformat(next_at):
            continue
        due.append(day)
    if not due:
        print('No due keiba post. Current JST:', local.isoformat())
        return
    failures = []
    for day in due:
        item = state[day]
        status = item.get('status')
        # Verify publication before any new automatic SMTP delivery. Legacy
        # smtp_accepted records are treated as uncertain, never as unsent.
        url, verified = public_post(day, item.get('first_send_utc') or item.get('smtp_accepted_utc'))
        if url:
            item['status'] = 'published'
            item['public_url'] = url
            item['published_verified_utc'] = datetime.now(UTC).isoformat()
            item.pop('next_attempt_utc', None)
            item.pop('last_error', None)
            save(state)
            print('Blogger publication VERIFIED:', day, url, flush=True)
            continue
        if status in ('smtp_accepted', 'awaiting_publication', 'delivery_uncertain'):
            item['status'] = 'awaiting_publication'
            item['next_attempt_utc'] = (now + timedelta(hours=1)).isoformat()
            item['last_error'] = ('Blogger feed unavailable; delivery status unknown' if not verified
                                  else 'SMTP accepted, but public Blogger post not yet verified')
            save(state)
            failures.append(day + ': ' + item['last_error'])
            print('No automatic resend: SMTP acceptance could already have published the post.', flush=True)
            continue
        if not verified:
            item['status'] = 'failed'
            item['next_attempt_utc'] = (now + timedelta(hours=1)).isoformat()
            item['last_error'] = 'Blogger feed unavailable; cannot safely send'
            save(state)
            failures.append(day + ': ' + item['last_error'])
            continue
        item['attempts'] = item.get('attempts', 0) + 1
        item['last_attempt_utc'] = now.isoformat()
        item['next_attempt_utc'] = (now + timedelta(hours=1)).isoformat()
        save(state)
        if run([sys.executable, 'keiba_new/keiba_new_all_races.py', '--date', day]) != 0:
            item['status'] = 'failed'
            item['last_error'] = 'Prediction generation failed'
        elif run([sys.executable, 'keiba_new/keiba_new_blog_post.py']) != 0:
            item['status'] = 'failed'
            item['last_error'] = 'Article rendering failed'
        else:
            # Mark uncertain BEFORE calling SMTP. A timeout can happen after
            # the SMTP server has accepted the message.
            item['status'] = 'delivery_uncertain'
            item.setdefault('first_send_utc', datetime.now(UTC).isoformat())
            save(state)
            code = run([sys.executable, 'keiba_new/keiba_new_blog_post.py', '--send'])
            if code == 0:
                item['status'] = 'awaiting_publication'
                item['smtp_accepted_utc'] = datetime.now(UTC).isoformat()
                item['last_error'] = 'SMTP accepted; awaiting public Blogger verification'
            else:
                item['status'] = 'delivery_uncertain'
                item['last_error'] = 'SMTP command failed; delivery may have occurred; no automatic resend'
        save(state)
        failures.append(day + ': ' + item.get('last_error', 'Not verified'))
    if failures:
        raise RuntimeError('Keiba posting not yet verified: ' + '; '.join(failures))


if __name__ == '__main__':
    main()
