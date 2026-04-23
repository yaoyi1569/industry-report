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

# Number of days to restrict Google Custom Search results to (base value)
DATE_RESTRICT_DAYS = 10

# Per-bucket daily quotas — total = 45 items
QUOTA_INDUSTRY = 20   # Bucket A: competitor / market intelligence
QUOTA_MACHINE = 20    # Bucket B: equipment / production R&D
QUOTA_ACADEMIC = 5    # Bucket C: J-STAGE / Google Scholar / patents

# Maximum search rounds when quota is not met (progressively wider date windows)
_SEARCH_DATE_WINDOWS = [10, 20, 30, 60]

# Path for deficit-tracking configuration
_SCRIPT_DIR = os.path.dirname(__file__)
SEARCH_CONFIG_PATH = os.path.normpath(os.path.join(_SCRIPT_DIR, '..', 'data', 'search_config.json'))

# ============================================================
# 简化后的搜索查询（使用 | 代替 OR，移除多余括号/引号）
# 每个查询控制在较短长度内，避免 400 错误
# ============================================================
SEARCH_QUERIES = [
    # Competitor / Market Intelligence — Segment A
    'ユニ・チャーム ティシュー|おむつ|衛生用品|ナプキン|決算|投資',
    '花王 ティシュー|家庭紙|衛生用品|おむつ|研究開発|投資',
    'P&G Japan おむつ|ナプキン|ティシュー|衛生用品',
    'ライオン トイレット|衛生用品|新製品|投資',
    '大王製紙|王子ホールディングス|日本製紙 家庭紙|トイレット|業界',
    'Essity Kimberly-Clark ティシュー|衛生用品|おむつ',
    '丸富製紙|カミ商事 家庭紙|ティシュー',
    '家庭紙 トイレットペーパー 業界 規制|値上げ',
    # Diaper segment
    'おむつ 新製品|技術|素材|吸収|ユニ・チャーム|花王',
    'オムツ 不織布|吸収体|研究開発|製造',
    # Sanitary napkin segment
    'ナプキン 生理用品 新製品|素材|技術|市場',
    '生理用品 衛生用品 業界|環境|サステナ',
    # Wet tissue segment
    'ウェットティッシュ Winner Medical 稳健医療 新製品|技術',
    'ウェットティシュ 市場|素材|不織布|製造',
    # Chinese manufacturers
    'Vinda 维达 ティシュー|家庭紙|衛生用品',
    'Hengan 恒安 ティシュー|おむつ|ナプキン|衛生用品',
    '中顺洁柔 C&S Paper 家庭紙|製紙',
    # Machine / Production R&D — Segment B
    '瑞光 Zuiko 加工機|設備|不織布',
    'GDM Fameccanica 吸収体 加工機',
    'OPTIMA packaging 包装機 衛生',
    'ファナック FANUC パレタイザー 衛生|包装',
]

# 学术/专利查询（进一步拆分，每个查询更短）
ACADEMIC_QUERIES = [
    # Group 1: Oji/Nippon Paper (split into two)
    'site:jstage.jst.go.jp 王子ホールディングス|王子ネピア ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:patents.google.com 王子ホールディングス|王子ネピア ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:jstage.jst.go.jp 日本製紙|日本製紙クレシア|カミ商事 ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    'site:patents.google.com 日本製紙|日本製紙クレシア|カミ商事 ティッシュ|タオル|パルプ 特許|発明|新技術 -大王製紙',
    # Group 2: Unicharm/Kao/P&G
    'site:jstage.jst.go.jp ユニ・チャーム|花王|P&G 不織布|おむつ|生理用品|吸収体 特許|発明|新技術',
    'site:patents.google.com ユニ・チャーム|花王|P&G 不織布|おむつ|生理用品|吸収体 特許|発明|新技術',
    # Group 3: Tokushu/Marufuji
    'site:jstage.jst.go.jp 特種東海製紙|丸富製紙 加工技術|包装|省エネルギー 特許|発明|新技術',
    'site:patents.google.com 特種東海製紙|丸富製紙 加工技術|包装|省エネルギー 特許|発明|新技術',
]

# Core terms for relevance filtering
TISSUE_CORE_TERMS = [
    '家庭紙', 'ティシュー', 'ティッシュ', 'トイレット', 'ちり紙', 'キッチンペーパー',
    'おむつ', 'オムツ', 'ナプキン', '生理用', '失禁', '衛生用品', '衛生用紙',
    'ウェットティシュ', 'ウェットティッシュ', '不織布', '吸収体', 'パルプ',
    '抽紙', '衛生紙',
]

