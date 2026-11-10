"""Scheduled, read-only, fail-closed JRA race-card discovery and form ranking."""
import json
import os
import sys
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from keiba_next_entries_fetch import parse_entries
from keiba_next_collect import collect
from keiba_next_race import rank

JST = timezone(timedelta(hours=9))
TARGET = {"race_date":"2026-11-14", "venue":"東京", "race_number":11,
          "race_name":"武蔵野ステークス", "official_page":"https://www.jra.go.jp/keiba/race/103/syutsuba.html"}

def discover(html, page_url):
    soup=BeautifulSoup(html,'html.parser')
    found=set()
    for a in soup.find_all('a',href=True):
        u=urljoin(page_url,a['href'])
        p=urlparse(u)
        if p.scheme=='https' and p.hostname=='www.jra.go.jp' and p.path=='/JRADB/accessD.html':
            found.add(u)
    if len(found)!=1:
        raise ValueError(f'Expected one official JRADB race-card link; found {len(found)}')
    return found.pop()

def main():
    today=datetime.now(JST).date()
    race_date=date.fromisoformat(TARGET['race_date'])
    if today < date(2026,11,12):
        print('WAIT: official card scheduled for November 12; no prediction yet')
        return 0
    if today > race_date:
        print('SKIP: configured race is in the past; update TARGET for a future race')
        return 0
    sess=requests.Session()
    sess.headers.update({'User-Agent':'Mozilla/5.0'})
    page=sess.get(TARGET['official_page'],timeout=25)
    page.raise_for_status(); page.encoding=page.apparent_encoding
    if '公開予定' in BeautifulSoup(page.text,'html.parser').get_text(' ',strip=True):
        print('WAIT: JRA race card not yet published')
        return 0
    try:
        card_url=discover(page.text,TARGET['official_page'])
        card=sess.get(card_url,timeout=25); card.raise_for_status();card.encoding=card.apparent_encoding
        # No guessed horse count. Extract first, then independently validate against
        # the official card's row count and the maximum field size; this alone does
        # NOT prove a complete race card. The pipeline remains preliminary.
        soup=BeautifulSoup(card.text,'html.parser')
        counts=[]
        for table in soup.find_all('table'):
            for tr in table.find_all('tr'):
                hdr=[x.get_text(' ',strip=True).replace(' ','') for x in tr.find_all(['th','td'],recursive=False)]
                if '馬番' in hdr and '馬名' in hdr:
                    counts.append(sum(1 for row in table.find_all('tr') if row.find_all('td',recursive=False)))
        if len(counts)!=1 or not 2<=counts[0]<=16:
            raise ValueError('Could not establish unique complete horse table')
        entries=parse_entries(card.text,card_url,TARGET['race_date'],TARGET['venue'],TARGET['race_number'],TARGET['race_name'],counts[0])
        # Do not publish a prediction when metadata / identity are not independently verified.
        Path('keiba_next_entries.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        history=collect(entries,session=sess)
        Path('keiba_next_history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        ranking=rank(entries,history)
        ranking['provisional']=True
        ranking['warning']='UNVERIFIED race metadata and identity; preliminary historical form ranking only, NOT a confirmed prediction.'
        Path('keiba_next_race_ranking.json').write_text(json.dumps(ranking,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('PRELIMINARY RANKING GENERATED; NOT VERIFIED; NO POSTING')
        return 0
    except (ValueError,KeyError,IndexError) as e:
        print('NOT READY / SAFE STOP:',e)
        return 0

if __name__=='__main__':
    sys.exit(main())
