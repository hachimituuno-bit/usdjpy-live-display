"""Mail2Blogger delivery for the new standalone articles repository.

Readable HTML posts (large Japanese text, clear paragraph breaks and links).
No Blogger API / OAuth / Apps Script.  SMTP acceptance is NOT publication proof.

Replace only this module in the *new* usdjpy-live-display repository.
All content generators call publish() through this common module.
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

# Inline CSS is more reliable in email than a <style> block.
# Blogger may still remove or modify styles during email import.
FONT = "'Hiragino Kaku Gothic ProN','Yu Gothic',Meiryo,sans-serif"
BASE_STYLE = f"font-family:{FONT};font-size:21px;line-height:2.0;color:#374151;overflow-wrap:break-word;"
P_STYLE = "font-size:21px;line-height:2.0;margin:0 0 26px 0;white-space:normal;"
H2_STYLE = "font-size:28px;line-height:1.55;font-weight:700;margin:34px 0 18px 0;color:#283747;"
H3_STYLE = "font-size:24px;line-height:1.6;font-weight:700;margin:30px 0 16px 0;color:#334155;"
LI_STYLE = "font-size:21px;line-height:1.9;margin:0 0 12px 0;"
A_STYLE = "color:#1761ad;text-decoration:underline;overflow-wrap:anywhere;"

ALLOWED_TAGS = {
    'p', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'a', 'ul', 'ol', 'li',
    'blockquote', 'br', 'img', 'hr', 'div',
}
BLOCK_TAGS = {'p', 'h2', 'h3', 'h4', 'ul', 'ol', 'blockquote', 'div'}
BANNED_TAGS = {'script', 'style', 'iframe', 'form', 'object', 'embed', 'svg', 'noscript', 'meta', 'link'}


def is_preview() -> bool:
    return os.getenv('DRY_RUN', 'true').strip().lower() != 'false'


def readable_text(markup: str) -> str:
    """Text-only fallback and dry-run excerpt, keeping paragraphs and URLs."""
    soup = BeautifulSoup(markup or '', 'html.parser')
    for tag in soup.find_all(list(BANNED_TAGS)):
        tag.decompose()
    for tag in soup.find_all('a', href=True):
        url = tag.get('href', '').strip()
        if url.startswith(('https://', 'http://')):
            tag.append(' (' + url + ')')
    for tag in soup.find_all('img', src=True):
        src = tag.get('src', '').strip()
        tag.replace_with('画像：' + src if src.startswith('https://') else '')
    for tag in soup.find_all(['h2', 'h3', 'h4', 'p', 'div', 'li', 'br', 'ol', 'ul']):
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


def _split_long_japanese_paragraph(text: str, ideal: int = 125) -> list[str]:
    """Divide long single-text paragraphs at sentence boundaries, not arbitrary characters.

    Text with nested links/emphasis stays intact and is not split by this helper.
    """
    clean = re.sub(r'\s+', ' ', text).strip()
    if len(clean) <= 180:
        return [clean] if clean else []
    # Keep Japanese closing quotes with the sentence before them.
    sentences = re.findall(r'.+?(?:[。！？!?]+[」』）】]*|$)', clean, re.DOTALL)
    sentences = [s.strip() for s in sentences if s.strip()]
    result, current = [], ''
    for sentence in sentences:
        if current and len(current) >= 65 and len(current) + len(sentence) > ideal:
            result.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        result.append(current)
    return result if len(result) > 1 else [clean]


def formatted_html(markup: str) -> str:
    """Create styled Blogger HTML, preserving clickable links and headings.

    Unlike the old gateway this does NOT strip every <p>/<h2>/<a> to plain text.
    The caller's generated story, trivia, AI and manga bodies all use this path.
    """
    source = str(markup or '').strip()
    if not source:
        raise ValueError('本文が空なので送信しません')
    soup = BeautifulSoup(source, 'html.parser')

    # Turn plain-text articles into paragraphs if a generator ever returns text.
    if not soup.find(BLOCK_TAGS) and '<' not in source:
        blocks = re.split(r'\n\s*\n|\r\n\s*\r\n', source)
        soup = BeautifulSoup(''.join('<p>' + html.escape(b.strip()).replace('\n', '<br>') + '</p>'
                                      for b in blocks if b.strip()), 'html.parser')

    for tag in list(soup.find_all(list(BANNED_TAGS))):
        tag.decompose()

    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        # Discard generated styles, event handlers and unknown URL schemes.
        href = tag.get('href', '') if tag.name == 'a' else ''
        src = tag.get('src', '') if tag.name == 'img' else ''
        tag.attrs.clear()
        if tag.name == 'a' and str(href).strip().startswith(('http://', 'https://')):
            tag['href'] = str(href).strip()
            tag['style'] = A_STYLE
            tag['rel'] = 'noopener noreferrer'
        elif tag.name == 'a':
            tag.unwrap()
        if tag.name == 'img' and str(src).strip().startswith('https://'):
            tag['src'] = str(src).strip()
            tag['style'] = 'max-width:100%;height:auto;display:block;margin:16px auto;'
        elif tag.name == 'img':
            tag.decompose()

    # A very long single <p> is also difficult to read on mobile. Split it
    # only if it contains plain text, so a nested hyperlink isn't damaged.
    for p in list(soup.find_all('p')):
        if p.find(True):
            continue
        segments = _split_long_japanese_paragraph(p.get_text(' ', strip=True))
        if len(segments) > 1:
            for segment in segments:
                node = soup.new_tag('p')
                node.string = segment
                p.insert_before(node)
            p.decompose()

    for tag in soup.find_all(True):
        styles = {
            'p': P_STYLE, 'h2': H2_STYLE, 'h3': H3_STYLE,
            'h4': H3_STYLE, 'li': LI_STYLE,
            'ul': 'margin:0 0 24px 0;padding-left:1.6em;',
            'ol': 'margin:0 0 24px 0;padding-left:1.6em;',
            'blockquote': 'font-size:21px;line-height:1.9;margin:22px 0 26px 0;padding:4px 0 4px 18px;border-left:3px solid #c8d4e0;',
        }
        if tag.name in styles:
            tag['style'] = styles[tag.name]

    inner = ''.join(str(c) for c in soup.contents).strip()
    if not BeautifulSoup(inner, 'html.parser').get_text(' ', strip=True):
        raise ValueError('本文をHTMLに整形できませんでした')
    # No signature is appended to programmatically created emails, so the
    # optional Blogger #end marker is unnecessary in an HTML email.
    return f'<div style="{BASE_STYLE}">' + inner + '</div>'


def validate_addr(name: str, raw: str) -> str:
    value = (raw or '').strip()
    label, addr = parseaddr(value)
    if not value or label or addr != value or '\n' in value or '\r' in value or '@' not in addr:
        raise ValueError(f'{name}はメールアドレスのみを設定してください')
    return addr


def build_mail(subject: str, body: str, from_addr: str, to_addr: str,
               attachment_path=None, mode: str = 'html') -> EmailMessage:
    """Create the email separately so its MIME structure can be tested without SMTP."""
    msg = EmailMessage()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg['Date'] = format_datetime(datetime.now(JST))
    msg['Message-ID'] = make_msgid()
    if mode == 'plain':
        msg.set_content(readable_text(body).rstrip() + '\n\n#end\n',
                        charset='utf-8', cte='quoted-printable')
    elif mode == 'html':
        # Keep it single-part text/html to avoid Blogger unexpectedly selecting
        # the text/plain part and discarding all paragraph formatting.
        msg.set_content(formatted_html(body), subtype='html',
                        charset='utf-8', cte='quoted-printable')
    else:
        raise ValueError('MAIL_CONTENT_MODEはhtmlまたはplainにしてください')
    if attachment_path:
        from pathlib import Path
        p = Path(attachment_path)
        if not p.is_file():
            raise FileNotFoundError('マンガ画像がありません')
        msg.add_attachment(p.read_bytes(), maintype='image', subtype='png', filename=p.name)
    return msg


def publish(title: str, body: str, labels=None, is_draft=False, *, attachment_path=None):
    """Return a local SMTP receipt (NOT a Blogger post ID)."""
    if is_draft and not is_preview():
        raise RuntimeError('Mail2Bloggerで下書き投稿の指定はできません')
    subject = str(title).strip()
    if not subject or '\r' in subject or '\n' in subject:
        raise ValueError('投稿タイトルが不正です')
    original_text = readable_text(body)
    mode = (os.getenv('MAIL_CONTENT_MODE') or 'html').strip().lower()
    # The label arguments are deliberately not shown as a fake 'カテゴリー' line.
    # Email posting cannot attach real Blogger labels.
    if is_preview():
        formatted_html(body) if mode == 'html' else None  # validate HTML early
        print('[PREVIEW] 件名:', subject)
        print('[PREVIEW] 本文文字数:', len(original_text))
        print('[PREVIEW] フォーマット:', mode)
        print('[PREVIEW] 本文抜粋:', original_text[:900])
        print('[PREVIEW] メール未送信・Blogger未投稿・状態ファイル更新なし')
        return {'id': 'preview-' + uuid.uuid4().hex[:10], 'url': '', 'mail_status': 'preview'}, False

    username = validate_addr('SMTP_USERNAME', os.getenv('SMTP_USERNAME', ''))
    recipient = validate_addr('BLOGGER_POST_EMAIL', os.getenv('BLOGGER_POST_EMAIL', ''))
    if recipient.rsplit('@', 1)[1].lower() != 'blogger.com':
        raise ValueError('BLOGGER_POST_EMAILは投稿専用の@blogger.comアドレスにしてください')
    password = os.getenv('SMTP_PASSWORD', '')
    if not password:
        raise RuntimeError('SMTP_PASSWORDがありません（既存のGmailアプリパスワードを設定してください）')
    host = (os.getenv('SMTP_HOST') or '').strip() or 'smtp.gmail.com'
    port_text = (os.getenv('SMTP_PORT') or '').strip() or '465'
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError('SMTP_PORTには465または587を数字で設定してください') from None
    if not host or '\n' in host or port not in (465, 587):
        raise ValueError('SMTP_HOST / SMTP_PORTの設定が不正です')
    msg = build_mail(subject, body, username, recipient, attachment_path, mode=mode)
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
    except (OSError, smtplib.SMTPException):
        raise RuntimeError('SMTP送信失敗。秘密情報はログに表示しません') from None
    receipt = 'smtp-' + uuid.uuid4().hex
    print('SMTP_ACCEPTED:', subject)
    print('送信形式:', mode)
    print('重要：SMTP_ACCEPTEDはBloggerの公開確認ではありません。投稿一覧で確認してください。')
    return {'id': receipt, 'url': '', 'mail_status': 'smtp_accepted'}, False
