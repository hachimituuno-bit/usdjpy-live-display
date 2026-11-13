"""Discover upcoming official JRA cards through the official card navigation.

Read-only, provisional rankings only. Never guess JRA CNAMEs or horse identity.
"""
import json
import re
import sys
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from keiba_next_entries_fetch import parse_entries
from keiba_next_collect import collect
from keiba_next_race import rank

JST = timezone(timedelta(hours=9))
HOME = "https://www.jra.go.jp/keiba/thisweek/"
INDEX = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dli00/F3"
VENUES = ("東京", "中山", "京都", "阪神", "中京", "新潟", "福島", "小倉", "札幌", "函館")
DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
ACTION_RE = re.compile(r"doAction\(\s*['\"](/JRADB/access[DI]\.html)['\"]\s*,\s*['\"]([^'\"]+)['\"]")
RACE_LINK = re.compile(r"^/keiba/race/\d+(?:/syutsuba\.html|\.html)$")
NAME_RE = re.compile(r"(?:第\s*\d+\s*回\s*)?([一-龥々ァ-ヶー・A-Za-z0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+?(?:ステークス|カップ|記念|大賞典|ダービー|優駿牝馬|天皇賞|菊花賞|皐月賞|有馬記念|ジャパンカップ))")
MAX_PAGES = 85
MAX_DEPTH = 4


def norm(value):
    return re.sub(r"\s+", "", value or "")


def fetch(session, url):
    """Follow observed JRA links. doAction uses POST cname; ordinary href uses GET."""
    parsed = urlparse(url)
    if parsed.fragment == "jra-post":
        values = parse_qs(parsed.query).get("CNAME", [])
        if len(values) != 1:
            raise ValueError("JRA POST requires exactly one observed CNAME")
        endpoint = parsed._replace(query="", fragment="").geturl()
        response = session.post(endpoint, data={"cname": values[0]}, timeout=20)
    else:
        response = session.get(url, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def official_card_url(url):
    p = urlparse(url)
    return (p.scheme == "https" and p.hostname == "www.jra.go.jp"
            and p.port is None and p.path in ("/JRADB/accessD.html", "/JRADB/accessI.html")
            and len(parse_qs(p.query).get("CNAME", [])) == 1)


def card_links(html, source_url):
    """Read observed JRA accessD/accessI links, including JavaScript doAction targets.

    An href ending in # on JRA pages often points BACK to the current page;
    onclick supplies the actual destination. Do not follow the misleading href.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for a in soup.find_all("a"):
        onclick = a.get("onclick", "")
        actions = ACTION_RE.findall(onclick)
        if actions:
            for path, cname in actions:
                target = "https://www.jra.go.jp" + path + "?CNAME=" + quote(cname, safe="/")
                if official_card_url(target):
                    found.add(target + "#jra-post")
            continue
        href = a.get("href")
        if href:
            target = urljoin(source_url, href).split("#", 1)[0]
            if official_card_url(target):
                found.add(target)
    return sorted(found, key=lambda u: (urlparse(u).path != "/JRADB/accessD.html", u))


def navigation_diagnostics(html, source_url, depth):
    """Report public JRA navigation markup without guessing a target URL."""
    soup = BeautifulSoup(html, "html.parser")
    links = card_links(html, source_url)
    anchors = soup.find_all("a")
    href_cnames = []
    action_samples = []
    for a in anchors:
        href = a.get("href", "")
        onclick = a.get("onclick", "")
        if "CNAME" in href or "accessD.html" in href or "accessI.html" in href:
            href_cnames.append(href[:180])
        if onclick and ("action" in onclick.lower() or "CNAME" in onclick):
            action_samples.append(onclick[:180])
    forms = soup.find_all("form")
    scripts = soup.find_all("script")
    script_hits = sum(bool(re.search(r"doAction|CNAME|access[DI]\.html", x.get_text(" ", strip=True), re.I)) for x in scripts)
    print(f"NAV PAGE depth={depth} url={source_url}", flush=True)
    print(f"NAV MARKUP anchors={len(anchors)} forms={len(forms)} scripts={len(scripts)} "
          f"navigation_scripts={script_hits} extracted_links={len(links)}", flush=True)
    for form in forms[:3]:
        print(f"NAV FORM method={form.get('method', 'GET')} action={form.get('action', '')[:180]}", flush=True)
    for link in links[:12]:
        print(f"NAV LINK {link}", flush=True)
    for href in href_cnames[:5]:
        print(f"NAV HREF SAMPLE {href}", flush=True)
    for action in action_samples[:5]:
        print(f"NAV ONCLICK SAMPLE {action}", flush=True)
    if not links:
        print("NAV BLOCKED: no matching links extracted from this page; URL discovery needs inspection", flush=True)
    return links


def card_details(html):
    """Verify one card's race identity and count from its own horse table caption."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for table in soup.find_all("table"):
        head, body, caption = table.find("thead"), table.find("tbody"), table.find("caption")
        if head is None or body is None or caption is None:
            continue
        headings = [norm(t.get_text(" ", strip=True)) for t in head.find_all("th")]
        if headings.count("馬番") != 1 or sum(h.startswith("馬名") for h in headings) != 1:
            continue
        date_line = caption.select_one(".date_line")
        race_name = caption.select_one(".race_name")
        race_number = caption.select_one(".race_number img[alt]")
        if not (date_line and race_name and race_number):
            continue
        date_text = date_line.get_text(" ", strip=True)
        dm = DATE_RE.search(date_text)
        nm = re.fullmatch(r"(1[0-2]|[1-9])レース", norm(race_number.get("alt", "")))
        if dm is None or nm is None:
            continue
        venues = [v for v in VENUES if re.search(r"\d+回" + v + r"\d+日", norm(date_text))]
        if len(venues) != 1:
            continue
        rows = body.find_all("tr", recursive=False)
        if not 2 <= len(rows) <= 18:
            continue
        num_index = headings.index("馬番")
        horse_numbers = []
        for row in rows:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) <= num_index:
                break
            raw = norm(cells[num_index].get_text(" ", strip=True))
            if not re.fullmatch(r"\d{1,2}", raw):
                break
            horse_numbers.append(int(raw))
        if len(horse_numbers) != len(rows) or len(set(horse_numbers)) != len(rows):
            continue
        name = norm(race_name.get_text(" ", strip=True))
        if not name:
            continue
        found.append((date(*map(int, dm.groups())), venues[0], int(nm.group(1)), name, len(rows)))
    if len(found) != 1:
        raise ValueError(f"Expected one complete JRA race card, found {len(found)}")
    return found[0]


