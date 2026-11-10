
"""Build horse history from downloaded JRA race JSON files.

Offline processing only.
No JRA requests, email, Blogger posting, or predictions.
"""

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


def load_races(directory):
    races = []

    for path in sorted(directory.glob("*.json")):
        if path.name == "summary.json":
            continue

        with path.open(encoding="utf-8") as file:
            data = json.load(file)

        required = (
            "date",
            "surface",
            "distance_m",
            "field_size",
            "horses",
        )

        for key in required:
            if key not in data:
                raise ValueError(
                    f"{path.name}: missing {key}"
                )

        race_date = date.fromisoformat(data["date"])

        if not isinstance(data["horses"], list):
            raise ValueError(
                f"{path.name}: invalid horses"
            )

        if len(data["horses"]) != data["field_size"]:
            raise ValueError(
                f"{path.name}: field size mismatch"
            )

        races.append((path.name, race_date, data))

    if not races:
        raise ValueError("No race JSON files found")

    return races


def build_history(races, target_date, max_runs):
    histories = defaultdict(list)

    for filename, race_date, race in races:
        # Never use results from the target date or future.
        if race_date >= target_date:
            continue

        seen_numbers = set()
        seen_names = set()

        for horse in race["horses"]:
            name = horse["name"]
            number = horse["number"]
            finish = horse["finish"]
            corners = horse["corner_positions"]

            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"{filename}: invalid horse name"
                )

            if name in seen_names:
                raise ValueError(
                    f"{filename}: duplicate horse name"
                )

            if number in seen_numbers:
                raise ValueError(
                    f"{filename}: duplicate horse number"
                )

            seen_names.add(name)
            seen_numbers.add(number)

            if not isinstance(corners, list):
                raise ValueError(
                    f"{filename}: invalid corners"
                )

            if not all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= race["field_size"]
                for value in corners
            ):
                raise ValueError(
                    f"{filename}: invalid corner position"
                )

            if (
                not isinstance(finish, int)
                or isinstance(finish, bool)
                or not 1 <= finish <= race["field_size"]
            ):
                raise ValueError(
                    f"{filename}: invalid finish"
                )

            histories[name].append({
                "date": race_date.isoformat(),
                "surface": race["surface"],
                "distance_m": race["distance_m"],
                "finish": finish,
                "field_size": race["field_size"],
                "corner_positions": corners,
                "source_file": filename,
            })

    result = {}

    for name, runs in sorted(histories.items()):
        runs.sort(
            key=lambda run: run["date"],
            reverse=True,
        )

        result[name] = {
            "name": name,
            "identity_status": "unverified_name_only",
            "runs": runs[:max_runs],
            "available_runs": len(runs),
        }

    return result


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input-dir",
        default="keiba_jra_results",
    )

    parser.add_argument(
        "--target-date",
        required=True,
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--max-runs",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--output",
        default="keiba_horse_history.json",
    )

    args = parser.parse_args()

    if not 3 <= args.max_runs <= 5:
        parser.error("--max-runs must be 3 to 5")

    target_date = date.fromisoformat(
        args.target_date
    )

    races = load_races(
        Path(args.input_dir)
    )

    histories = build_history(
        races,
        target_date,
        args.max_runs,
    )

    output = {
        "target_date": target_date.isoformat(),
        "max_runs": args.max_runs,
        "identity_warning": (
            "Horse names are not verified unique IDs. "
            "Do not use for automatic predictions "
            "until horse identity is verified."
        ),
        "horse_count": len(histories),
        "horses": histories,
        "posting": False,
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
        f"OK: {len(histories)} horses / "
        f"target {target_date} / "
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
