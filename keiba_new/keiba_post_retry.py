"""Persistent hourly retry gate for weekend race posts (JST).

A successful SMTP send means SMTP accepted the message, NOT that Blogger published it.
Unknown SMTP outcomes cannot be retried safely without a publication verification API.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path('keiba_new/posting_state.json')
JST = ZoneInfo('Asia/Tokyo')
UTC = timezone.utc


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
        day = requested or local.date().isoformat()
        if not publish:
            if run([sys.executable, 'keiba_new/keiba_new_all_races.py', '--date', day]) != 0:
                sys.exit(1)
            sys.exit(run([sys.executable, 'keiba_new/keiba_new_blog_post.py']))
        if state.get(day, {}).get('status') == 'smtp_accepted':
            print('Already accepted by SMTP; refusing duplicate send:', day)
            return
        state.setdefault(day, {'status': 'pending'})
    else:
        # Saturday/Sunday 01:00 JST starts that day's attempt.
        if local.weekday() in (5, 6) and local.hour >= 1:
            day = local.date().isoformat()
            state.setdefault(day, {'status': 'pending'})
    save(state)
    due = []
    for day, item in sorted(state.items()):
        if item.get('status') not in ('pending', 'failed'):
            continue
        next_at = item.get('next_attempt_utc')
        if next_at and now < datetime.fromisoformat(next_at):
            continue
        due.append(day)
    if not due:
        print('No due keiba post. Current JST:', local.isoformat())
        return
    for day in due:
        item = state[day]
        item['attempts'] = item.get('attempts', 0) + 1
        item['last_attempt_utc'] = now.isoformat()
        # Commit a durable retry schedule even when prediction generation fails.
        item['next_attempt_utc'] = (now + timedelta(hours=1)).isoformat()
        save(state)
        if run([sys.executable, 'keiba_new/keiba_new_all_races.py', '--date', day]) != 0:
            item['status'] = 'failed'
            item['last_error'] = 'Prediction generation failed'
            save(state)
            continue
        if run([sys.executable, 'keiba_new/keiba_new_blog_post.py']) != 0:
            item['status'] = 'failed'
            item['last_error'] = 'Article rendering failed'
            save(state)
            continue
        # SMTP may accept the email before a network error is raised. An ambiguous
        # outcome cannot be safely auto-retried without checking Blogger itself.
        code = run([sys.executable, 'keiba_new/keiba_new_blog_post.py', '--send'])
        if code == 0:
            item['status'] = 'smtp_accepted'
            item['smtp_accepted_utc'] = datetime.now(UTC).isoformat()
            item.pop('next_attempt_utc', None)
            item.pop('last_error', None)
            print('SMTP accepted. Blogger publication NOT verified:', day)
        else:
            item['status'] = 'failed'
            item['last_error'] = 'SMTP send command failed; check logs for ambiguous delivery'
            print('SMTP send failed; next hourly attempt scheduled:', day)
        save(state)


if __name__ == '__main__':
    main()
