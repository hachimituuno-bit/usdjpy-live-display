"""Discover official JRA race cards and build provisional historical rankings.

No guessed CNAME, no fabricated entries, no publishing. A missing or ambiguous
card fails closed. Publication remains blocked until independent identity checks
are implemented in the history collector.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from keiba_next_entries_fetch import parse_entries
from keiba_next_collect import collect
from keiba_next_race import rank

JST = timezone(timedelta(hours=9))
HOME = "https://www.jra.go.jp/keiba/thisweek/"
INDEX = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dli00/F3"
VENUES = ("東京", "中山", "京都", "阪神", "中京", "新潟", "福島", "小倉", "札幌", "函館")
RACE_LINK = re.compile(r"^/keiba/race/\d+(?:/syutsuba\.html|\.html)$")
DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
ACTION_RE = re.compile(r"doAction\(\s*['\"](/JRADB/accessD\.html)['\"]\s*,\s*['\"]([^'\"]+)['\"]")


def fetch(session, url):
    response = session.get(url, timeout=25)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def normalized(text):
    return re.sub(r"\s+", "", text or "")


def featured_urls(html):
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for a in soup.find_all("a", href=True):
        url = urljoin(HOME, a["href"])
        p = urlparse(url)
        if p.scheme == "https" and p.hostname in ("www.jra.go.jp", "jra.jp") and RACE_LINK.fullmatch(p.path):
            urls.add("https://www.jra.go.jp" + p.path)
    return sorted(urls)


def featured_metadata(html, today):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main, article, [role='main']") or soup
    text = main.get_text(" ", strip=True)
    dates = {date(*map(int, m)) for m in DATE_RE.findall(text)}
    dates = {d for d in dates if today <= d <= today + timedelta(days=9)}
    if len(dates) != 1:
        raise ValueError("Featured race date is not unique")
    titles = [normalized(x.get_text(" ", strip=True)) for x in soup.find_all(["h1", "h2", "h3", "title"])]
    pattern = re.compile(r"(?:第\d+回)?([一-龥々ァ-ヶー・A-Za-z0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+?(?:ステークス|カップ|記念|大賞典|ダービー|優駿牝馬|天皇賞|菊花賞|皐月賞|有馬記念|ジャパンカップ))")
    names = {m.group(1) for title in titles for m in pattern.finditer(title)}
    if len(names) != 1:
        raise ValueError("Featured race name is not unique")
    name = names.pop()
    venue_sources = [t for t in titles if name in t]
    venues = {v for t in venue_sources for v in VENUES if v + "競馬場" in t}
    if len(venues) != 1:
        # The race-specific main text may include the venue even when headings do not.
        near = normalized(text)
        at = near.find(name)
        if at >= 0:
            venues = {v for v in VENUES if v + "競馬場" in near[max(0, at-150):at+len(name)+150]}
    if len(venues) != 1:
        raise ValueError("Featured race venue is not unique")
    return dates.pop(), venues.pop(), name


def card_links(html, source_url):
    """Extract *actual* JRADB links and JRA doAction targets, never guess IDs."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for a in soup.find_all("a"):
        if a.get("href"):
            url = urljoin(source_url, a["href"])
            p = urlparse(url)
            if p.scheme == "https" and p.hostname == "www.jra.go.jp" and p.path == "/JRADB/accessD.html":
                if len(parse_qs(p.query).get("CNAME", [])) == 1:
                    found.add(url.split("#", 1)[0])
        for attr in ("onclick",):
            action = a.get(attr, "")
            for m in ACTION_RE.finditer(action):
                # Index links are not cards; verification later rejects them.
                found.add("https://www.jra.go.jp" + m.group(1) + "?CNAME=" + m.group(2))
    return sorted(found)


