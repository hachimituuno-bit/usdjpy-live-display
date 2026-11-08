"""Original, evergreen Japanese horse-racing education posts. No external racing data."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
import os
import smtplib

JST = timezone(timedelta(hours=9))
TOPICS = [
    ("距離適性の考え方", "同じ馬でも、短距離と中長距離では求められる能力が異なります。", [
        ("ペースの違い", "短距離では序盤から速い流れになりやすく、中長距離では道中の折り合いや持久力も大切になります。"),
        ("過去成績の見方", "着順だけでなく、走った距離、道中の位置取り、上がりの傾向をあわせて整理しましょう。"),
        ("注意する点", "一度の好走や凡走だけで距離適性を決めつけないことが重要です。")]),
    ("先行馬と差し馬の違い", "レースの流れを考えるとき、脚質は重要な切り口のひとつです。", [
        ("先行タイプ", "前方の位置を確保する馬は、ペースが落ち着けば力を残しやすい一方、先行争いが激しくなると負担が増えます。"),
        ("差しタイプ", "後方から運ぶ馬は、前が速い流れになったときに浮上する場合があります。ただし進路や展開の影響も受けます。"),
        ("評価のポイント", "各馬の脚質だけでなく、同じレースに前へ行きたい馬が何頭いるかを考えます。")]),
    ("芝とダートの違い", "芝とダートでは走路の性質が異なるため、同じ距離でも結果が変わることがあります。", [
        ("芝の見方", "走路状態や時計の出方によって、スピードの持続と末脚の評価が変わります。"),
        ("ダートの見方", "砂を走る適性に加え、スタート後の位置取りや砂をかぶる経験などが関係する場合があります。"),
        ("比較の注意", "芝の成績をそのままダートに換算せず、同じ条件での実績を優先して確認します。")]),
    ("馬場状態の読み方", "馬場状態は競馬予想で確認しておきたい情報のひとつです。", [
        ("良・稍重・重・不良", "馬場の区分は含水量などの状況に基づき発表されますが、区分だけで全レースの傾向が決まるわけではありません。"),
        ("馬ごとの適性", "過去の道悪実績を見るときは、相手関係や展開にも注意しましょう。"),
        ("当日確認", "実際の予想を行う場合は、主催者の発表する最新の馬場状態を確認する必要があります。")]),
    ("枠順とコース形状", "枠順は重要な条件ですが、有利不利を一律に決めつけることはできません。", [
        ("コース形状", "スタートから最初のコーナーまでの距離や直線の長さによって、位置取りの難しさは変わります。"),
        ("頭数との関係", "同じ枠順でも、多頭数と少頭数では周囲の馬との関係が異なります。"),
        ("評価のポイント", "コース形状・脚質・頭数を組み合わせて考えます。")]),
    ("斤量をどう考えるか", "負担重量は走りに影響する条件のひとつですが、数字だけで予想を決めることはできません。", [
        ("比較の基本", "過去走との斤量差を確認するときは、相手関係や距離・馬場の違いも整理します。"),
        ("単純計算の落とし穴", "斤量差を必ず一定の着差に換算できるわけではありません。"),
        ("総合判断", "馬の能力・展開・コース適性とあわせて評価するのが基本です。")]),
    ("着順だけで馬を評価しない", "過去成績を確認するときは、着順の背景を読むことが大切です。", [
        ("相手関係", "同じ5着でも、どのような相手と走ったかで評価は変わります。"),
        ("展開", "先行争いが激しいレースや進路が狭くなったレースでは、能力を発揮しきれなかった可能性があります。"),
        ("継続性", "複数レースを比較し、再現しやすい特徴を探します。")]),
    ("中穴候補の探し方", "中穴馬を考えるときは、人気が低いこと自体を強みと考えないのが大切です。", [
        ("評価と人気の違い", "自分の評価が高いのに人気が相対的に低い馬がいれば、注目候補になり得ます。"),
        ("オッズの扱い", "単勝オッズは随時変動します。オッズなしで『5〜20倍の馬』と断定することはできません。"),
        ("限界", "独自評価が市場より正確である保証はなく、的中や利益を約束するものではありません。")]),
]

def choose_topic(day: date):
    if day.weekday() not in (5, 6):
        raise ValueError('土曜日または日曜日のみ対象です')
    # Alternate topics between weekends, while retaining deterministic outputs for re-runs.
    index = (day.toordinal() - date(2026, 1, 3).toordinal()) // 7 * 2 + (day.weekday() == 6)
    return TOPICS[index % len(TOPICS)]

def build_article(day: date):
    title, intro, sections = choose_topic(day)
    title_full = f'【競馬の基礎】{title}｜{day:%Y年%m月%d日}'
    pieces = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<style>body{font-family:Meiryo,"Yu Gothic",sans-serif;color:#242424;line-height:1.9;font-size:19px;max-width:800px;margin:30px auto;padding:0 20px}'
        'h1{font-size:30px;line-height:1.5}h2{font-size:24px;border-bottom:2px solid #bbb;padding-bottom:7px;margin-top:32px}'
        'p{margin:18px 0}.notice{padding:14px;background:#f5f5f5;border-left:4px solid #888}</style></head><body>',
        f'<h1>{escape(title_full)}</h1>',
        '<p class="notice">この記事は競馬の基礎知識を解説する一般記事です。'
        '当日の出走馬・開催レース・オッズを確認した予想ではありません。</p>',
        f'<p>{escape(intro)}</p>'
    ]
    for heading, body in sections:
        pieces.append(f'<h2>{escape(heading)}</h2><p>{escape(body)}</p>')
    pieces.append('<h2>まとめ</h2><p>ひとつの条件だけで判断せず、距離・馬場・展開・相手関係などを組み合わせて考えることが大切です。</p>')
    pieces.append('<p class="notice">独自に作成した一般解説です。特定のレースの予想や馬券購入を勧めるものではありません。</p></body></html>')
    return title_full, '\n'.join(pieces) + '\n'

def email_post(subject: str, body_html: str):
    host = os.environ['COURSE_SMTP_HOST']
    port = int(os.environ.get('COURSE_SMTP_PORT', '465'))
    user = os.environ['COURSE_SMTP_USER']
    password = os.environ['COURSE_SMTP_PASSWORD']
    target = os.environ['COURSE_BLOGGER_EMAIL']
    msg = EmailMessage()
    msg['From'] = user
    msg['To'] = target
    msg['Subject'] = subject
    msg.set_content('HTML形式の記事です。')
    msg.add_alternative(body_html, subtype='html')
    with smtplib.SMTP_SSL(host, port, timeout=25) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', default=datetime.now(JST).date().isoformat())
    p.add_argument('--out-dir', default='keiba_course_drafts')
    p.add_argument('--send', action='store_true', help='明示指定時のみメール送信')
    args = p.parse_args()
    day = date.fromisoformat(args.date)
    title, document = build_article(day)
    path = Path(args.out_dir) / f'{day.isoformat()}_COURSE.html'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding='utf-8')
    print(f'COURSE_READY: {path} / {title}')
    if args.send:
        email_post(title, document)
        print('MAIL_ACCEPTED_BY_SMTP: Blogger公開の成否は別途確認が必要です')
    else:
        print('DRAFT_ONLY: メール送信なし')

if __name__ == '__main__':
    main()
