"""JRA production orchestration: official entries only, read-only, no odds or betting."""
import argparse, json, re, sys, time
from collections import deque
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

JST=ZoneInfo('Asia/Tokyo')
VENUES=('札幌','函館','福島','新潟','東京','中山','中京','京都','阪神','小倉')
BASES=('https://www.jra.go.jp/JRADB/accessD.html','https://sp.jra.jp/JRADB/accessD.html')
OUT=Path('keiba_production_results')
SESSION=requests.Session(); SESSION.headers.update({'User-Agent':'Mozilla/5.0 (compatible; JRA-read-only/1.0)'})

def fetch(url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in ('www.jra.go.jp','sp.jra.jp','jra.jp'):
        raise ValueError('Non-JRA source rejected: '+url)
    r=SESSION.get(url,timeout=22); r.raise_for_status(); r.encoding=r.apparent_encoding
    return BeautifulSoup(r.text,'html.parser')

def discover(day, limit=350):
    """Find pre-race entry pages using official JRA links; record diagnostics."""
    seeds = list(BASES) + [
        'https://www.jra.go.jp/keiba/',
        'https://www.jra.go.jp/keiba/thisweek/',
        f'https://www.jra.go.jp/keiba/calendar{day.year}/{day.year}/{day.month:02d}/{day:%m%d}.html',
    ]
    queue=deque((url,0) for url in seeds)
    seen=set(); found={}; errors=[]; diagnostics=[]; link_samples=[]
    target=day.strftime('%Y%m%d')
    while queue and len(seen)<limit:
        url,depth=queue.popleft()
        if url in seen: continue
        seen.add(url)
        try: soup=fetch(url)
        except Exception as e:
            errors.append(f'{url}: {e}')
            continue
        parsed=urlparse(url)
        if len(diagnostics)<45:
            diagnostics.append({'url':url,'title':(soup.title.get_text(' ',strip=True) if soup.title else '')[:130], 'table_count':len(soup.find_all('table')), 'horse_links':len(soup.select('a[href*="accessU.html"]')), 'race_links':len(soup.select('a[href*="accessD.html"]'))})
        if len(link_samples)<40:
            for a in soup.find_all('a',href=True):
                href=urljoin(url,a['href'])
                if 'accessD.html' in href or 'shutuba' in href.lower() or 'syutuba' in href.lower():
                    if href not in link_samples: link_samples.append(href)
                    if len(link_samples)>=40: break
        cname=parse_qs(parsed.query).get('CNAME',[''])[0]
        page_text=soup.get_text(' ',strip=True)
        if parsed.path.endswith('/accessD.html') and target in cname:
            venue=next((v for v in VENUES if re.search(r'(?:\d+回)?'+v+r'\d+日',page_text[:1200])),None)
            matches=re.findall(r'(?<!\d)(1[0-2]|[1-9])\s*(?:R|レース)',page_text[:1200])
            if venue and matches:
                num=int(matches[0]); found[(venue,num)]=(url,soup)
        if depth>=5: continue
        for a in soup.find_all('a',href=True):
            link=urljoin(url,a['href']); p=urlparse(link)
            if p.scheme!='https' or p.hostname not in ('www.jra.go.jp','sp.jra.jp','jra.jp'): continue
            path=p.path
            if path.endswith('/accessD.html'):
                # JRA CNAME formats vary. Check the target date on the destination page.
                if link not in seen: queue.append((link,depth+1))
            elif depth<3 and (path.endswith('/accessI.html') or '/keiba/thisweek/' in path or '/keiba/calendar' in path):
                if link not in seen: queue.append((link,depth+1))
    if not found:
        errors.append(f'No verified official entries for {target}; visited {len(seen)} pages. JRA site layout or access restrictions may require a supported data source.')
    OUT.mkdir(exist_ok=True)
    (OUT/'discovery_diagnostics.json').write_text(json.dumps({'date':day.isoformat(),'visited_count':len(seen),'sample_pages':diagnostics,'sample_entry_links':link_samples,'errors':errors[:30]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return found,errors

def extract_entries(day,venue,num,url,soup):
    """Fail closed on ambiguous/incomplete horse IDs, rather than fabricating a prediction."""
    horses=[]; used=set()
    for tr in soup.find_all('tr'):
        links=[]
        for a in tr.find_all('a',href=True):
            link=urljoin(url,a['href']); p=urlparse(link)
            if p.hostname in ('www.jra.go.jp','jra.jp') and p.path=='/JRADB/accessU.html' and parse_qs(p.query).get('CNAME'):
                links.append((a,link))
        if len(links)!=1: continue
        a,link=links[0]
        name=a.get_text(' ',strip=True)
        cells=tr.find_all(['td','th'],recursive=False)
        nums=[int(c.get_text(' ',strip=True)) for c in cells[:3] if re.fullmatch(r'\d{1,2}',c.get_text(' ',strip=True))]
        number=next((n for n in nums if 1<=n<=18),None)
        if number is None or not name or number in used: continue
        used.add(number)
        horses.append({'number':number,'name':name,'horse_url':link,'identity_candidate':parse_qs(urlparse(link).query)['CNAME'][0]})
    horses.sort(key=lambda h:h['number'])
    if len(horses)<2 or len(horses)!=len(set(h['identity_candidate'] for h in horses)):
        raise ValueError(f'Official entrant extraction incomplete or ambiguous: {venue} {num}R ({len(horses)} horses)')
    text=soup.get_text(' ',strip=True)
    if '出馬表' not in text[:500] and '出走馬' not in text[:500]:
        raise ValueError('Not verified as an official pre-race entry page')
    return {'race_date':day.isoformat(),'venue':venue,'race_number':num,'race_name':f'{venue}{num}R','horses':horses,'posting':False}

def predict(entries, outdir):
    from keiba_next_collect import collect
    from keiba_next_race import rank
    outdir.mkdir(parents=True,exist_ok=True)
    hist=collect(entries,max_runs=5,delay=0.15)
    ranking=rank(entries,hist)
    if len(ranking['horses'])!=len(entries['horses']): raise ValueError('Incomplete ranking')
    for name,obj in [('entries',entries),('history',hist),('prediction',ranking)]:
        (outdir/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=[f"{entries['race_date']} {entries['venue']} {entries['race_number']}R",'No odds / No betting / Ranking scores are not probabilities']
    for h in ranking['horses']:
        lines.append(f"{h.get('rank','?')}. {h.get('horse_number',h.get('number','?'))} {h.get('horse_name',h.get('name','?'))} | score={h.get('form_score',h.get('score','?'))}")
    (outdir/'prediction.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines),flush=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=['auto','manual','results'],default='auto')
    ap.add_argument('--date',default=''); ap.add_argument('--venue',default=''); ap.add_argument('--race',type=int,default=0)
    a=ap.parse_args(); day=date.fromisoformat(a.date) if a.date else datetime.now(JST).date()
    OUT.mkdir(exist_ok=True)
    report={'mode':a.mode,'date':day.isoformat(),'status':'STARTED','predicted':[],'failed':[],'notes':[]}
    try:
        if a.mode=='results':
            # Results ingestion is intentionally separate from pre-race prediction.
            # Never infer hit rate from an unavailable result or overwrite predictions.
            report['status']='NOT_IMPLEMENTED'; report['notes'].append('Official post-race result reconciliation is not yet implemented; no accuracy claim generated')
        else:
            found,errors=discover(day)
            report['discovered_races']=[f'{v}{n}R' for v,n in sorted(found)]
            report['discovery_errors']=errors[:12]
            selected=sorted(found) if a.mode=='auto' else [(a.venue,a.race)]
            if not selected: raise RuntimeError('No JRA race pages discovered; do not claim success')
            for venue,num in selected:
                key=f'{venue}{num}R'
                try:
                    if (venue,num) not in found: raise ValueError('Race not discovered on official JRA entries')
                    url,soup=found[(venue,num)]
                    entries=extract_entries(day,venue,num,url,soup)
                    predict(entries,OUT/f'{venue}_{num:02d}R')
                    report['predicted'].append(key)
                except Exception as e:
                    report['failed'].append({'race':key,'error':str(e)})
                    print('FAILED',key,str(e),flush=True)
            report['status']='SUCCESS' if report['predicted'] and not report['failed'] else ('PARTIAL' if report['predicted'] else 'FAILED')
            if report['status']!='SUCCESS': raise RuntimeError(f"Prediction incomplete: {report['status']}")
    except Exception as e:
        if report['status']=='STARTED': report['status']='FAILED'
        report['notes'].append(str(e))
        raise
    finally:
        (OUT/'run_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('SUMMARY',json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
