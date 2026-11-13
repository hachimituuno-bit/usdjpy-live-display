"""Manual JRA pre-race prediction. Read-only; no posting/betting."""
import argparse
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from keiba_next_collect import collect, validate_entries
from keiba_next_race import rank


def run(entries, outdir):
    validate_entries(entries)
    race_day = date.fromisoformat(entries['race_date'])
    if race_day < date.today():
        raise ValueError('Past race dates are not accepted by the live prediction command')
    for h in entries['horses']:
        p = urlparse(h['horse_url'])
        if p.scheme != 'https' or p.hostname != 'www.jra.go.jp' or p.path != '/JRADB/accessU.html' or len(parse_qs(p.query).get('CNAME', [])) != 1:
            raise ValueError('Only official JRA horse profile URLs are accepted')
    history = collect(entries, max_runs=5, delay=0.5)
    ranking = rank(entries, history)
    if len(ranking['horses']) != len(entries['horses']):
        raise ValueError('Ranking is incomplete')
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / 'prediction.json').write_text(json.dumps(ranking, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (outdir / 'history.json').write_text(json.dumps(history, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [f"JRA {entries['race_date']} {entries['venue']} {entries['race_number']}R {entries['race_name']}",
             'Prediction only / NOT a win probability / No odds, posting, or betting', '']
    for h in ranking['horses']:
        lines.append(f"{h['rank']:>2}. {h['horse_number']:>2} {h['horse_name']} | form_score={h['form_score']} | prior_runs={h['completed_runs']} | top3_runs={h['top3_finishes']}")
    (outdir / 'prediction.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--entries', required=True)
    p.add_argument('--output-dir', default='keiba_production_results')
    args = p.parse_args()
    entries = json.loads(Path(args.entries).read_text(encoding='utf-8'))
    run(entries, Path(args.output_dir))


if __name__ == '__main__':
    main()
