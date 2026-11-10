
"""Test access to one JRA horse information page.

Manual, read-only inspection.
No predictions, email, or blog posting.
"""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from keiba_jra_result_fetch import check_url


RACE_URL = (
    "https://www.jra.go.jp/JRADB/accessS.html"
    "?CNAME=pw01sde1005202605021020261108/4B"
)

TARGET_HORSE = "ワイドクリーガー"

ERROR_WORDS = (
    "パラメータエラー",
    "ご指定のページが見つかりませんでした",
    "エラー013",
)


def get_page(session, url):
    response = session.get(
        url,
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

    return BeautifulSoup(
        response.text,
        "html.parser",
    )


def check_error_page(soup):
    text = soup.get_text(" ", strip=True)

    if any(word in text for word in ERROR_WORDS):
        raise ValueError(
            "JRA returned an error page"
        )

    return text


def find_horse_link(soup):
    for table in soup.find_all("table"):
        headers = " ".join(
            th.get_text(" ", strip=True)
            for th in table.find_all("th")
        )

        if not all(
            word in headers
            for word in ("着順", "馬名", "コーナー")
        ):
            continue

        for tr in table.find_all("tr"):
            cells = tr.find_all(
                "td",
                recursive=False,
            )

            if len(cells) < 4:
                continue

            name = cells[3].get_text(
                " ", strip=True
            )

            if name != TARGET_HORSE:
                continue

            links = cells[3].find_all("a")

            if len(links) != 1:
                raise ValueError(
                    "Expected exactly one horse link"
                )

            href = links[0].get(
                "href", ""
            ).strip()

            if not href:
                raise ValueError(
                    "Horse link is empty"
                )

            return urljoin(RACE_URL, href)

    raise ValueError(
        "Target horse link not found"
    )


def inspect_horse_page(soup, horse_url):
    text = check_error_page(soup)

    if TARGET_HORSE not in text:
        raise ValueError(
            "Horse name not found on page"
        )

    tables = []

    for table in soup.find_all("table"):
        headers = [
            th.get_text(" ", strip=True)
            for th in table.find_all("th")
        ]

        if headers:
            tables.append(headers[:20])

    history_keywords = (
        "出走レース",
        "競走成績",
        "レース成績",
        "全成績",
    )

    found_keywords = [
        word
        for word in history_keywords
        if word in text
    ]

    if not found_keywords:
        raise ValueError(
            "Horse page loaded, but no "
            "history section was identified"
        )

    return {
        "horse_name": TARGET_HORSE,
        "horse_url": horse_url,
        "page_validated": True,
        "history_keywords": found_keywords,
        "table_headers": tables,
        "history_rows_parsed": False,
        "posting": False,
        "note": (
            "Page structure inspection only. "
            "Past race rows and horse identity "
            "have not yet been verified."
        ),
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output",
        default="keiba_horse_page_test.json",
    )

    args = parser.parse_args()

    check_url(RACE_URL)

    session = requests.Session()

    print(
        "Fetching JRA race result page",
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

    parsed = urlparse(horse_url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.jra.go.jp"
        or parsed.path != "/JRADB/accessU.html"
    ):
        raise ValueError(
            "Unexpected horse page URL"
        )

    if not re.search(
        r"(?:^|[?&])CNAME=",
        parsed.query,
    ):
        raise ValueError(
            "Horse page CNAME is missing"
        )

    print(
        "Fetching linked horse information page",
        flush=True,
    )

    horse_soup = get_page(
        session,
        horse_url,
    )

    result = inspect_horse_page(
        horse_soup,
        horse_url,
    )

    Path(args.output).write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        "OK: horse page inspected / "
        "history structure found / "
        "no posting",
        flush=True,
    )


if __name__ == "__main__":
    main()
