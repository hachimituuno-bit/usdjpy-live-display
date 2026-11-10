
"""Test JRA past-run extraction for multiple horses.

Manual read-only test.
No predictions, email, or Blogger posting.
"""

import argparse
import json
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from keiba_jra_horse_page_test import (
    RACE_URL,
    check_error_page,
    get_page,
)

from keiba_jra_past_runs_test import (
    extract_runs,
    validate_horse_url,
)


def find_horses(soup):
    result_tables = []

    for table in soup.find_all("table"):
        headers = " ".join(
            th.get_text(" ", strip=True)
            for th in table.find_all("th")
        )

        if all(
            word in headers
            for word in ("着順", "馬名", "コーナー")
        ):
            result_tables.append(table)

    if len(result_tables) != 1:
        raise ValueError(
            "Expected one race result table; "
            f"found {len(result_tables)}"
        )

    horses = []

    for tr in result_tables[0].find_all("tr"):
        cells = tr.find_all(
            "td",
            recursive=False,
        )

        if len(cells) < 4:
            continue

        finish_text = cells[0].get_text(
            " ", strip=True
        )

        if not finish_text.isdecimal():
            continue

        number_text = cells[2].get_text(
            " ", strip=True
        )

        name = cells[3].get_text(
            " ", strip=True
        )

        if not number_text.isdecimal() or not name:
            raise ValueError(
                "Invalid horse number or name"
            )

        links = cells[3].find_all("a")

        if len(links) != 1:
            raise ValueError(
                f"{name}: expected one horse link"
            )

        href = links[0].get(
            "href", ""
        ).strip()

        if not href:
            raise ValueError(
                f"{name}: empty horse link"
            )

        horse_url = urljoin(
            RACE_URL,
            href,
        )

        validate_horse_url(horse_url)

        parsed = urlparse(horse_url)

        cname_values = parse_qs(
            parsed.query
        ).get("CNAME", [])

        if len(cname_values) != 1:
            raise ValueError(
                f"{name}: invalid CNAME"
            )

        horses.append({
            "number": int(number_text),
            "name": name,
            "horse_url": horse_url,
            "identity_candidate": cname_values[0],
        })

    if len(horses) != 11:
        raise ValueError(
            "Expected 11 horses; "
            f"found {len(horses)}"
        )

    for key in (
        "number",
        "name",
        "horse_url",
        "identity_candidate",
    ):
        values = [
            horse[key] for horse in horses
        ]

        if len(values) != len(set(values)):
            raise ValueError(
                f"Duplicate horse {key}"
            )

    return horses


def fetch_horse_history(
    session,
    horse,
    target_date,
    max_runs,
):
    soup = get_page(
        session,
        horse["horse_url"],
    )

    text = check_error_page(soup)

    if horse["name"] not in text:
        raise ValueError(
            f'{horse["name"]}: '
            "horse name not found on page"
        )

    runs, available = extract_runs(
        soup,
        target_date,
        max_runs,
        horse_name=horse["name"],
    )

    if not 1 <= len(runs) <= max_runs:
        raise ValueError(
            f'{horse["name"]}: invalid run count'
        )

    if any(
        date.fromisoformat(run["date"]) >= target_date
        for run in runs
    ):
        raise ValueError(
            f'{horse["name"]}: future race detected'
        )

    return {
        "number": horse["number"],
        "name": horse["name"],
        "horse_url": horse["horse_url"],
        "identity_candidate": (
            horse["identity_candidate"]
        ),
        "identity_verified": False,
        "available_runs": available,
        "extracted_runs": len(runs),
        "runs": runs,
    }


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
        default="keiba_multi_horse_test.json",
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

    horses = find_horses(race_soup)

    print(
        f"Found {len(horses)} horses",
        flush=True,
    )

    results = []

    for index, horse in enumerate(horses):
        print(
            f'Fetching {index + 1}/{len(horses)}: '
            f'{horse["name"]}',
            flush=True,
        )

        result = fetch_horse_history(
            session,
            horse,
            target_date,
            args.max_runs,
        )

        results.append(result)

        print(
            f'OK: {horse["name"]} / '
            f'{result["extracted_runs"]} runs',
            flush=True,
        )

        if index < len(horses) - 1:
            time.sleep(2)

    if len(results) != len(horses):
        raise ValueError(
            "Horse count mismatch"
        )

    output = {
        "source_race_url": RACE_URL,
        "target_date": target_date.isoformat(),
        "max_runs": args.max_runs,
        "horse_count": len(results),
        "total_extracted_runs": sum(
            horse["extracted_runs"]
            for horse in results
        ),
        "horses": results,
        "posting": False,
        "note": (
            "Manual test only. "
            "Horse identity is not yet verified. "
            "Corner positions are not collected. "
            "Do not use for automatic predictions."
        ),
    }

    Path(args.output).write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f'COMPLETE: {len(results)} horses / '
        f'{output["total_extracted_runs"]} runs / '
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