TISSUE_INDUSTRY_COMPANIES = [
    'ユニ・チャーム', 'unicharm',
    '大王製紙', '王子製紙', '王子ホールディングス', '日本製紙', '丸富製紙',
    '瑞光', 'zuiko', 'gdm', 'fameccanica',
    'winner medical', '稳健', 'essity', 'kimberly-clark', 'kimberly clark',
    'キンバリー', 'カミ商事',
    'vinda', '维达', 'hengan', '恒安', '中顺洁柔', 'c&s paper',
]

OFFTOPIC_TERMS = [
    '洗剤', '柔軟剤', '洗濯洗剤', 'アリエール', 'レノア', 'ボールド', 'ジョイ',
    'ファブリーズ', '漂白剤', '洗濯槽',
    'シャンプー', 'リンス', 'コンディショナー', 'ボディソープ',
    '化粧品', 'リップ', 'ファンデーション', '美容液', 'スキンケア', '口紅',
    '食品', '飲料', 'コーヒー', 'ビール', '菓子', 'サプリ',
]


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
    'Winner Medical（稳健医疗）', '丸富製紙', 'カミ商事',
    'Vinda（维达）', 'Hengan（恒安）', '中顺洁柔', 'C&S Paper',
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


def fetch_from_google_cse(query, api_key, cse_id, num=10, date_restrict_days=None):
    """Return list of items on success, None on a fatal API error (400/403/429)."""
    url = 'https://www.googleapis.com/customsearch/v1'
    restrict = date_restrict_days if date_restrict_days else DATE_RESTRICT_DAYS
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': num,
        'lr': 'lang_ja',
        'sort': 'date',
        'dateRestrict': f'd{restrict}',
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get('items', [])
        print(f'  [ENGINE=Google-CSE] {len(items)} results for: {query[:60]}')
        return items
    except requests.exceptions.HTTPError as e:
        print(f"  [Google-CSE] Error fetching query '{query}': {e}")
        fatal = False
        try:
            body = e.response.json()
            err = body.get('error', {})
            code = err.get('code')
            reason = err.get('errors', [{}])[0].get('reason')
            print(f"  [Google-CSE] API error {code}: {err.get('message')} (reason: {reason})")
            # Treat 400, 403, 429 as fatal (switch to fallback)
            if code in (400, 403, 429):
                print(f'  [Google-CSE] Fatal error {code} — switching to fallback.')
                fatal = True
        except Exception:
            pass
        return None if fatal else []
    except Exception as e:
        print(f"  [Google-CSE] Error fetching query '{query}': {e}")
        return []


def strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()


def fetch_from_duckduckgo(query, max_items=15):
    if not _ddgs_available:
        print('  [DuckDuckGo] duckduckgo_search library not available; skipping.')
        return []
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.news(query, region='jp-ja', max_results=max_items):
                results.append({
                    'title': r.get('title', ''),
                    'link': r.get('url', ''),
                    'snippet': r.get('body', ''),
                    'displayLink': r.get('source', ''),
                })
        print(f'  [ENGINE=DuckDuckGo] {len(results)} results for: {query[:60]}')
        return results
    except Exception as e:
        print(f"  [DuckDuckGo] Error fetching query '{query}': {e}")
        return []


def fetch_from_google_news_rss(query, max_items=100):
    if not _feedparser_available:
        print('  [RSS] feedparser not available; skipping RSS fallback.')
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
        print(f'  [ENGINE=Google-RSS] {len(items)} results for: {query[:60]}')
        return items
    except Exception as e:
        print(f'  [RSS] Fetch error for query \'{query}\': {e}')
        return []


def _fetch_with_fallback(query, api_key, cse_id, use_google_cse, use_ddgs, restrict_days):
    if use_google_cse:
        cse_items = fetch_from_google_cse(query, api_key, cse_id, date_restrict_days=restrict_days)
        if cse_items is not None:
            return cse_items, True, use_ddgs
        print('  [FALLBACK] Google CSE failed; switching to DuckDuckGo for remaining queries.')
        use_google_cse = False

    if use_ddgs:
        ddg_items = fetch_from_duckduckgo(query)
        if ddg_items:
            return ddg_items, False, True
        print('  [FALLBACK] DuckDuckGo returned no results; trying Google News RSS.')

    return fetch_from_google_news_rss(query, max_items=100), False, use_ddgs


