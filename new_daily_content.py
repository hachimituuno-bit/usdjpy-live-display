"""Trivia and original 30-episode serialized fiction for Mail2Blogger.
Standalone in usdjpy-live-display: no imports or state from the old repository.
The full story plan remains encrypted because this repository is public.
"""
import html
import json
import os
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from new_ai_sideincome import gemini, clean_article
from new_mail_gateway import publish as mail_publish, is_preview
from new_encrypted_state import load as decrypt_state, save as encrypt_state

JST = timezone(timedelta(hours=9))
GENRES = [
    'ミステリー・サスペンス', 'SF・タイムリープ', 'ホラー・都市伝説',
    '青春・友情', 'ファンタジー・冒険', '恋愛・感動',
    '歴史風フィクション', 'コメディ・日常の謎',
]
# Known introductory science/history topics. The generated article must not invent
# study results, dates, numerical figures, references or personal experiences.
TRIVIA = [
    ('宇宙', '金星では自転の周期が公転の周期より長い', 'NASAの惑星解説で確認できる基本事実'),
    ('生物', 'タコには心臓が3つある', '海洋生物の基礎知識'),
    ('地理', '南極大陸は非常に乾燥した砂漠でもある', '降水量の定義を説明する'),
    ('言葉', '「アルゴリズム」という語は数学者の名前に由来する', '語源の説明に集中する'),
    ('動物', 'カモノハシは卵を産む哺乳類である', '単孔類の特徴を説明する'),
    ('宇宙', '月は常にほぼ同じ面を地球に向けている', '潮汐固定を易しく説明する'),
    ('科学', '水は凍ると多くの液体と違って体積が増える', '氷の分子構造の説明'),
    ('地球', '地球の大気の主成分は窒素である', '割合を断定するなら検証を要求'),
    ('生物', 'キノコは植物ではなく菌類に分類される', '光合成との違いを説明する'),
    ('歴史', '「曜日」は天体の名前と結びついている', '文化圏で呼び方が異なる点も説明'),
    ('食べ物', 'バナナの果実は植物学ではベリーに分類される', '日常語と植物学の定義の違い'),
    ('動物', 'コウモリは自力で羽ばたいて飛べる哺乳類である', '滑空との違いを説明する'),
    ('科学', '音は真空中を伝わらない', '音の伝わり方を説明する'),
    ('宇宙', '太陽の光が地球へ届くには時間がかかる', '光速の基本を説明する'),
    ('地理', '海底にも山脈がある', '中央海嶺の例を説明する'),
    ('生物', 'サンゴは植物ではなく動物である', '共生藻類と区別して説明する'),
    ('科学', '雷が光ってから音が聞こえるまでには差がある', '光と音の速さの違いを説明する'),
    ('言葉', 'ロボットという語は戯曲を通じて広まった', '語源を厳密に扱う'),
    ('地球', '地球の自転軸は公転面に対して傾いている', '季節が生じる理由を説明する'),
    ('科学', '虹は見る人の位置によって見え方が変わる', '光の屈折と反射を説明する'),
    ('動物', '鳥類は現生の恐竜の一系統である', '進化分類学の見方を説明する'),
    ('生活', '電子レンジは食品中の水分子などに作用する', '誤解を避けて誘電加熱を説明する'),
    ('科学', '磁石を半分に切っても通常はN極とS極ができる', '磁区の説明を易しく行う'),
    ('生物', 'ヒトの骨は生きた組織で絶えず作り替えられる', '骨リモデリングを説明する'),
    ('宇宙', '木星の大赤斑は巨大な嵐である', '観測や規模の数値は慎重に扱う'),
    ('食べ物', 'ハチミツは保存条件によって結晶化する', '腐敗と結晶化を混同しない'),
    ('海', '深海には日光が届かない領域がある', '深さの断定を避けて説明する'),
    ('歴史', '古代ローマでは現代と異なる暦が使われた時期がある', '暦改革の経緯を説明する'),
    ('科学', 'ガラスは通常の室温では固体として振る舞う', '流れる液体という俗説を扱う'),
    ('生物', '植物の根も呼吸している', '水はけと酸素の関係を説明する'),
]


def load_state():
    return decrypt_state()


def save_state(state):
    if is_preview():
        print('PREVIEW: 雑学・連載の暗号化状態を変更しません')
        return
    encrypt_state(state)


