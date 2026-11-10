
"""Diagnose JRA horse profile name locations.

Manual read-only test.
No predictions, email, or Blogger posting.
"""

import argparse
import json
import re
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
    return re.sub(
        r"\s+", " ", value
    ).strip()


def validate_candidate(horse):
    parsed = urlparse(
        horse["horse_url"]
    )

    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.jra.go.jp"
        or parsed.path != "/JRADB/accessU.html"
    ):
        raise ValueError(
            "Unexpected horse URL"
        )

    candidates = parse_qs(
        parsed.query
    ).get("CNAME", [])

    if (
        len(candidates) != 1
        or not candidates[0]
        or candidates[0]
        != horse["identity_candidate"]
    ):
        raise ValueError(
            "Invalid CNAME candidate"
        )

    return candidates[0]


def describe_element(element):
    text = clean(
        element.get_text(
            " ", strip=True
        )
    )

    return {
        "tag": element.name,
        "id": element.get("id"),
        "class": element.get(
            "class", []
        ),
        "text": text[:200],
    }


def inspect_name_locations(
    soup,
    expected_name,
):
    """Find name occurrences without claiming identity."""

    exact_matches = []
    containing_elements = []

    for element in soup.find_all(True):
        if element.name in (
            "script",
            "style",
            "noscript",
        ):
            continue

        text = clean(
            element.get_text(
                " ", strip=True
            )
        )

        if not text:
            continue

        if text == expected_name:
            exact_matches.append(
                describe_element(element)
            )

        elif expected_name in text:
            # Keep only the smallest matching
            # elements to avoid collecting
            # entire page containers.
            child_contains_name = any(
                expected_name in clean(
                    child.get_text(
                        " ", strip=True
                    )
                )
                for child in element.find_all(
                    True, recursive=False
                )
            )

            if not child_contains_name:
                containing_elements.append(
                    describe_element(element)
                )

    return {
        "expected_name": expected_name,
        "exact_matches": (
            exact_matches[:20]
        ),
        "containing_elements": (
            containing_elements[:20]
        ),
        "exact_match_count": len(
            exact_matches
        ),
        "containing_element_count": len(
            containing_elements
        ),
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

    check_error_page(
        race_soup
    )

    horses = find_horses(
        race_soup
    )

    if len(horses) != 11:
        raise ValueError(
            "Expected 11 horses; "
            f"found {len(horses)}"
        )

    candidates = [
        horse["identity_candidate"]
        for horse in horses
    ]

    if len(candidates) != len(
        set(candidates)
    ):
        raise ValueError(
            "Duplicate CNAME candidates"
        )

    # Diagnose one known horse first.
    # Do not make assumptions about
    # the other ten profiles.
    horse = horses[0]

    print(
        f'Diagnosing: {horse["name"]}',
        flush=True,
    )

    candidate = validate_candidate(
        horse
    )

    soup = get_page(
        session,
        horse["horse_url"],
    )

    check_error_page(
        soup
    )

    diagnosis = inspect_name_locations(
        soup,
        horse["name"],
    )

    history_table_found = False
    history_error = None

    try:
        find_history_table(soup)
        history_table_found = True
    except ValueError as exc:
        history_error = str(exc)

    output = {
        "source_race_url": RACE_URL,
        "horse_name": horse["name"],
        "horse_url": horse["horse_url"],
        "identity_candidate": candidate,
        "name_diagnosis": diagnosis,
        "history_table_found": (
            history_table_found
        ),
        "history_error": history_error,
        "identity_verified": False,
        "official_horse_id_verified": False,
        "posting": False,
        "note": (
            "Diagnostic only. "
            "Name occurrences do not "
            "independently prove identity."
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
        "DIAGNOSIS COMPLETE: "
        f'{horse["name"]} / '
        f'{diagnosis["exact_match_count"]} '
        "exact elements / "
        f'{diagnosis["containing_element_count"]} '
        "containing elements / "
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
