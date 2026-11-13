"""Offline tests of parser contracts; no network, prediction or posting."""
import importlib.util
from pathlib import Path
from bs4 import BeautifulSoup
from keiba_jra_result_fetch import extract
from keiba_safe_historical_entries import historical_entries

URL='https://www.jra.go.jp/JRADB/accessS.html?CNAME=test'
# Metadata parser requires a compact shared metadata container.
def sample(nonfinish=True):
    rows=[]
    count=15 if nonfinish else 14
    for n in range(1,count+1):
        status='中止' if nonfinish and n==12 else str(n if n<12 or not nonfinish else n-1)
        cols=[status,'x',str(n),f'<a href="https://www.jra.go.jp/JRADB/accessU.html?CNAME=horse{n}">馬{n}</a>',
              'x','x','x','x','x',str(count if n==1 else 1)]
        if status=='中止': cols[-1]=''
        rows.append('<tr>'+''.join(f'<td>{v}</td>' for v in cols)+'</tr>')
    return ('<html><body><section>2026年9月27日 コース：1,800メートル（ダート）</section>'
            '<table><tr><th>着順</th><th>馬名</th><th>コーナー</th></tr>'
            +''.join(rows)+'</table></body></html>')

def main():
    for dnf in (False,True):
        html=sample(dnf)
        actual=extract(URL,html)
        entries=historical_entries(html,URL,actual)
        assert actual['field_size']==(15 if dnf else 14)
        assert len(entries['horses'])==actual['field_size']
        if dnf:
            assert next(h for h in actual['horses'] if h['number']==12)['finish'] is None
            assert max(p for h in actual['horses'] for p in h['corner_positions'])==15
    print('PASS_OFFLINE_PARSER_CONTRACT (synthetic only)')

if __name__=='__main__': main()