def sync_story_with_public_blogger(state):
    """Fail closed if latest published episode cannot be checked.

    Old state snapshot is from Oct 30. Read the public feed and incorporate any
    newer episodes before writing the next one. Never assume the last episode.
    """
    import requests
    from bs4 import BeautifulSoup
    series = state.get('series')
    if not series:
        return
    expected = int(state.get('episode', 1)) - 1
    if expected < 1:
        return
    base = 'https://hachinosuke7.blogspot.com'
    try:
        r = requests.get(base + '/feeds/posts/default',
                         params={'alt':'json', 'max-results':150}, timeout=30)
        r.raise_for_status()
        entries = r.json().get('feed', {}).get('entry', [])
    except Exception as ex:
        raise RuntimeError('公開Bloggerフィードの確認に失敗しました。安全のため小説を送信しません。' + type(ex).__name__) from None
    prefix = '【' + str(series['series_title']) + '】'
    matched = {}
    for e in entries:
        title = e.get('title', {}).get('$t', '')
        if not title.startswith(prefix):
            continue
        m = re.search(r'第\s*(\d+)\s*話', title)
        if not m:
            continue
        n = int(m.group(1))
        if not 1 <= n <= 30:
            continue
        urls = [link.get('href','') for link in e.get('link', []) if link.get('rel') == 'alternate']
        raw = e.get('content', {}).get('$t', '')
        plain = BeautifulSoup(raw, 'html.parser').get_text(' ', strip=True)
        published = e.get('published', {}).get('$t', '')
        try:
            day = datetime.fromisoformat(published.replace('Z','+00:00')).astimezone(JST).date().isoformat()
        except ValueError:
            day = ''
        if n in matched:
            raise RuntimeError('公開ブログに同じ話数が複数あります。自動投稿を停止します。')
        matched[n] = {'title':title,'body':plain,'url':urls[0] if urls else '', 'day':day}
    if expected not in matched:
        raise RuntimeError(f'第{expected}話の公開が確認できません。前回のメール不達またはフィード制限の可能性があるので、小説を送信しません。')
    latest = max(matched)
    if latest < expected:
        raise RuntimeError('公開話数が保存した進行状況より古いので送信しません')
    if latest == expected:
        series['previous_url'] = matched[expected]['url'] or series.get('previous_url','')
        print(f'公開済み第{expected}話を確認。次は第{expected+1}話')
        return
    if latest > 30:
        raise RuntimeError('公開話数が30を超えています。手動点検が必要です')
    print(f'旧方式の続き {expected+1}〜{latest} 話を公開ブログから同期します')
    for n in range(expected + 1, latest + 1):
        item = matched.get(n)
        if not item or len(item['body']) < 120:
            raise RuntimeError(f'第{n}話の全文が取得できません。省略・二重投稿を避けて停止します。')
        prompt = f"""小説の継続用あらすじをJSONで作り直す。読者公開済みの本文以外の新事実を捏造しない。
現在のあらすじ：{series.get('recap','')}
公開済みの第{n}話（全文）：{item['body'][:15000]}
次回の話を書くために必要な設定・時系列・人物の行動・新事実を日本語200〜450字でまとめる。
{{"recap":"..."}} のJSONのみ返す。"""
        updated = gemini(prompt)
        memo = str(updated.get('recap','')) if isinstance(updated, dict) else ''
        if len(memo) < 100:
            raise RuntimeError(f'第{n}話の継続メモを作れないため送信しません。')
        series['recap'] = memo
        series['previous_url'] = item['url']
        state['episode'] = n + 1
        if item['day']:
            state['last_story_date'] = item['day']
        print(f'第{n}話の公開内容を連載状態に反映')
    if state['episode'] > 30:
        raise RuntimeError('第30話まで旧方式で投稿済みです。新シリーズ開始は手動確認が必要です。')
    save_state(state)


