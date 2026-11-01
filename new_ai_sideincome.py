import os, json, re, hashlib, html, sys, time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(__file__)
CFG = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
STATE = os.path.join(ROOT, 'new_ai_state.json')
TIMEOUT = 25
JST = timezone(timedelta(hours=9))

MONEY_TOPICS = [
    ('AI画像・デザイン', '地域の飲食店向けにメニュー画像や店頭ポスターを制作する仕事'),
    ('AI動画制作', 'ショート動画の台本と字幕づくりを請け負う仕事'),
    ('AI音声・ナレーション', '説明動画のナレーション原稿を制作する仕事'),
    ('AI翻訳・ローカライズ', '小規模店舗の外国語メニューを整える仕事'),
    ('AI教材・教育', '塾や家庭教師向けの練習問題を作る仕事'),
    ('AI資料・スライド', '個人事業主の営業資料を整える仕事'),
    ('AI表計算・事務', '店舗の売上管理表や業務テンプレートを整える仕事'),
    ('AI求人・採用', '小規模企業の求人票と採用FAQを整える仕事'),
    ('AIカスタマー対応', 'ネットショップ向けの問い合わせ回答集を作る仕事'),
    ('AIネットショップ', '商品ページの比較表やサイズガイドを整える仕事'),
    ('AI写真・画像補助', '物販写真の背景処理や商品画像構成を提案する仕事'),
    ('AIブログ編集', '個人事業主の既存記事を読みやすく再編集する仕事'),
    ('AIニュースレター', '専門店のメールニュースレターを制作する仕事'),
    ('AI不動産・住宅', '不動産会社の物件紹介文を整える仕事'),
    ('AI旅行・観光', '観光施設の多言語案内文を整える仕事'),
    ('AIイベント運営', 'イベント告知と参加者案内の文章を作る仕事'),
    ('AIポッドキャスト', '配信の構成案と概要欄を作る仕事'),
    ('AIリサーチ', '業界動向を公開資料から整理する調査補助の仕事'),
    ('AIプレゼン支援', 'ウェビナーの台本・要約資料を制作する仕事'),
    ('AI業務自動化', 'ノーコードで小規模店舗の定型業務を効率化する仕事'),
    ('AIクリエイター支援', '作家や講師の作品紹介ページを整える仕事'),
    ('AIデジタル商品', '配布用チェックリストやテンプレートを作って販売する方法'),
    ('AIコミュニティ運営', 'オンラインサロンの要約やFAQを整える仕事'),
    ('AI飲食店支援', '季節限定メニューの紹介文とSNS投稿案を作る仕事'),
    ('AI医療・福祉広報', '医療行為の助言を避けた施設案内文を整える仕事'),
    ('AI学習支援', '資格学習の復習カードを制作する仕事'),
    ('AIハンドメイド販売', '作品の説明文と販売ページの構成を整える仕事'),
    ('AIスポーツ・趣味', '教室・サークルの告知文と案内資料を作る仕事'),
    ('AIシニア向け支援', '地域講座の分かりやすい配布資料を制作する仕事'),
    ('AI法人研修', '社内向けAI入門マニュアルを制作する仕事'),
]


def fetch_candidates():
    import feedparser
    candidates = []
    for topic in CFG.get('topics', []):
        for query in topic.get('queries', []):
            url = 'https://news.google.com/rss/search?q=' + quote(query + ' when:7d') + '&hl=ja&gl=JP&ceid=JP:ja'
            try:
                response = requests.get(url, timeout=TIMEOUT, headers={'User-Agent': 'AutoBlogResearch/1.0'})
                response.raise_for_status()
                feed = feedparser.parse(response.content)
                for item in feed.entries[:8]:
                    title = re.sub(r'\s+-\s+[^-]+$', '', item.get('title', '')).strip()
                    link = item.get('link', '')
                    if not title or not link:
                        continue
                    published = item.get('published_parsed')
                    age = (time.time() - __import__('calendar').timegm(published)) / 86400 if published else 7
                    candidates.append({
                        'title': title, 'url': link, 'topic': topic['name'],
                        'score': topic.get('commercial_score', 0) * 10 + max(0, 7 - age),
                        'source': item.get('source', {}).get('title', '不明')
                    })
            except Exception as e:
                print('RSS error:', str(e)[:150])
    return candidates


