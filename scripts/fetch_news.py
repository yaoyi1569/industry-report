import re
import requests
from datetime import datetime, timedelta, timezone
import json
import os

try:
    import feedparser
    _feedparser_available = True
except ImportError:
    _feedparser_available = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Search queries for the industry — scoped tightly to tissue/hygiene sector
SEARCH_QUERIES = [
    'ユニ・チャーム ティシュー OR おむつ OR 衛生用品 OR 決算 OR 投資',
    '花王 ティシュー OR 家庭紙 OR 衛生用品 OR 研究開発 OR 投資',
    'P&G Japan ティシュー OR おむつ OR 衛生用品',
    'ライオン トイレット OR 衛生用品 OR 新製品 OR 投資',
    '大王製紙 OR 王子ホールディングス OR 日本製紙 家庭紙 OR トイレット OR 業界',
    '瑞光 Zuiko 加工機 OR 設備 不織布',
    'GDM Fameccanica 吸収体 加工機',
    'OPTIMA packaging 包装機 衛生',
    'ファナック FANUC パレタイザー 衛生 OR 包装',
    'Essity Kimberly-Clark ティシュー OR 衛生用品',
    'ウェットティッシュ Winner Medical 稳健医療',
    '家庭紙 トイレットペーパー 業界 規制 OR 値上げ',
    '丸富製紙 OR カミ商事 家庭紙 OR ティシュー',
]

# Core tissue/hygiene terms — at least one must appear in title+snippet
TISSUE_CORE_TERMS = [
    '家庭紙', 'ティシュー', 'ティッシュ', 'トイレット', 'ちり紙', 'キッチンペーパー',
    'おむつ', 'オムツ', 'ナプキン', '生理用', '失禁', '衛生用品', '衛生用紙',
    'ウェットティシュ', 'ウェットティッシュ', '不織布', '吸収体', 'パルプ',
    '抽紙', '衛生紙',
]

# Industry-specific companies (presence alone qualifies the article)
TISSUE_INDUSTRY_COMPANIES = [
    'ユニ・チャーム', 'unicharm',
    '大王製紙', '王子製紙', '王子ホールディングス', '日本製紙', '丸富製紙',
    '瑞光', 'zuiko', 'gdm', 'fameccanica',
    'winner medical', '稳健', 'essity', 'kimberly-clark', 'kimberly clark',
    'キンバリー', 'カミ商事',
]

# Off-topic product terms — if any appear WITHOUT core tissue terms, reject the article
OFFTOPIC_TERMS = [
    '洗剤', '柔軟剤', '洗濯洗剤', 'アリエール', 'レノア', 'ボールド', 'ジョイ',
    'ファブリーズ', '漂白剤', '洗濯槽',
    'シャンプー', 'リンス', 'コンディショナー', 'ボディソープ',
    '化粧品', 'リップ', 'ファンデーション', '美容液', 'スキンケア', '口紅',
    '食品', '飲料', 'コーヒー', 'ビール', '菓子', 'サプリ',
]


def is_industry_relevant(title, snippet):
    """Return True only if the article is relevant to the tissue/hygiene/paper industry."""
    text = (title + ' ' + snippet).lower()
    has_core = any(term.lower() in text for term in TISSUE_CORE_TERMS)
    has_company = any(name.lower() in text for name in TISSUE_INDUSTRY_COMPANIES)
    has_offtopic = any(term.lower() in text for term in OFFTOPIC_TERMS)
    # Explicitly off-topic articles without any tissue/hygiene signal are rejected
    if has_offtopic and not has_core:
        return False
    return has_core or has_company

CATEGORY_KEYWORDS = {
    '①': ['ユニ・チャーム', '花王', 'P&G', 'ライオン', 'キンバリー', 'Kimberly', 'Essity', '衛生用品', 'おむつ'],
    '②': ['製紙', 'パルプ', '王子', '日本製紙', 'Essity', '大王製紙'],
    '③': ['瑞光', 'Zuiko', 'GDM', 'Fameccanica', '加工機', '不織布', '吸収体'],
    '④': ['OPTIMA', 'ファナック', 'FANUC', '包装機', 'パレタイ', 'ロボット'],
    '⑤': ['ウェット', 'Winner Medical', '稳健'],
    '⑥': ['ティシュー', 'ティッシュ', 'トイレット', '家庭紙', '衛生用紙'],
}

CATEGORY_NAMES = {
    '①': '日用品・衛生用品メーカー',
    '②': '製紙・パルプメーカー',
    '③': '不織布・吸収体加工機メーカー',
    '④': '包装機・パレタイジング設備メーカー',
    '⑤': 'ウェットティッシュ製造メーカー',
    '⑥': 'ティッシュペーパー・家庭紙専業メーカー',
}

KNOWN_COMPANIES = [
    'ユニ・チャーム', '花王', 'P&G Japan', 'P&G', 'ライオン', 'キンバリー・クラーク',
    'Kimberly-Clark', '大王製紙', '王子ホールディングス', '日本製紙', 'Essity',
    '株式会社瑞光（Zuiko）', '瑞光', 'GDM', 'Fameccanica', 'OPTIMA Packaging', 'ファナック',
    'Winner Medical（稳健医疗）', '丸富製紙', 'カミ商事',
]


def map_category(text):
    for cat_id, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                return cat_id, CATEGORY_NAMES[cat_id]
    return '⑥', CATEGORY_NAMES['⑥']


def extract_company(text):
    for company in KNOWN_COMPANIES:
        if company.lower() in text.lower():
            return company
    return '不明'