def ensure_valid_html(generated, min_chars=200, story=False):
    # main.clean_article() requires a 12–90 character article title.
    # A story subtitle is intentionally shorter; the full published title
    # (series name + episode number + subtitle) is added later.
    subtitle = str(generated.get('title', '')).strip() if isinstance(generated, dict) else ''
    if story and not (2 <= len(subtitle) <= 90):
        raise RuntimeError('小説の副題が空、短すぎる、または長すぎます')

    candidate = generated
    if story:
        candidate = dict(generated)
        candidate['title'] = '連載小説の本文品質確認用タイトル'

    cleaned = clean_article(candidate)
    if not cleaned:
        raise RuntimeError('記事のタイトルまたは本文が品質基準を満たしていません')

    title, body = cleaned
    if len(re.sub(r'<[^>]+>', '', body).strip()) < min_chars:
        raise RuntimeError('本文が短すぎるため投稿を中止')

    return (subtitle if story else title), body



def post(title, content, label):
    post_data, draft = mail_publish(title, content, label, is_draft=False)
    if not post_data.get('id'):
        raise RuntimeError('投稿IDを受け取れないため状態を更新しません')
    print(('Preview' if is_preview() else 'SMTP accepted (publication unconfirmed)') + ': ' + str(post_data.get('id'))) 
    return post_data


def publish_trivia(state, today):
    if state.get('last_trivia_date') == today and not is_preview():
        print('今日は雑学を投稿済み。重複を回避します。')
        return False
    position = int(state.get('trivia_count', 0)) % len(TRIVIA)
    category, fact, guidance = TRIVIA[position]
    prompt = f'''日本語の知的エンタメ記事を1本作る。読者は友達に話したくなる意外な雑学が好き。
確認すべき中心事実：{fact}
分野：{category}
説明の留意点：{guidance}

条件：
- タイトルは「え、そうなの？」と思わせる具体的なもの。ただし誇張・嘘は禁止。
- まず正しい答えを明かし、なぜそうなのかを初学者に分かる言葉で解説する。
- 小ネタと会話で使える一言を付ける。本文は650〜950字目安。
- 根拠を確認していない数値、研究者名、年号、実験や体験談、出典URLを捏造しない。
- 固有の専門情報に自信がなければ、断定を避けて基本事実の解説に限定する。
- AIで稼ぐ方法は書かない。これは純粋な雑学コーナー。
- HTMLにはh2,h3,p,ul,ol,li,strong,emだけを使う。
- JSONだけ返す: {{"title":"...","html":"<p>...</p>"}}
'''
    title, content = ensure_valid_html(gemini(prompt))
    title = '【人に話したくなる雑学】' + title
    content += '<p>※内容は一般向けの解説です。数値や最新研究は公式資料でご確認ください。</p>'
    created = post(title, content, '今日の雑学')
    state['trivia_count'] = int(state.get('trivia_count', 0)) + 1
    state['last_trivia_date'] = today
    state.setdefault('published', []).append({'kind': 'trivia', 'date': today, 'post_id': str(created['id']), 'title': title})
    state['published'] = state['published'][-120:]
    save_state(state)
    return True


def new_series(number):
    genre = GENRES[(number - 1) % len(GENRES)]
    prompt = f'''オリジナル連載小説の「全30話の設計図」を作成してください。本文はまだ書かない。
シリーズ番号：{number}、ジャンル：{genre}
主な読者：日本語のWeb連載を読む人。
必須条件：
- 全30話で完結し、途中で設定が矛盾しない。第1話ですぐに事件が起きる。
- 各話に新展開と明確な目的があり、最終話で伏線を回収する。
- 既存作品・有名キャラクターの模倣をしない。架空の人物と場所を使用する。
- 怖い・暗いジャンルでも過度に残酷な描写は避ける。
- 30話それぞれの「今回起こること」を一文で計画する。
出力はJSONだけ：
{{"series_title":"タイトル","premise":"全体の設定150字程度","characters":"主要人物・関係・秘密","secret":"最終的な真相と伏線回収","outlines":["第1話の展開", ... 全30要素]}}
'''
    for _ in range(2):
        result = gemini(prompt)
        if isinstance(result, dict) and isinstance(result.get('outlines'), list) and len(result['outlines']) == 30:
            if all(str(result.get(k, '')).strip() for k in ('series_title', 'premise', 'characters', 'secret')):
                return {'genre': genre, 'series_title': result['series_title'], 'premise': result['premise'],
                        'characters': result['characters'], 'secret': result['secret'], 'outlines': result['outlines'],
                        'recap': '', 'previous_url': ''}
    raise RuntimeError('全30話の構成案を作れなかったため投稿しません')


