"""Friday scheduled: future race only. Manual: historical backtest fallback.
No publishing, no betting. Logs clearly distinguish success from no data.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def execute(script):
    result = subprocess.run([sys.executable, script], capture_output=True, text=True, check=False)
    print(result.stdout, end='', flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end='', flush=True)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=('scheduled', 'manual'), required=True)
    args = p.parse_args()
    print(f'MODE: {args.mode}', flush=True)
    if not Path('keiba_rolling_run.py').is_file():
        raise SystemExit('ERROR: keiba_rolling_run.py missing')
    future = execute('keiba_rolling_run.py')
    if future.returncode == 0 and 'SUCCESS PRELIMINARY' in future.stdout:
        print('LIVE_PRELIMINARY_SUCCESS: future race ranking generated', flush=True)
        return 0
    print('LIVE_UNAVAILABLE: future race ranking not generated', flush=True)
    if args.mode == 'scheduled':
        print('SCHEDULED_WAIT: no historical fallback on Friday schedule', flush=True)
        return 0 if future.returncode == 0 else future.returncode
    print('MANUAL_FALLBACK: running historical backtest', flush=True)
    if not Path('keiba_historical_backtest.py').is_file():
        raise SystemExit('ERROR: keiba_historical_backtest.py missing')
    backtest = execute('keiba_historical_backtest.py')
    if backtest.returncode == 0 and 'BACKTEST SUCCESS' in backtest.stdout:
        print('HISTORICAL_BACKTEST_SUCCESS: NOT a future prediction', flush=True)
        return 0
    raise SystemExit('HISTORICAL_BACKTEST_FAILED: inspect logs; no ranking confirmed')


if __name__ == '__main__':
    raise SystemExit(main())
