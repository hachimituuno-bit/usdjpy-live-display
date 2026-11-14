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
DIAG=OUT/'diagnostics'
_saved_diagnostics=set()
VENUES=('札幌','函館','福島','新潟','東京','中山','中京','京都','阪神','小倉')
S=requests.Session();S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36','Accept-Language':'ja-JP,ja;q=0.9'})
def get(url):
    u=urlparse(url)
    if u.scheme!='https' or u.hostname!='sports.yahoo.co.jp' or not u.path.startswith('/keiba/'):
        raise ValueError('Unexpected URL '+url)
    r=S.get(url,timeout=25);r.raise_for_status();r.encoding=r.apparent_encoding
    return BeautifulSoup(r.text,'html.parser')
def race_links(day):
    """Find actual Yahoo race IDs via date-verified venue lists, not YYYYMMDD URL guesses."""
    target=f'{day.year}年{day.month}月{day.day}日'
    found={}; errors=[]; candidates=set(); seen=set()
    # The homepage and calendar expose real YY+venue+meeting+day IDs.
    seeds=['https://sports.yahoo.co.jp/keiba/',
           f'https://sports.yahoo.co.jp/keiba/schedule/list/{day.year}{day.month:02d}']
    pattern=re.compile(r'/keiba/race/(?:list|denma|index|result)/([0-9]{8,10})(?:[/?#]|$)')
    for url in seeds:
        try:
            soup=get(url)
            for a in soup.select('a[href]'):
                href=urljoin(url,a['href']); m=pattern.search(href)
                if m and m.group(1).startswith(day.strftime('%y')):
                    candidates.add(m.group(1)[:8])
        except Exception as e: errors.append(f'{url}: {e}')
    # Each candidate MUST be checked against the actual date printed on the list page.
    for prefix in sorted(candidates):
        url='https://sports.yahoo.co.jp/keiba/race/list/'+prefix
        try:
            soup=get(url)
            text=soup.get_text(' ',strip=True)
            if not re.search(rf'{day.year}年\s*{day.month}月\s*{day.day}日',text[:1200]):
                continue
            seen.add(prefix)
            for a in soup.select('a[href]'):
                href=urljoin(url,a['href'])
                m=re.search(r'/keiba/race/(?:denma|index|result)/([0-9]{10})(?:[/?#]|$)',href)
                if m and m.group(1).startswith(prefix) and 1<=int(m.group(1)[-2:])<=12:
                    rid=m.group(1)
                    found[rid]='https://sports.yahoo.co.jp/keiba/race/denma/'+rid+'?detail=1'
            # List pages sometimes link to race pages only through script-rendered UI.
            # Validate each race individually before accepting it downstream.
            if not any(r.startswith(prefix) for r in found):
                for n in range(1,13):
                    rid=f'{prefix}{n:02d}'
                    found[rid]='https://sports.yahoo.co.jp/keiba/race/denma/'+rid+'?detail=1'
        except Exception as e: errors.append(f'{url}: {e}')
    errors.append(f'Diagnostics: candidate_list_ids={sorted(candidates)}, verified_list_ids={sorted(seen)}')
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
    page_text=soup.get_text(' ',strip=True)
    if not re.search(rf'{day.year}年\s*{day.month}月\s*{day.day}日',page_text[:1500]):
        raise ValueError('Race date not confirmed on entry page '+rid)
    if '出馬表' not in title and '出走表' not in title and '出馬表' not in soup.get_text(' ',strip=True)[:2000]:
        raise ValueError('Not verified as an entry page '+rid)
    return venue,number

