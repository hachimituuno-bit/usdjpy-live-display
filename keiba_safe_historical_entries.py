"""Post-race entrants for retrospective testing only; not a pre-race card."""
import re
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup

def historical_entries(html, url, actual):
    soup = BeautifulSoup(html, 'html.parser')
    tables = [t for t in soup.find_all('table') if all(
        w in ' '.join(th.get_text(' ', strip=True) for th in t.find_all('th'))
        for w in ('着順', '馬名', 'コーナー'))]
    if len(tables) != 1:
        raise ValueError('Cannot identify unique official result table')
    truth = {(h['number'], h['name']) for h in actual['horses']}
    horses = []
    for tr in tables[0].find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) < 10:
            continue
        status = cells[0].get_text(' ', strip=True)
        if not (re.fullmatch(r'\d+', status) or status in ('中止', '失格')):
            continue
        number_text = cells[2].get_text(' ', strip=True)
        if not re.fullmatch(r'\d+', number_text):
            raise ValueError('Invalid entrant number')
        number = int(number_text)
        name_cell = cells[3]
        name = name_cell.get_text(' ', strip=True)
        links = []
        for a in name_cell.find_all('a', href=True):
            u = urljoin(url, a['href'])
            p = urlparse(u)
            if (p.scheme == 'https' and p.hostname == 'www.jra.go.jp'
                    and p.path == '/JRADB/accessU.html'
                    and len(parse_qs(p.query).get('CNAME', [])) == 1):
                links.append(u)
        if len(links) != 1:
            raise ValueError(f'{name}: horse profile URL not unique')
        identity = parse_qs(urlparse(links[0]).query)['CNAME'][0]
        horses.append({'number': number, 'name': name, 'horse_url': links[0],
                       'identity_candidate': identity})
    if (len(horses) != actual['field_size'] or
            {(h['number'], h['name']) for h in horses} != truth or
            len({h['number'] for h in horses}) != len(horses)):
        raise ValueError('Entrants do not match official starters')
    return {'race_date': actual['date'], 'venue': 'historical_JRA',
            'race_number': 1, 'race_name': 'historical_backtest',
            'posting': False, 'horses': horses,
            'warning': 'Retrospective entrants sourced from result page, NOT a pre-race card'}
