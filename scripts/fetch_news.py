import re
import requests
from datetime import datetime, timedelta, timezone
import json
import os

try:
    import pytz
    _PYTZ_AVAILABLE = True
except ImportError:
    _PYTZ_AVAILABLE = False

try:
    import feedparser
    _feedparser_available = True
except ImportError:
    _feedparser_available = False

try:
    from duckduckgo_search import DDGS
    _ddgs_available = True
except ImportError:
    _ddgs_available = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
# 配置常量
# ============================================================
DATE_RESTRICT_DAYS = 60           # Google CSE 日期窗口（但一般用不到，因为 CSE 失效）
MAX_AGE_DAYS = 30                 # 硬过滤：只保留最近30天内的新闻

_SCRIPT_DIR = os.path.dirname(__file__)
SEARCH_CONFIG_PATH = os.path.normpath(os.path.join(_SCRIPT_DIR, '..', 'data', 'search_config.json'))

# ============================================================
# 搜索查询（和之前一样）
# ============================================================
SEARCH_QUERIES = [
    'ユニ・チャーム ティシュー|おむつ|衛生用品|ナプキン|決算|投資',
    '花王 ティシュー|家庭紙|衛生用品|おむつ|研究開発|投資',
    'P&G Japan おむつ|ナプキン|ティシュー|衛生用品',
    'ライオン トイレット|衛生用品|新製品|投資',
    '大王製紙|王子ホールディングス|日本製紙 家庭紙|トイレット|業界',
    'Essity Kimberly-Clark ティシュー|衛生用品|おむつ',
    '丸富製紙|カミ商事 家庭紙|ティシュー',
    '家庭紙 トイレットペーパー 業界 規制|値上げ',
    'おむつ 新製品|技術|素材|吸収|ユニ・チャーム|花王',
    'オムツ 不織布|吸収体|研究開発|製造',
    'ナプキン 生理用品 新製品|素材|技術|市場',
    '生理用品 衛生用品 業界|環境|サステナ',
    'ウェットティッシュ Winner Medical 稳健医療 新製品|技術',
    'ウェットティシュ 市場|素材|不織布|製造',
    'Vinda 维达 ティシュー|家庭紙|衛生用品',
    'Hengan 恒安 ティシュー|おむつ|ナプキン|衛生用品',
    '中顺洁柔 C&S Paper 家庭紙|製紙',
    '瑞光 Zuiko 加工機|設備|不織布',
    'GDM Fameccanica 吸収体 加工機',
    'OPTIMA packaging 包装機 衛生',
    'ファナック FANUC パレタイザー 衛生|包装',
]

ACADEMIC_QUERIES = [
    'site:jstage.jst.go.jp 王子ホールディングス|王子ネピア ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:patents.google.com 王子ホールディングス|王子ネピア ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:jstage.jst.go.jp 日本製紙|日本製紙クレシア|カミ商事 ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:patents.google.com 日本製紙|日本製紙クレシア|カミ商事 ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:jstage.jst.go.jp ユニ・チャーム|花王|P&G 不織布|おむつ|生理用品|吸収体 特許|発明|新技術',
    'site:patents.google.com ユニ・チャーム|花王|P&G 不織布|おむつ|生理用品|吸収体 特許|発明|新技術',
    'site:jstage.jst.go.jp 特種東海製紙|丸富製紙 加工技術|包装|省エネルギー 特許|発明|新技術',
    'site:patents.google.com 特種東海製紙|丸富製紙 加工技術|包装|省エネルギー 特許|発明|新技術',
]

# ============================================================
# 相关性过滤关键词（不变）
# ============================================================
TISSUE_CORE_TERMS = [
    '家庭紙', 'ティシュー', 'ティッシュ', 'トイレット', 'ちり紙', 'キッチンペーパー',
    'おむつ', 'オムツ', 'ナプキン', '生理用', '失禁', '衛生用品', '衛生用紙',
    'ウェットティシュ', 'ウェットティッシュ', '不織布', '吸収体', 'パルプ',
    '抽紙', '衛生紙',
]

TISSUE_INDUSTRY_COMPANIES = [
    'ユニ・チャーム', 'unicharm', '大王製紙', '王子製紙', '王子ホールディングス', '日本製紙', '丸富製紙',
    '瑞光', 'zuiko', 'gdm', 'fameccanica', 'winner medical', '稳健', 'essity', 'kimberly-clark',
    'キンバリー', 'カミ商事', 'vinda', '维达', 'hengan', '恒安', '中顺洁柔', 'c&s paper',
]

