
"""Fetch multiple JRA historical race results.

Manual test only. Does not publish or send messages.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests

from keiba_jra_result_fetch import extract


RACES = [
    {
        "id": "tokyo_20261108_10r",
        "url": (
            "https://www.jra.go.jp/JRADB/accessS.html"
            "?CNAME=pw01sde1005202605021020261108/4B"
        ),
        "date": "2026-11-08",
        "surface": "dirt",
        "distance_m": 1600,
    },
    {
        "id": "tokyo_20261108_11r",
        "url": (
            "https://www.jra.go.jp/JRADB/accessS.html"
            "?CNAME=pw01sde1005202605021120261108/00"
        ),
        "date": "2026-11-08",
        "surface": "turf",
        "distance_m": 2500,
    },
]


def fetch_race(session, race):
    response = session.get(
        race["url"],
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; authorized-research/1.0)"
            )
        },
    )

    response.raise_for_status()
    response.encoding = response.apparent_encoding

    result = extract(race["url"], response.text)

    for key in ("date", "surface", "distance_m"):
        if result[key] != race[key]:
            raise ValueError(
                f'{race["id"]}: {key} mismatch. '
                f'Expected {race[key]}, got {result[key]}'
            )

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="keiba_jra_results",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if len({race["id"] for race in RACES}) != len(RACES):
        raise ValueError("Duplicate race IDs")

    session = requests.Session()
    summary = []

    for index, race in enumerate(RACES):
        print(f'Fetching: {race["id"]}', flush=True)

        result = fetch_race(session, race)

        output_file = output_dir / f'{race["id"]}.json'

        output_file.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        summary.append({
            "race_id": race["id"],
            "date": result["date"],
            "surface": result["surface"],
            "distance_m": result["distance_m"],
            "field_size": result["field_size"],
            "file": output_file.name,
        })

        print(
            f'OK: {race["id"]} / '
            f'{result["field_size"]} horses',
            flush=True,
        )

        if index < len(RACES) - 1:
            time.sleep(2)

    summary_file = output_dir / "summary.json"
    summary_file.write_text(
        json.dumps(
            {
                "race_count": len(summary),
                "races": summary,
                "posting": False,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"COMPLETE: {len(summary)} races / "
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