def cluster_candidates(items, seen):
    groups = {}
    for item in items:
        normalized = re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]', '', item['title'].lower())
        if len(normalized) < 12:
            continue
        key = hashlib.sha256(normalized.encode()).hexdigest()
        if key in seen:
            continue
        groups.setdefault(item['topic'], []).append((key, item))
    return sorted(groups.items(), key=lambda x: max(y[1]['score'] for y in x[1]), reverse=True)


def gemini(prompt):
    key = os.environ['GEMINI_API_KEY']
    model = CFG['gemini_model']
    endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    import time

    for attempt in range(3):
        try:
            r = requests.post(
                endpoint,
                headers={'x-goog-api-key': key},
                json={
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {
                        'temperature': 0.65,
                        'responseMimeType': 'application/json'
                    }
                },
                timeout=90
            )
            r.raise_for_status()
            break

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError
        ) as e:
            status = (
                e.response.status_code
                if isinstance(e, requests.exceptions.HTTPError)
                and e.response is not None
                else None
            )

            if status is not None and status not in (429, 500, 502, 503, 504):
                raise

            if attempt == 2:
                raise

            print(f'Gemini API一時エラー。20秒後に再試行します（{attempt + 1}/3）')
            time.sleep(20)
    data = r.json()
    raw = ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts'])
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(raw):
            if ch == '{':
                try:
                    result, _ = decoder.raw_decode(raw[i:])
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    continue
        raise ValueError('Geminiの返答からJSONを読み取れませんでした')

STYLE = '''あなたは「AIを使って収入を増やしたい・出費を減らしたい」初心者向けの人気ブログ編集者です。
テーマはAI副業、AIを使った仕事、節約、収益化の現実的な方法。
記事は日本語で本文700〜1000文字程度を目安にする。結論を最初に書き、短い段落と小見出しを使う。読者が検索する疑問に具体的に答え、必要な準備、実践手順、費用、注意点を説明する。内容の薄い文章や同じ説明の繰り返しは禁止する。未確認の情報を事実として書かない。
文章は親しみやすく、テンポよく、軽いツッコミやユーモアを1〜2回入れる。堅苦しい前置きは禁止。
「何をする？」「最初の一歩」「お金や時間の注意点」がすぐ分かるようにする。
必ず具体的な行動を2〜3個示す。初心者でも理解できる言葉を使う。
実際に稼げる金額や成果を保証しない。未確認の収益額、架空の体験談、実績、価格、統計、口コミ、出典を捏造しない。
投資・税務・法律の個別助言をしない。AIだけで放置して稼げるなどと誤解させない。
タイトルは検索する人の疑問に答える具体的なものにする。「AI副業」「副収入」「節約」など記事内容に合う検索語を自然に含める。初心者向けの手順や注意点など、記事で実際に説明する内容が分かるタイトルにする。誇張や根拠のない数字は禁止。HTMLはh2、h3、p、ul、ol、li、strong、emだけで書く。
JSONのみで {"title":"記事タイトル", "html":"<h2>...</h2><p>...</p>"} を返す。'''


def clean_article(result):
    title = str(result.get('title', '')).strip()
    soup = BeautifulSoup(str(result.get('html', '')), 'html.parser')
    for tag in soup.find_all(['script', 'style', 'iframe', 'form', 'img', 'a']):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.name not in {'p', 'h2', 'h3', 'ul', 'ol', 'li', 'strong', 'em'}:
            tag.unwrap()
        else:
            tag.attrs = {}
    text = soup.get_text(' ', strip=True)
    if not (12 <= len(title) <= 90 and 250 <= len(text) <= 2000):
        print('Rejected: article length or title out of range')
        return None
    if any(x in text for x in ['絶対に儲かる', '確実に稼げる', '実際に使ってみた', '誰でも必ず稼げる']):
        print('Rejected: unsupported earnings or experience claims')
        return None
    # Inline styles survive Blogger's post body independently of the selected theme.
    for tag in soup.find_all(True):
        if tag.name == 'p' or tag.name == 'li':
            tag['style'] = 'font-size:20px;line-height:1.9;margin:0 0 18px;'
        elif tag.name == 'h2':
            tag['style'] = 'font-size:28px;line-height:1.5;margin:28px 0 14px;font-weight:700;'
        elif tag.name == 'h3':
            tag['style'] = 'font-size:24px;line-height:1.5;margin:24px 0 12px;font-weight:700;'
    return title, str(soup)