def fetch_news(existing_urls=None, use_rss_fallback=False, date_restrict_days=None):
    api_key = os.environ.get('GOOGLE_API_KEY', '')
    cse_id = os.environ.get('GOOGLE_CSE_ID', '')
    today = _today_jst()
    results = []
    _existing = existing_urls or set()
    restrict_days = date_restrict_days or DATE_RESTRICT_DAYS

    use_google_cse = bool(api_key and cse_id) and not use_rss_fallback
    use_ddgs = _ddgs_available
    if not use_google_cse:
        print('WARNING: GOOGLE_API_KEY or GOOGLE_CSE_ID not set. Starting from DuckDuckGo/RSS.')

    for query in SEARCH_QUERIES:
        print(f'  Searching ({restrict_days}d): {query[:80]}')
        items, use_google_cse, use_ddgs = _fetch_with_fallback(
            query, api_key, cse_id, use_google_cse, use_ddgs, restrict_days
        )

        for item in items:
            title = item.get('title', '')
            url = item.get('link', '')
            snippet = strip_html(item.get('snippet', ''))
            source_name = item.get('displayLink', '')

            if url and url in _existing:
                print(f'  [SKIP existing] {title[:60]}')
                continue

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

    rss_fallback_flag = not use_google_cse
    return results, rss_fallback_flag


def fetch_academic_news(existing_urls=None, date_restrict_days=None, use_rss_fallback=False):
    api_key = os.environ.get('GOOGLE_API_KEY', '')
    cse_id = os.environ.get('GOOGLE_CSE_ID', '')
    today = _today_jst()
    results = []
    _existing = existing_urls or set()
    restrict_days = date_restrict_days or DATE_RESTRICT_DAYS

    use_google_cse = bool(api_key and cse_id) and not use_rss_fallback
    use_ddgs = _ddgs_available

    for query in ACADEMIC_QUERIES:
        print(f'  [ACADEMIC] Searching ({restrict_days}d): {query[:80]}')
        items, use_google_cse, use_ddgs = _fetch_with_fallback(
            query, api_key, cse_id, use_google_cse, use_ddgs, restrict_days
        )

        for item in items:
            title = item.get('title', '')
            url = item.get('link', '')
            snippet = strip_html(item.get('snippet', ''))
            source_name = item.get('displayLink', '')

            if url and url in _existing:
                print(f'  [SKIP existing] {title[:60]}')
                continue

            if not is_industry_relevant(title, snippet):
                print(f'  [SKIP non-relevant] {title[:60]}')
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


