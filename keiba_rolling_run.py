"""Rolling JRA featured-race discovery, safe preliminary form ranking.

Automatically chooses the earliest *future* featured race with a published card.
No guessed entrants, dates, horse counts or race names. No posting or wagering.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from keiba_auto_run import discover
from keiba_next_entries_fetch import parse_entries
from keiba_next_collect import collect
from keiba_next_race import rank

JST = timezone(timedelta(hours=9))
HOME = "https://www.jra.go.jp/keiba/thisweek/"
RACE_LINK = re.compile(r"^/keiba/race/\d+(?:/syutsuba\.html|\.html)$")
DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
VENUES = ("東京", "中山", "京都", "阪神", "中京", "新潟", "福島", "小倉", "札幌", "函館")
UNAVAILABLE = ("公開予定", "出馬表は、", "出馬表は、\n")


def fetch(session, url):
    r = session.get(url, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


def featured_urls(home_html):
    """Collect candidate URLs together with their actual JRA link labels."""
    soup = BeautifulSoup(home_html, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        u = urljoin(HOME, a["href"])
        p = urlparse(u)
        if p.scheme != "https" or p.hostname not in ("www.jra.go.jp", "jra.jp"):
            continue
        if not RACE_LINK.fullmatch(p.path):
            continue
        canonical = "https://www.jra.go.jp" + p.path
        label = re.sub(r"\s+", "", a.get_text(" ", strip=True))
        found.setdefault(canonical, set()).add(label)
    return [(url, sorted(labels)) for url, labels in sorted(found.items())]


def metadata(page_html, today, labels=()):
    soup = BeautifulSoup(page_html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = DATE_RE.search(text)
    if not m:
        raise ValueError("Official race date not found (may be a race introduction page)")
    race_day = date(*map(int, m.groups()))
    if not today <= race_day <= today + timedelta(days=35):
        raise ValueError("Not an upcoming race within 35 days")
    sources = list(labels)
    for tag in soup.find_all(["h1", "h2", "h3", "title"]):
        sources.append(tag.get_text(" ", strip=True))
    for tag in soup.find_all("meta"):
        if tag.get("property") in ("og:title", "twitter:title") or tag.get("name") == "description":
            sources.append(tag.get("content", ""))
    sources.append(text[:1200])
    # Match a full race title, including Japanese names (e.g. 天皇賞, 菊花賞).
    race_pattern = re.compile(
        r"(?:第\s*\d+\s*回\s*)?"
        r"([一-龥々ァ-ヶー・A-Za-z0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+?"
        r"(?:ステークス|カップ|記念|大賞典|ダービー|優駿牝馬|天皇賞|菊花賞|皐月賞|有馬記念|ジャパンカップ))"
    )
    names = set()
    for source in sources:
        for match in race_pattern.finditer(source):
            candidate = match.group(1)
            # Page headings may prefix the race name with the event year.
            # Normalize only a leading four-digit year followed by 年.
            candidate = re.sub(r"^20\d{2}年", "", candidate)
            names.add(candidate)
    if len(names) != 1:
        raise ValueError(f"Race name cannot be verified (labels={list(labels)[:4]}, candidates={sorted(names)[:5]})")
    venue_matches = [v for v in VENUES if re.search(rf"{v}競馬場", text[:2000])]
    if len(venue_matches) != 1:
        raise ValueError(f"Cannot establish unique venue (found={venue_matches})")
    return race_day, venue_matches[0], names.pop()


def calendar_race_number(session, day, venue, race_name):
    url = f"https://www.jra.go.jp/keiba/calendar{day.year}/{day.year}/{day.month:02d}/{day.month:02d}{day.day:02d}.html"
    html = fetch(session, url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    # Split venue blocks; a name/number must be found in the correct venue block.
    pattern = re.compile(r"(?:\d+回)?(" + "|".join(VENUES) + r")\d+日")
    blocks = list(pattern.finditer(text))
    matches = []
    norm_name = re.sub(r"\s+", "", race_name).replace("（", "(").replace("）", ")")
    for i, block in enumerate(blocks):
        if block.group(1) != venue:
            continue
        segment = text[block.end():blocks[i+1].start() if i+1<len(blocks) else len(text)]
        for m in re.finditer(r"(\d{1,2})レース\s*(.*?)(?=\d{1,2}レース|$)", segment):
            num, desc = int(m.group(1)), m.group(2)
            compact = re.sub(r"\s+", "", desc).replace("（", "(").replace("）", ")")
            if norm_name in compact and 1 <= num <= 12:
                matches.append(num)
    if len(matches) != 1:
        raise ValueError(f"Cannot verify unique race number on JRA calendar: {url}")
    return matches[0]


def card_row_count(html):
    soup = BeautifulSoup(html, "html.parser")
    counts = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            heads = [re.sub(r"\s+", "", c.get_text(" ", strip=True)) for c in cells]
            if heads.count("馬番") == 1 and heads.count("馬名") == 1:
                ni, hi = heads.index("馬番"), heads.index("馬名")
                nums = []
                for r in rows[i+1:]:
                    c = r.find_all(["th", "td"], recursive=False)
                    if len(c) > max(ni, hi):
                        raw = re.sub(r"\s+", "", c[ni].get_text(" ", strip=True))
                        if re.fullmatch(r"\d{1,2}", raw):
                            nums.append(int(raw))
                if nums:
                    counts.append(len(nums))
    if len(counts) != 1 or not 2 <= counts[0] <= 18:
        raise ValueError("Cannot identify exactly one complete horse table")
    return counts[0]


def main():
    today = datetime.now(JST).date()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; read-only race data check)"})
    urls = featured_urls(fetch(session, HOME))
    print(f"DISCOVERY: {len(urls)} featured race-card pages", flush=True)
    candidates = []
    for url, labels in urls:
        try:
            page = fetch(session, url)
            day, venue, name = metadata(page, today, labels)
            candidates.append((day, venue, name, url, page))
        except (ValueError, requests.RequestException) as e:
            print(f"SKIP {url}: {e}", flush=True)
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    if not candidates:
        print("WAIT: no future featured races verified from JRA homepage")
        return 0

    for day, venue, name, url, page in candidates:
        key = f"{day.isoformat()}_{venue}_{name}"
        print(f"CHECK {key}", flush=True)
        try:
            if "公開予定" in BeautifulSoup(page, "html.parser").get_text(" ", strip=True):
                print("WAIT: card not yet published", flush=True)
                continue
            number = calendar_race_number(session, day, venue, name)
            # Never manufacture a race-card URL from a race-introduction URL.
            # The official page must link directly to a unique JRADB card.
            card_url = discover(page, url)
            card = fetch(session, card_url)
            count = card_row_count(card)
            entries = parse_entries(card, card_url, day.isoformat(), venue, number, name, count)
            # Save per-race outputs; prevent one race overwriting another.
            folder = Path("keiba_rolling_results") / f"{day.isoformat()}_{venue}_{number:02d}R"
            folder.mkdir(parents=True, exist_ok=True)
            (folder/"entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            history = collect(entries, session=session)
            (folder/"history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            ranking = rank(entries, history)
            ranking["provisional"] = True
            ranking["warning"] = "Automated historical form ranking, NOT a verified win prediction; confirm horse identity and race details."
            (folder/"ranking.json").write_text(json.dumps(ranking, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            print(f"SUCCESS PRELIMINARY {key} {count} horses; NO POSTING", flush=True)
            return 0
        except (ValueError, KeyError, IndexError, requests.RequestException) as e:
            print(f"SAFE SKIP {key}: {type(e).__name__}: {e}", flush=True)
    print("WAIT: no safely processable race card; no prediction fabricated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