def choose_and_write(groups):
    # Money-focused evergreen articles take priority. News is only used when relevant.
    money_words = ('副業', '稼', '収益', '節約', 'お金', '仕事', '経費', '費用', '料金')
    for topic, pairs in groups:
        relevant = [(k, i) for k, i in pairs if any(w in i['title'] for w in money_words)]
        unique = []
        publishers = set()
        for key, item in relevant:
            if item['source'] in publishers or item['source'] == '不明':
                continue
            publishers.add(item['source'])
            unique.append((key, item))
            if len(unique) == 6:
                break
        if len(unique) < CFG.get('min_sources', 2):
            continue
        sources = [p[1] for p in unique]
        prompt = STYLE + '''\n以下はニュースの「見出し」だけです。本文は読んでいません。
複数の別ニュースを同一事実の裏付けとして扱わない。見出しから詳細を推測して断定しない。
記事の中心はニュースの要約ではなく、読者がAIで収入を増やしたり出費を減らすための実用的な行動。
出典が未検証であることを踏まえ、具体的なニュース内容を断定せず一般的な方法を紹介する。
ジャンル: ''' + topic + '\n見出し:\n' + json.dumps(sources, ensure_ascii=False)
        cleaned = clean_article(gemini(prompt))
        if not cleaned:
            continue
        title, body = cleaned
        refs = '<h2 style="font-size:24px;">参考にしたニュース見出し</h2><ul>' + ''.join(
            '<li style="font-size:16px;line-height:1.7;"><a href="' + html.escape(s['url'], quote=True) +
            '" rel="nofollow noopener noreferrer">' + html.escape(s['title']) + '</a>（' +
            html.escape(s['source']) + '）</li>' for s in sources) + '</ul>'
        note = '<p style="font-size:15px;line-height:1.7;">※AI作成記事です。ニュース本文や最新の収益情報を検証したものではありません。収入は保証されません。</p>'
        return title, body + refs + note, [p[0] for p in unique], 'AI副業・収益化'
    return None


def write_backup_article(state):
    # Rotate practical side-job niches rather than generic AI advice.
    used = set(state.get('backup_used', []))
    recent_categories = state.get('category_history', [])[-8:]
    recent_titles = state.get('article_history', [])[-80:]

    available = [(category, angle) for category, angle in MONEY_TOPICS
                 if category + '|' + angle not in used]
    if not available:
        # After one full rotation, start again; the recent-category guard still applies.
        available = MONEY_TOPICS[:]
        used = set()
        state['backup_used'] = []

    # Prefer a niche not used in the most recent eight articles.
    available.sort(key=lambda item: (
        item[0] in recent_categories,
        recent_categories[::-1].index(item[0]) if item[0] in recent_categories else 999,
        MONEY_TOPICS.index(item)
    ))
    category, angle = available[0]
    recent_titles_excerpt = '\n'.join('- ' + str(title)[:100] for title in recent_titles[-20:]) or '（まだ記録なし）'

    prompt = STYLE + f'''\n【今回の記事は今までとは別ジャンルにする】
今回の分野：{category}
今回の具体的な仕事：{angle}

最近公開した記事のタイトル（これらと切り口・仕事・タイトルを重複させない）：
{recent_titles_excerpt}

【記事の構成】
・記事タイトルは、上記の具体的な仕事が一目で分かるものにする。「月5,000円」を無理に入れない。
・冒頭で、誰のどんな困りごとを解決する仕事かを具体的に説明する。
・対象顧客、実際の納品物、完成までの手順、AIと人間の役割を示す。
・初案件の探し方、納品前の確認項目、想定される失敗と対策を具体的に書く。
・料金や収入を断定せず、見積もりをどう決めるかを説明する。
・読者が試せる「最初の30分の作業」を1つ入れる。
・抽象的な「AIを活用しよう」「スキルを磨こう」の繰り返しを避ける。
・過去に人気だった記事・成功事例を実際に参照できていない場合は、実例を見たふりや引用の捏造をしない。
・架空の成功者、売上実績、需要統計、顧客の声、体験談を実話として書かない。
・紹介する作業に法令、著作権、個人情報、AI生成物の品質確認が関わる場合は注意を説明する。
・本文は700〜1000文字程度、自然な日本語。似た小見出しや空疎な前置きを避ける。
・STYLEに指定したJSON形式のみ返す。
'''
    cleaned = clean_article(gemini(prompt))
    if not cleaned:
        return None
    title, body = cleaned
    # Do not accept an identical or almost identical normalized title.
    normalize = lambda t: re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff]', '', t.lower())
    if normalize(title) in {normalize(str(t)) for t in recent_titles}:
        print('Rejected: duplicate article title')
        return None
    note = '<p style="font-size:15px;line-height:1.7;">※AIによる一般的な解説です。収入・受注を保証するものではありません。最新の規約や利用条件は公式情報で確認してください。</p>'
    return title, body + note, [], category, category + '|' + angle



