
"""Inspect horse links on a JRA race result page.

Read-only manual test.
No prediction, email, or blog posting.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from keiba_jra_result_fetch import check_url


DEFAULT_URL = (
    "https://www.jra.go.jp/JRADB/accessS.html"
    "?CNAME=pw01sde1005202605021020261108/4B"
)


def inspect_links(url):
    check_url(url)

    response = requests.get(
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

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    result_table = None

    for table in soup.find_all("table"):
        headers = " ".join(
            th.get_text(" ", strip=True)
            for th in table.find_all("th")
        )

        if all(
            word in headers
            for word in ("着順", "馬名", "コーナー")
        ):
            result_table = table
            break

    if result_table is None:
        raise ValueError(
            "JRA race result table not found"
        )

    horses = []

    for tr in result_table.find_all("tr"):
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

        links = []

        for anchor in cells[3].find_all("a"):
            href = anchor.get("href", "").strip()

            if not href:
                continue

            absolute_url = urljoin(url, href)
            parsed = urlparse(absolute_url)

            links.append({
                "text": anchor.get_text(
                    " ", strip=True
                ),
                "href": href,
                "absolute_url": absolute_url,
                "hostname": parsed.hostname,
                "path": parsed.path,
                "cname_candidates": parse_qs(
                    parsed.query
                ).get("CNAME", []),
            })

        horses.append({
            "finish": int(finish_text),
            "number": int(number_text),
            "name": name,
            "links": links,
            "link_count": len(links),
            "identity_verified": False,
        })

    if not horses:
        raise ValueError(
            "No horse rows found"
        )

    if len({
        horse["number"] for horse in horses
    }) != len(horses):
        raise ValueError(
            "Duplicate horse numbers"
        )

    return {
        "source_url": url,
        "horse_count": len(horses),
        "horses": horses,
        "note": (
            "Link inspection only. "
            "CNAME values are candidates, "
            "not verified horse IDs. "
            "Linked pages were not fetched."
        ),
        "posting": False,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
    )

    parser.add_argument(
        "--output",
        default="keiba_horse_links_test.json",
    )

    args = parser.parse_args()

    data = inspect_links(args.url)

    output = Path(args.output)

    output.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f'OK: {data["horse_count"]} horses / '
        "link inspection complete / "
        "no posting",
        flush=True,
    )

    for horse in data["horses"]:
        print(
            f'{horse["number"]}: '
            f'{horse["name"]} / '
            f'{horse["link_count"]} links',
            flush=True,
        )


if __name__ == "__main__":
    main()