OFFTOPIC_TERMS = [
    '洗剤', '柔軟剤', '洗濯洗剤', 'アリエール', 'レノア', 'ボールド', 'ジョイ',
    'ファブリーズ', '漂白剤', '洗濯槽', 'シャンプー', 'リンス', 'コンディショナー', 'ボディソープ',
    '化粧品', 'リップ', 'ファンデーション', '美容液', 'スキンケア', '口紅',
    '食品', '飲料', 'コーヒー', 'ビール', '菓子', 'サプリ',
]

CATEGORY_KEYWORDS = {
    '①': ['ユニ・チャーム', '花王', 'P&G', 'ライオン', 'キンバリー', 'Kimberly', 'Essity',
           '衛生用品', 'おむつ', 'オムツ', 'ナプキン', '生理用', 'Vinda', '维达', 'Hengan', '恒安', '中顺洁柔'],
    '②': ['製紙', 'パルプ', '王子', '日本製紙', 'Essity', '大王製紙'],
    '③': ['瑞光', 'Zuiko', 'GDM', 'Fameccanica', '加工機', '不織布', '吸収体'],
    '④': ['OPTIMA', 'ファナック', 'FANUC', '包装機', 'パレタイ', 'ロボット'],
    '⑤': ['ウェット', 'Winner Medical', '稳健'],
    '⑥': ['ティシュー', 'ティッシュ', 'トイレット', '家庭紙', '衛生用紙'],
    '⑦': ['jstage', 'patents.google', 'scholar.google', '特許', '論文', '学会', 'jst.go.jp'],
}

CATEGORY_NAMES = {
    '①': '日用品・衛生用品メーカー',
    '②': '製紙・パルプメーカー',
    '③': '不織布・吸収体加工機メーカー',
    '④': '包装機・パレタイジング設備メーカー',
    '⑤': 'ウェットティッシュ製造メーカー',
    '⑥': 'ティッシュペーパー・家庭紙専業メーカー',
    '⑦': '学術論文・特許情報',
}

KNOWN_COMPANIES = [
    'ユニ・チャーム', '花王', 'P&G Japan', 'P&G', 'ライオン', 'キンバリー・クラーク',
    'Kimberly-Clark', '大王製紙', '王子ホールディングス', '日本製紙', 'Essity',
    '株式会社瑞光（Zuiko）', '瑞光', 'GDM', 'Fameccanica', 'OPTIMA Packaging', 'ファナック',
    'Winner Medical（稳健医疗）', '丸富製紙', 'カミ商事', 'Vinda（维达）', 'Hengan（恒安）', '中顺洁柔', 'C&S Paper',
]

# ============================================================
# 辅助函数
# ============================================================
def _today_jst():
    if _PYTZ_AVAILABLE:
        return datetime.now(pytz.timezone('Asia/Tokyo')).strftime('%Y-%m-%d')
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime('%Y-%m-%d')

def is_industry_relevant(title, snippet):
    text = (title + ' ' + snippet).lower()
    has_core = any(term.lower() in text for term in TISSUE_CORE_TERMS)
    has_company = any(name.lower() in text for name in TISSUE_INDUSTRY_COMPANIES)
    has_offtopic = any(term.lower() in text for term in OFFTOPIC_TERMS)
    if has_offtopic and not has_core:
        return False
    return has_core or has_company

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

def strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()

# ============================================================
# 抓取函数（仅 RSS，因为 Google CSE 和 DuckDuckGo 都失效）
# ============================================================
def fetch_from_google_news_rss(query, max_items=100, max_age_days=MAX_AGE_DAYS):
    if not _feedparser_available:
        return []
    feed_url = 'https://news.google.com/rss/search?q={}&hl=ja&gl=JP&ceid=JP:ja'.format(
        requests.utils.quote(query)
    )
    try:
        feed = feedparser.parse(feed_url)
        items = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        for entry in feed.entries[:max_items]:
            # 日期过滤
            published = entry.get('published_parsed')
            if published:
                pub_date = datetime.fromtimestamp(time.mktime(published), tz=timezone.utc)
                if pub_date < cutoff:
                    continue
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
        print(f'  [Google-RSS] {len(items)} fresh (≤{max_age_days}d) for: {query[:60]}')
        return items
    except Exception as e:
        print(f'  [RSS] Error: {e}')
        return []

