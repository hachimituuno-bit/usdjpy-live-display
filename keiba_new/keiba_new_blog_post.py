"""Generate one readable Blogger email post from a complete day of predictions."""
import argparse
import html
import json
import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

BASE = Path('keiba_new_results')
VENUES = ('福島', '東京', '京都', '札幌', '函館', '新潟', '中山', '中京', '阪神', '小倉')

def build(base=BASE):
    summary = json.loads((base / 'run_summary.json').read_text(encoding='utf-8'))
    if summary.get('status') != 'SUCCESS' or summary.get('failed'):
        raise ValueError('Predictions not fully successful; blog post stopped')
    day = date.fromisoformat(summary['date'])
    expected = set(summary['predicted'])
    if len(expected) != len(summary['predicted']):
        raise ValueError('Duplicate race names in summary')
    # Auto mode must be a complete meeting: each venue exactly 1R..12R.
    if summary.get('mode') != 'auto' or len(expected) != 36:
        raise ValueError('Only a complete 36-race auto run can publish one-day article')
    venues = [v for v in VENUES if any(x.startswith(v) for x in expected)]
    if len(venues) != 3 or expected != {f'{v}{n}R' for v in venues for n in range(1, 13)}:
        raise ValueError('Expected exactly 3 venues x 12 races; refused partial publication')
    title = f'【新方式】{day.year}年{day.month}月{day.day}日 中央競馬36レース予想｜馬番一覧'
    css = 'font-family:Arial,"Noto Sans JP",sans-serif;font-size:18px;line-height:1.8;color:#20242a;max-width:780px;margin:auto'
    parts = [f'<div style="{css}">', f'<h2 style="font-size:27px;line-height:1.4">{html.escape(title)}</h2>',
             '<p style="font-size:18px">各レースの<strong>予想上位3頭の馬番</strong>を左から順に掲載します。馬券購入を勧めるものではありません。</p>']
    for venue in venues:
        parts.append(f'<h2 style="font-size:25px;border-bottom:3px solid #234b75;padding-bottom:7px;margin-top:30px">{html.escape(venue)}（1〜12R）</h2>')
        for n in range(1, 13):
            p = base / f'{venue}_{n:02d}R' / 'prediction.json'
            data = json.loads(p.read_text(encoding='utf-8'))
            if data.get('race_date') != day.isoformat() or data.get('venue') != venue or data.get('race_number') != n:
                raise ValueError(f'Mismatched prediction data: {p}')
            horses = data['horses']
            scored = sorted((h for h in horses if h.get('form_score') is not None and h.get('rank') is not None), key=lambda h: h['rank'])
            top = scored[:3]
            if len(top) == 3:
                nums = '　'.join(f'{h["horse_number"]}番' for h in top)
                names = ' ／ '.join(f'{h["horse_number"]}番 {html.escape(h["horse_name"])}' for h in top)
                parts.append(f'<section style="padding:13px 0;border-bottom:1px solid #d5dbe2"><h3 style="font-size:21px;margin:0">{n}R</h3>'
                             f'<div style="font-size:29px;font-weight:800;line-height:1.55;overflow-wrap:anywhere">{nums}</div>'
                             f'<div style="font-size:16px;color:#485363">{names}</div>'
                             f'<div style="font-size:15px;color:#606b78">左から予想1位・2位・3位。評価可能 {len(scored)}/{len(horses)}頭</div></section>')
            else:
                parts.append(f'<section style="padding:13px 0;border-bottom:1px solid #d5dbe2"><h3 style="font-size:21px;margin:0">{n}R</h3>'
                             '<div style="font-size:22px;font-weight:700">予想保留（過去走データ不足）</div></section>')
    parts += ['<h2 style="font-size:25px;margin-top:35px;border-bottom:3px solid #234b75">基本的な馬の決め方</h2>',
              '<p>各馬の過去走から、3着以内の実績85％と着順の相対評価15％を基礎点とし、直近のレースを重視します。さらに<strong>開催場・芝ダート・距離の適性補正係数0.5</strong>を用いて順位を算出します。オッズ・人気順位は使いません。</p>',
              '<p>馬番は評価点の高い順です。<strong>オッズ・人気順位は使用していません。</strong>過去走が確認できない馬は無理に点数を付けず、評価対象から外します。全馬のデータが不足するレースは「予想保留」と表示します。</p>',
              '<p style="font-size:16px;color:#5c6570">※この数値は独自の参考評価であり、3着以内に入る確率や的中を保証するものではありません。出走取消・馬場状態などの当日条件は反映していません。</p>', '</div>']
    return title, '\n'.join(parts)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--send', action='store_true', help='Send only when all SMTP secrets are present')
    args = parser.parse_args()
    title, body = build()
    BASE.mkdir(exist_ok=True)
    (BASE / 'blog_article.html').write_text(body, encoding='utf-8')
    (BASE / 'blog_article_title.txt').write_text(title, encoding='utf-8')
    if not args.send:
        print('PREVIEW ONLY: article generated, email not sent')
        return
    keys = ('BLOGGER_POST_EMAIL', 'SMTP_USERNAME', 'SMTP_PASSWORD')
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise ValueError('Missing email configuration: ' + ', '.join(missing))
    host = (os.getenv('SMTP_HOST') or 'smtp.gmail.com').strip()
    port = int((os.getenv('SMTP_PORT') or '465').strip())
    if port not in (465, 587):
        raise ValueError('SMTP_PORT must be 465 (SSL) or 587 (STARTTLS)')
    sender = os.environ['SMTP_USERNAME'].strip()
    recipient = os.environ['BLOGGER_POST_EMAIL'].strip()
    for name, address in (('SMTP_USERNAME', sender), ('BLOGGER_POST_EMAIL', recipient)):
        _, parsed = parseaddr(address)
        if parsed != address or '@' not in address or any(c in address for c in ('\r', '\n', ' ')):
            raise ValueError(f'Invalid email address in {name}')
    if not recipient.lower().endswith('@blogger.com'):
        raise ValueError('BLOGGER_POST_EMAIL must be a @blogger.com posting address')
    msg = EmailMessage()
    msg['Subject'] = title
    msg['From'] = sender
    msg['To'] = recipient
    msg.set_content('このメールはHTML形式の競馬予想記事です。')
    msg.add_alternative(body, subtype='html')
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(sender, os.environ['SMTP_PASSWORD'])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(sender, os.environ['SMTP_PASSWORD'])
            smtp.send_message(msg)
    print('Blog article email accepted by SMTP server (Blogger publication must be checked separately)')

if __name__ == '__main__':
    main()
