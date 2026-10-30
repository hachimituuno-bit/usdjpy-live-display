"""Public market-data-only stock ranking for the USDJPY display site.

Run: python stocks_generate.py
Output: docs/stocks_latest.json
No Google Cloud, OAuth, Blogger API, or secrets required.
Ranking is *only* among CANDIDATE_STOCKS, not all TSE listings.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

JST = ZoneInfo('Asia/Tokyo')
OUT = Path('docs/stocks_latest.json')

# Clearly defined candidate universe; expand here if desired.
CANDIDATE_STOCKS = {
    '1605.T': 'INPEX',
    '1802.T': '大林組',
    '1925.T': '大和ハウス工業',
    '2914.T': '日本たばこ産業',
    '4063.T': '信越化学工業',
    '4502.T': '武田薬品工業',
    '6501.T': '日立製作所',
    '6758.T': 'ソニーグループ',
    '6861.T': 'キーエンス',
    '7203.T': 'トヨタ自動車',
    '7267.T': '本田技研工業',
    '7751.T': 'キヤノン',
    '7974.T': '任天堂',
    '8035.T': '東京エレクトロン',
    '8306.T': '三菱UFJフィナンシャル・グループ',
    '8316.T': '三井住友フィナンシャルグループ',
    '8591.T': 'オリックス',
    '9432.T': 'NTT',
    '9433.T': 'KDDI',
    '9984.T': 'ソフトバンクグループ',
}


def evaluate(symbol, name):
    df = yf.Ticker(symbol).history(period='6mo', interval='1d', auto_adjust=True)
    if df.empty or len(df) < 40:
        raise ValueError('日足データが不足')
    close = df['Close'].astype(float).dropna()
    if len(close) < 40:
        raise ValueError('終値データが不足')

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    g, l = float(gain.iloc[-1]), float(loss.iloc[-1])
    rsi = 100.0 if l == 0 and g > 0 else (50.0 if g == l == 0 else 100.0 - 100.0 / (1.0 + g / l))

    # Score: MA 40, MACD 35, RSI 25. Rules, not probabilities.
    ma_points = 40 if ma5 > ma20 else 0
    macd_points = 35 if macd.iloc[-1] > signal.iloc[-1] else 0
    rsi_points = 25 if rsi > 50 else 0
    score = ma_points + macd_points + rsi_points

    hist = close.tail(22)
    return {
        'symbol': symbol,
        'name': name,
        'score_buy': score,
        'score_sell': 100 - score,
        'close': round(float(close.iloc[-1]), 2),
        'ma5': round(float(ma5), 2),
        'ma20': round(float(ma20), 2),
        'macd': round(float(macd.iloc[-1]), 4),
        'signal': round(float(signal.iloc[-1]), 4),
        'rsi14': round(float(rsi), 1),
        'date': close.index[-1].strftime('%Y-%m-%d'),
        'score_detail': {'ma': ma_points, 'macd': macd_points, 'rsi': rsi_points},
        'chart': [{'date': dt.strftime('%Y-%m-%d'), 'close': round(float(v), 2)} for dt, v in hist.items()],
    }


def main():
    ranked = []
    for symbol, name in CANDIDATE_STOCKS.items():
        try:
            ranked.append(evaluate(symbol, name))
            print('OK', symbol, ranked[-1]['score_buy'])
        except Exception as error:
            print('SKIP', symbol, str(error)[:150])
    if len(ranked) < 3:
        raise RuntimeError('有効な銘柄が3件未満。既存のJSONは上書きしません。')
    ranked.sort(key=lambda item: (-item['score_buy'], -item['rsi14'], item['symbol']))
    result = {
        'generated_at_jst': datetime.now(JST).isoformat(timespec='seconds'),
        'universe': '固定候補20銘柄から買いスコア上位3銘柄を抽出（東証全銘柄ではありません）',
        'method': 'MA5>MA20:40点、MACD(12,26,9)>Signal:35点、RSI14>50:25点。確率ではありません。',
        'data_source': 'Yahoo Finance via yfinance; データは遅延することがあります。',
        'stocks': ranked[:3],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('STOCKS_UPDATED:', result['generated_at_jst'], 'top3=', ', '.join(x['symbol'] for x in result['stocks']))


if __name__ == '__main__':
    main()