def publish_story(state, today):
    sync_story_with_public_blogger(state)
    if state.get('last_story_date') == today and not is_preview():
        print('今日は小説を投稿済み。重複を回避します。')
        return False
    number = int(state.get('series_number', 1))
    episode = int(state.get('episode', 1))
    if not 1 <= episode <= 30:
        raise RuntimeError('話数に異常があります。投稿を停止')
    series = state.get('series') or new_series(number)
    outline = str(series['outlines'][episode - 1])
    previous = series.get('recap', '')
    final = (episode == 30)
    prompt = f'''あなたは連載エンタメ小説の作家。次の30話構成に厳密に沿って、今夜の1話だけ執筆する。
シリーズタイトル：{series['series_title']}
ジャンル：{series['genre']}
全体設定：{series['premise']}
人物と秘密：{series['characters']}
【作者だけが知る真相。本文で安易に全公開しない】{series['secret']}
全30話の構成：{json.dumps(series['outlines'], ensure_ascii=False)}
前回までのあらすじ：{previous or '第1話。まだなし。'}
今回：第{episode}話
今回必ず起こること：{outline}

条件：
- 本文は約1000〜1500字。場面、行動、会話、具体的描写を重視して、説明だけで終わらせない。
- 冒頭から引き込む。人物・時間・事件・伏線に一貫性を保つ。
- 第1〜29話は今夜の小さな進展と、次が気になる引きを両立する。
- 第30話は未解決の重大な謎を解き、必ず結末まで描く。次回へ引き延ばさない。
- タイトルにシリーズ名・話数を書かない（外側のプログラムが付ける）。
- JSONの"recap"は、次回の執筆に必要な新事実、手掛かり、人物関係を200〜350字で記録する。前回recapに今回の出来事を足し、整合性を保つ。
- 完全にオリジナル。既存の作品の文章や人物をコピーしない。
- HTMLはh2,h3,p,ul,ol,li,strong,emのみ。
JSONのみ返す：{{"title":"今回の副題","html":"<p>...</p>","recap":"継続用あらすじ"}}
'''
    
    for attempt in range(3):
        try:
            result = gemini(prompt)

            if not isinstance(result, dict):
                raise ValueError('AIの返答形式が不正です')

            if len(str(result.get('recap', '')).strip()) < 30:
                raise ValueError('物語の継続用メモが短すぎます')

            subtitle, content = ensure_valid_html(
                result, min_chars=250, story=True
            )

            print('小説の生成に成功しました')
            break

        except (ValueError, RuntimeError) as e:
            print(f'小説の再生成 {attempt + 1}/3: {e}')

            if attempt == 2:
                raise RuntimeError(
                    '3回生成しても品質基準を満たさなかったため投稿を中止しました'
                ) from e

    series_title = str(series['series_title'])
    title = f'【{series_title}】第{episode}話「{subtitle}」'
    if episode > 1 and series.get('previous_url'):
        safe = html.escape(series['previous_url'], quote=True)
        content = f'<p><a href="{safe}">前回（第{episode-1}話）を読む</a></p>' + content
    if final:
        content += '<p><strong>―― 全30話・完 ――</strong></p>'
    else:
        content += f'<p><strong>次回：第{episode+1}話へ続く</strong></p>'
    created = post(title, content, ['連載小説', f'連載小説・{series["genre"]}'])
    state['last_story_date'] = today
    state.setdefault('published', []).append({'kind': 'story', 'date': today, 'post_id': str(created['id']),
                                               'series': number, 'episode': episode, 'title': title})
    state['published'] = state['published'][-120:]
    if final:
        state['series_number'] = number + 1
        state['episode'] = 1
        state['series'] = None
    else:
        series['recap'] = str(result['recap'])
        series['previous_url'] = created.get('url', '')
        state['episode'] = episode + 1
        state['series'] = series
    save_state(state)
    return True


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('trivia', 'story'):
        raise SystemExit('Usage: python new_daily_content.py trivia|story')
    for k in ('GEMINI_API_KEY',):
        if not os.environ.get(k):
            raise RuntimeError('Missing GitHub secret: ' + k)
    mode = sys.argv[1]
    today = datetime.now(JST).date().isoformat()
    state = load_state()
    if mode == 'trivia':
        publish_trivia(state, today)
    else:
        publish_story(state, today)


if __name__ == '__main__':
    main()
