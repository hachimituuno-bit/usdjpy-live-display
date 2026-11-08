"""Convert *authorized, operator-supplied* race JSON into keiba_predict.py JSON.

Offline-only. No JRA/netkeiba scraping, network, Blogger, Gmail, or LLM.
Scores are transparent editorial heuristics; not probabilities.
"""
from __future__ import annotations
import argparse
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

class InputError(ValueError):
    pass

PACE_FIT = {
    'fast': {'front': 35, 'press': 55, 'stalker': 75, 'closer': 85},
    'average': {'front': 70, 'press': 75, 'stalker': 75, 'closer': 60},
    'slow': {'front': 85, 'press': 75, 'stalker': 55, 'closer': 35},
}


def _str(obj, key):
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise InputError(f'{key}: 空欄または文字列ではありません')
    return v.strip()


def _int(obj, key, lo, hi):
    v = obj.get(key)
    if type(v) is not int or not lo <= v <= hi:
        raise InputError(f'{key}: {lo}〜{hi} の整数が必要です')
    return v


def _num(obj, key, lo, hi):
    v = obj.get(key)
    if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
        raise InputError(f'{key}: {lo}〜{hi} の数値が必要です')
    return float(v)


def _time(value):
    try:
        t = datetime.fromisoformat(value)
    except (ValueError, TypeError) as e:
        raise InputError('日時はタイムゾーン付きISO8601で指定してください') from e
    if not t.tzinfo or t.utcoffset() is None:
        raise InputError('日時にタイムゾーンが必要です')
    return t


def score_horse(horse, race, day):
    runs = horse.get('recent_runs')
    if not isinstance(runs, list) or not 3 <= len(runs) <= 5:
        raise InputError(f'{horse.get("name")}: 直近3〜5走が必要です')
    race_surface = _str(race, 'surface')
    if race_surface not in ('turf', 'dirt'):
        raise InputError('surface は turf / dirt のみ対応')
    distance = _int(race, 'distance_m', 800, 4000)
    dated = []
    for run in runs:
        if not isinstance(run, dict):
            raise InputError('recent_runs はオブジェクトの配列です')
        run_day = date.fromisoformat(_str(run, 'date'))
        if run_day >= day:
            raise InputError(f'{horse.get("name")}: 予想対象日当日以降の成績が含まれています')
        finish = _int(run, 'finish', 1, 30)
        field = _int(run, 'field_size', 2, 30)
        if finish > field:
            raise InputError('着順が出走頭数を超えています')
        rd = _int(run, 'distance_m', 800, 4000)
        rs = _str(run, 'surface')
        if rs not in ('turf', 'dirt'):
            raise InputError('過去走 surface が不正です')
        result = 100 * (field - finish) / (field - 1)
        dated.append((run_day, result, rd, rs))
    dated.sort(key=lambda x: x[0], reverse=True)
    if len({item[0] for item in dated}) != len(dated):
        raise InputError('同一馬の過去走の日付が重複しています')
    weights = [1 / (1 + i * .35) for i in range(len(dated))]
    recent = sum(row[1] * w for row, w in zip(dated, weights)) / sum(weights)
    relevant = [(result, abs(rd - distance)) for _, result, rd, rs in dated if rs == race_surface and abs(rd - distance) <= 400]
    if not relevant:
        raise InputError(f'{horse.get("name")}: 同じ芝/ダート・±400mの参考成績がありません')
    # Distance similarity + race performance, based ONLY on supplied past races.
    course_distance = sum((result * .8 + (1 - gap / 500) * 100 * .2) for result, gap in relevant) / len(relevant)
    scenario = _str(race, 'pace_scenario')
    style = _str(horse, 'running_style')
    if scenario not in PACE_FIT or style not in PACE_FIT[scenario]:
        raise InputError('pace_scenario: fast/average/slow, running_style: front/press/stalker/closer')
    return {'recent_form_rating': round(recent, 2), 'course_distance_rating': round(course_distance, 2), 'pace_rating': float(PACE_FIT[scenario][style])}


