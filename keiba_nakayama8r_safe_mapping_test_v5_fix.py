"""Step 76: read-only JRA result mapping diagnostic; no prediction, scoring, or posting."""
import json
import re
import traceback
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde1006202604090820260927/96"
OUTPUT = Path("keiba_nakayama8r_safe_mapping_test_v5.json")


def inspect(html):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for table in soup.find_all("table"):
        headers = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if all(term in headers for term in ("着順", "馬名", "コーナー")):
            candidates.append(table)
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one results table; found {len(candidates)}")

    rows = []
    for tr in candidates[0].find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        vals = [c.get_text(" ", strip=True) for c in cells]
        if len(vals) < 10:
            raise ValueError(f"Unexpected result row with {len(vals)} columns")
        number_text = vals[2]
        if not re.fullmatch(r"\d+", number_text):
            raise ValueError(f"Invalid horse number: {number_text!r}")
        corners = vals[9]
        if not re.fullmatch(r"\d+(?:\s+\d+)*", corners):
            raise ValueError(f"Invalid corner positions for horse {number_text}: {corners!r}")
        rows.append({
            "number": int(number_text),
            "name": vals[3],
            "finish_text": vals[0],
            "finish": int(vals[0]) if re.fullmatch(r"\d+", vals[0]) else None,
            "corner_positions": [int(n) for n in corners.split()],
        })

    numbers = [r["number"] for r in rows]
    if len(numbers) != len(set(numbers)):
        raise ValueError("Duplicate horse number in full results table")

    finished = [r for r in rows if r["finish"] is not None]
    excluded = [r for r in rows if r["finish"] is None]
    field_size = len(rows)
    for row in rows:
        if not all(1 <= p <= field_size for p in row["corner_positions"]):
            raise ValueError(f"Corner position out of actual field size: {row['number']}")

    # Compare by horse number, never by list index; preserving full-field count.
    full_by_number = {r["number"]: r for r in rows}
    finished_by_number = {r["number"]: r for r in finished}
    for number, row in finished_by_number.items():
        if full_by_number[number] is not row:
            raise AssertionError(f"Horse-number mapping mismatch: {number}")

    expected = {
        "field_size": 15,
        "numeric_finish_count": 14,
        "excluded_numbers": [12],
    }
    observed = {
        "field_size": field_size,
        "numeric_finish_count": len(finished),
        "excluded_numbers": sorted(r["number"] for r in excluded),
    }
    if observed != expected:
        raise ValueError(f"Race structure differs: observed={observed}, expected={expected}")
    if excluded[0]["finish_text"] not in ("中止", "競走中止"):
        raise ValueError(f"Unexpected non-numeric finish: {excluded[0]['finish_text']}")

    return {
        "status": "PASS_INDEPENDENT_MAPPING_ONLY",
        "race_url": URL,
        "actual_field_size": field_size,
        "numeric_finish_count": len(finished),
        "excluded_horses": [{"number": r["number"], "name": r["name"], "finish_text": r["finish_text"]} for r in excluded],
        "finished_horse_numbers": sorted(finished_by_number),
        "all_horse_numbers": sorted(full_by_number),
        "corner_positions_checked_against": "actual_field_size",
        "mapping_key": "horse_number",
        "existing_backtest_compatibility": "NOT_TESTED",
        "prediction_executed": False,
        "scoring_executed": False,
        "posting_executed": False,
        "existing_files_modified": False,
    }


def main():
    report = {
        "status": "NOT_TESTED",
        "race_url": URL,
        "existing_backtest_compatibility": "NOT_TESTED",
        "prediction_executed": False,
        "scoring_executed": False,
        "posting_executed": False,
        "existing_files_modified": False,
    }
    try:
        response = requests.get(URL, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        report = inspect(response.text)
        print("RESULT", report["status"])
        print("Full field:", report["actual_field_size"], "Numeric finishes:", report["numeric_finish_count"])
        print("Excluded:", report["excluded_horses"])
        print("No prediction, scoring, posting, or existing-code modification.")
    except Exception as exc:
        report["status"] = "ERROR"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        print(report["traceback"])
        raise
    finally:
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