def featured_names(html):
    """Use race-specific link labels; never parse site-wide page headings as names."""
    soup = BeautifulSoup(html, "html.parser")
    names = set()
    for a in soup.find_all("a", href=True):
        p = urlparse(urljoin(HOME, a["href"]))
        if p.hostname not in ("www.jra.go.jp", "jra.jp") or not RACE_LINK.fullmatch(p.path):
            continue
        label = norm(a.get_text(" ", strip=True))
        for m in NAME_RE.finditer(label):
            names.add(m.group(1))
    return names


def discover(session, today):
    home_html = fetch(session, HOME)
    names = featured_names(home_html)
    print(f"FEATURED LABELS: {len(names)} race names", flush=True)
    if not names:
        print("WAIT: featured race names not verifiable from official links", flush=True)
        return []
    # The index is an official entry point, not itself a horse card.
    # accessI is a navigation page, not evidence of a verified race card.
    queue = deque([(INDEX + "#jra-post", 0)])
    seen = set()
    cards = []
    while queue and len(seen) < MAX_PAGES:
        url, depth = queue.popleft()
        if url in seen or not official_card_url(url):
            continue
        seen.add(url)
        try:
            html = fetch(session, url)
        except requests.RequestException as exc:
            print(f"SKIP FETCH {url}: {exc}", flush=True)
            continue
        try:
            details = card_details(html) if urlparse(url).path == "/JRADB/accessD.html" else None
        except ValueError:
            details = None
        if details:
            day, venue, number, name, count = details
            if today < day <= today + timedelta(days=9):
                if names and not any(n == name or n in name or name in n for n in names):
                    print(f"SKIP NON-FEATURED {day} {venue}{number}R {name}", flush=True)
                else:
                    cards.append((*details, url, html))
                    print(f"VERIFIED CARD {day} {venue}{number}R {name} {count} horses", flush=True)
            continue
        if depth < MAX_DEPTH:
            for target in navigation_diagnostics(html, url, depth):
                if target not in seen:
                    queue.append((target, depth + 1))
    print(f"DISCOVERY: {len(seen)} official pages inspected; {len(cards)} matching cards", flush=True)
    # Do not guess between multiple URLs for the same race.
    grouped = {}
    for item in cards:
        grouped.setdefault(item[:4], []).append(item)
    safe = []
    for key, group in grouped.items():
        if len(group) == 1:
            safe.append(group[0])
        else:
            print(f"SAFE SKIP ambiguous race {key}: {len(group)} cards", flush=True)
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
        print("WAIT: no verified future JRA race card; no prediction fabricated", flush=True)
        return 0
    successes = 0
    for day, venue, number, name, count, url, html in candidates:
        key = f"{day.isoformat()}_{venue}_{number:02d}R"
        print(f"CHECK {key} {name}", flush=True)
        try:
            entries = parse_entries(html, url, day.isoformat(), venue, number, name, count)
            history = collect(entries, session=session)
            ranking = rank(entries, history)
            ranking["provisional"] = True
            ranking["warning"] = "Historical form only; horse identities not independently verified."
            folder = Path("keiba_rolling_results") / key
            folder.mkdir(parents=True, exist_ok=True)
            for filename, payload in (("entries.json", entries), ("history.json", history), ("ranking.json", ranking)):
                (folder / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"SUCCESS PRELIMINARY {key}: {count} horses; NO POSTING", flush=True)
            successes += 1
        except (ValueError, KeyError, IndexError, TypeError, requests.RequestException) as exc:
            print(f"SAFE SKIP {key}: {type(exc).__name__}: {exc}", flush=True)
    if successes:
        print(f"LIVE_PRELIMINARY_SUCCESS: {successes}/{len(candidates)} future race rankings generated; NO POSTING", flush=True)
    else:
        print("WAIT: no safely processable race card; no prediction fabricated", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
