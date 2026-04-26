import re
import requests
from datetime import datetime, timedelta, timezone
import json
import os
import time

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
# 配置常量（移除每日配额限制，改为无限制追加）
# ============================================================
DATE_RESTRICT_DAYS = 10
MIN_TOTAL_NEWS = 30   # 仅用于日志提醒，不再硬性限制

_SEARCH_DATE_WINDOWS = [10, 20, 30, 60]   # 扩大窗口
_HARD_AGE_DAYS = [3, 7, 14, 30]           # 对应的硬过滤天数

_SCRIPT_DIR = os.path.dirname(__file__)
SEARCH_CONFIG_PATH = os.path.normpath(os.path.join(_SCRIPT_DIR, '..', 'data', 'search_config.json'))

# ============================================================
# 搜索查询（已简化）
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
# 搜索引擎（带硬日期过滤，支持 CSE）
# ============================================================
def fetch_from_google_cse(query, api_key, cse_id, num=10, date_restrict_days=None):
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
        print(f'  [Google-CSE] {len(items)} results for: {query[:60]}')
        return items
    except requests.exceptions.HTTPError as e:
        print(f"  [Google-CSE] Error: {e}")
        fatal = False
        try:
            body = e.response.json()
            err = body.get('error', {})
            code = err.get('code')
            print(f"  [Google-CSE] API error {code}: {err.get('message')}")
            if code in (400, 403, 429):
                print(f'  [Google-CSE] Fatal error {code} — switching to fallback.')
                fatal = True
        except Exception:
            pass
        return None if fatal else []
    except Exception as e:
        print(f"  [Google-CSE] Exception: {e}")
        return []

def fetch_from_duckduckgo(query, max_items=15, max_age_days=3):
    if not _ddgs_available:
        return []
    try:
        results = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with DDGS() as ddgs:
            for r in ddgs.news(query, region='jp-ja', max_results=max_items):
                date_str = r.get('date')
                if date_str:
                    try:
                        pub_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        if pub_date < cutoff:
                            continue
                    except Exception:
                        pass
                results.append({
                    'title': r.get('title', ''),
                    'link': r.get('url', ''),
                    'snippet': r.get('body', ''),
                    'displayLink': r.get('source', ''),
                })
        print(f'  [DuckDuckGo] {len(results)} fresh (≤{max_age_days}d) for: {query[:60]}')
        return results
    except Exception as e:
        print(f"  [DuckDuckGo] Error: {e}")
        return []

def fetch_from_google_news_rss(query, max_items=100, max_age_days=3):
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

def _fetch_with_fallback(query, api_key, cse_id, use_google_cse, use_ddgs, restrict_days, max_age_days):
    if use_google_cse and api_key and cse_id:
        cse_items = fetch_from_google_cse(query, api_key, cse_id, date_restrict_days=restrict_days)
        if cse_items is not None:
            return cse_items, True, use_ddgs
        print('  [FALLBACK] Google CSE failed; switching to DuckDuckGo.')
        use_google_cse = False
    if use_ddgs:
        ddg_items = fetch_from_duckduckgo(query, max_age_days=max_age_days)
        if ddg_items:
            return ddg_items, False, True
        print('  [FALLBACK] DuckDuckGo failed; trying RSS.')
    rss_items = fetch_from_google_news_rss(query, max_age_days=max_age_days)
    return rss_items, False, use_ddgs

# ============================================================
# 抓取主函数（行业新闻）
# ============================================================
def fetch_news(existing_urls=None, use_rss_fallback=False, date_restrict_days=None, max_age_days=None):
    api_key = os.environ.get('GOOGLE_API_KEY', '')
    cse_id = os.environ.get('GOOGLE_CSE_ID', '')
    today = _today_jst()
    results = []
    _existing = existing_urls or set()
    restrict_days = date_restrict_days or DATE_RESTRICT_DAYS
    if max_age_days is None:
        max_age_days = 3
    use_google_cse = bool(api_key and cse_id) and not use_rss_fallback
    use_ddgs = _ddgs_available
    if not use_google_cse:
        print('WARNING: Google CSE not available (key missing or fallback forced). Using DuckDuckGo/RSS.')
    for query in SEARCH_QUERIES:
        print(f'  Searching ({restrict_days}d, age≤{max_age_days}d): {query[:80]}')
        items, use_google_cse, use_ddgs = _fetch_with_fallback(
            query, api_key, cse_id, use_google_cse, use_ddgs, restrict_days, max_age_days
        )
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
    rss_fallback_flag = not use_google_cse
    return results, rss_fallback_flag

# ============================================================
# 抓取学术/专利新闻
# ============================================================
def fetch_academic_news(existing_urls=None, date_restrict_days=None, use_rss_fallback=False, max_age_days=None):
    api_key = os.environ.get('GOOGLE_API_KEY', '')
    cse_id = os.environ.get('GOOGLE_CSE_ID', '')
    today = _today_jst()
    results = []
    _existing = existing_urls or set()
    restrict_days = date_restrict_days or DATE_RESTRICT_DAYS
    if max_age_days is None:
        max_age_days = 3
    use_google_cse = bool(api_key and cse_id) and not use_rss_fallback
    use_ddgs = _ddgs_available
    for query in ACADEMIC_QUERIES:
        print(f'  [ACADEMIC] Searching ({restrict_days}d, age≤{max_age_days}d): {query[:80]}')
        items, use_google_cse, use_ddgs = _fetch_with_fallback(
            query, api_key, cse_id, use_google_cse, use_ddgs, restrict_days, max_age_days
        )
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
# 配置与数据持久化
# ============================================================
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

