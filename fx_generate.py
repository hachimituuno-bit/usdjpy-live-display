"""USD/JPY live-data generator; no Google Cloud, Blogger API, Apps Script."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import yfinance as yf

JST = timezone(timedelta(hours=9))

def calculate_fx_four_timeframes():
    import pandas as pd
    ticker = yf.Ticker('JPY=X')
    daily = ticker.history(period='2y', interval='1d')
    hourly = ticker.history(period='60d', interval='1h')
    intraday = ticker.history(period='30d', interval='15m')
    if daily.empty or hourly.empty or intraday.empty:
        raise ValueError('USD/JPYの時間足データを取得できませんでした')
    daily_close = daily['Close'].dropna()
    hourly_close = hourly['Close'].dropna()
    intraday_close = intraday['Close'].dropna()
    hourly_close.index = hourly_close.index.tz_convert('UTC') if hourly_close.index.tz is not None else hourly_close.index.tz_localize('UTC')
    h4_close = hourly_close.resample('4h', origin='start_day').last().dropna()
    scores = {}
    for name, close, weight in [('日足', daily_close, 20), ('4時間足', h4_close, 20), ('1時間足', hourly_close, 20)]:
        if len(close) < 28:
            raise ValueError(f'{name}のMA28を計算するためのデータが足りません')
        ma7 = float(close.tail(7).mean())
        ma28 = float(close.tail(28).mean())
        if ma7 == ma28:
            raise ValueError(f'{name}のMA7とMA28が同値で方向を確定できません')
        scores[name] = {'direction': '買い' if ma7 > ma28 else '売り', 'weight': weight, 'ma7': ma7, 'ma28': ma28}
    if len(intraday_close) < 50:
        raise ValueError('15分足の指標計算に必要なデータが足りません')
    ma7 = float(intraday_close.tail(7).mean())
    ma28 = float(intraday_close.tail(28).mean())
    macd_series = intraday_close.ewm(span=12, adjust=False).mean() - intraday_close.ewm(span=26, adjust=False).mean()
    macd_main = float(macd_series.iloc[-1])
    macd_signal = float(macd_series.ewm(span=7, adjust=False).mean().iloc[-1])
    delta = intraday_close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    if float(down.iloc[-1]) == 0:
        rsi = 100.0 if float(up.iloc[-1]) > 0 else 50.0
    else:
        rsi = float(100 - 100 / (1 + up.iloc[-1] / down.iloc[-1]))
    directions = []
    if ma7 != ma28:
        directions.append('買い' if ma7 > ma28 else '売り')
    if macd_main != 0:
        directions.append('買い' if macd_main > 0 else '売り')
    if rsi != 50:
        directions.append('買い' if rsi < 50 else '売り')
    buy_votes = directions.count('買い')
    sell_votes = directions.count('売り')
    if buy_votes == sell_votes:
        raise ValueError('15分足の3指標が拮抗し方向を確定できません')
    scores['15分足'] = {'direction': '買い' if buy_votes > sell_votes else '売り', 'weight': 40, 'ma7': ma7, 'ma28': ma28, 'macd': macd_main, 'macd_signal': macd_signal, 'rsi': rsi, 'buy_votes': buy_votes, 'sell_votes': sell_votes}
    buy = sum((item['weight'] for item in scores.values() if item['direction'] == '買い'))
    return {'buy': buy, 'sell': 100 - buy, 'timeframes': scores, 'asof': str(intraday_close.index[-1]), 'source': 'Yahoo Finance (JPY=X)'}


def should_run(now):
    # Temporary weekend-test workflows can explicitly bypass the normal market pause.
    if os.getenv('WEEKEND_TEST_MODE', '').strip().lower() == 'true':
        return True
    # Preserve the original production weekend guard for the normal workflow.
    
    return not (
        (now.weekday() == 5 and now.hour >= 7)
        or now.weekday() == 6
        or (now.weekday() == 0 and now.hour < 7)
    )



def main():
    now = datetime.now(JST)
    if not should_run(now):
        print('Scheduled pause: Saturday 07:00 to Sunday 06:59 JST')
        return
    result = calculate_fx_four_timeframes()  # raises on missing/ambiguous data
    if result['buy'] + result['sell'] != 100:
        raise ValueError('score total invalid')
    result['generated_at_jst'] = now.isoformat(timespec='seconds')
    result['model'] = 'MA7/28 D1/H4/H1; MA7/28 MACD(12,26,7) RSI14 M15'
    result['note'] = 'Direction scores, not forecast probabilities.'
    dest = Path('docs/latest.json')
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(dest)
    print('DATA_UPDATED:', result['generated_at_jst'], 'buy=', result['buy'])


if __name__ == '__main__':
    main()
