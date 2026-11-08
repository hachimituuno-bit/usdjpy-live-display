"""Offline comparison gate for model-extracted race records. No network, email or posting.

A second, independently verified reference is REQUIRED. This checks agreement, not
whether the reference is true or its reuse is permitted.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

class GuardError(ValueError):
    pass

def _load(path):
    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise GuardError(f'JSONに重複キーがあります: {key}')
            obj[key] = value
        return obj
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_pairs,
                          parse_constant=lambda v: (_ for _ in ()).throw(GuardError(f'無効な数値: {v}')))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError(f'JSONを読み込めません: {exc}') from exc

def _str(value, field):
    if not isinstance(value, str) or not value.strip():
        raise GuardError(f'{field} が空か文字列ではありません')
    return value.strip()

def _records(data, label):
    if not isinstance(data, dict):
        raise GuardError(f'{label}: JSONのトップがオブジェクトではありません')
    day = _str(data.get('date'), f'{label}.date')
    demo = data.get('synthetic_demo')
    if not isinstance(demo, bool):
        raise GuardError(f'{label}: synthetic_demo が真偽値ではありません')
    races = data.get('races')
    if not isinstance(races, list) or not races:
        raise GuardError(f'{label}: racesがありません')
    found = {}
    for race in races:
        if not isinstance(race, dict):
            raise GuardError(f'{label}: レース形式が不正です')
        venue = _str(race.get('venue'), 'venue')
        number = race.get('race_number')
        if type(number) is not int or number not in (10,11,12):
            raise GuardError(f'{label}: 10〜12R以外のレースがあります')
        key = (venue, number)
        if key in found:
            raise GuardError(f'{label}: レース重複 {key}')
        horses = race.get('horses')
        if not isinstance(horses, list) or len(horses) < 5:
            raise GuardError(f'{label}: {key} 出走馬が不足')
        hs = {}
        for h in horses:
            if not isinstance(h, dict):
                raise GuardError(f'{label}: 馬データ不正')
            n = h.get('number')
            if type(n) is not int or not 1 <= n <= 30 or n in hs:
                raise GuardError(f'{label}: {key} 馬番の重複・不正')
            name = _str(h.get('name'), f'{label}: 馬名')
            odds = h.get('win_odds')
            if type(odds) not in (int, float) or not math.isfinite(odds) or not 1.01 <= odds <= 1000:
                raise GuardError(f'{label}: {key} {n}番のオッズが不正')
            hs[n] = (name, float(odds))
        found[key] = {
            'name': _str(race.get('race_name'), 'race_name'),
            'post': _str(race.get('post_time'), 'post_time'),
            'grade': _str(race.get('grade'), 'grade'),
            'horses': hs,
        }
    return day, demo, found

def verify(extracted, reference, *, demo=False):
    ed, em, er = _records(extracted, 'Gemini')
    rd, rm, rr = _records(reference, '参照元')
    if ed != rd:
        raise GuardError('日付が一致しません')
    if em != demo or rm != demo:
        raise GuardError('デモ・実データの区分が一致しません')
    if er.keys() != rr.keys():
        raise GuardError('競馬場・レース番号の集合が一致しません')
    if not any({10, 11, 12}.issubset({n for v, n in er if v == venue}) for venue, _ in er):
        raise GuardError('10・11・12Rがそろった競馬場がありません')
    for key in er:
        a, b = er[key], rr[key]
        for field in ('name', 'post', 'grade'):
            if a[field] != b[field]:
                raise GuardError(f'{key}: {field} 不一致')
        if a['horses'].keys() != b['horses'].keys():
            raise GuardError(f'{key}: 出走馬番が一致しません')
        for n in a['horses']:
            if a['horses'][n] != b['horses'][n]:
                raise GuardError(f'{key} {n}番: 馬名か単勝オッズが不一致')
    return len(er)

def main():
    ap = argparse.ArgumentParser(description='Geminiの抽出値を独立した参照データと照合（通信・投稿なし）')
    ap.add_argument('--extracted', required=True)
    ap.add_argument('--reference', required=True)
    ap.add_argument('--demo', action='store_true')
    args = ap.parse_args()
    try:
        count = verify(_load(args.extracted), _load(args.reference), demo=args.demo)
    except GuardError as exc:
        print(f'GUARD_BLOCKED: {exc}')
        return 1
    print(f'GUARD_MATCH_OK: {count} races / mail=off / post=off')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
