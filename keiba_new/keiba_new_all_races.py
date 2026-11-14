#!/usr/bin/env python3
"""新方式の全36レース収集・係数0.5採点。旧方式ファイルは読み書きしない。"""
import argparse, json, re, sys, time
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from bs4 import BeautifulSoup
import requests
from keiba_old_discovery_reference import race_links, race_metadata
from keiba_history_extraction_fixed import extract
from keiba_backtest import score, similarity, run_value

JST=timezone(timedelta(hours=9))
BASE=Path('keiba_new_results')
VENUES=('福島','東京','京都','札幌','函館','新潟','中山','中京','阪神','小倉')

def condition(soup, rid):
    """出馬表のレース見出しだけからコース条件を確定する。

    過去走・他レースへのリンク・ページ下部の条件は混ぜない。
    判断できない場合は安全のため予想を中止する。
    """
    # Yahooのレース条件はレース見出し周辺に表示される。
    # ページ全体を走査すると過去走のコースが混ざるため禁止。
    candidates = []
    selectors = (
        '.hr-raceData', '.hr-raceData__data', '.hr-raceData__course',
        '.hr-raceInfo', '.hr-raceInfo__data',
        '.raceData', '.raceData__data', '.raceInfo',
        'main h1', 'main h2', 'h1', 'h2',
    )
    for selector in selectors:
        for node in soup.select(selector):
            value = node.get_text(' ', strip=True)
            if value and len(value) <= 500:
                candidates.append((selector, value))
    # レースページの先頭部分だけを最終フォールバックにする。
    # 全文の過去走欄から芝・距離を拾うのは厳禁。
    main = soup.select_one('main')
    if main:
        candidates.append(('main冒頭', main.get_text(' ', strip=True)[:350]))
    candidates.append(('ページ冒頭', soup.get_text(' ', strip=True)[:350]))

    def parse(text):
        text = text.replace('\u3000', ' ').replace('ｍ', 'm')
        # 例: 芝1400m、芝・左 1400m、ダート右 1800m
        pattern = (r'(芝|ダート|ダ)(?!障害)\s*'
                   r'(?:[・･/／]\s*)?'
                   r'(?:(?:右|左|直線|外|内|外回り|内回り|右回り|左回り|外・内)'
                   r'\s*[・･/／]?\s*){0,3}'
                   r'(\d{3,4})\s*m?')
        result = set()
        for surface, distance in re.findall(pattern, text):
            distance = int(distance)
            if 800 <= distance <= 4000:
                result.add(('ダート' if surface in ('ダ', 'ダート') else '芝', distance))
        return result

    # レースのコース情報を最も限定的に表示している要素を優先。
    for label, value in candidates:
        if '障害' in value and ('障害' in value[:60]):
            raise ValueError(f'{rid}: 障害レースは平地用適性モデルの対象外')
        found = parse(value)
        if len(found) == 1:
            return next(iter(found))
        if len(found) > 1:
            # 先頭のページ概要に複数条件が混じる場合は次の候補へ進む。
            continue
    raise ValueError(f'{rid}: レース見出しから芝ダート・距離を確定できません（推測禁止）')

def rank_new(horses, venue, surface, distance, day):
    if not 2<=len(horses)<=18 or len({h['horse_number'] for h in horses})!=len(horses):
        raise ValueError('出走馬の重複または不足')
    ranked=[]
    for h in horses:
        valid=[]
        for r in h['runs']:
            rd=date.fromisoformat(r['date'].replace('/','-'))
            if rd>=day: raise ValueError('対象日以降の過去走混入')
            if '障害' not in r.get('raw_text',''):
                valid.append((rd,r))
        valid.sort(key=lambda x:x[0],reverse=True)
        valid=[r for _,r in valid[:5]]
        # データ不足の馬は順位付けしない
        if not valid or any(run_value(r) is None for r in valid):
            ranked.append({'horse_number':h['horse_number'],'horse_name':h['horse_name'],'form_score':None,'rank':None})
            continue
        numer=denom=0.0
        for i,r in enumerate(valid):
            fit=similarity(r,venue,surface,distance)
            weight=(0.8**i)*(1+0.5*(0.5 if fit is None else fit))
            numer+=weight*run_value(r);denom+=weight
        ranked.append({'horse_number':h['horse_number'],'horse_name':h['horse_name'],'form_score':round(100*numer/denom,2),'rank':None})
    eligible=sorted((h for h in ranked if h['form_score'] is not None),key=lambda h:(-h['form_score'],h['horse_number']))
    for i,h in enumerate(eligible,1):h['rank']=i
    return ranked

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',default='',help='YYYY-MM-DD (blank=today JST)')
    ap.add_argument('--delay',type=float,default=1.0)
    a=ap.parse_args();day=date.fromisoformat(a.date) if a.date else datetime.now(JST).date()
    if day<datetime.now(JST).date():raise ValueError('過去日の新規予想は禁止')
    BASE.mkdir(exist_ok=True)
    summary={'date':day.isoformat(),'mode':'auto','status':'FAILED','predicted':[],'failed':[],'source':'Yahoo!競馬','coefficient':0.5}
    try:
        ids,errors=race_links(day)
        summary['discovery_errors']=errors
        # 開催場ごとに1R〜12Rの完全性を厳密に要求
        by_venue={}
        for rid,url in ids.items():
            venue={'01':'札幌','02':'函館','03':'福島','04':'新潟','05':'東京','06':'中山','07':'中京','08':'京都','09':'阪神','10':'小倉'}.get(rid[2:4])
            if venue:by_venue.setdefault(venue,{})[int(rid[-2:])]=(rid,url)
        if len(by_venue)!=3 or any(set(races)!=set(range(1,13)) for races in by_venue.values()):
            raise ValueError(f'36レースの出馬表リンクを確認できません: {[(v,len(r)) for v,r in by_venue.items()]}')
        session=requests.Session();session.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'ja-JP,ja;q=0.9'})
        for venue in VENUES:
            if venue not in by_venue:continue
            for n in range(1,13):
                rid,url=by_venue[venue][n]
                try:
                    if datetime.now(JST).date()>day:raise ValueError('対象日が過去になりました')
                    response=session.get(url,timeout=30);response.raise_for_status();response.encoding=response.apparent_encoding
                    soup=BeautifulSoup(response.text,'html.parser')
                    v,num=race_metadata(soup,day,rid,venue)
                    surface,distance=condition(soup,rid)
                    horses=extract(response.text)
                    ranked=rank_new(horses,venue,surface,distance,day)
                    if len([h for h in ranked if h['rank'] is not None])<3:raise ValueError('評価可能な馬が3頭未満')
                    data={'race_date':day.isoformat(),'venue':venue,'race_number':n,'race_id':rid,'surface':surface,'distance_m':distance,'horses':ranked,'coefficient':0.5}
                    dest=BASE/f'{venue}_{n:02d}R';dest.mkdir(exist_ok=True)
                    (dest/'prediction.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                    summary['predicted'].append(f'{venue}{n}R')
                    print('OK',venue,n,'R',surface,distance,'m',flush=True)
                except Exception as e:
                    summary['failed'].append({'race_id':rid,'error':str(e)})
                    print('FAILED',rid,str(e),flush=True)
                time.sleep(max(0.5,a.delay))
        summary['status']='SUCCESS' if len(summary['predicted'])==36 and not summary['failed'] else 'PARTIAL'
    except Exception as e:
        summary['failed'].append({'error':str(e)})
    finally:
        (BASE/'run_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['status']=='SUCCESS' else 1
if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('ERROR',e,file=sys.stderr);sys.exit(1)
