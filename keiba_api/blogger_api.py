#!/usr/bin/env python3
"""Publish the existing keiba HTML article through Blogger API v3 (no SMTP)."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RESULTS = Path('keiba_new_results')
TOKEN_URL = 'https://oauth2.googleapis.com/token'
POST_URL = 'https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/'

def request_json(url, payload, *, token=None):
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
    try:
        with urlopen(req, timeout=35) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        detail = exc.read(1500).decode('utf-8', errors='replace')
        raise RuntimeError(f'Blogger API HTTP {exc.code}: {detail}') from None
    except URLError as exc:
        raise RuntimeError(f'Blogger API connection error: {exc.reason}') from None

def access_token():
    keys = ('BLOGGER_CLIENT_ID', 'BLOGGER_CLIENT_SECRET', 'BLOGGER_REFRESH_TOKEN')
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise RuntimeError('Missing GitHub secrets: ' + ', '.join(missing))
    form = urlencode({
        'client_id': os.environ['BLOGGER_CLIENT_ID'],
        'client_secret': os.environ['BLOGGER_CLIENT_SECRET'],
        'refresh_token': os.environ['BLOGGER_REFRESH_TOKEN'],
        'grant_type': 'refresh_token',
    }).encode('utf-8')
    req = Request(TOKEN_URL, data=form, headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urlopen(req, timeout=35) as response:
            data = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        # OAuth error details can contain sensitive data; do not print the body.
        raise RuntimeError(f'OAuth token refresh failed: HTTP {exc.code} (check credentials/consent status)') from None
    if not data.get('access_token'):
        raise RuntimeError('OAuth token response did not contain access_token')
    return data['access_token']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true', help='Publish to Blogger; otherwise preview only')
    parser.add_argument('--draft', action='store_true', help='Create Blogger draft (requires --publish)')
    args = parser.parse_args()
    if args.draft and not args.publish:
        parser.error('--draft requires --publish')
    title = (RESULTS / 'blog_article_title.txt').read_text(encoding='utf-8').strip()
    content = (RESULTS / 'blog_article.html').read_text(encoding='utf-8').strip()
    if not title or not content:
        raise RuntimeError('Article title/body is empty')
    print('Article:', title)
    print('HTML length:', len(content))
    if not args.publish:
        print('PREVIEW ONLY: no Blogger API request, no publication')
        return
    blog_id = os.getenv('BLOGGER_BLOG_ID', '').strip()
    if not blog_id.isdigit():
        raise RuntimeError('BLOGGER_BLOG_ID must be a numeric blog ID')
    url = POST_URL.format(blog_id=blog_id) + '?' + urlencode({'isDraft': 'true' if args.draft else 'false'})
    result = request_json(url, {'kind': 'blogger#post', 'title': title, 'content': content}, token=access_token())
    if not result.get('id'):
        raise RuntimeError('Blogger API returned no post ID; check Blogger dashboard')
    print('BLOGGER_API_SUCCESS')
    print('Post ID:', result['id'])
    print('Status:', result.get('status', '(not returned)'))
    print('URL:', result.get('url', '(draft may not have a public URL)'))
    print('No duplicate prevention: each execution creates a new post')

if __name__ == '__main__':
    main()
