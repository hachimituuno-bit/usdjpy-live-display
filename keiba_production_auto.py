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
    seeds=[f'https://sports.yahoo.co.jp/keiba/race/list/{day:%Y%m%d}']
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

def race_metadata(soup, day, rid, expected_venue=''):
    """Race ID identifies venue (JRA venue codes), meeting/day and race number.
    Date must be verified on the DATE-SPECIFIC list page, not inferred from the race ID.
    """
    codes={'01':'札幌','02':'函館','03':'福島','04':'新潟','05':'東京',
           '06':'中山','07':'中京','08':'京都','09':'阪神','10':'小倉'}
    venue=codes.get(rid[2:4]); number=int(rid[-2:])
    if not venue or not 1<=number<=12: raise ValueError('Invalid race ID '+rid)
    if expected_venue and venue!=expected_venue: raise ValueError('Venue mismatch '+rid)
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    if '出馬表' not in title and '出走表' not in title and '出馬表' not in soup.get_text(' ',strip=True)[:2000]:
        raise ValueError('Not verified as an entry page '+rid)
    return venue,number

def entries_from_table(soup,rid):
    """Read entrant names/horse numbers only from the same table row.
    Try common Yahoo formats: explicit horse-number cell or frame+horse-number.
    """
    candidates=[]
    for table in soup.select('table'):
        horses=[]; seen=set(); ids=set()
        for tr in table.select('tr'):
            anchors=[a for a in tr.select('a[href]') if re.search(r'/keiba/horse/(\d+)',a['href'])]
            if len(anchors)!=1: continue
            a=anchors[0]; name=a.get_text(' ',strip=True)
            match=re.search(r'/keiba/horse/(\d+)',a['href'])
            if not name or not match: continue
            cells=tr.find_all(['td','th'],recursive=False)
            digits=[]
            for c in cells[:5]:
                val=c.get_text(' ',strip=True)
                if re.fullmatch(r'\d{1,2}',val):digits.append(int(val))
            if len(digits)<2:continue
            num=digits[1]
            if not 1<=num<=18 or num in seen or match[1] in ids: continue
            seen.add(num);ids.add(match[1])
            horses.append({'number':num,'name':name,'identity_candidate':'yahoo:'+match[1],
                           'horse_url':'https://sports.yahoo.co.jp/keiba/horse/'+match[1]})
        if len(horses)>len(candidates):candidates=horses
    if len(candidates)<2:raise ValueError(f'Cannot verify entrant rows for {rid} (found {len(candidates)})')
    return sorted(candidates,key=lambda h:h['number'])

def history_from_yahoo(entry,day):
    soup=get(entry['horse_url']); text=soup.get_text(' ',strip=True)
    if entry['name'] not in text[:1600]:raise ValueError('Horse name/profile mismatch: '+entry['name'])
    runs=[]
    for table in soup.select('table'):
        headers=[c.get_text(' ',strip=True) for c in table.select('thead th')]
        # Yahoo may publish finishing position and runners in separate columns.
        date_idx=next((i for i,h in enumerate(headers) if '日付' in h or '年月日' in h),None)
        finish_idx=next((i for i,h in enumerate(headers) if h in ('着順','着')),None)
        field_idx=next((i for i,h in enumerate(headers) if h in ('頭数','出走頭数')),None)
        for tr in table.select('tr'):
            cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'],recursive=False)]
            if len(cells)<4:continue
            dates=[]
            search_cells=[cells[date_idx]] if date_idx is not None and date_idx<len(cells) else cells[:3]
            for cell in search_cells:
                m=re.search(r'(20\d{2})[./年](\d{1,2})[./月](\d{1,2})',cell)
                if m:
                    try:dates.append(date(*map(int,m.groups())))
                    except ValueError:pass
            if len(dates)!=1 or dates[0]>=day:continue
            finish=field=None
            if finish_idx is not None and field_idx is not None and max(finish_idx,field_idx)<len(cells):
                a,b=cells[finish_idx],cells[field_idx]
                if re.fullmatch(r'\d{1,2}',a) and re.fullmatch(r'\d{1,2}',b):finish,field=int(a),int(b)
            if finish is None:
                pairs=[re.fullmatch(r'\s*(\d{1,2})\s*[/／]\s*(\d{1,2})\s*',c) for c in cells]
                pairs=[m for m in pairs if m]
                if len(pairs)==1:finish,field=map(int,pairs[0].groups())
            if finish is not None and 1<=finish<=field<=18:
                runs.append({'date':dates[0].isoformat(),'finish':finish,'field_size':field,'finish_status':'finished'})
    unique={}
    for r in runs:
        if r['date'] in unique and unique[r['date']]!=r:raise ValueError('Conflicting historical data: '+entry['name'])
        unique[r['date']]=r
    runs=sorted(unique.values(),key=lambda r:r['date'],reverse=True)[:5]
    if not runs:raise ValueError('No verified historical finish/field-size data for '+entry['name'])
    return {'name':entry['name'],'identity_candidate':entry['identity_candidate'],
            'identity_verified':False,'runs':runs,'available_runs':len(runs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['auto','manual'],default='auto');ap.add_argument('--date',default='');ap.add_argument('--venue',default='');ap.add_argument('--race',type=int,default=0)
    a=ap.parse_args();day=date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo('Asia/Tokyo')).date();OUT.mkdir(exist_ok=True)
    summary={'date':day.isoformat(),'mode':a.mode,'source':'Yahoo!競馬','status':'FAILED','predicted':[],'failed':[],'notes':[]}
    try:
        ids,errors=race_links(day);summary['discovery_errors']=errors;summary['discovered_race_ids']=sorted(ids)
        if not ids:raise RuntimeError('Yahoo date-specific race list yielded no race links; do not claim success')
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