def fetch_news(existing_urls=None):
    """直接使用 RSS 抓取，无 fallback"""
    today = _today_jst()
    results = []
    _existing = existing_urls or set()
    for query in SEARCH_QUERIES:
        print(f'  Searching (RSS, age≤{MAX_AGE_DAYS}d): {query[:80]}')
        items = fetch_from_google_news_rss(query, max_age_days=MAX_AGE_DAYS)
        for item in items:
            title = item.get('title', '')
            url = item.get('link', '')
            snippet = strip_html(item.get('snippet', ''))
            source_name = item.get('displayLink', '')
            if url and url in _existing:
                continue
            if not is_industry_relevant(title, snippet):
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

def fetch_academic_news(existing_urls=None):
    today = _today_jst()
    results = []
    _existing = existing_urls or set()
    for query in ACADEMIC_QUERIES:
        print(f'  [ACADEMIC] Searching (RSS, age≤{MAX_AGE_DAYS}d): {query[:80]}')
        items = fetch_from_google_news_rss(query, max_age_days=MAX_AGE_DAYS)
        for item in items:
            title = item.get('title', '')
            url = item.get('link', '')
            snippet = strip_html(item.get('snippet', ''))
            source_name = item.get('displayLink', '')
            if url and url in _existing:
                continue
            if not is_industry_relevant(title, snippet):
                continue
            company = extract_company(title + ' ' + snippet)
            info_type = determine_info_type(title + ' ' + snippet)
            results.append({
                'title': title,
                'summary': snippet,
                'company': company,
                'date': today,
                'category_id': '⑦',
                'category_name': CATEGORY_NAMES['⑦'],
                'info_type': info_type,
                'url': url,
                'source_name': source_name,
                'confidence': '高' if company != '不明' else '中',
                'is_academic': True,
            })
    return results

# ============================================================
# 数据持久化（直接追加）
# ============================================================
def load_existing(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw, None, [], []
        if 'dates' in raw:
            items = []
            for date_items in raw.get('dates', {}).values():
                items.extend(date_items)
            patents = raw.get('patents', [])
            return items, raw.get('last_updated'), raw.get('highlights', []), patents
        return raw.get('items', []), raw.get('last_updated'), raw.get('highlights', []), []
    return [], None, [], []

def save_data(path, items, highlights=None, patents=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    dates = {}
    for item in items:
        d = item.get('date', 'unknown')
        dates.setdefault(d, []).append(item)
    payload = {
        'last_updated': now,
        'highlights': highlights or [],
        'dates': dates,
        'patents': patents or [],
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

# ============================================================
# 主入口（简化版：一次抓取，直接追加）
# ============================================================
if __name__ == '__main__':
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    existing, _, highlights, patents = load_existing(data_path)
    existing_urls = {item['url'] for item in existing if item.get('url')}
    existing_urls.update(item['url'] for item in patents if item.get('url'))

    print(f'Existing items: {len(existing)} regular, {len(patents)} patents')
    print(f'Fetching news (max age = {MAX_AGE_DAYS} days) ...')

    # 抓取行业新闻
    industry_items = fetch_news(existing_urls=existing_urls)
    # 抓取学术新闻
    academic_items = fetch_academic_news(existing_urls=existing_urls)

    # 合并去重（基于 URL）
    all_new = []
    seen_urls = set()
    for item in industry_items + academic_items:
        u = item.get('url')
        if not u:
            continue
        if u in existing_urls or u in seen_urls:
            continue
        seen_urls.add(u)
        all_new.append(item)

    # 追加到 existing
    appended = 0
    for item in all_new:
        existing.append(item)
        appended += 1
        print(f'  [NEW] {item["title"][:60]}')

    # 修剪超过30天的旧新闻（保留永久专利）
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    kept = []
    for item in existing:
        if item.get('permanent_record'):
            kept.append(item)
        elif item.get('date', '9999-99-99') >= cutoff_str:
            kept.append(item)
        else:
            print(f'  [PRUNED-OLD-NEWS] {item.get("title", "")[:60]} ({item.get("date", "")})')
    pruned = len(existing) - len(kept)
    if pruned:
        print(f'Pruned {pruned} items older than 30 days.')
    existing = kept

    existing.sort(key=lambda x: x.get('date', ''), reverse=True)
    save_data(data_path, existing, highlights=highlights, patents=patents)

    print(f'Appended {appended} new items. Total: {len(existing)} regular + {len(patents)} patents saved to {data_path}')
