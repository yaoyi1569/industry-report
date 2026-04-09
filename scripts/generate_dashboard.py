import json
import os
import re
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google import genai as google_genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text or '').strip()


def ai_summarize(title, snippet, company, api_key):
    """Generate a Japanese factual news summary using Gemini API.

    Returns a 2-tuple: (is_relevant: bool, summary: str | None).
    Returns (False, None) if Gemini determines the article is off-topic.
    Returns (True, None) if the API is unavailable or the article looks paywalled.
    """
    if not GENAI_AVAILABLE or not api_key:
        return True, None
    # Skip paywall-only articles: snippet is essentially empty or just repeats the title
    clean_snippet = (snippet or '').strip()
    if len(clean_snippet) < 30 or clean_snippet == title.strip():
        print(f'  [SKIP paywall/no-body] {title[:60]}')
        return False, None
    try:
        client = google_genai.Client(api_key=api_key)
        prompt = (
            'あなたは家庭紙・衛生用品業界の専門記者です。\n\n'
            '【ステップ1: 関連性チェック】\n'
            'この記事が「家庭紙・ティッシュ・トイレットペーパー・おむつ・衛生用品・不織布・'
            '吸収体加工機・包装機・パレタイザー」に直接関連する業界ニュースかどうかを判断してください。\n'
            '洗剤・柔軟剤・シャンプー・化粧品・食品・飲料など、家庭紙／衛生用品と無関係な'
            'FMCGニュースであれば「IRRELEVANT」とだけ出力してください。\n\n'
            '【ステップ2: 要約（関連する場合のみ）】\n'
            '業界関連ニュースの場合は、本文スニペットを深く読み込み、'
            '「誰が・いつ・何を・どのように・数値」が明確に伝わる、'
            '業界関係者向けの日本語ニュースサマリーを80〜150字で作成してください。\n'
            'タイトルをそのまま言い換えるだけでなく、本文から得た具体的な数字・背景・意義を'
            '含めた独自の文章にしてください。本文に数値がない場合もタイトル以外の情報を補足して'
            'ください。\n\n'
            '【出力例】\n'
            '「ユニ・チャームは2026年4月1〜3日に普通株式584,800株を取得価額約5.5億円で取得し、'
            '2月12日決議の自己株式取得を完了した。」\n\n'
            f'会社名: {company}\n'
            f'タイトル: {title}\n'
            f'本文スニペット: {clean_snippet}\n\n'
            f'出力（「IRRELEVANT」またはサマリー日本語のみ）:'
        )
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        text = response.text.strip()
        if text.strip().upper() == 'IRRELEVANT':
            print(f'  [AI-IRRELEVANT] {title[:60]}')
            return False, None
        return True, text[:300]
    except Exception as e:
        print(f'  Gemini error for "{title[:40]}...": {e}')
        return True, None


def generate_highlights(items, api_key):
    """Use Gemini to select the 3 most impactful news items and produce strategic insights."""
    if not GENAI_AVAILABLE or not api_key or not items:
        return []
    try:
        # Build a compact news list for the prompt (limit to 40 most recent items)
        candidates = items[:40]
        news_list = '\n'.join(
            f'{i+1}. [{item.get("category_name","")}/{item.get("info_type","")}] '
            f'{item.get("company","不明")} — {item.get("title","")} ({item.get("date","")})'
            for i, item in enumerate(candidates)
        )
        client = google_genai.Client(api_key=api_key)
        prompt = (
            '以下は家庭紙・衛生用品業界の最新ニュース一覧です。\n'
            '大王製紙の技術開発部にとって最もインパクトの大きい3件を選び、'
            '各件について以下のJSON形式で回答してください。\n'
            'JSON以外の文章は一切出力しないでください。\n\n'
            '出力形式:\n'
            '[\n'
            '  {\n'
            '    "rank": 1,\n'
            '    "title": "ニュースタイトル（短く要約）",\n'
            '    "company": "企業名",\n'
            '    "category": "カテゴリー／情報種別",\n'
            '    "date": "YYYY-MM-DD",\n'
            '    "impact": "大王製紙への戦略的インパクトや競合・市場への影響（60〜100字）"\n'
            '  },\n'
            '  ...\n'
            ']\n\n'
            f'ニュース一覧:\n{news_list}\n'
        )
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        text = response.text.strip()
        # Strip markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        highlights = json.loads(text)
        if isinstance(highlights, list):
            return highlights[:3]
        return []
    except Exception as e:
        print(f'  Gemini highlights error: {e}')
        return []


def load_data(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw, None, []
        return raw.get('items', []), raw.get('last_updated'), raw.get('highlights', [])
    return [], None, []


def save_data(path, items, highlights=None, last_updated=None):
    payload = {
        'last_updated': last_updated or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'highlights': highlights or [],
        'items': items,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        print('WARNING: GEMINI_API_KEY not set. Summaries will not be updated.')

    data, last_updated, existing_highlights = load_data(data_path)
    if not data:
        print('No data found. Run fetch_news.py first.')
        return

    updated = 0
    irrelevant_indices = []
    for idx, item in enumerate(data):
        summary = strip_html(item.get('summary', ''))
        # Strip HTML from existing summary if it contained tags
        if item.get('summary', '') != summary:
            item['summary'] = summary

        # Skip if already has a quality AI-generated summary (plain text, >=80 chars)
        if len(summary) >= 80 and '<' not in summary:
            continue

        title = item.get('title', '')
        company = item.get('company', '不明')
        snippet = summary or ''

        is_relevant, new_summary = ai_summarize(title, snippet, company, api_key)
        if not is_relevant:
            # Gemini flagged as off-topic or paywall-only — mark for removal
            irrelevant_indices.append(idx)
            continue
        if new_summary:
            item['summary'] = new_summary
            updated += 1
        elif not summary:
            # Only keep the title fallback if it's not a pure paywall stub
            item['summary'] = title[:200]

    # Remove items flagged as irrelevant by Gemini (in reverse order to preserve indices)
    for idx in sorted(irrelevant_indices, reverse=True):
        removed_title = data[idx].get('title', '')[:60]
        print(f'  Removing irrelevant item: {removed_title}')
        data.pop(idx)

    if irrelevant_indices:
        print(f'Removed {len(irrelevant_indices)} irrelevant/paywall items.')

    # Sort newest first
    data.sort(key=lambda x: x.get('date', ''), reverse=True)

    # Regenerate Top 3 highlights — prefer today's items, fall back to most recent 40
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    today_items = [item for item in data if item.get('date', '') == today]
    source_items = today_items if today_items else data[:40]
    if api_key and source_items:
        print(f'Generating Top 3 highlights from {len(source_items)} items...')
        highlights = generate_highlights(source_items, api_key)
        if not highlights:
            highlights = existing_highlights
    else:
        highlights = existing_highlights

    save_data(data_path, data, highlights=highlights)
    print(f'Updated {updated} summaries. Highlights: {len(highlights)}. Total items: {len(data)}')


if __name__ == '__main__':
    main()
