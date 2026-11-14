"""Yahoo!競馬優先の出馬表・過去走取得。検証できないデータでは予想しない。"""
import argparse,json,re,sys,traceback
from datetime import datetime,date
from pathlib import Path
from urllib.parse import urljoin,urlparse
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from keiba_next_race import rank
OUT=Path('keiba_production_results')
VENUES=('札幌','函館','福島','新潟','東京','中山','中京','京都','阪神','小倉')
S=requests.Session();S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36','Accept-Language':'ja-JP,ja;q=0.9'})
def get(url):
    u=urlparse(url)
    if u.scheme!='https' or u.hostname!='sports.yahoo.co.jp' or not u.path.startswith('/keiba/'):
        raise ValueError('Unexpected URL '+url)
    r=S.get(url,timeout=25);r.raise_for_status();r.encoding=r.apparent_encoding
    return BeautifulSoup(r.text,'html.parser')
def race_links(day):
    # Yahoo race IDs: YY + 2-digit venue code + 2-digit meeting + 2-digit day + 2-digit race
    # Discover links rather than inventing meeting/day codes.
    seeds=[f'https://sports.yahoo.co.jp/keiba/race/list/{day:%Y%m%d}', 'https://sports.yahoo.co.jp/keiba/']
    found={};errors=[]
    for url in seeds:
        try:
            soup=get(url)
            for a in soup.select('a[href]'):
                href=urljoin(url,a['href']);m=re.search(r'/keiba/race/(?:denma|result|index)/([0-9]{10})(?:[/?#]|$)',href)
                if m and m.group(1).startswith(day.strftime('%y')):
                    rid=m.group(1);found[rid]='https://sports.yahoo.co.jp/keiba/race/denma/'+rid+'?detail=1'
        except Exception as e:errors.append(str(e))
    return found,errors

def race_metadata(soup,day,rid):
    text=soup.get_text(' ',strip=True);title=soup.title.get_text(' ',strip=True) if soup.title else ''
    # Require date and venue on the page, not just in the race ID.
    date_ok=bool(re.search(rf'{day.year}年\s*{day.month}月\s*{day.day}日',text))
    venue=next((v for v in VENUES if v in title),None)
    if not venue:venue=next((v for v in VENUES if v in text[:650]),None)
    if not date_ok or not venue:raise ValueError(f'Race date/venue unverified: {rid}, title={title[:90]}')
    return venue,int(rid[-2:])

def entries_from_table(soup,rid):
    # Match horse link to horse number within the SAME table row.
    candidates=[]
    for table in soup.select('table'):
        horses=[];seen=set()
        for tr in table.select('tr'):
            anchors=[a for a in tr.select('a[href]') if re.search(r'/keiba/horse/\d+',a['href'])]
            if len(anchors)!=1:continue
            a=anchors[0];name=a.get_text(' ',strip=True);m=re.search(r'/keiba/horse/(\d+)',a['href'])
            cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'],recursive=False)]
            # Horse number is typically the second numeric cell (after frame number).
            digits=[int(x) for x in cells[:4] if re.fullmatch(r'\d{1,2}',x)]
            if not digits or not name:continue
            num=digits[1] if len(digits)>=2 else digits[0]
            if not 1<=num<=18 or num in seen:continue
            seen.add(num);horses.append({'number':num,'name':name,'identity_candidate':'yahoo:'+m.group(1),'horse_url':'https://sports.yahoo.co.jp/keiba/horse/'+m.group(1)})
        if len(horses)>len(candidates):candidates=horses
    if len(candidates)<2:raise ValueError(f'Cannot verify entrant rows for {rid} (found {len(candidates)})')
    return sorted(candidates,key=lambda h:h['number'])

