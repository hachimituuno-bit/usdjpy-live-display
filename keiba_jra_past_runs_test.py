
"""Test extraction of past runs from one JRA horse page.

Manual, read-only test.
No predictions, email, or Blogger posting.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from keiba_jra_horse_page_test import (
    RACE_URL,
    TARGET_HORSE,
    check_error_page,
    find_horse_link,
    get_page,
)

DATE_RE = re.compile(
    r"(\d{4})[年./](\d{1,2})[月./](\d{1,2})日?"
)

DISTANCE_RE = re.compile(
    r"(芝|ダート|障害)\s*([\d,，]+)"
)

ERROR_TEXT = (
    "パラメータエラー",
    "ご指定のページが見つかりませんでした",
    "エラー013",
)


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value):
    match = DATE_RE.search(clean(value))

    if not match:
        raise ValueError(
            f"Unrecognized race date: {value}"
        )

    year, month, day = map(
        int, match.groups()
    )

    return date(year, month, day)


def parse_course(value):
    value = clean(value)
    match = DISTANCE_RE.search(value)

    if not match:
        raise ValueError(
            f"Unrecognized course: {value}"
        )

    surface_ja, distance_text = match.groups()

    surface = {
        "芝": "turf",
        "ダート": "dirt",
        "障害": "jump",
    }[surface_ja]

    distance_m = int(
        distance_text.replace(",", "").replace("，", "")
    )

    if not 800 <= distance_m <= 5000:
        raise ValueError(
            f"Invalid distance: {distance_m}"
        )

    return surface, distance_m


def parse_positive_int(value, label):
    value = clean(value)

    if not re.fullmatch(r"\d+", value):
        raise ValueError(
            f"Invalid {label}: {value}"
        )

    result = int(value)

    if result < 1:
        raise ValueError(
            f"Invalid {label}: {value}"
        )

    return result


def find_history_table(soup):
    candidates = []

    for table in soup.find_all("table"):
        headers = [
            clean(th.get_text(" ", strip=True))
            for th in table.find_all("th")
        ]

        header_text = " ".join(headers)

        if all(
            word in header_text
            for word in (
                "年月日",
                "競馬場",
                "距離",
                "着順",
            )
        ):
            candidates.append(table)

    if len(candidates) != 1:
        raise ValueError(
            "Expected exactly one history table; "
            f"found {len(candidates)}"
        )

    return candidates[0]


def extract_runs(soup, target_date, max_runs):
    text = soup.get_text(" ", strip=True)

    if any(word in text for word in ERROR_TEXT):
        raise ValueError(
            "JRA returned an error page"
        )

    if TARGET_HORSE not in text:
        raise ValueError(
            "Target horse name not found"
        )

    table = find_history_table(soup)

    rows = table.find_all("tr")
    extracted = []

    for tr in rows:
        cells = tr.find_all(
            "td",
            recursive=False,
        )

        if not cells:
            continue

        values = [
            clean(cell.get_text(" ", strip=True))
            for cell in cells
        ]

        # Require the expected 14-column format.
        if len(values) != 14:
            raise ValueError(
                "Unexpected history row columns: "
                f"{len(values)} / {values}"
            )

        race_date = parse_date(values[0])

        # Never include the target date or later.
        if race_date >= target_date:
            continue

        venue = values[1]
        race_name = values[2]

        surface, distance_m = parse_course(
            values[3]
        )

        field_size = parse_positive_int(
            values[5],
            "field size",
        )

        finish = parse_positive_int(
            values[7],
            "finish",
        )

        if finish > field_size:
            raise ValueError(
                "Finish exceeds field size"
            )

        if not venue or not race_name:
            raise ValueError(
                "Missing venue or race name"
            )

        extracted.append({
            "date": race_date.isoformat(),
            "venue": venue,
            "race_name": race_name,
            "surface": surface,
            "distance_m": distance_m,
            "field_size": field_size,
            "finish": finish,
            "corner_positions": None,
            "source": "jra_horse_page",
        })

    if not extracted:
        raise ValueError(
            "No valid historical runs found"
        )

    extracted.sort(
        key=lambda item: item["date"],
        reverse=True,
    )

    return extracted[:max_runs], len(extracted)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--target-date",
        default="2026-11-10",
    )

    parser.add_argument(
        "--max-runs",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--output",
        default="keiba_past_runs_test.json",
    )

    args = parser.parse_args()

    if not 1 <= args.max_runs <= 5:
        parser.error(
            "--max-runs must be between 1 and 5"
        )

    target_date = date.fromisoformat(
        args.target_date
    )

    session = requests.Session()

    race_soup = get_page(
        session,
        RACE_URL,
    )

    check_error_page(race_soup)

    horse_url = find_horse_link(
        race_soup
    )

    parsed = urlparse(horse_url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.jra.go.jp"
        or parsed.path != "/JRADB/accessU.html"
    ):
        raise ValueError(
            "Unexpected horse information URL"
        )

    horse_soup = get_page(
        session,
        horse_url,
    )

    check_error_page(horse_soup)

    runs, available = extract_runs(
        horse_soup,
        target_date,
        args.max_runs,
    )

    result = {
        "horse_name": TARGET_HORSE,
        "horse_url": horse_url,
        "horse_identity_verified": False,
        "target_date": target_date.isoformat(),
        "available_runs": available,
        "extracted_runs": len(runs),
        "runs": runs,
        "posting": False,
        "note": (
            "Historical runs extracted for testing. "
            "Horse identity and race row mapping "
            "require further verification. "
            "Corner positions are not yet collected."
        ),
    }

    Path(args.output).write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"OK: {TARGET_HORSE} / "
        f"{len(runs)} past runs / "
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
