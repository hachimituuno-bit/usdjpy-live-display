
"""Test identity consistency of JRA horse pages.

Manual read-only test.
No predictions, email, or Blogger posting.
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from keiba_jra_horse_page_test import (
    RACE_URL,
    check_error_page,
    get_page,
)

from keiba_jra_multi_horse_test import (
    find_horses,
)

from keiba_jra_past_runs_test import (
    find_history_table,
)


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


def find_profile_name(soup, expected_name):
    """Find an exact horse name in a heading or table cell.

    Do not accept a mere substring of the entire page.
    """

    matches = []

    for element in soup.find_all(
        ["h1", "h2", "h3", "h4", "th", "td"]
    ):
        if element.find(
            ["h1", "h2", "h3", "h4", "th", "td"]
        ):
            continue

        text = clean(
            element.get_text(" ", strip=True)
        )

        if text == expected_name:
            matches.append(element)

    if not matches:
        raise ValueError(
            "Exact horse name not found "
            f"in profile elements: {expected_name}"
        )

    return expected_name


def validate_identity_candidate(horse):
    parsed = urlparse(horse["horse_url"])

    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.jra.go.jp"
        or parsed.path != "/JRADB/accessU.html"
    ):
        raise ValueError(
            f'{horse["name"]}: unexpected horse URL'
        )

    values = parse_qs(
        parsed.query
    ).get("CNAME", [])

    if len(values) != 1 or not values[0]:
        raise ValueError(
            f'{horse["name"]}: invalid CNAME'
        )

    if values[0] != horse["identity_candidate"]:
        raise ValueError(
            f'{horse["name"]}: CNAME mismatch'
        )

    return values[0]


def verify_horse(session, horse):
    candidate = validate_identity_candidate(
        horse
    )

    soup = get_page(
        session,
        horse["horse_url"],
    )

    check_error_page(soup)

    profile_name = find_profile_name(
        soup,
        horse["name"],
    )

    table = find_history_table(soup)

    history_rows = 0

    for tr in table.find_all("tr"):
        cells = tr.find_all(
            "td",
            recursive=False,
        )

        if len(cells) == 14:
            history_rows += 1

    if history_rows < 1:
        raise ValueError(
            f'{horse["name"]}: no history rows'
        )

    return {
        "number": horse["number"],
        "race_result_name": horse["name"],
        "profile_name": profile_name,
        "horse_url": horse["horse_url"],
        "identity_candidate": candidate,
        "name_exact_match": True,
        "history_rows_found": history_rows,
        "identity_verified": False,
        "official_horse_id_verified": False,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output",
        default="keiba_identity_test.json",
    )

    args = parser.parse_args()

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

    if len(horses) != 11:
        raise ValueError(
            f"Expected 11 horses, got {len(horses)}"
        )

    candidates = [
        horse["identity_candidate"]
        for horse in horses
    ]

    if len(candidates) != len(set(candidates)):
        raise ValueError(
            "Duplicate CNAME candidates"
        )

    results = []

    for index, horse in enumerate(horses):
        print(
            f'Checking {index + 1}/{len(horses)}: '
            f'{horse["name"]}',
            flush=True,
        )

        result = verify_horse(
            session,
            horse,
        )

        results.append(result)

        print(
            f'OK: {horse["name"]} / '
            f'{result["history_rows_found"]} '
            "history rows",
            flush=True,
        )

        if index < len(horses) - 1:
            time.sleep(2)

    output = {
        "source_race_url": RACE_URL,
        "horse_count": len(results),
        "name_exact_matches": sum(
            result["name_exact_match"]
            for result in results
        ),
        "unique_cname_candidates": len(
            set(candidates)
        ),
        "horses": results,
        "posting": False,
        "note": (
            "Checks exact name occurrence in "
            "profile elements, CNAME uniqueness, "
            "and history table presence. "
            "Does not establish an official "
            "horse ID or independently prove "
            "horse identity."
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
        f"COMPLETE: {len(results)} horses / "
        f'{output["name_exact_matches"]} '
        "exact name matches / no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
