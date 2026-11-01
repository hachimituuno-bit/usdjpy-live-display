"""Encrypt private story plans before keeping state in a public GitHub repository.

The encryption key is stored ONLY as the GitHub Actions secret CONTENT_STATE_KEY.
Never commit the key or an unencrypted novel state file.
"""
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

DATA = Path(__file__).resolve().parent / 'new_daily_content_state.json.enc'


def cipher():
    key = os.getenv('CONTENT_STATE_KEY', '').strip()
    if not key:
        raise RuntimeError('GitHub Secret CONTENT_STATE_KEY が未設定。小説の未来の筋書きは公開リポジトリに平文保存しません。')
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, TypeError) as e:
        raise RuntimeError('CONTENT_STATE_KEYの書式が不正です') from e


def load():
    if not DATA.is_file():
        raise RuntimeError('暗号化した連載状態ファイルが見つかりません。初期状態を復元するまで投稿を停止します。')
    try:
        return json.loads(cipher().decrypt(DATA.read_bytes()))
    except InvalidToken as e:
        raise RuntimeError('連載状態を復号できません。CONTENT_STATE_KEYが初期設定と同じか確認してください。') from e


def save(state):
    tmp = DATA.with_suffix(DATA.suffix + '.tmp')
    tmp.write_bytes(cipher().encrypt(json.dumps(state, ensure_ascii=False, indent=2).encode('utf-8')))
    tmp.replace(DATA)
