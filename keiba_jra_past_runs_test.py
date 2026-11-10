
"""Test extraction of past runs from JRA horse pages.

Manual read-only test.
No predictions, email, or Blogger posting.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from keiba_jra_horse_page_test import (
    RACE_URL,
    TARGET_HORSE,
    check_error_page,
    find_horse_link,
    get_page,
)


EXPECTED_HEADERS = [
    "年月日",
    "場",
    "レース名",
    "距離",
    "馬場",
    "頭数",
    "人気",
    "着順",
    "騎手名",
    "負担重量",
    "馬体重",
    "タイム",
    "Rt",
    "1着馬（2着馬）",
]

DATE_PATTERN = re.compile(
    r"^(\d{4})[年./](\d{1,2})[月./]"
    r"(\d{1,2})日?$"
)

DISTANCE_PATTERN = re.compile(
    r"^(芝|ダート|ダ|障害)\s*"
    r"([\d,，]+)(?:m|メートル)?$"
)

SURFACE_MAP = {
    "芝": "turf",
    "ダート": "dirt",
    "ダ": "dirt",
    "障害": "jump",
}

FINISH_STATUS_MAP = {
    "除外": "excluded",
    "取消": "scratched",
    "中止": "did_not_finish",
    "失格": "disqualified",
}


def clean(value):
    return re.sub(
        r"\s+", " ", value
    ).strip()


def normalize_header(value):
    return re.sub(
        r"\s+", "", value
    ).strip()


def parse_race_date(value):
    value = clean(value)
    match = DATE_PATTERN.fullmatch(value)

    if match is None:
        raise ValueError(
            f"Unexpected date format: {value}"
        )

    year, month, day = map(
        int, match.groups()
    )

    return date(year, month, day)


def parse_positive_int(value, label):
    value = clean(value)

    if not re.fullmatch(r"\d+", value):
        raise ValueError(
            f"Invalid {label}: {value}"
        )

    number = int(value)

    if number < 1:
        raise ValueError(
            f"Invalid {label}: {value}"
        )

    return number


def parse_distance(value):
    value = clean(value)

    match = DISTANCE_PATTERN.fullmatch(value)

    if match is not None:
        surface_text, distance_text = (
            match.groups()
        )

        distance = int(
            distance_text.replace(
                ",", ""
            ).replace("，", "")
        )

        surface_hint = SURFACE_MAP[
            surface_text
        ]

    elif re.fullmatch(
        r"[\d,，]+(?:m|メートル)?",
        value,
    ):
        distance_text = re.sub(
            r"(?:m|メートル)$", "", value
        )

        distance = int(
            distance_text.replace(
                ",", ""
            ).replace("，", "")
        )

        surface_hint = None

    else:
        raise ValueError(
            f"Unexpected distance: {value}"
        )

    if not 800 <= distance <= 5000:
        raise ValueError(
            f"Distance out of range: {distance}"
        )

    return distance, surface_hint


def parse_surface(value):
    value = clean(value)

    if value in ("良", "稍重", "重", "不良"):
        return None, value

    match = re.fullmatch(
        r"(芝|ダート|ダ|障害)"
        r"(?:[・\s]*(良|稍重|重|不良))?",
        value,
    )

    if match is None:
        raise ValueError(
            f"Unexpected surface: {value}"
        )

    surface_text, condition = match.groups()

    return (
        SURFACE_MAP[surface_text],
        condition,
    )


def parse_finish(value, field_size):
    """Return numeric finish and its status."""

    value = clean(value)

    if re.fullmatch(r"\d+", value):
        finish = int(value)

        if not 1 <= finish <= field_size:
            raise ValueError(
                "Finish outside field size: "
                f"{finish} / {field_size}"
            )

        return finish, "finished"

    if value in FINISH_STATUS_MAP:
        return None, FINISH_STATUS_MAP[value]

    raise ValueError(
        f"Unexpected finish status: {value}"
    )


def find_history_table(soup):
    candidates = []

    expected = [
        normalize_header(value)
        for value in EXPECTED_HEADERS
    ]

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            headers = [
                normalize_header(
                    th.get_text(" ", strip=True)
                )
                for th in tr.find_all(
                    "th", recursive=False
                )
            ]

            if headers == expected:
                candidates.append(table)
                break

    if len(candidates) != 1:
        raise ValueError(
            "Expected one JRA history table; "
            f"found {len(candidates)}"
        )

    return candidates[0]


def extract_runs(
    soup,
    target_date,
    max_runs,
    horse_name=TARGET_HORSE,
):
    """Extract past runs for the specified horse."""

    text = check_error_page(soup)

    if not isinstance(horse_name, str):
        raise ValueError(
            "Invalid horse name"
        )

    horse_name = horse_name.strip()

    if not horse_name:
        raise ValueError(
            "Horse name is empty"
        )

    if horse_name not in text:
        raise ValueError(
            f"Horse name not found: {horse_name}"
        )

    table = find_history_table(soup)

    extracted = []
    data_rows = 0

    for tr in table.find_all("tr"):
        cells = tr.find_all(
            "td", recursive=False
        )

        if not cells:
            continue

        values = [
            clean(
                cell.get_text(" ", strip=True)
            )
            for cell in cells
        ]

        if len(values) != 14:
            raise ValueError(
                "Unexpected history row columns: "
                f"{len(values)} / {values}"
            )

        data_rows += 1

        race_date = parse_race_date(
            values[0]
        )

        if race_date >= target_date:
            continue

        venue = values[1]
        race_name = values[2]

        distance_m, distance_surface = (
            parse_distance(values[3])
        )

        surface, track_condition = (
            parse_surface(values[4])
        )

        if surface is None:
            if distance_surface is None:
                raise ValueError(
                    "Race surface cannot be determined"
                )

            surface = distance_surface

        elif (
            distance_surface is not None
            and surface != distance_surface
        ):
            raise ValueError(
                "Distance and surface disagree"
            )

        field_size = parse_positive_int(
            values[5],
            "field size",
        )

        finish, finish_status = parse_finish(
            values[7],
            field_size,
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
            "track_condition": track_condition,
            "field_size": field_size,
            "finish": finish,
            "finish_status": finish_status,
            "finish_raw": values[7],
            "corner_positions": None,
            "source": "jra_horse_page",
        })

    if data_rows == 0:
        raise ValueError(
            "History table contains no data rows"
        )

    if not extracted:
        raise ValueError(
            "No historical runs before target date"
        )

    extracted.sort(
        key=lambda item: item["date"],
        reverse=True,
    )

    dates = [
        run["date"] for run in extracted
    ]

    if len(dates) != len(set(dates)):
        raise ValueError(
            "Duplicate race dates found; "
            "manual verification required"
        )

    return (
        extracted[:max_runs],
        len(extracted),
    )


def validate_horse_url(url):
    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.jra.go.jp"
        or parsed.path != "/JRADB/accessU.html"
        or not parse_qs(
            parsed.query
        ).get("CNAME")
    ):
        raise ValueError(
            "Unexpected JRA horse information URL"
        )


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
            "--max-runs must be 1 to 5"
        )

    try:
        target_date = date.fromisoformat(
            args.target_date
        )
    except ValueError:
        parser.error(
            "Invalid target date; use YYYY-MM-DD"
        )

    session = requests.Session()

    print(
        "Fetching JRA race result",
        flush=True,
    )

    race_soup = get_page(
        session,
        RACE_URL,
    )

    check_error_page(race_soup)

    horse_url = find_horse_link(
        race_soup
    )

    validate_horse_url(horse_url)

    print(
        "Fetching JRA horse information",
        flush=True,
    )

    horse_soup = get_page(
        session,
        horse_url,
    )

    runs, available = extract_runs(
        horse_soup,
        target_date,
        args.max_runs,
        horse_name=TARGET_HORSE,
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
            "Manual test only. "
            "Horse identity and race data "
            "require source verification. "
            "Corner positions are not collected."
        ),
    }

    output_path = Path(args.output)

    output_path.write_text(
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
        f"{available} available / "
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
