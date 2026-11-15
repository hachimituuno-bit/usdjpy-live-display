"""Send a new field-specific Blogger email on a manual FX or stocks workflow run.
No deduplication. Clearly label previously saved data if fresh retrieval failed.
SMTP acceptance cannot guarantee Blogger publication.
"""
import argparse
import html
from datetime import datetime, timezone, timedelta
from pathlib import Path
from mail_to_blogger import read_json, format_jst, send_post

JST = timezone(timedelta(hours=9))
BASE = Path(__file__).resolve().parent / 'docs'

def build(kind):
    now = datetime.now(JST)
    stamp = now.strftime('%Y-%m-%d %H:%M JST')
    if kind == 'fx':
        data = read_json(BASE / 'latest.json')
        subject = f'【ドル円分析・手動更新】{stamp}'
        rows = [f'買いスコア：{data["buy"]}/100', f'売りスコア：{data["sell"]}/100']
        for key in ('日足', '4時間足', '1時間足', '15分足'):
            info = data['timeframes'][key]
            rows.append(f'{key}：{info["direction"]}（配点{info["weight"]}）')
        source = 'https://hachimituuno-bit.github.io/usdjpy-live-display/index.html'
    else:
        data = read_json(BASE / 'stocks_latest.json')
        subject = f'【日本株トップ3・手動更新】{stamp}'
        stocks = data['stocks']
        if not stocks:
            raise ValueError('株式データに銘柄がありません')
        rows = [f'{i}位：{item["name"]}（{item["symbol"]}） 買いスコア{item["score_buy"]}/100、終値{item["close"]}円、対象日{item["date"]}' for i, item in enumerate(stocks, 1)]
        source = 'https://hachimituuno-bit.github.io/usdjpy-live-display/stocks.html'
    updated = format_jst(data.get('generated_at_jst', '不明'))
    note = f'記事送信：{stamp}／保存データの更新日時：{updated}。最新データ取得に失敗した場合は保存済みデータを掲載しています。リアルタイム価格とは限りません。'
    lines = [subject, note, *rows, '詳細：' + source, '※独自スコアは予測確率ではありません。']
    plain = '\n'.join(lines)
    body = '<h2>' + html.escape(subject) + '</h2><p>' + html.escape(note) + '</p>'
    body += ''.join('<p>' + html.escape(line) + '</p>' for line in rows)
    body += '<p><a href="' + html.escape(source, quote=True) + '">詳細ページ</a></p>'
    body += '<p>※独自スコアは予測確率ではありません。</p>'
    return subject, plain, body

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('fx', 'stocks'))
    args = parser.parse_args()
    subject, plain, body = build(args.kind)
    print('MANUAL_SEND_REQUESTED:', args.kind, subject)
    send_post(subject, plain, body)
    print('SMTP_ACCEPTED (Blogger publication not verified)')

if __name__ == '__main__':
    main()