def blogger_post(title, body, topic):
    from new_mail_gateway import publish
    return publish(title, body, topic, is_draft=False)



def add_related_articles(body, topic):
    try:
        blog_id = os.environ["BLOG_ID"]
        url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts"

        response = requests.get(
            url,
            params={"status": "live", "maxResults": 50},
            timeout=TIMEOUT
        )
        response.raise_for_status()

        posts = response.json().get("items", [])
        related = [
            post for post in posts
            if topic in post.get("labels", [])
            and post.get("url", "").startswith("https://")
        ][:3]

        if not related:
            return body

        links = '<h2 style="font-size:26px;">関連記事</h2><ul>'
        for post in related:
            links += (
                '<li style="font-size:20px;line-height:1.9;">'
                '<a href="' + html.escape(post["url"], quote=True) + '">'
                + html.escape(post.get("title", "関連記事"))
                + '</a></li>'
            )
        links += '</ul>'

        return body + links

    except Exception as e:
        print("関連記事取得エラー:", e)
        return body
        

def main():
    
    if not os.getenv('GEMINI_API_KEY'):
        sys.exit('Missing GitHub Secret: GEMINI_API_KEY')

    source_state = STATE if os.path.exists(STATE) else os.path.join(ROOT, 'state.json')
    state = json.load(open(source_state, encoding='utf-8')) if os.path.exists(source_state) else {'seen': []}
    state.setdefault('seen', [])
    state.setdefault('backup_used', [])
    state.setdefault('category_history', [])
    state.setdefault('article_history', [])
    # Prevent a second successful post on the same Japan calendar day.
    
    today = datetime.now(JST).date().isoformat()
    from new_mail_gateway import is_preview
    if state.get('last_post_date') == today and not is_preview():
        print('本日のAI副業記事は既に送信済み。再投稿しません。')
        return

    # Focus on one distinct side-income niche per article. No fragile RSS dependency.
    backup = write_backup_article(state)
    if not backup:
        print('No article passed quality checks; no post created')
        return
    title, body, keys, topic, backup_key = backup
    print('Selected AI side-income niche: ' + topic)

    # No BLOG_ID/OAuth required for the email-based version.
    # Related-post API links are not fetched; original article text is preserved.
    post, draft = blogger_post(title, body, [topic, 'AI副収入'])
    state['last_post_date'] = today
    from new_mail_gateway import is_preview
    print(('Preview' if is_preview() else 'SMTP accepted (Blogger publication unconfirmed)') + ': ' + str(post.get('id')))
    state['seen'] = (state['seen'] + keys)[-2000:]
    if backup_key:
        state['backup_used'].append(backup_key)
    state['category_history'] = (state['category_history'] + [topic])[-30:]
    state['article_history'] = (state['article_history'] + [title])[-80:]
    
    from new_mail_gateway import is_preview
    if not is_preview():
        with open(STATE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    else:
        print('PREVIEW: AI記事の履歴を変更しません')


if __name__ == '__main__':
    main()