def entries_and_history(soup, rid, day):
    """Parse Yahoo's two aligned tables from a single saved entry page.

    Never infer finish from odds/popularity. Every historical result needs
    its own date, finishing position and verified field size.
    """
    entry_table=soup.select_one('table.hr-tableScrollFix')
    form_table=soup.select_one('table.hr-tableScroll')
    if not entry_table or not form_table:
        raise ValueError(f'Missing paired Yahoo entry/form tables for {rid}')
    entry_rows=entry_table.select('tr')[1:]
    form_rows=form_table.select('tr')[1:]
    if len(entry_rows)!=len(form_rows) or not (2<=len(entry_rows)<=18):
        raise ValueError(f'Misaligned entrant/form rows: {len(entry_rows)}/{len(form_rows)}')
    horses=[]; histories=[]; numbers=set(); identities=set()
    for row,form in zip(entry_rows,form_rows):
        num_node=row.select_one('.hr-denma__number')
        name_link=row.select_one('a[href^="/keiba/directory/horse/"]')
        if num_node is None or name_link is None:
            raise ValueError('Missing number/name in an entrant row')
        number_text=num_node.get_text(' ',strip=True)
        m=re.fullmatch(r'/keiba/directory/horse/(\d+)/?',name_link.get('href',''))
        if not re.fullmatch(r'\d{1,2}',number_text) or not m:
            raise ValueError('Invalid number or horse profile URL')
        number=int(number_text); name=name_link.get_text(' ',strip=True)
        cid='yahoo:'+m.group(1)
        if not 1<=number<=18 or not name or number in numbers or cid in identities:
            raise ValueError('Duplicate or invalid horse identity/number')
        numbers.add(number);identities.add(cid)
        horses.append({'number':number,'name':name,'identity_candidate':cid,
                       'horse_url':'https://sports.yahoo.co.jp'+m.group(0)})
        runs=[]
        for cell in form.select('td.hr-denma__third, td.hr-tableScroll__data--race'):
            date_node=cell.select_one('p.hr-denma__date span')
            finish_node=cell.select_one('.hr-denma__arrival')
            if not date_node or not finish_node:continue
            date_match=re.fullmatch(r'(20\d{2})/(\d{1,2})/(\d{1,2})',date_node.get_text(' ',strip=True))
            finish_text=finish_node.get_text(' ',strip=True)
            field_match=re.search(r'(?<!\d)(\d{1,2})頭',cell.get_text(' ',strip=True))
            if not date_match or not re.fullmatch(r'\d{1,2}',finish_text) or not field_match:continue
            try:run_day=date(*map(int,date_match.groups()))
            except ValueError:continue
            finish=int(finish_text); field=int(field_match.group(1))
            if run_day>=day or not 1<=finish<=field<=18:continue
            runs.append({'date':run_day.isoformat(),'finish':finish,'field_size':field,'finish_status':'finished'})
        dates=[r['date'] for r in runs]
        if len(dates)!=len(set(dates)):
            raise ValueError('Duplicate historical race dates: '+name)
        runs=sorted(runs,key=lambda x:x['date'],reverse=True)[:5]
        histories.append({'name':name,'identity_candidate':cid,'identity_verified':False,
                          'runs':runs,'available_runs':len(runs)})
    if sorted(numbers)!=list(range(1,len(horses)+1)):
        raise ValueError('Entrant numbers not contiguous; possible incomplete page')
    return sorted(horses,key=lambda x:x['number']),histories

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['auto','manual'],default='auto');ap.add_argument('--date',default='');ap.add_argument('--venue',default='');ap.add_argument('--race',type=int,default=0)
    a=ap.parse_args();day=date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo('Asia/Tokyo')).date();OUT.mkdir(exist_ok=True)
    if a.mode=='manual' and (a.venue not in VENUES or not 1<=a.race<=12):
        ap.error('manual requires --venue and --race 1..12')
    summary={'date':day.isoformat(),'mode':a.mode,'source':'Yahoo!競馬','status':'FAILED','predicted':[],'failed':[],'notes':[]}
    try:
        ids,errors=race_links(day);summary['discovery_errors']=errors;summary['discovered_race_ids']=sorted(ids)
        if not ids:raise RuntimeError('Yahoo date-specific race list yielded no race links; do not claim success')
        for rid,url in sorted(ids.items()):
            try:
                soup=get(url);venue,num=race_metadata(soup,day,rid)
                if a.mode=='manual' and (venue!=a.venue or num!=a.race):continue
                horses,form=entries_and_history(soup,rid,day)
                entries={'race_date':day.isoformat(),'venue':venue,'race_number':num,'race_name':f'{venue}{num}R','posting':False,'horses':horses}
                history={'target_date':day.isoformat(),'posting':False,'horses':form}
                prediction=rank(entries,history)
                directory=OUT/f'{venue}_{num:02d}R';directory.mkdir(exist_ok=True)
                for filename,obj in [('entries.json',entries),('history.json',history),('prediction.json',prediction)]:
                    (directory/filename).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                lines=[f'{day} {venue}{num}R (Yahoo!競馬; odds unused)']+[f"{h['rank']}. {h['horse_number']} {h['horse_name']} score={h['form_score']}" for h in prediction['horses']]
                (directory/'prediction.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
                summary['predicted'].append(f'{venue}{num}R');print('\n'.join(lines),flush=True)
            except Exception as e:
                summary['failed'].append({'race_id':rid,'error':str(e)})
                print('FAILED',rid,str(e),flush=True)
        summary['status']='SUCCESS' if summary['predicted'] and not summary['failed'] else ('PARTIAL' if summary['predicted'] else 'FAILED')
        if a.mode=='manual' and not summary['predicted']:summary['notes'].append('Requested venue/race not successfully predicted')
    except Exception as e:summary['notes'].append(str(e));print('ERROR',str(e),flush=True)
    finally:
        (OUT/'run_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['status']=='SUCCESS' else 1
if __name__=='__main__':sys.exit(main())