def convert(raw, expected_day, demo=False):
    if not isinstance(raw, dict) or raw.get('date') != expected_day:
        raise InputError('date が指定日と一致しません')
    day = date.fromisoformat(expected_day)
    if day.weekday() not in (5, 6):
        raise InputError('現バージョンは土日開催のみ対応')
    if raw.get('synthetic_demo') is not demo:
        raise InputError('demo 指定と synthetic_demo が一致しません')
    asof = _time(_str(raw, 'data_asof'))
    if asof.date() > day:
        raise InputError('data_asof が予想日より後です')
    if not demo:
        if raw.get('reuse_permission_verified') is not True:
            raise InputError('データ利用許可の確認がありません')
        for field in ('source_name', 'source_url', 'permission_evidence'):
            _str(raw, field)
        if not raw['source_url'].startswith('https://'):
            raise InputError('source_url は https:// から始めてください')
        if asof < datetime.combine(day, datetime.min.time(), asof.tzinfo) - timedelta(days=2):
            raise InputError('データが古すぎます')
    races = raw.get('races')
    if not isinstance(races, list) or not races:
        raise InputError('races がありません')
    output_races = []
    for race in races:
        if not isinstance(race, dict):
            raise InputError('race が不正です')
        race_number = _int(race, 'race_number', 1, 12)
        if race_number not in (10, 11, 12):
            continue
        post = _time(_str(race, 'post_time'))
        if post.date() != day or asof >= post:
            raise InputError('発走日時と基準時刻が不正です')
        if not demo and post - asof > timedelta(hours=36):
            raise InputError('オッズの鮮度が不足しています')
        scenario = _str(race, 'pace_scenario')
        if scenario not in PACE_FIT:
            raise InputError('pace_scenario が不正です')
        horses = race.get('horses')
        if not isinstance(horses, list) or len(horses) < 5:
            raise InputError('5頭以上の馬データが必要です')
        converted = []
        numbers = set()
        names = set()
        for horse in horses:
            number = _int(horse, 'number', 1, 30)
            name = _str(horse, 'name')
            if number in numbers or name in names:
                raise InputError('馬番・馬名が重複しています')
            numbers.add(number)
            names.add(name)
            odds = _num(horse, 'win_odds', 1.01, 1000)
            if horse.get('scratched') not in (None, False):
                raise InputError('出走取消が含まれています')
            converted.append({'number': number, 'name': name, 'win_odds': odds,
                              **score_horse(horse, race, day)})
        output_races.append({
            'venue': _str(race, 'venue'), 'race_number': race_number,
            'race_name': _str(race, 'race_name'), 'grade': _str(race, 'grade'),
            'post_time': post.isoformat(), 'surface': race['surface'],
            'distance_m': race['distance_m'], 'horses': converted,
            'editorial_priority': race.get('editorial_priority', 0),
            'ratings_method': ('直近3〜5走の相対着順（時系列加重）、同馬場・距離±400m成績、'
                               '入力された展開シナリオと脚質の適合度による固定ヒューリスティック。'
                               '近走45％／距離35％／展開20％。勝率ではありません。')
        })
    result = {'date': expected_day, 'synthetic_demo': demo, 'data_asof': asof.isoformat(),
              'races': output_races}
    if not demo:
        for k in ('reuse_permission_verified', 'permission_evidence', 'source_name', 'source_url'):
            result[k] = raw[k]
    # Reuse existing article generator's integrity checks before writing anything.
    from keiba_predict import validate
    validate(result, expected_day, demo=demo)
    return result


def main():
    p = argparse.ArgumentParser(description='提供許可があるローカル実データ→中穴予想JSON（オフライン）')
    p.add_argument('--input', required=True)
    p.add_argument('--date', required=True)
    p.add_argument('--output-dir', default='keiba_data')
    p.add_argument('--demo', action='store_true')
    p.add_argument('--draft-dir', default='keiba_drafts')
    a = p.parse_args()
    path = Path(a.input)
    raw = json.loads(path.read_text(encoding='utf-8'))
    result = convert(raw, a.date, demo=a.demo)
    output_dir = Path(a.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f'{a.date}_ADAPTER_DEMO_DO_NOT_POST.json' if a.demo else f'{a.date}.json'
    out = output_dir / name
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    from keiba_predict import validate, render
    venue, races = validate(result, a.date, demo=a.demo)
    draft_dir = Path(a.draft_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft = draft_dir / (f'{a.date}_ADAPTER_DEMO_DO_NOT_POST.html' if a.demo else f'{a.date}_ADAPTER_DRAFT.html')
    draft.write_text(render(result, venue, races, demo=a.demo), encoding='utf-8')
    print(f'CONVERTED: {out} / {len(result["races"])} races; network/mail/post = none')
    print(f'DRAFT_READY: {draft} / 競馬場: {venue} / 送信なし・公開なし')

if __name__ == '__main__':
    main()
