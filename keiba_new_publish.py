#!/usr/bin/env python3
"""独立・新方式係数0.5の予想記事生成。明示 --send のときのみSMTP送信。"""
import argparse, json, re, sys
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from html import escape
from hashlib import sha256
from mail_to_blogger import send_post
JST=timezone(timedelta(hours=9))
ROOT=Path(__file__).resolve().parent

def validated_snapshot(path):
    raw=path.read_bytes(); d=json.loads(raw)
    if d.get('schema')!='keiba-forward-v1': raise ValueError('未対応のスナップショット形式')
    day=date.fromisoformat(d['race_date'])
    captured=datetime.fromisoformat(d['captured_at_jst'])
    if captured.tzinfo is None or captured.utcoffset()!=timedelta(hours=9) or captured.date()>day: raise ValueError('事前保存日時が不正')
    if day < datetime.now(JST).date(): raise ValueError('過去レースの予想は投稿できません')
    rid=str(d['race_id'])
    if not re.fullmatch(r'\d{10}',rid): raise ValueError('レースIDが不正')
    if not rid.startswith(day.strftime('%y')): raise ValueError('レースIDと日付が不一致')
    if d.get('surface') not in ('芝','ダート') or not isinstance(d.get('distance_m'),int): raise ValueError('コース情報不正')
    horses=d.get('horses_and_runs'); ranks=d.get('coefficient_rankings',{}).get('0.5')
    if not isinstance(horses,list) or not isinstance(ranks,list) or len(horses)<2 or len(horses)!=len(ranks): raise ValueError('出走馬と予想の件数不一致')
    identities={(h['horse_number'],h['horse_name']) for h in horses}
    ranked={(h['number'],h['name']) for h in ranks}
    if len(identities)!=len(horses) or identities!=ranked or len({h['number'] for h in ranks})!=len(ranks): raise ValueError('馬番・馬名不整合')
    if any(not isinstance(h.get('score'),(int,float)) for h in ranks): raise ValueError('予想スコア不正')
    if any(not h.get('runs') for h in horses): raise ValueError('過去走不足')
    if any(date.fromisoformat(r['date'].replace('/','-'))>=day for h in horses for r in h['runs']): raise ValueError('未来情報混入')
    start=d.get('scheduled_start_jst')
    if start:
        dt=datetime.fromisoformat(start)
        if dt.tzinfo is None or dt.utcoffset()!=timedelta(hours=9) or dt.date()!=day: raise ValueError('発走時刻不正')
        if captured>=dt or datetime.now(JST)>=dt: raise ValueError('発走後・発走時刻以降は投稿禁止')
    else:
        # 旧スナップショットは発走時刻未記録。明示的な指定が必要
        pass
    return d, sorted(ranks,key=lambda h:(-h['score'],h['number'])),sha256(raw).hexdigest()

def main():
    p=argparse.ArgumentParser(description='新方式0.5：記事プレビュー／明示送信')
    p.add_argument('--snapshot',required=True)
    p.add_argument('--start',help='旧形式JSONの公式発走時刻 HH:MM')
    p.add_argument('--send',action='store_true',help='SMTP送信（Blogger公開は別途確認）')
    p.add_argument('--output',default='keiba_new_preview.html')
    a=p.parse_args(); path=Path(a.snapshot)
    d,ranks,digest=validated_snapshot(path)
    day=date.fromisoformat(d['race_date'])
    if not d.get('scheduled_start_jst'):
        if not a.start or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',a.start): raise ValueError('旧形式JSONには --start HH:MM が必須です')
        start=datetime.fromisoformat(f'{day.isoformat()}T{a.start}:00').replace(tzinfo=JST)
        captured=datetime.fromisoformat(d['captured_at_jst'])
        if captured>=start or datetime.now(JST)>=start: raise ValueError('発走時刻以降の投稿は禁止')
    else: start=datetime.fromisoformat(d['scheduled_start_jst'])
    venue=d['venue']; race_no=int(str(d['race_id'])[-2:]); race=f'{day.isoformat()} {venue}{race_no}R {d["surface"]}{d["distance_m"]}m'
    title=f'【新方式・競馬予想】{race}｜適性補正0.5'
    tr=''.join('<tr>'+''.join('<td style="padding:8px;border:1px solid #ddd">'+escape(str(v))+'</td>' for v in (i,h['number'],h['name'],f"{h['score']*100:.2f}"))+'</tr>' for i,h in enumerate(ranks,1))
    markup=f'<h2>{escape(title)}</h2><p>レース前に保存したデータによる新方式の予想。適性補正係数0.5。旧方式とは別記事です。</p><p>予想保存日時：{escape(d["captured_at_jst"])} / 発走予定：{escape(start.strftime("%H:%M"))} JST</p><table style="border-collapse:collapse"><tr><th>順位</th><th>馬番</th><th>馬名</th><th>評価点</th></tr>{tr}</table><p>※過去成績に基づく参考順位です。勝率や的中を保証するものではありません。</p>'
    plain='\n'.join([title,'新方式・適性補正係数0.5']+[f'{i}位 馬番{h["number"]} {h["name"]} 評価{h["score"]*100:.2f}' for i,h in enumerate(ranks,1)]+['※参考予想。的中保証なし。'])
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(markup,encoding='utf-8')
    print('PREVIEW:',out,'RACE:',race,'SNAPSHOT_SHA256:',digest,'TOP3:',[(h['number'],h['name']) for h in ranks[:3]],flush=True)
    if a.send:
        # Actionsの同一runでの二重送信抑止。別runの再実行にはBlogger上の確認が必要。
        marker=out.with_suffix('.smtp-accepted.json')
        if marker.exists(): raise ValueError('この実行環境では既にSMTP送信済み。再送信しません')
        if datetime.now(JST)>=start: raise ValueError('発走時刻を過ぎたため送信禁止')
        send_post(title,plain,markup)
        marker.write_text(json.dumps({'snapshot_sha256':digest,'smtp_accepted_at':datetime.now(JST).isoformat()},ensure_ascii=False),encoding='utf-8')
        print('SMTP受付完了。Blogger公開はブログ画面で別途確認してください。')
    else: print('DRY RUN: メール送信・ブログ投稿なし')

if __name__=='__main__':
    try: main()
    except Exception as e: print('ERROR:',e,file=sys.stderr); sys.exit(1)
