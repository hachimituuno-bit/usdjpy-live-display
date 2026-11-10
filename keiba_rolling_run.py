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
    name = names.pop()
    # Navigation/footer lists all ten JRA venues. Never inspect the entire page
    # for a venue: use race-specific metadata or the closest race-title block.
    race_sources = []
    for tag in soup.find_all(["h1", "h2", "h3", "title"]):
        race_sources.append(tag.get_text(" ", strip=True))
    for tag in soup.find_all("meta"):
        if tag.get("property") in ("og:title", "twitter:title") or tag.get("name") == "description":
            race_sources.append(tag.get("content", ""))
    # Explicit venue in the race-specific heading/title is strongest evidence.
    venue_matches = set()
    for source in race_sources:
        if name in re.sub(r"\s+", "", source):
            venue_matches.update(v for v in VENUES if re.search(rf"{v}競馬場", source))
    if len(venue_matches) != 1:
        # Try a small context around the named race within the main article,
        # excluding global navigation and unrelated race listings.
        for region in soup.select("main, article, [role='main']"):
            for node in region.find_all(string=True):
                if name not in str(node):
                    continue
                parent = node.parent
                for _ in range(3):
                    if parent is None or parent == region:
                        break
                    nearby = parent.get_text(" ", strip=True)
                    if len(nearby) <= 450:
                        found = {v for v in VENUES if re.search(rf"{v}競馬場", nearby)}
                        if len(found) == 1:
                            venue_matches.update(found)
                            break
                    parent = parent.parent
    if len(venue_matches) != 1:
        raise ValueError(f"Cannot establish unique venue from race-specific context (found={sorted(venue_matches)})")
    return race_day, venue_matches.pop(), name


def discover_official_card(session, intro_html, intro_url, day, venue, race_name):
    """Use the JRA featured race's own official syutsuba page.

    Never treat an unpublished placeholder as a valid horse card. The source
    URL must be one of the already-verified featured race URLs.
    """
    parsed = urlparse(intro_url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.jra.go.jp", "jra.jp"):
        raise ValueError("Untrusted race introduction URL")
    match = re.fullmatch(r"/keiba/race/(\d+)(?:/syutsuba\.html|\.html)", parsed.path)
    if not match:
        raise ValueError("Unexpected JRA featured race URL")
    card_url = f"https://www.jra.go.jp/keiba/race/{match.group(1)}/syutsuba.html"
    card_html = intro_html if parsed.path.endswith("/syutsuba.html") else fetch(session, card_url)
    soup = BeautifulSoup(card_html, "html.parser")
    # A pre-publication page can omit the venue/date/race heading entirely.
    # Classify that state BEFORE checking identity, but never treat it as a card.
    body = soup.get_text(" ", strip=True)
    compact_body = re.sub(r"\s+", "", body)
    if re.search(r"出馬表.{0,120}(?:公開予定|公開していません|公開前)", compact_body):
        raise ValueError("Official syutsuba page not yet published")

    # Only race-specific title/headings and descriptive metadata are identity
    # evidence; site-wide navigation and unrelated race links are excluded.
    sources = [t.get_text(" ", strip=True) for t in soup.find_all(["h1", "h2", "h3", "title"])]
    for tag in soup.find_all("meta"):
        if tag.get("property") in ("og:title", "twitter:title") or tag.get("name") == "description":
            sources.append(tag.get("content", ""))
    compact = re.sub(r"\s+", "", " ".join(sources))
    if race_name not in compact or venue not in compact:
        raise ValueError("Official syutsuba page race name or venue not verifiable")
    if not any(d in compact for d in (f"{day.year}年{day.month}月{day.day}日", f"{day.month}月{day.day}日")):
        raise ValueError("Official syutsuba page date not verifiable")
    # Downstream validation still requires exactly one complete horse table,
    # a verified race number, and fully parseable entries.
    return card_url, card_html


def card_race_number(card_html, venue, race_name):
    """Read a race number from the official race card, never from a guess."""
    soup = BeautifulSoup(card_html, "html.parser")
    # The card must identify the expected race; do not accept a bare 11R
    # from a navigation menu or another race on the same page.
    norm = lambda s: re.sub(r"\s+", "", s).replace("（", "(").replace("）", ")")
    expected = norm(race_name)
    blocks = []
    for tag in soup.find_all(["h1", "h2", "h3", "title"]):
        blocks.append(tag.get_text(" ", strip=True))
    for tag in soup.find_all("meta"):
        if tag.get("property") in ("og:title", "twitter:title"):
            blocks.append(tag.get("content", ""))
    for tag in soup.select("main, article, [role='main']"):
        blocks.append(tag.get_text(" ", strip=True)[:1200])
    matches = set()
    for block in blocks:
        compact = norm(block)
        if expected not in compact:
            continue
        # Keep the number local to the race name to avoid navigation links.
        at = compact.find(expected)
        nearby = compact[max(0, at-100):at+len(expected)+100]
        for m in re.finditer(r"(?<!\d)(1[0-2]|[1-9])\s*(?:R|レース)(?!\d)", nearby, re.I):
            matches.add(int(m.group(1)))
    if len(matches) != 1:
        raise ValueError(f"Cannot verify unique race number from official card (found={sorted(matches)})")
    return matches.pop()


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
    # Intro and syutsuba links for the same race must be processed only once.
    unique = {}
    for candidate in candidates:
        day, venue, name, url, page = candidate
        key = (day, venue, name)
        if key not in unique or (url.endswith("/syutsuba.html") and not unique[key][3].endswith("/syutsuba.html")):
            unique[key] = candidate
    candidates = sorted(unique.values(), key=lambda c: (c[0], c[1], c[2]))
    if not candidates:
        print("WAIT: no future featured races verified from JRA homepage")
        return 0

    for day, venue, name, url, page in candidates:
        key = f"{day.isoformat()}_{venue}_{name}"
        print(f"CHECK {key}", flush=True)
        try:
            # Never manufacture a race-card URL from a race-introduction URL.
            # The official page must link directly to a unique JRADB card.
            card_url, card = discover_official_card(session, page, url, day, venue, name)
            count = card_row_count(card)
            number = card_race_number(card, venue, name)
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
