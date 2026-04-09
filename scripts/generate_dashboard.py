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
    """Generate a Japanese factual news summary using Gemini API."""
    if not GENAI_AVAILABLE or not api_key:
        return None
    try:
        client = google_genai.Client(api_key=api_key)
        prompt = (
            f'あなたは家庭紙・衛生用品業界の専門記者です。\n'
            f'以下のニュース記事について、タイトルに含まれる数値・日付・固有名詞・金額を正確に活用し、'
            f'「誰が・いつ・何を・どのように」が明確に伝わる、業界関係者向けの日本語ニュースサマリーを'
            f'80〜150字で作成してください。\n'
            f'タイトルをそのまま言い換えるだけでなく、具体的な数字・背景・意義を補足した文章にしてください。\n'
            f'【出力例】\n'
            f'「ユニ・チャームは2026年4月1〜3日に普通株式584,800株を取得価額約5.5億円で取得し、'
            f'2月12日決議の自己株式取得を完了した。」\n'
            f'「日本製紙は熊本県八代工場に約310億円を投じ、トイレットペーパー等家庭紙生産ラインを導入。'
            f'2028年2月稼働・年4万トン規模を計画している。」\n\n'
            f'会社名: {company}\n'
            f'タイトル: {title}\n'
            f'スニペット: {snippet}\n\n'
            f'サマリー（日本語のみ、80〜150字）:'
        )
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        return response.text.strip()[:300]
    except Exception as e:
        print(f'  Gemini error for "{title[:40]}...": {e}')
        return None


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
    for item in data:
        summary = strip_html(item.get('summary', ''))
        # Strip HTML from existing summary if it contained tags
        if item.get('summary', '') != summary:
            item['summary'] = summary

        # Skip if already has a quality AI-generated summary (plain text, >=80 chars)
        if len(summary) >= 80 and '<' not in summary:
            continue

        title = item.get('title', '')
        company = item.get('company', '不明')
        snippet = summary or title

        new_summary = ai_summarize(title, snippet, company, api_key)
        if new_summary:
            item['summary'] = new_summary
            updated += 1
        elif not summary:
            item['summary'] = title[:200]

    # Sort newest first
    data.sort(key=lambda x: x.get('date', ''), reverse=True)

    # Regenerate Top 3 highlights from today's most recent items
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    today_items = [item for item in data if item.get('date', '') == today]
    if today_items and api_key:
        print(f'Generating Top 3 highlights from {len(today_items)} today\'s items...')
        highlights = generate_highlights(today_items, api_key)
        if not highlights:
            # Fall back to all recent items if no today items matched
            highlights = generate_highlights(data, api_key)
    else:
        highlights = existing_highlights

    save_data(data_path, data, highlights=highlights)
    print(f'Updated {updated} summaries. Highlights: {len(highlights)}. Total items: {len(data)}')


if __name__ == '__main__':
    main()