def history_from_yahoo(entry,day):
    soup=get(entry['horse_url'])
    if entry['name'] not in soup.get_text(' ',strip=True)[:900]:
        raise ValueError('Horse name/profile mismatch: '+entry['name'])
    runs=[]
    for tr in soup.select('tr'):
        cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'],recursive=False)]
        if len(cells)<5:continue
        match=next((re.search(r'(20\d{2})[./年](\d{1,2})[./月](\d{1,2})',c) for c in cells[:3] if re.search(r'20\d{2}',c)),None)
        if not match:continue
        try:d=date(int(match[1]),int(match[2]),int(match[3]))
        except ValueError:continue
        if d>=day:continue
        # Accept finish and field size ONLY when both can be identified in a single result cell (e.g. 3/16).
        # Do not guess from unrelated numeric columns such as popularity or horse number.
        paired=next((re.fullmatch(r'\s*(\d{1,2})\s*[/／]\s*(\d{1,2})\s*',c) for c in cells if re.fullmatch(r'\s*\d{1,2}\s*[/／]\s*\d{1,2}\s*',c)),None)
        if paired:
            finish,field=map(int,paired.groups())
            if 1<=finish<=field<=18:runs.append({'date':d.isoformat(),'finish':finish,'field_size':field,'finish_status':'finished'})
    runs=sorted({r['date']:r for r in runs}.values(),key=lambda r:r['date'],reverse=True)[:5]
    if not runs:raise ValueError('No unambiguous historical finish/field-size pairs for '+entry['name'])
    return {'name':entry['name'],'identity_candidate':entry['identity_candidate'],'identity_verified':False,'runs':runs,'available_runs':len(runs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['auto','manual'],default='auto');ap.add_argument('--date',default='');ap.add_argument('--venue',default='');ap.add_argument('--race',type=int,default=0)
    a=ap.parse_args();day=date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo('Asia/Tokyo')).date();OUT.mkdir(exist_ok=True)
    summary={'date':day.isoformat(),'mode':a.mode,'source':'Yahoo!競馬','status':'FAILED','predicted':[],'failed':[],'notes':[]}
    try:
        ids,errors=race_links(day);summary['discovery_errors']=errors;summary['discovered_race_ids']=sorted(ids)
        if not ids:raise RuntimeError('Yahoo race list yielded no verified race links; do not claim success')
        for rid,url in sorted(ids.items()):
            try:
                soup=get(url);venue,num=race_metadata(soup,day,rid)
                if a.mode=='manual' and (venue!=a.venue or num!=a.race):continue
                entries={'race_date':day.isoformat(),'venue':venue,'race_number':num,'race_name':f'{venue}{num}R','posting':False,'horses':entries_from_table(soup,rid)}
                history={'target_date':day.isoformat(),'posting':False,'horses':[history_from_yahoo(h,day) for h in entries['horses']]}
                prediction=rank(entries,history)
                directory=OUT/f'{venue}_{num:02d}R';directory.mkdir(exist_ok=True)
                for filename,obj in [('entries.json',entries),('history.json',history),('prediction.json',prediction)]:
                    (directory/filename).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                lines=[f'{day} {venue}{num}R (Yahoo!競馬; odds unused)']+[f"{h['rank']}. {h['horse_number']} {h['horse_name']} score={h['form_score']}" for h in prediction['horses']]
                (directory/'prediction.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
                summary['predicted'].append(f'{venue}{num}R');print('\n'.join(lines),flush=True)
            except Exception as e:summary['failed'].append({'race_id':rid,'error':str(e)});print('FAILED',rid,str(e),flush=True)
        summary['status']='SUCCESS' if summary['predicted'] and not summary['failed'] else ('PARTIAL' if summary['predicted'] else 'FAILED')
        if a.mode=='manual' and not summary['predicted']:summary['notes'].append('Requested venue/race not successfully predicted')
    except Exception as e:summary['notes'].append(str(e));print('ERROR',str(e),flush=True)
    finally:
        (OUT/'run_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['status']=='SUCCESS' else 1
if __name__=='__main__':sys.exit(main())
