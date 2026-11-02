"""Publish a new styled Blogger article by Mail2Blogger (no OAuth)."""
from __future__ import annotations
import html
import json
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, format_datetime, make_msgid
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


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def direction(value: object) -> str:
    label = str(value)
    color = '#b91c1c' if '買' in label else '#1d4ed8' if '売' in label else '#334155'
    return f'<strong style="color:{color}">{esc(label)}</strong>'


def panel(title: str, content: str, color: str) -> str:
    return (f'<section style="border:1px solid #d8e0eb;border-radius:12px;overflow:hidden;margin:16px 0;background:#fff">'
            f'<h2 style="background:{color};color:#fff;padding:12px 16px;margin:0;font-size:19px">{esc(title)}</h2>'
            f'<div style="padding:14px 16px">{content}</div></section>')


def build_post(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(JST)
    fx = read_json(FX_DATA)
    stock_data = read_json(STOCK_DATA)
    try:
        buy, sell = int(fx['buy']), int(fx['sell'])
        if not (0 <= buy <= 100 and 0 <= sell <= 100 and buy + sell == 100):
            raise ValueError('USD/JPYスコアが不正')
        frames = fx['timeframes']
        frame_rows = []
        for key in ('日足', '4時間足', '1時間足', '15分足'):
            entry = frames[key]
            frame_rows.append((key, str(entry['direction']), int(entry['weight'])))
        top = stock_data['stocks']
        if not isinstance(top, list) or len(top) != 3:
            raise ValueError('日本株の上位3銘柄データが不正')
        stock_rows = []
        for i, item in enumerate(top, 1):
            score = int(item['score_buy'])
            if not 0 <= score <= 100:
                raise ValueError('日本株の買いスコアが不正')
            stock_rows.append((i, str(item['name']), str(item['symbol']), score,
                               str(item['close']), str(item['date'])))
        fx_at = str(fx['generated_at_jst'])
        stock_at = str(stock_data['generated_at_jst'])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'分析データの項目が不正です: {exc}') from None

    timestamp = now.strftime('%Y-%m-%d %H:%M JST')
    subject = f'【新方式・{timestamp}】ドル円と日本株3銘柄の分析'
    th = 'padding:10px;border:1px solid #dbe2ea;background:#eff4fa;text-align:left'
    td = 'padding:10px;border:1px solid #dbe2ea;vertical-align:top'
    table_start = '<table style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.5">'
    fx_label = '買い' if buy > sell else '売り' if sell > buy else '中立'
    fx_content = (f'<p style="font-size:17px;margin:0 0 10px">テクニカル判定：{direction(fx_label)}</p>'
                  f'<div style="font-size:22px;font-weight:bold">買い {buy}/100　<span style="color:#64748b">|</span>　売り {sell}/100</div>'
                  '<p style="color:#64748b;font-size:13px">※現在値・前回比は元データに項目が確認できないため表示していません。</p>')
    frame_content = table_start + f'<tr><th style="{th}">時間足</th><th style="{th}">判定</th><th style="{th}">配点</th></tr>'
    for key, label, weight in frame_rows:
        frame_content += f'<tr><td style="{td}">{esc(key)}</td><td style="{td}">{direction(label)}</td><td style="{td}">{weight}点</td></tr>'
    frame_content += '</table>'
    stocks_content = table_start + f'<tr><th style="{th}">順位・銘柄</th><th style="{th}">終値</th><th style="{th}">買いスコア</th></tr>'
    for i, name, symbol, score, close, date in stock_rows:
        stocks_content += (f'<tr><td style="{td}"><strong>{i}位 {esc(name)}</strong><div style="color:#64748b">{esc(symbol)}</div></td>'
                           f'<td style="{td}">{esc(close)}円<div style="font-size:12px;color:#64748b">{esc(date)}</div></td>'
                           f'<td style="{td}"><strong style="color:#b91c1c">{score}/100</strong></td></tr>')
    stocks_content += '</table>'
    links = ''.join(f'<p style="margin:8px 0"><a href="{esc(url)}" style="color:#075aa6;text-decoration:underline">{esc(label)}</a></p>'
                    for label, url in [('為替・株式 統合画面', DASHBOARD_URL), ('為替 詳細', FX_URL), ('日本株 詳細', STOCK_URL)])
    notes = ('<p>※スコアは指標による判定値であり、将来の値動きの確率ではありません。</p>'
             '<p>※日本株は固定候補20銘柄の中から抽出した3銘柄で、東証全銘柄の順位ではありません。</p>'
             '<p>※市況データには遅延があり、休日には直近取引日の情報が表示されることがあります。</p>')
    body = ('<div style="max-width:850px;margin:0 auto;color:#172033;font-family:Arial,\'Noto Sans JP\',sans-serif;line-height:1.65">'
            '<div style="background:#092b53;color:#fff;padding:20px;border-radius:12px">'
            '<h1 style="font-size:24px;margin:0">【新方式】ドル円と日本株3銘柄の分析</h1>'
            f'<p style="margin:6px 0 0">投稿時刻：{esc(timestamp)}</p></div>'
            + panel('USD/JPY（ドル円）', fx_content, '#07518d')
            + panel('時間足別の分析', frame_content, '#5935a2')
            + panel('日本株：買いスコア上位3銘柄', stocks_content, '#176c34')
            + panel('データ更新情報', f'<p>為替：{esc(fx_at)}</p><p>日本株：{esc(stock_at)}</p>', '#475569')
            + panel('分析画面・詳細ページ', links, '#126da3')
            + panel('注意事項', notes, '#475569')
            + '</div>')
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
    msg['Date'] = format_datetime(datetime.now(JST))
    msg['Message-ID'] = make_msgid()
    msg.set_content('この記事はHTML形式です。HTML対応の表示でご覧ください。\n#end', charset='utf-8', cte='quoted-printable')
    msg.add_alternative(body + '\n<!-- #end -->', subtype='html', charset='utf-8', cte='quoted-printable')
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
        raise RuntimeError(f'SMTP送信に失敗しました（{type(exc).__name__}）。GitHub SecretsやSMTP設定を確認してください。') from None
    print('メール送信をSMTPサーバーが受け付けました。Bloggerでの公開完了は別途確認してください。')


def main() -> None:
    subject, body = build_post()
    dry_run = (os.environ.get('DRY_RUN') or 'true').strip().lower() != 'false'
    if dry_run:
        print('DRY RUN: メールは送信していません。')
        print('件名:', subject)
        print('本文（HTMLプレビュー）:\n', body)
        return
    send_post(subject, body)


if __name__ == '__main__':
    main()