def card_details(html):
    """Read identity from a single JRA race-card table, not site navigation."""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for table in soup.find_all("table"):
        if not table.select_one("thead th") or not table.select_one("tbody tr"):
            continue
        header = normalized(table.select_one("thead").get_text(" ", strip=True))
        if "馬番" not in header or "馬名" not in header:
            continue
        caption = table.find("caption")
        if caption is None:
            continue
        d = DATE_RE.search(caption.get_text(" ", strip=True))
        race_name = caption.select_one(".race_name")
        race_number = caption.select_one(".race_number img[alt]")
        if not d or not race_name or not race_number:
            continue
        n = re.fullmatch(r"(1[0-2]|[1-9])レース", race_number.get("alt", ""))
        if not n:
            continue
        day = date(*map(int, d.groups()))
        race = normalized(race_name.get_text(" ", strip=True))
        # "5回東京3日" in the card's date line uniquely establishes venue.
        date_line = caption.select_one(".date_line")
        venue_text = normalized(date_line.get_text(" ", strip=True)) if date_line else ""
        venues = [v for v in VENUES if re.search(r"\d+回" + v + r"\d+日", venue_text)]
        if len(venues) != 1:
            continue
        rows = table.select("tbody tr")
        if not 2 <= len(rows) <= 18:
            continue
        cards.append((day, venues[0], int(n.group(1)), race, len(rows)))
    if len(cards) != 1:
        raise ValueError(f"Expected one complete JRA race card, found {len(cards)}")
    return cards[0]


def discover(session, today):
    featured = []
    home = fetch(session, HOME)
    urls = featured_urls(home)
    print(f"DISCOVERY: {len(urls)} featured race pages", flush=True)
    source_pages = [(HOME, home)]
    for url in urls:
        try:
            html = fetch(session, url)
            source_pages.append((url, html))
            try:
                featured.append(featured_metadata(html, today))
            except ValueError as exc:
                print(f"SKIP FEATURED METADATA {url}: {exc}", flush=True)
        except requests.RequestException as exc:
            print(f"SKIP FEATURED FETCH {url}: {exc}", flush=True)
    try:
        source_pages.append((INDEX, fetch(session, INDEX)))
    except requests.RequestException as exc:
        print(f"SKIP OFFICIAL INDEX: {exc}", flush=True)
    candidate_urls = set()
    for source_url, html in source_pages:
        candidate_urls.update(card_links(html, source_url))
    print(f"CARD LINKS: {len(candidate_urls)} official JRA links found", flush=True)
    matches = []
    for url in sorted(candidate_urls):
        try:
            card = fetch(session, url)
            day, venue, number, name, count = card_details(card)
            if not today < day <= today + timedelta(days=9):
                continue
            if (day, venue, name) not in featured:
                # Never publish a race discovered only from an unrelated link.
                continue
            matches.append((day, venue, number, name, count, url, card))
        except (ValueError, requests.RequestException) as exc:
            print(f"SKIP CARD {url}: {exc}", flush=True)
    # One unique official card per race. Ambiguous URLs must not be guessed.
    by_race = {}
    for candidate in matches:
        key = candidate[:4]
        by_race.setdefault(key, []).append(candidate)
    safe = []
    for key, group in by_race.items():
        if len(group) != 1:
            print(f"SAFE SKIP ambiguous official cards {key}: {len(group)}", flush=True)
            continue
        safe.append(group[0])
    return sorted(safe, key=lambda c: c[:4])


def main():
    today = datetime.now(JST).date()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    try:
        candidates = discover(session, today)
    except requests.RequestException as exc:
        print(f"WAIT: JRA discovery unavailable: {exc}", flush=True)
        return 0
    if not candidates:
        print("WAIT: no verified future featured race card; no prediction fabricated", flush=True)
        return 0
    for day, venue, number, name, count, card_url, card in candidates:
        key = f"{day.isoformat()}_{venue}_{number:02d}R"
        print(f"CHECK {key} {name}", flush=True)
        try:
            entries = parse_entries(card, card_url, day.isoformat(), venue, number, name, count)
            # Generate outputs only after the entire pipeline succeeds.
            history = collect(entries, session=session)
            ranking = rank(entries, history)
            ranking["provisional"] = True
            ranking["warning"] = "Historical form only; horse identities not independently verified."
            folder = Path("keiba_rolling_results") / key
            folder.mkdir(parents=True, exist_ok=True)
            for filename, payload in (("entries.json", entries), ("history.json", history), ("ranking.json", ranking)):
                (folder / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"SUCCESS PRELIMINARY {key}: {count} horses; NO POSTING", flush=True)
            return 0
        except (ValueError, KeyError, IndexError, TypeError, requests.RequestException) as exc:
            print(f"SAFE SKIP {key}: {type(exc).__name__}: {exc}", flush=True)
    print("WAIT: no safely processable race card; no prediction fabricated", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
