"""Friday schedule: future-race ranking only.
Manual run: future-race ranking followed by an independent historical backtest.
No publishing or betting actions are performed by this dispatcher.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def execute(script):
    result = subprocess.run(
        [sys.executable, script],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout, end='', flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end='', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('scheduled', 'manual'), required=True)
    args = parser.parse_args()
    print(f'MODE: {args.mode}', flush=True)

    if not Path('keiba_rolling_run.py').is_file():
        raise SystemExit('ERROR: keiba_rolling_run.py missing')

    future = execute('keiba_rolling_run.py')
    future_success = (
        future.returncode == 0
        and 'SUCCESS PRELIMINARY' in future.stdout
    )
    if future_success:
        print('LIVE_PRELIMINARY_SUCCESS: future race ranking generated', flush=True)
    else:
        print('LIVE_UNAVAILABLE: future race ranking not generated', flush=True)

    if args.mode == 'scheduled':
        print('SCHEDULED_WAIT: historical backtest not run', flush=True)
        return 0 if future.returncode == 0 else future.returncode

    print('MANUAL_BACKTEST: running historical backtest independently', flush=True)
    if not Path('keiba_historical_backtest.py').is_file():
        raise SystemExit('ERROR: keiba_historical_backtest.py missing')

    backtest = execute('keiba_historical_backtest.py')
    backtest_success = (
        backtest.returncode == 0
        and 'BACKTEST SUCCESS' in backtest.stdout
    )
    if backtest_success:
        print('HISTORICAL_BACKTEST_SUCCESS: NOT a future prediction', flush=True)
    else:
        print('HISTORICAL_BACKTEST_FAILED: inspect logs', flush=True)

    if future.returncode != 0 or not backtest_success:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
