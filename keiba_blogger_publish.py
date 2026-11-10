"""Publish a validated future-race ranking by Mail2Blogger (scheduled only)."""
import argparse
import json
import os
import re
from datetime import date, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from mail_to_blogger import send_post

JST = ZoneInfo("Asia/Tokyo")
BASE = Path("keiba_rolling_results")
LEDGER = Path("keiba_published_races.json")


def candidate_posts():
    today = datetime.now(JST).date()
    results = []
    for path in BASE.glob("*/ranking.json"):
        try:
            ranking = json.loads(path.read_text(encoding="utf-8"))
            entries = json.loads((path.parent / "entries.json").read_text(encoding="utf-8"))
            day = date.fromisoformat(ranking["race_date"])
            if not (today < day <= date.fromordinal(today.toordinal() + 9)):
                continue
            if entries["race_date"] != ranking["race_date"] or entries["venue"] != ranking["venue"] or entries["race_number"] != ranking["race_number"]:
                continue
            horses = ranking["horses"]
            entry_horses = entries["horses"]
            if not isinstance(horses, list) or len(horses) < 2 or len(horses) != len(entry_horses):
                continue
            if not all(isinstance(h.get("form_score"), (int, float)) and h.get("completed_runs", 0) >= 1 for h in horses):
                continue
            if not all(h.get("identity_verified") is True for h in horses):
                print("SAFE SKIP: horse identity not verified", path)
                continue
            entry_set = {(h["number"], h["name"]) for h in entry_horses}
            rank_set = {(h["horse_number"], h["horse_name"]) for h in horses}
            if len(entry_set) != len(horses) or entry_set != rank_set:
                continue
            if sorted(h["rank"] for h in horses) != list(range(1, len(horses) + 1)):
                continue
            key = f"{day.isoformat()}_{ranking['venue']}_{ranking['race_number']}R"
            results.append((key, ranking))
        except (OSError, ValueError, KeyError, TypeError):
            print("SAFE SKIP: malformed or incomplete ranking", path)
    return sorted(results)


def render(r):
    race = f"{r['race_date']} {r['venue']} {r['race_number']}R {r['race_name']}"
    subject = f"【JRA競馬予想】{race} 過去成績ランキング"
    lines = [race, "過去成績による参考順位（的中確率ではありません）", ""]
    rows = []
    for h in sorted(r["horses"], key=lambda x: x["rank"]):
        line = f"{h['rank']}位　馬番{h['horse_number']}　{h['horse_name']}　評価{h['form_score']:.2f}　過去{h['completed_runs']}走"
        lines.append(line)
        rows.append("<tr>" + "".join(f"<td style='border:1px solid #ddd;padding:7px'>{escape(str(v))}</td>" for v in (h['rank'],h['horse_number'],h['horse_name'],f"{h['form_score']:.2f}",h['completed_runs'])) + "</tr>")
    lines += ["", "※過去成績に基づく機械的評価であり、勝率・的中保証ではありません。", "#end"]
    html = f"<h2>{escape(race)}</h2><p>過去成績による参考順位（的中確率ではありません）</p><table style='border-collapse:collapse'><tr><th>順位</th><th>馬番</th><th>馬名</th><th>評価</th><th>対象走数</th></tr>{''.join(rows)}</table><p>※過去成績に基づく機械的評価であり、勝率・的中保証ではありません。</p>"
    return subject, "\n".join(lines), html


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("scheduled", "manual"), required=True)
    args = p.parse_args()
    if args.mode != "scheduled":
        print("NO POST: manual execution")
        return
    now = datetime.now(JST)
    if now.weekday() != 4:
        print("NO POST: not Friday JST")
        return
    published = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    if not isinstance(published, dict):
        raise ValueError("Invalid published ledger")
    candidates = [(k,r) for k,r in candidate_posts() if k not in published]
    if not candidates:
        print("NO POST: no complete, identity-verified future rankings or all already sent")
        return
    for key, ranking in candidates:
        subject, plain, markup = render(ranking)
        print("SENDING:", subject, flush=True)
        send_post(subject, plain, markup)
        published[key] = {"smtp_accepted_at": datetime.now(JST).isoformat(), "subject": subject}
        LEDGER.write_text(json.dumps(published, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print("SMTP_ACCEPTED:", key, flush=True)


if __name__ == "__main__":
    main()
