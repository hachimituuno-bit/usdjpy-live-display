"""Mail2Blogger: new article with both plain-text and styled HTML alternatives."""
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
FX_DATA = ROOT / "docs" / "latest.json"
STOCK_DATA = ROOT / "docs" / "stocks_latest.json"
DASHBOARD_URL = "https://hachimituuno-bit.github.io/usdjpy-live-display/dashboard.html"
FX_URL = "https://hachimituuno-bit.github.io/usdjpy-live-display/index.html"
STOCK_URL = "https://hachimituuno-bit.github.io/usdjpy-live-display/stocks.html"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"分析データを読み込めません: {path.name} ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: JSONの形式が不正です")
    return data


def format_jst(value: object) -> str:
    """Display ISO timestamps in JST without ISO separators or timezone suffix."""
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def build_post(now: datetime | None = None) -> tuple[str, str, str]:
    now = now or datetime.now(JST)
    fx = read_json(FX_DATA)
    stock_data = read_json(STOCK_DATA)
    try:
        buy, sell = int(fx["buy"]), int(fx["sell"])
        if not (0 <= buy <= 100 and 0 <= sell <= 100 and buy + sell == 100):
            raise ValueError("USD/JPYスコアが不正")
        frames = fx["timeframes"]
        frame_rows = []
        for key in ("日足", "4時間足", "1時間足", "15分足"):
            entry = frames[key]
            frame_rows.append((key, str(entry["direction"]), int(entry["weight"])))
        top = stock_data["stocks"]
        if not isinstance(top, list) or len(top) != 3:
            raise ValueError("日本株の上位3銘柄データが不正")
        stock_rows = []
        for i, item in enumerate(top, 1):
            score = int(item["score_buy"])
            if not 0 <= score <= 100:
                raise ValueError("日本株の買いスコアが不正")
            stock_rows.append((i, str(item["name"]), str(item["symbol"]),
                               str(item["close"]), str(item["date"]), score))
        fx_at = format_jst(fx["generated_at_jst"])
        stock_at = format_jst(stock_data["generated_at_jst"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"分析データの項目が不正です: {exc}") from None

    overall = "買い" if buy > sell else "売り" if sell > buy else "中立"
    overall_color = "#c62828" if buy > sell else "#1768ac" if sell > buy else "#555555"
    timestamp = now.strftime("%Y-%m-%d %H:%M JST")
    subject = f"【新方式・{timestamp}】ドル円と日本株3銘柄の分析"
    plain_lines = [
        "【新方式】ドル円（USD/JPY）・日本株3銘柄 分析速報",
        f"投稿時刻：{timestamp}", "",
        "■ USD/JPY テクニカル分析",
        f"総合判定：{overall}",
        f"買いスコア：{buy}/100、売りスコア：{sell}/100",
        f"分析データ更新：{fx_at}",
    ]
    plain_lines += [f"・{k}：{d}（配点{w}点）" for k, d, w in frame_rows]
    plain_lines += ["", "■ 日本株 買いスコア上位3銘柄",
                    f"分析データ更新：{stock_at}"]
    plain_lines += [
        f"{i}位：{name}（{symbol}） 買いスコア {score}/100 終値 {close}円 対象日 {date}"
        for i, name, symbol, close, date, score in stock_rows
    ]
    plain_lines += [
        "", "■ 分析画面",
        f"為替・株式 統合画面：{DASHBOARD_URL}",
        f"為替 詳細：{FX_URL}",
        f"日本株 詳細：{STOCK_URL}", "",
        "※スコアは指標による判定値であり、将来の値動きの確率ではありません。",
        "※日本株は固定候補20銘柄の中から抽出した3銘柄で、東証全銘柄の順位ではありません。",
        "※市況データには遅延があり、休日には直近取引日の情報が表示されることがあります。",
        "", "#end",
    ]
    plain = "\n".join(plain_lines)

    cell = "padding:10px;border:1px solid #dce4ec;text-align:left;"
    def table(headers, rows):
        th = "".join(f'<th style="{cell}background:#e9f0f8;">{esc(h)}</th>' for h in headers)
        trs = "".join("<tr>" + "".join(f'<td style="{cell}">{v}</td>' for v in row) + "</tr>" for row in rows)
        return f'<table style="width:100%;border-collapse:collapse;font-size:14px;"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'

    def decision(value):
        color = "#c62828" if "買" in value else "#1768ac" if "売" in value else "#555"
        return f'<strong style="color:{color};">{esc(value)}</strong>'

    frame_table = table(
        ("時間足", "判定", "配点"),
        [(esc(k), decision(d), f"{w}点") for k, d, w in frame_rows]
    )
    stock_table = table(
        ("順位", "銘柄", "終値", "買いスコア", "対象日"),
        [(str(i), f"{esc(name)}<div style='font-size:12px;color:#666;'>{esc(symbol)}</div>",
          f"{esc(close)}円", f"<strong style='color:#c62828;'>{score}/100</strong>", esc(date))
         for i, name, symbol, close, date, score in stock_rows]
    )
    def section(title, content, color="#123d69"):
        return (f'<div style="margin:18px 0;border:1px solid #dce4ec;border-radius:8px;overflow:hidden;">'
                f'<div style="background:{color};color:white;padding:12px 15px;font-weight:bold;">{esc(title)}</div>'
                f'<div style="padding:14px;">{content}</div></div>')
    links = "".join(
        f'<div style="margin:9px 0;"><a href="{esc(url)}" style="color:#1565c0;">{esc(label)}</a></div>'
        for label, url in (("為替・株式 統合画面", DASHBOARD_URL),
                           ("為替 詳細", FX_URL), ("日本株 詳細", STOCK_URL))
    )
    notes = ("※スコアは指標による判定値であり、将来の値動きの確率ではありません。<br>"
             "※日本株は固定候補20銘柄の中から抽出した3銘柄で、東証全銘柄の順位ではありません。<br>"
             "※市況データには遅延があり、休日には直近取引日の情報が表示されることがあります。")
    markup = (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:12px;background:#f4f7fa;font-family:Arial,sans-serif;color:#182636;">'
        '<div style="max-width:850px;margin:auto;background:white;padding:18px;">'
        f'<div style="background:#0b2f56;color:white;padding:18px;border-radius:8px;">'
        '<div style="font-size:21px;font-weight:bold;">【新方式】ドル円と日本株3銘柄の分析</div>'
        f'<div style="margin-top:8px;">投稿時刻：{esc(timestamp)}</div></div>'
        + section("USD/JPY テクニカル分析",
                  f'<div style="font-size:24px;font-weight:bold;margin-bottom:12px;">'
                  f'総合判定：<span style="color:{overall_color};">{overall}</span></div>'
                  f'<div style="font-size:18px;margin-bottom:12px;">'
                  f'買い <strong style="color:#c62828;">{buy}/100</strong>　'
                  f'売り <strong style="color:#1768ac;">{sell}/100</strong></div>'
                  + frame_table + f'<p style="font-size:12px;color:#555;">分析データ更新：{esc(fx_at)}</p>')
        + section("日本株 買いスコア上位3銘柄",
                  stock_table + f'<p style="font-size:12px;color:#555;">分析データ更新：{esc(stock_at)}</p>', "#146338")
        + section("分析画面・詳細ページ", links, "#345e89")
        + f'<div style="font-size:12px;color:#555;line-height:1.8;padding:10px;">{notes}</div>'
        + '<div style="display:none;">#end</div></div></body></html>'
    )
    return subject, plain, markup


def email_address(name: str, value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{name}が設定されていません、または不正です")
    label, address = parseaddr(value)
    if label or value != address or "@" not in address or " " in address:
        raise ValueError(f"{name}にメールアドレスのみを指定してください")
    return address


def send_post(subject: str, plain: str, markup: str) -> None:
    host = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int((os.environ.get("SMTP_PORT") or "465").strip())
    username = email_address("SMTP_USERNAME", (os.environ.get("SMTP_USERNAME") or "").strip())
    password = os.environ.get("SMTP_PASSWORD") or ""
    recipient = email_address("BLOGGER_POST_EMAIL", (os.environ.get("BLOGGER_POST_EMAIL") or "").strip())
    if not password:
        raise ValueError("SMTP_PASSWORDが設定されていません")
    if recipient.rsplit("@", 1)[1].lower() != "blogger.com":
        raise ValueError("BLOGGER_POST_EMAILはBloggerの投稿専用 @blogger.com アドレスを指定してください")
    if "\n" in host or "\r" in host or not host:
        raise ValueError("SMTP_HOSTが不正です")
    if port not in (465, 587):
        raise ValueError("SMTP_PORTは465（SSL）または587（STARTTLS）を指定してください")
    msg = EmailMessage()
    msg["From"] = username
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(JST))
    msg["Message-ID"] = make_msgid()
    msg.set_content(plain, charset="utf-8", cte="quoted-printable")
    msg.add_alternative(markup, subtype="html", charset="utf-8")
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
        raise RuntimeError(f"SMTP送信に失敗しました（{type(exc).__name__}）。GitHub SecretsやSMTP設定を確認してください。") from None
    print("メール送信をSMTPサーバーが受け付けました。Bloggerでの公開完了は別途確認してください。")


def main() -> None:
    subject, plain, markup = build_post()
    dry_run = (os.environ.get("DRY_RUN") or "true").strip().lower() != "false"
    if dry_run:
        print("DRY RUN: メールは送信していません。")
        print("件名:", subject)
        print("本文（テキストプレビュー）:\n", plain)
        print("HTML本文生成済み（HTMLはログに出力しません）。")
        return
    send_post(subject, plain, markup)


if __name__ == "__main__":
    main()