def determine_info_type(text):
    if any(k in text for k in ['投資', '買収', '出資', 'M&A', '資金', 'acquisition', '決算', '株価']):
        return '投資'
    if any(k in text for k in ['特許', 'patent', '知的']):
        return '特許'
    if any(k in text for k in ['研究', '論文', '学会', '技術開発', 'research', 'development', 'NEDO']):
        return '研究開発'
    if any(k in text for k in ['加工機', 'マシン', '設備', 'machine']):
        return '加工機技術'
    if any(k in text for k in ['包装機', 'パッケージ', '充填', 'packaging']):
        return '包装機技術'
    if any(k in text for k in ['新製品', '新商品', '新発売', 'new product', 'launch', 'リニューアル']):
        return '新製品'
    if any(k in text for k in ['環境', 'エコ', 'サステナ', 'sustainability', 'eco', 'carbon', 'CDP']):
        return '環境'
    if any(k in text for k in ['規制', 'law', '法律', 'regulation', '値上げ', '施行']):
        return '規制'
    return '其他'


def fetch_from_google_cse(query, api_key, cse_id, num=10):
    """Return list of items on success, None on a fatal API error (e.g. 403 forbidden)."""
    url = 'https://www.googleapis.com/customsearch/v1'
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': num,
        'lr': 'lang_ja',
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get('items', [])
    except requests.exceptions.HTTPError as e:
        print(f"  Error fetching query '{query}': {e}")
        fatal = False
        try:
            body = e.response.json()
            err = body.get('error', {})
            code = err.get('code')
            reason = err.get('errors', [{}])[0].get('reason')
            print(f"  Google API error {code}: {err.get('message')} (reason: {reason})")
            # 403 = API not enabled / project lacks access; treat as fatal and fall back to RSS
            if code == 403:
                print('  Fatal API error — switching to Google News RSS fallback for all queries.')
                fatal = True
        except Exception:
            pass
        return None if fatal else []
    except Exception as e:
        print(f"  Error fetching query '{query}': {e}")
        return []


def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text or '').strip()


def fetch_from_google_news_rss(query, max_items=10):
    """Fetch news items from Google News RSS (no API key required)."""
    if not _feedparser_available:
        print('  feedparser not available; skipping RSS fallback.')
        return []
    feed_url = 'https://news.google.com/rss/search?q={}&hl=ja&gl=JP&ceid=JP:ja'.format(
        requests.utils.quote(query)
    )
    try:
        feed = feedparser.parse(feed_url)
        items = []
        for entry in feed.entries[:max_items]:
            title = entry.get('title', '')
            link = entry.get('link', '')
            summary = entry.get('summary', '')
            source_info = entry.get('source')
            source = source_info.get('title', '') if isinstance(source_info, dict) else ''
            items.append({
                'title': title,
                'link': link,
                'snippet': summary,
                'displayLink': source,
            })
        return items
    except Exception as e:
        print(f'  RSS fetch error for query \'{query}\': {e}')
        return []


def fetch_news():
    api_key = os.environ.get('GOOGLE_API_KEY', '')
    cse_id = os.environ.get('GOOGLE_CSE_ID', '')

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    results = []
    use_rss_fallback = False

    if not api_key or not cse_id:
        print('WARNING: GOOGLE_API_KEY or GOOGLE_CSE_ID not set. Falling back to Google News RSS.')
        use_rss_fallback = True

    for query in SEARCH_QUERIES:
        print(f'  Searching: {query}')
        if use_rss_fallback:
            items = fetch_from_google_news_rss(query)
        else:
            cse_items = fetch_from_google_cse(query, api_key, cse_id)
            if cse_items is None:
                # None signals a fatal API error; switch to RSS for remaining queries
                use_rss_fallback = True
                items = fetch_from_google_news_rss(query)
            else:
                items = cse_items

        for item in items:
            title = item.get('title', '')
            url = item.get('link', '')
            snippet = strip_html(item.get('snippet', ''))
            source_name = item.get('displayLink', '')

            # Mandatory relevance check — discard off-topic articles (e.g. laundry detergent)
            if not is_industry_relevant(title, snippet):
                print(f'  [SKIP non-relevant] {title[:60]}')
                continue

            full_text = title + ' ' + snippet
            category_id, category_name = map_category(full_text)
            company = extract_company(full_text)
            info_type = determine_info_type(full_text)

            results.append({
                'title': title,
                'summary': snippet,
                'company': company,
                'date': today,
                'category_id': category_id,
                'category_name': category_name,
                'info_type': info_type,
                'url': url,
                'source_name': source_name,
                'confidence': '高' if company != '不明' else '中',
            })

    return results


def load_existing(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        # Support both legacy array format and new {"last_updated":..., "items":[...]} format
        if isinstance(raw, list):
            return raw, None
        return raw.get('items', []), raw.get('last_updated')
    return [], None


def save_data(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = {'last_updated': now, 'items': items}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    existing, _ = load_existing(data_path)
    existing_urls = {item['url'] for item in existing}

    print(f'Existing items: {len(existing)}')
    print('Fetching new items via Google Custom Search API...')

    new_items = fetch_news()
    appended = 0
    for item in new_items:
        if appended >= 40:
            print(f'  Daily cap of 40 new items reached; skipping remaining.')
            break
        if item['url'] and item['url'] not in existing_urls:
            existing.append(item)
            existing_urls.add(item['url'])
            appended += 1

    # Prune items older than 90 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    before_prune = len(existing)
    existing = [item for item in existing if item.get('date', '9999-99-99') >= cutoff_str]
    pruned = before_prune - len(existing)
    if pruned:
        print(f'Pruned {pruned} items older than 90 days.')

    # Sort newest first
    existing.sort(key=lambda x: x.get('date', ''), reverse=True)

    save_data(data_path, existing)
    print(f'Appended {appended} new items. Total: {len(existing)} items saved to {data_path}')