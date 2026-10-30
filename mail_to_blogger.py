"""Publish a NEW Blogger article by Mail2Blogger, independent from auto-blog.

Runs on Python standard library only. It does NOT use Google Cloud OAuth,
Google Apps Script, the Blogger API, or a post-title/history duplicate check.
Each non-dry-run invocation sends one new email; it never edits existing posts.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
FX_DATA = ROOT / 'docs' / 'latest.json'
STOCK_DATA = ROOT / 'docs' / 'stocks_latest.json'
DASHBOARD_URL = 'https://hachimituuno-bit.github.io/usdjpy-live-display/dashboard.html'
FX_URL = 'https://hachimituuno-bit.github.io/usdjpy-live-display/index.html'
STOCK_URL = 'https://hachimituuno-bit.github.io/usdjpy-live-display/stocks.html'


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'分析データを読み込めません: {path.name} ({type(exc).__name__})') from None
    if not isinstance(data, dict):
        raise ValueError(f'{path.name}: JSONの形式が不正です')
    return data


def build_post(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(JST)
    fx = read_json(FX_DATA)
    stock_data = read_json(STOCK_DATA)
    try:
        buy = int(fx['buy'])
        sell = int(fx['sell'])
        if not (0 <= buy <= 100 and 0 <= sell <= 100 and buy + sell == 100):
            raise ValueError('USD/JPYスコアが不正')
        frames = fx['timeframes']
        frame_lines = []
        for key in ('日足', '4時間足', '1時間足', '15分足'):
            entry = frames[key]
            frame_lines.append(f"・{key}：{entry['direction']}（配点{int(entry['weight'])}点）")
        top = stock_data['stocks']
        if not isinstance(top, list) or len(top) != 3:
            raise ValueError('日本株の上位3銘柄データが不正')
        stock_lines = []
        for i, item in enumerate(top, 1):
            score = int(item['score_buy'])
            if not 0 <= score <= 100:
                raise ValueError('日本株の買いスコアが不正')
            stock_lines.append(
                f"{i}位：{item['name']}（{item['symbol']}）  "
                f"買いスコア {score}/100  終値 {item['close']}円  "
                f"対象日 {item['date']}"
            )
        fx_at = str(fx['generated_at_jst'])
        stock_at = str(stock_data['generated_at_jst'])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'分析データの項目が不正です: {exc}') from None

    timestamp = now.strftime('%Y-%m-%d %H:%M JST')
    subject = f'【新方式・{timestamp}】ドル円と日本株3銘柄の分析'
    body = '\n'.join([
        '【新方式】ドル円（USD/JPY）・日本株3銘柄 分析速報',
        f'投稿時刻：{timestamp}',
        '',
        '■ USD/JPY テクニカル分析',
        f'買いスコア：{buy}/100、売りスコア：{sell}/100',
        f'分析データ更新：{fx_at}',
        *frame_lines,
        '',
        '■ 日本株 買いスコア上位3銘柄',
        f'分析データ更新：{stock_at}',
        *stock_lines,
        '',
        '■ 分析画面',
        f'為替・株式 統合画面：{DASHBOARD_URL}',
        f'為替 詳細：{FX_URL}',
        f'日本株 詳細：{STOCK_URL}',
        '',
        '※スコアは指標による判定値であり、将来の値動きの確率ではありません。',
        '※日本株は固定候補20銘柄の中から抽出した3銘柄で、東証全銘柄の順位ではありません。',
        '※市況データには遅延があり、休日には直近取引日の情報が表示されることがあります。',
        '',
        '#end',
    ])
    return subject, body


def email_address(name: str, value: str) -> str:
    if not value or '\n' in value or '\r' in value:
        raise ValueError(f'{name}が設定されていません、または不正です')
    label, address = parseaddr(value)
    if label or value != address or '@' not in address or ' ' in address:
        raise ValueError(f'{name}にメールアドレスのみを指定してください')
    return address


def send_post(subject: str, body: str) -> None:
    host = (os.environ.get('SMTP_HOST') or 'smtp.gmail.com').strip()
    port = int((os.environ.get('SMTP_PORT') or '465').strip())
    username = email_address('SMTP_USERNAME', (os.environ.get('SMTP_USERNAME') or '').strip())
    password = os.environ.get('SMTP_PASSWORD') or ''
    recipient = email_address('BLOGGER_POST_EMAIL', (os.environ.get('BLOGGER_POST_EMAIL') or '').strip())
    if not password:
        raise ValueError('SMTP_PASSWORDが設定されていません')
    if recipient.rsplit('@', 1)[1].lower() != 'blogger.com':
        raise ValueError('BLOGGER_POST_EMAILはBloggerの投稿専用 @blogger.com アドレスを指定してください')
    if '\n' in host or '\r' in host or not host:
        raise ValueError('SMTP_HOSTが不正です')
    if port not in (465, 587):
        raise ValueError('SMTP_PORTは465（SSL）または587（STARTTLS）を指定してください')

    msg = EmailMessage()
    msg['From'] = username
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.set_content(body, charset='utf-8')

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # Do not expose SMTP credentials or secret Blogger address in logs.
        raise RuntimeError(f'SMTP送信に失敗しました（{type(exc).__name__}）。GitHub SecretsやSMTP設定を確認してください。') from None

    print('メール送信をSMTPサーバーが受け付けました。Bloggerでの公開完了は別途確認してください。')


def main() -> None:
    subject, body = build_post()
    dry_run = (os.environ.get('DRY_RUN') or 'true').strip().lower() != 'false'
    if dry_run:
        print('DRY RUN: メールは送信していません。')
        print('件名:', subject)
        print('本文（プレビュー）:\n', body)
        return
    send_post(subject, body)


if __name__ == '__main__':
    main()