def load_search_config():
    defaults = {'academic_deficit': 0, 'last_run_date': ''}
    if os.path.exists(SEARCH_CONFIG_PATH):
        try:
            with open(SEARCH_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            defaults.update(cfg)
        except Exception as e:
            print(f'  [WARN] Could not read search_config.json: {e}')
    return defaults


def save_search_config(cfg):
    os.makedirs(os.path.dirname(SEARCH_CONFIG_PATH), exist_ok=True)
    with open(SEARCH_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def academic_date_restrict(deficit):
    return DATE_RESTRICT_DAYS + min(deficit * 3, 60)


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


if __name__ == '__main__':
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    existing, _, highlights, patents = load_existing(data_path)
    existing_urls = {item['url'] for item in existing if item.get('url')}
    existing_urls.update(item['url'] for item in patents if item.get('url'))

    print(f'Existing items: {len(existing)} regular, {len(patents)} patents')

    cfg = load_search_config()
    deficit = cfg.get('academic_deficit', 0)
    restrict_days = academic_date_restrict(deficit)
    print(f'Academic deficit from previous runs: {deficit}. '
          f'Using {restrict_days}-day window for academic queries.')

    print('Fetching industry news (Bucket A + B)...')
    industry_items, rss_fallback = fetch_news(existing_urls=existing_urls)

    print('Fetching academic / patent news (Bucket C)...')
    academic_items = fetch_academic_news(
        existing_urls=existing_urls,
        date_restrict_days=restrict_days,
        use_rss_fallback=rss_fallback,
    )

    seen_new_urls = set()
    deduped_industry = []
    for item in industry_items:
        u = item.get('url', '')
        if u and u in seen_new_urls:
            continue
        seen_new_urls.add(u) if u else None
        deduped_industry.append(item)

    deduped_academic = []
    for item in academic_items:
        u = item.get('url', '')
        if u and (u in seen_new_urls or u in existing_urls):
            continue
        seen_new_urls.add(u) if u else None
        deduped_academic.append(item)

    MACHINE_CATEGORY_IDS = {'③', '④'}
    MACHINE_KEYWORDS = ['zuiko', '瑞光', 'gdm', 'fameccanica', 'optima', 'fanuc', 'ファナック']

    def is_machine_item(item):
        if item.get('category_id') in MACHINE_CATEGORY_IDS:
            return True
        text = (item.get('company', '') + ' ' + item.get('title', '')).lower()
        return any(kw in text for kw in MACHINE_KEYWORDS)

    bucket_a_new = [it for it in deduped_industry if not is_machine_item(it)]
    bucket_b_new = [it for it in deduped_industry if is_machine_item(it)]
    bucket_c_new = deduped_academic

    today_str = _today_jst()
    today_existing = [it for it in existing if it.get('date') == today_str]
    today_academic = [it for it in patents if it.get('date') == today_str]

    existing_a = len([it for it in today_existing if not is_machine_item(it)
                      and it.get('category_id') != '⑦'])
    existing_b = len([it for it in today_existing if is_machine_item(it)])
    existing_c = len([it for it in today_existing if it.get('category_id') == '⑦']) + len(today_academic)

    cap_a = max(0, QUOTA_INDUSTRY - existing_a)
    cap_b = max(0, QUOTA_MACHINE - existing_b)
    cap_c = max(0, QUOTA_ACADEMIC - existing_c)

    def append_capped(source_list, cap, label):
        added = 0
        for item in source_list:
            if added >= cap:
                print(f'  [{label}] Daily cap of {cap} reached; skipping remaining.')
                break
            if item.get('url') and item['url'] in existing_urls:
                continue
            existing_urls.add(item['url'])
            existing.append(item)
            added += 1
            print(f'  [{label}-NEW] {item["title"][:60]}')
        return added

    appended_a = append_capped(bucket_a_new, cap_a, 'BUCKET-A')
    appended_b = append_capped(bucket_b_new, cap_b, 'BUCKET-B')
    appended_c = append_capped(bucket_c_new, cap_c, 'BUCKET-C')

    if appended_a < cap_a:
        leftover = [it for it in bucket_b_new if it.get('url') not in existing_urls]
        extra = append_capped(leftover, cap_a - appended_a, 'FILL-A-from-B')
        appended_a += extra
    if appended_b < cap_b:
        leftover = [it for it in bucket_a_new if it.get('url') not in existing_urls]
        extra = append_capped(leftover, cap_b - appended_b, 'FILL-B-from-A')
        appended_b += extra

    for window in _SEARCH_DATE_WINDOWS[1:]:
        total_a = existing_a + appended_a
        total_b = existing_b + appended_b
        total_c = existing_c + appended_c
        if total_a >= QUOTA_INDUSTRY and total_b >= QUOTA_MACHINE and total_c >= QUOTA_ACADEMIC:
            break

        print(f'  [RETRY] Quotas not met (A={total_a}/{QUOTA_INDUSTRY}, '
              f'B={total_b}/{QUOTA_MACHINE}, C={total_c}/{QUOTA_ACADEMIC}). '
              f'Broadening search to {window}-day window...')

        if total_a < QUOTA_INDUSTRY or total_b < QUOTA_MACHINE:
            extra_industry, rss_fallback = fetch_news(
                existing_urls=existing_urls,
                use_rss_fallback=rss_fallback,
                date_restrict_days=window,
            )
            extra_a = [it for it in extra_industry if not is_machine_item(it)]
            extra_b = [it for it in extra_industry if is_machine_item(it)]
            if total_a < QUOTA_INDUSTRY:
                appended_a += append_capped(extra_a, QUOTA_INDUSTRY - total_a, f'RETRY-A-{window}d')
            if total_b < QUOTA_MACHINE:
                appended_b += append_capped(extra_b, QUOTA_MACHINE - total_b, f'RETRY-B-{window}d')

        if total_c < QUOTA_ACADEMIC:
            extra_academic = fetch_academic_news(
                existing_urls=existing_urls,
                date_restrict_days=window,
                use_rss_fallback=rss_fallback,
            )
            appended_c += append_capped(
                extra_academic, QUOTA_ACADEMIC - total_c, f'RETRY-C-{window}d'
            )

    appended_total = appended_a + appended_b + appended_c
    if not appended_total:
        print(f'No recent news found after exhausting all search windows.')

    actual_academic_today = existing_c + appended_c
    daily_shortfall = max(0, QUOTA_ACADEMIC - actual_academic_today)
    new_deficit = max(0, deficit + daily_shortfall - max(0, appended_c - cap_c))
    cfg['academic_deficit'] = new_deficit
    cfg['last_run_date'] = today_str
    save_search_config(cfg)
    print(f'Academic quota: target={QUOTA_ACADEMIC}, fetched today={actual_academic_today}, '
          f'deficit={new_deficit} (was {deficit}).')

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
    total_appended = appended_a + appended_b + appended_c
    print(f'Appended {total_appended} new items '
          f'(A={appended_a}, B={appended_b}, C={appended_c}). '
          f'Total: {len(existing)} regular + {len(patents)} patents saved to {data_path}')
