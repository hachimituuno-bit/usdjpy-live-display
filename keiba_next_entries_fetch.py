"""Extract a JRA race entry list from an operator-confirmed official race-card URL.

No automatic race discovery, no publishing. Fail closed on incomplete or
ambiguous race cards. Race metadata is supplied by the operator and must be
checked independently before publication.
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

ERROR_TEXT = ("パラメータエラー", "ご指定のページが見つかりません", "エラー013")


def normalize(value):
    return re.sub(r"\s+", "", value or "")


def official_url(url, path=None):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "www.jra.go.jp" or parsed.port is not None:
        raise ValueError("Only https://www.jra.go.jp is allowed")
    if not parsed.path.startswith("/JRADB/"):
        raise ValueError("Expected an official JRA race database page")
    if path and parsed.path != path:
        raise ValueError("Unexpected JRA page path")
    return url


def parse_entries(html, source_url, race_date, venue, race_number, race_name,
                  expected_horses=None):
    official_url(source_url, "/JRADB/accessD.html")
    if len(parse_qs(urlparse(source_url).query).get("CNAME", [])) != 1:
        raise ValueError("Official race-card CNAME is required")
    date.fromisoformat(race_date)
    if not venue or not race_name or type(race_number) is not int or not 1 <= race_number <= 12:
        raise ValueError("Race metadata is incomplete")
    if type(expected_horses) is not int or not 2 <= expected_horses <= 30:
        raise ValueError("Expected horse count (2..30) is required")

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    if any(word in page_text for word in ERROR_TEXT):
        raise ValueError("JRA returned an error page")

    candidates = []
    for table in soup.find_all("table"):
        thead = table.find("thead")
        tbody = table.find("tbody")
        if thead is None or tbody is None:
            continue
        headings = thead.find_all("th")
        num_cols = [i for i, cell in enumerate(headings) if normalize(cell.get_text(" ", strip=True)) == "馬番"]
        horse_cols = [i for i, cell in enumerate(headings) if normalize(cell.get_text(" ", strip=True)).startswith("馬名")]
        if len(num_cols) != 1 or len(horse_cols) != 1:
            continue
        num_idx, horse_idx = num_cols[0], horse_cols[0]
        horses = []
        for row in tbody.find_all("tr", recursive=False):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) <= max(num_idx, horse_idx):
                raise ValueError("Incomplete race-card row")
            raw_num = normalize(cells[num_idx].get_text(" ", strip=True))
            if not re.fullmatch(r"\d{1,2}", raw_num):
                raise ValueError("Invalid horse number in race-card row")
            number = int(raw_num)
            name_cell = cells[horse_idx]
            name_container = name_cell.select_one(".name")
            if name_container is None:
                raise ValueError("Horse-name element missing")
            profile_links = []
            for anchor in name_container.find_all("a", href=True):
                target = urljoin(source_url, anchor["href"])
                if urlparse(target).path == "/JRADB/accessU.html":
                    profile_links.append((normalize(anchor.get_text(" ", strip=True)), target))
            if len(profile_links) != 1:
                raise ValueError(f"Horse {number}: exactly one JRA profile link required")
            name, horse_url = profile_links[0]
            official_url(horse_url, "/JRADB/accessU.html")
            if len(parse_qs(urlparse(horse_url).query).get("CNAME", [])) != 1:
                raise ValueError(f"{name}: JRA horse ID missing")
            if not name or not 1 <= number <= 30:
                raise ValueError("Invalid horse name or number")
            horses.append({"number": number, "name": name, "horse_url": horse_url})
        if horses:
            candidates.append(horses)

    if len(candidates) != 1:
        raise ValueError(f"Expected one recognizable race-card table; found {len(candidates)}")
    horses = candidates[0]
    if len(horses) != expected_horses:
        raise ValueError(f"Incomplete card: {len(horses)} horses, expected {expected_horses}")
    for field in ("number", "name", "horse_url"):
        if len({h[field] for h in horses}) != len(horses):
            raise ValueError(f"Duplicate {field}")

    # Metadata and identity must be independently verified before publishing.
    return {"race_date": race_date, "venue": venue, "race_number": race_number,
            "race_name": race_name, "posting": False, "source_url": source_url,
            "race_metadata_verified": False, "identity_verified": False,
            "warning": "Verify race date, venue, race number, horse count, and names against the official race card before use.",
            "horses": horses}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--race-number", type=int, required=True)
    parser.add_argument("--race-name", required=True)
    parser.add_argument("--expected-horses", type=int, required=True)
    parser.add_argument("--output", default="keiba_next_entries.json")
    args = parser.parse_args()
    official_url(args.url, "/JRADB/accessD.html")
    response = requests.get(args.url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    result = parse_entries(response.text, args.url, args.date, args.venue,
                           args.race_number, args.race_name, args.expected_horses)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ENTRY EXTRACTION OK: {len(result['horses'])} horses; metadata and identities require independent verification; no posting")


if __name__ == "__main__":
    main()
