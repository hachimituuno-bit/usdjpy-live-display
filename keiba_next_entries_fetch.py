"""Create JRA race-entry JSON from an operator-specified official race-card URL.

Fail closed if the page does not provide a recognizable, complete horse table.
This does not discover the next JRA race automatically.
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS_NUMBER = {"馬番"}
HEADERS_NAME = {"馬名"}
ERROR_TEXT = ("パラメータエラー", "ご指定のページが見つかりません", "エラー013")


def normalize(value):
    return re.sub(r"\s+", "", value or "")


def official_url(url, path=None):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "www.jra.go.jp":
        raise ValueError("Only https://www.jra.go.jp is allowed")
    if not parsed.path.startswith("/JRADB/"):
        raise ValueError("Expected an official JRA race database page")
    if path and parsed.path != path:
        raise ValueError("Unexpected JRA page path")
    return url


def parse_entries(html, source_url, race_date, venue, race_number, race_name,
                  expected_horses=None):
    official_url(source_url)
    date.fromisoformat(race_date)
    if not venue or not race_name or type(race_number) is not int or not 1 <= race_number <= 12:
        raise ValueError("Race metadata is incomplete")
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    if any(word in page_text for word in ERROR_TEXT):
        raise ValueError("JRA returned an error page")

    candidates = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for header_index, row in enumerate(rows):
            header_cells = row.find_all(["th", "td"], recursive=False)
            headers = [normalize(cell.get_text(" ", strip=True)) for cell in header_cells]
            number_indices = [i for i, h in enumerate(headers) if h in HEADERS_NUMBER]
            name_indices = [i for i, h in enumerate(headers) if h in HEADERS_NAME]
            if len(number_indices) != 1 or len(name_indices) != 1:
                continue
            num_idx, name_idx = number_indices[0], name_indices[0]
            horses = []
            for data_row in rows[header_index + 1:]:
                cells = data_row.find_all(["th", "td"], recursive=False)
                if len(cells) <= max(num_idx, name_idx):
                    continue
                raw_num = normalize(cells[num_idx].get_text(" ", strip=True))
                if not re.fullmatch(r"\d{1,2}", raw_num):
                    continue
                number = int(raw_num)
                name_cell = cells[name_idx]
                name = normalize(name_cell.get_text(" ", strip=True))
                links = []
                for anchor in name_cell.find_all("a", href=True):
                    url = urljoin(source_url, anchor["href"])
                    if urlparse(url).path == "/JRADB/accessU.html":
                        links.append(url)
                if len(links) != 1:
                    raise ValueError(f"{name or number}: exactly one JRA horse-profile link required")
                horse_url = official_url(links[0], "/JRADB/accessU.html")
                if not re.search(r"(?:^|&)CNAME=[^&]+", urlparse(horse_url).query):
                    raise ValueError(f"{name}: JRA horse ID missing")
                if not name or number < 1 or number > 30:
                    raise ValueError("Invalid horse name or number")
                horses.append({"number": number, "name": name, "horse_url": horse_url})
            if horses:
                candidates.append(horses)

    if len(candidates) != 1:
        raise ValueError(f"Expected one recognizable race-card table; found {len(candidates)}")
    horses = candidates[0]
    if not 2 <= len(horses) <= 30:
        raise ValueError("Unexpected number of horses")
    if expected_horses is None:
        raise ValueError("Expected horse count is required to prevent incomplete entries")
    if len(horses) != expected_horses:
        raise ValueError(f"Incomplete card: {len(horses)} horses, expected {expected_horses}")
    if len({h["number"] for h in horses}) != len(horses):
        raise ValueError("Duplicate horse numbers")
    if len({h["name"] for h in horses}) != len(horses):
        raise ValueError("Duplicate horse names")
    if len({h["horse_url"] for h in horses}) != len(horses):
        raise ValueError("Duplicate horse profile URLs")

    # Date and race metadata are provided by the operator, not extracted/verified
    # from the source page. This limitation is explicit in the output.
    return {"race_date": race_date, "venue": venue, "race_number": race_number,
            "race_name": race_name, "posting": False, "source_url": source_url,
            "race_metadata_verified": False,
            "identity_verified": False,
            "warning": "Verify race date, venue, race number, horse count, and names against the official race card before use.",
            "horses": horses}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Official JRA race-card URL")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--venue", required=True)
    parser.add_argument("--race-number", type=int, required=True)
    parser.add_argument("--race-name", required=True)
    parser.add_argument("--expected-horses", type=int, required=True)
    parser.add_argument("--output", default="keiba_next_entries.json")
    args = parser.parse_args()
    official_url(args.url)
    response = requests.get(args.url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    result = parse_entries(response.text, args.url, args.date, args.venue,
                           args.race_number, args.race_name, args.expected_horses)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"ENTRY EXTRACTION OK: {len(result['horses'])} horses; metadata and identities require independent verification; no posting")


if __name__ == "__main__":
    main()