# ============================================================
# 辅助：根据窗口计算硬过滤天数
# ============================================================
def get_hard_age_from_window(window_days):
    if window_days <= 10:
        return 3
    elif window_days <= 20:
        return 7
    elif window_days <= 30:
        return 14
    else:
        return 30

# ============================================================
# 主入口：动态调整窗口，抓取所有新文章并直接追加（无配额限制）
# ============================================================
if __name__ == '__main__':
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    existing, _, highlights, patents = load_existing(data_path)
    existing_urls = {item['url'] for item in existing if item.get('url')}
    existing_urls.update(item['url'] for item in patents if item.get('url'))

    print(f'Existing items: {len(existing)} regular, {len(patents)} patents')

    cfg = load_search_config()
    deficit = cfg.get('academic_deficit', 0)
    base_restrict_days = academic_date_restrict(deficit)
    print(f'Academic deficit: {deficit}, base window: {base_restrict_days}d.')

    # 收集所有新抓取条目（跨重试）
    all_new_items = []
    rss_fallback = False

    for idx, (window, hard_age) in enumerate(zip(_SEARCH_DATE_WINDOWS, _HARD_AGE_DAYS)):
        actual_window = max(window, base_restrict_days)
        # 硬过滤天数基于实际窗口计算
        actual_hard_age = get_hard_age_from_window(actual_window)
        print(f'\n=== Attempt {idx+1}: date window = {actual_window}d, hard age filter = {actual_hard_age}d ===')
        # 抓取行业新闻
        industry_items, rss_fallback = fetch_news(
            existing_urls=existing_urls,
            use_rss_fallback=rss_fallback,
            date_restrict_days=actual_window,
            max_age_days=actual_hard_age,
        )
        academic_items = fetch_academic_news(
            existing_urls=existing_urls,
            date_restrict_days=actual_window,
            use_rss_fallback=rss_fallback,
            max_age_days=actual_hard_age,
        )

        # 合并去重（本次内部去重）
        seen_this_round = set()
        combined = []
        for item in industry_items + academic_items:
            u = item.get('url')
            if u and (u in existing_urls or u in seen_this_round):
                continue
            if u:
                seen_this_round.add(u)
            combined.append(item)

        if not combined:
            print('  No new items in this attempt.')
            continue

        # 将本次新条目添加到总列表，并更新 existing_urls 防止后续重复
        all_new_items.extend(combined)
        for item in combined:
            if item.get('url'):
                existing_urls.add(item['url'])

        # 统计当前累计总数
        MACHINE_CATS = {'③', '④'}
        def is_machine(it):
            if it.get('category_id') in MACHINE_CATS:
                return True
            txt = (it.get('company', '') + it.get('title', '')).lower()
            return any(k in txt for k in ['zuiko', '瑞光', 'gdm', 'fameccanica', 'optima', 'fanuc', 'ファナック'])
        cnt_a = sum(1 for it in all_new_items if not is_machine(it) and it.get('category_id') != '⑦')
        cnt_b = sum(1 for it in all_new_items if is_machine(it))
        cnt_c = sum(1 for it in all_new_items if it.get('category_id') == '⑦' or it.get('is_academic'))
        total_new = cnt_a + cnt_b + cnt_c
        print(f'  This round: A={cnt_a}, B={cnt_b}, C={cnt_c}; cumulative total={total_new}')

        if total_new >= MIN_TOTAL_NEWS:
            print(f'  Reached target {MIN_TOTAL_NEWS} items, stopping further expansion.')
            break
    else:
        print(f'WARNING: Only {total_new} new items fetched, below target {MIN_TOTAL_NEWS}.')

    # ===== 直接将 all_new_items 追加到 existing（无配额限制）=====
    appended_total = 0
    for item in all_new_items:
        u = item.get('url')
        if u and u in existing_urls:
            continue
        if u:
            existing_urls.add(u)
        existing.append(item)
        appended_total += 1
        print(f'  [NEW] {item["title"][:60]}')

    # 更新学术 deficit
    today_str = _today_jst()
    today_academic = [it for it in all_new_items if it.get('category_id') == '⑦' or it.get('is_academic')]
    actual_academic_today = len(today_academic)
    daily_shortfall = max(0, 5 - actual_academic_today)   # 目标每天5条学术专利
    new_deficit = max(0, deficit + daily_shortfall)
    cfg['academic_deficit'] = new_deficit
    cfg['last_run_date'] = today_str
    save_search_config(cfg)
    print(f'Academic quota: target=5, fetched today={actual_academic_today}, deficit={new_deficit} (was {deficit}).')

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

    print(f'Appended {appended_total} new items. '
          f'Total: {len(existing)} regular + {len(patents)} patents saved to {data_path}')
