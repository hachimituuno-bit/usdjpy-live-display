"""Mail2Blogger delivery for the private auto-blog repository.

No Blogger API, OAuth token, Google Cloud OAuth app, or Apps Script.
SMTP acceptance is *not* proof that Blogger published the article.
"""
from __future__ import annotations

import html
import os
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr

from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))


def is_preview() -> bool:
    return os.getenv('DRY_RUN', 'true').strip().lower() != 'false'


def readable_text(markup: str) -> str:
    """Convert the original Blogger HTML to readable plain text for reliable email delivery."""
    soup = BeautifulSoup(markup or '', 'html.parser')
    for tag in soup(['script', 'style', 'iframe', 'form']):
        tag.decompose()
    for tag in soup.find_all('a', href=True):
        url = tag['href'].strip()
        if url.startswith(('https://', 'http://')):
            tag.append(' (' + url + ')')
    for tag in soup.find_all('img', src=True):
        src = tag['src'].strip()
        tag.replace_with('画像：' + src if src.startswith('https://') else '')
    for tag in soup.find_all(['h1', 'h2', 'h3', 'p', 'div', 'li', 'br', 'ol', 'ul']):
        if tag.name == 'li':
            tag.insert_before('・')
        tag.insert_before('\n')
        tag.insert_after('\n')
    out = html.unescape(soup.get_text(' ', strip=False))
    out = re.sub(r'[ \t]+', ' ', out)
    out = re.sub(r'\n[ \t]+', '\n', out)
    out = re.sub(r'\n{3,}', '\n\n', out).strip()
    if not out:
        raise ValueError('本文が空なので送信しません')
    return out


def validate_addr(name: str, raw: str) -> str:
    value = (raw or '').strip()
    label, addr = parseaddr(value)
    if not value or label or addr != value or '\n' in value or '\r' in value or '@' not in addr:
        raise ValueError(f'{name}はメールアドレスのみを設定してください')
    return addr


def publish(title: str, body: str, labels=None, is_draft=False, *, attachment_path=None):
    """Blogger-compatible return value. 'id' is a local SMTP receipt, not a Blogger post ID."""
    if is_draft and not is_preview():
        raise RuntimeError('Mail2Bloggerはこのプログラムで下書き投稿を指定できません。Blogger側の設定を確認してください')
    subject = str(title).strip()
    if not subject or '\r' in subject or '\n' in subject:
        raise ValueError('投稿タイトルが不正です')
    text = readable_text(body)
    category = ', '.join(labels) if isinstance(labels, (list, tuple)) else str(labels or '')
    if category:
        text = 'カテゴリー：' + category + '\n\n' + text
    content = text.rstrip() + '\n\n#end\n'
    # Mail2Blogger cannot set Blogger post labels. Do not claim otherwise.
    if is_preview():
        print('[PREVIEW] 件名:', subject)
        print('[PREVIEW] 本文文字数:', len(content))
        print('[PREVIEW] 本文抜粋:', content[:900])
        print('[PREVIEW] メール未送信・Blogger未投稿・状態ファイル更新なし')
        return {'id': 'preview-' + uuid.uuid4().hex[:10], 'url': '', 'mail_status': 'preview'}, False

    username = validate_addr('SMTP_USERNAME', os.getenv('SMTP_USERNAME', ''))
    recipient = validate_addr('BLOGGER_POST_EMAIL', os.getenv('BLOGGER_POST_EMAIL', ''))
    if recipient.rsplit('@', 1)[1].lower() != 'blogger.com':
        raise ValueError('BLOGGER_POST_EMAILは投稿専用の@blogger.comアドレスにしてください')
    password = os.getenv('SMTP_PASSWORD', '')
    if not password:
        raise RuntimeError('SMTP_PASSWORDがありません（既存のGmailアプリパスワードを設定してください）')
    host = os.getenv('SMTP_HOST', 'smtp.gmail.com').strip()
    port = int(os.getenv('SMTP_PORT', '465'))
    if not host or '\n' in host or port not in (465, 587):
        raise ValueError('SMTP_HOST / SMTP_PORTの設定が不正です')
    msg = EmailMessage()
    msg['From'] = username
    msg['To'] = recipient
    msg['Subject'] = subject
    msg['Date'] = format_datetime(datetime.now(JST))
    msg['Message-ID'] = make_msgid()
    msg.set_content(content, charset='utf-8', cte='quoted-printable')
    if attachment_path:
        from pathlib import Path
        p = Path(attachment_path)
        if not p.is_file():
            raise FileNotFoundError('マンガ画像がありません')
        # Blogger's email posting supports attached images, but delivery can still fail.
        msg.add_attachment(p.read_bytes(), maintype='image', subtype='png', filename=p.name)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=35) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=35) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise RuntimeError(f'SMTP送信失敗: {type(exc).__name__}。秘密情報はログに表示しません') from None
    receipt = 'smtp-' + uuid.uuid4().hex
    print('SMTP_ACCEPTED:', subject)
    print('重要：SMTP_ACCEPTED は Blogger の公開確認ではありません。投稿一覧と配信エラーメールを確認してください。')
    return {'id': receipt, 'url': '', 'mail_status': 'smtp_accepted'}, False
