import json
import os
import re
import time
import requests
import pytz
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Maximum retry attempts for high-value items that fail the formatting check
MAX_RETRIES = 3
# Items with a score above this threshold are retried if formatting is poor
RETRY_SCORE_THRESHOLD = 80

# OpenRouter configuration — DeepSeek V3 only (no fallback to other providers)
_OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'
_OPENROUTER_MODEL = 'deepseek/deepseek-chat'
_OPENROUTER_MAX_RETRIES = 5

# 20+20+5 bucket quota — Bucket A: industry (20), Bucket B: machine R&D (20), Bucket C: academic/patents (5)
BUCKET_QUOTA = 20
ACADEMIC_QUOTA = 5
TOP_N = BUCKET_QUOTA * 2 + ACADEMIC_QUOTA  # 45

# Bucket B: Production / Machine R&D
# An item belongs to Bucket B when its category_id or info_type match any of these signals.
BUCKET_B_CATEGORY_IDS = {'③', '④'}
# Only machine-specific info types qualify for Bucket B (research/patent go to Bucket C)
BUCKET_B_INFO_TYPES = {'加工機技術', '包装機技術'}
BUCKET_B_COMPANY_KEYWORDS = [
    'zuiko', '瑞光', 'gdm', 'fameccanica', 'optima', 'fanuc', 'ファナック',
]


def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text or '').strip()


def _openrouter_generate(prompt):
    """Call OpenRouter (DeepSeek V3) with automatic retry on failure.

    Retries up to _OPENROUTER_MAX_RETRIES times with exponential back-off.
    Raises RuntimeError if all attempts fail.
    Requires the OPENROUTER_API_KEY environment variable to be set.
    """
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        raise RuntimeError('OPENROUTER_API_KEY not set')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'HTTP-Referer': 'https://github.com/industry-analysis-yao/industry-report',
        'X-Title': 'Industry Analysis Report',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': _OPENROUTER_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
    }

    last_error = None
    for attempt in range(_OPENROUTER_MAX_RETRIES):
        try:
            resp = requests.post(_OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f'  [OPENROUTER] Attempt {attempt + 1}/{_OPENROUTER_MAX_RETRIES} failed: {e}')
            last_error = e
            if attempt < _OPENROUTER_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise last_error if last_error else RuntimeError('OpenRouter failed after all retries')


# ============================================================
# AGENT A — Summarizer
# ============================================================

def ai_summarize(title, snippet, company, api_key=None, retry_feedback=None):
    """Agent A: Generate a Japanese factual news summary using OpenRouter (DeepSeek V3).

    When *retry_feedback* is provided (a string with specific improvement instructions
    from Agent B), it is appended to the prompt so the model can correct the issues.

    Returns a 2-tuple: (is_relevant: bool, summary: str | None).
    Returns (False, None) if the model determines the article is off-topic.
    Returns (True, 'AI Summary Pending') if the API is unavailable.
    """
    # Skip paywall-only articles: snippet is essentially empty or just repeats the title
    clean_snippet = (snippet or '').strip()
    if len(clean_snippet) < 30 or clean_snippet == title.strip():
        print(f'  [SKIP paywall/no-body] {title[:60]}')
        return False, None
    try:
        retry_section = ''
        if retry_feedback:
            retry_section = (
                f'\n\n【前回審査からのフィードバック（必ず反映してください）】\n'
                f'{retry_feedback}\n'
                f'上記の指摘をすべて改善した新しい要約を作成してください。\n'
            )

        prompt = (
            'あなたは家庭紙・衛生用品業界の専門記者です。\n\n'
            '【ステップ1: 関連性チェック】\n'
            'この記事が「家庭紙・ティッシュ・トイレットペーパー・おむつ・ナプキン・衛生用品・不織布・'
            '吸収体加工機・包装機・パレタイザー・学術論文・特許」に直接関連する業界ニュースかどうかを判断してください。\n'
            '洗剤・柔軟剤・シャンプー・化粧品・食品・飲料など、家庭紙／衛生用品と無関係な'
            'FMCGニュースであれば「IRRELEVANT」とだけ出力してください。\n\n'
            '【ステップ2: 要約（関連する場合のみ）】\n'
            '業界関連ニュースの場合は、本文スニペットを深く読み込み、'
            '「誰が・いつ・何を・どのように・数値」が明確に伝わる、'
            '業界関係者向けの日本語ニュースサマリーを80〜150字で作成してください。\n\n'
            '【厳禁事項】\n'
            '・タイトルに含まれる単語・フレーズを要約中で使用することは絶対禁止です。\n'
            '・本文スニペットから、タイトルに記載されていない具体的な数値・技術仕様・戦略的事実を'
            '必ず1つ以上抽出して要約に含めてください。\n'
            '・タイトルの言い換えや単純な要約は不可です。本文から独自の情報を付加してください。\n'
            + retry_section +
            '\n【出力例】\n'
            '「ユニ・チャームは2026年4月1〜3日に普通株式584,800株を取得価額約5.5億円で取得し、'
            '2月12日決議の自己株式取得を完了した。」\n\n'
            f'会社名: {company}\n'
            f'タイトル: {title}\n'
            f'本文スニペット: {clean_snippet}\n\n'
            f'出力（「IRRELEVANT」またはサマリー日本語のみ）:'
        )

        text = _openrouter_generate(prompt)

        if text and text.strip().upper() == 'IRRELEVANT':
            print(f'  [AI-IRRELEVANT] {title[:60]}')
            return False, None
        return True, (text or '')[:300]
    except Exception as e:
        print(f'  OpenRouter error for "{title[:40]}...": {e}')
        return True, 'AI Summary Pending'


# ============================================================
# AGENT B — Auditor
# ============================================================

def audit_item(title, summary, company, api_key=None):
    """Agent B: Critically evaluate a news summary as a senior R&D director at Daio Paper.

    Assigns a unique 1–100 impact score with heavy weight on strategic R&D relevance.
    Returns a 3-tuple: (score: int, impact_analysis: str, formatting_feedback: str | None).
    *formatting_feedback* is non-None only when there are correctable formatting issues.
    """
    try:
        prompt = (
            'あなたは大王製紙の最上席研究開発ディレクターです。業界歴30年以上、競合他社の技術動向・'
            '市場変化・設備投資・研究開発に精通した、業界随一の厳格な審査官として行動してください。\n\n'
            '以下のニュース要約を容赦なく評価し、JSON形式のみで回答してください。\n\n'
            '【評価基準（合計100点、同点禁止・必ず整数）】\n'
            '1. 大王製紙R&D戦略への直接的インパクト（最重要・40点）\n'
            '   - 自社技術開発・製造プロセス・競合優位性・特許戦略への影響\n'
            '   - 競合他社の技術革新・設備投資・新製品が自社R&Dに与える脅威または機会\n'
            '   - 家庭紙・ティッシュ・トイレットペーパーに直結する内容は高得点\n'
            '   - おむつ・ナプキン・ウェットティッシュの新技術・新製品も同等の高得点\n'
            '   - 加工機・包装機・パレタイザー等の生産設備技術革新は、競合製品ローンチと同等の高得点\n'
            '2. 市場・業界構造への影響度（25点）\n'
            '   - 価格動向、市場シェア変動、規制・政策変更、原料需給への影響\n'
            '3. 情報の具体性・信頼性（20点）\n'
            '   - 具体的数値（金額・比率・容量・日付）の有無、一次情報ソース\n'
            '4. 緊急性・時宜性（15点）\n'
            '   - 即時対応・意思決定が必要か、競合の動向として見逃せないか\n\n'
            '【フォーマット失格チェック】\n'
            '以下のいずれかに該当する場合のみformatting_feedbackに具体的改善指示を記載してください。\n'
            '該当しない場合は必ずnullにしてください：\n'
            '- タイトルをほぼそのまま言い換えただけで独自情報が皆無\n'
            '- 具体的数値・金額・比率・日付が一切含まれていない\n'
            '- 80字未満の著しく短い要約\n'
            '- 文章が途中で切れる・構造的エラー\n\n'
            '【出力形式（JSON以外は一切出力禁止）】\n'
            '{\n'
            '  "score": <1〜100の整数、他のニュースと同点不可>,\n'
            '  "impact_analysis": "<大王製紙技術開発部への具体的戦略的含意・競合対応策（60〜120字）>",\n'
            '  "formatting_feedback": <null または "<具体的改善指示（数値不足・タイトル丸写し等の指摘）>">\n'
            '}\n\n'
            f'会社名: {company}\n'
            f'タイトル: {title}\n'
            f'要約: {summary}\n'
        )

        text = _openrouter_generate(prompt)

        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text)
        score = int(result.get('score', 0))
        score = max(1, min(100, score))
        impact_analysis = (result.get('impact_analysis') or '')[:300]
        formatting_feedback = result.get('formatting_feedback') or None
        return score, impact_analysis, formatting_feedback
    except Exception as e:
        print(f'  Audit error for "{title[:40]}...": {e}')
        return 0, '', None


# ============================================================
# DUAL-AGENT PIPELINE WITH RETRY
# ============================================================

def process_item_with_retry(item, api_key=None):
    """Run Agent A (Summarizer) → Agent B (Auditor) pipeline.

    For items that score > 80 but fail the formatting check, the item is sent back
    to Agent A with specific feedback.  At most MAX_RETRIES attempts are made.
    High-value items are never discarded due to formatting failures — the best
    result across all attempts is retained.

    Mutates *item* in place with updated summary, score, and impact_analysis.
    Returns True if item is relevant, False if it should be removed.
    """
    title = item.get('title', '')
    snippet = strip_html(item.get('summary', ''))
    company = item.get('company', '不明')

    best_score = item.get('score') or 0
    # Use whatever snippet is available as the initial best_summary; quality
    # will be improved (or confirmed) once Agent B evaluates it.
    best_summary = snippet
    best_impact = item.get('impact_analysis') or ''
    feedback = None

    for attempt in range(MAX_RETRIES):
        is_relevant, new_summary = ai_summarize(
            title, snippet, company, retry_feedback=feedback
        )
        if not is_relevant:
            return False

        current_summary = new_summary or best_summary
        if not current_summary:
            # Cannot obtain a usable summary; stop retrying
            break

        score, impact_analysis, fmt_feedback = audit_item(
            title, current_summary, company
        )

        # Prefer results with a higher score, OR the same score but with
        # resolved formatting issues (no feedback from Agent B).
        is_better = score > best_score or (
            score == best_score and fmt_feedback is None and best_impact == ''
        )
        if is_better:
            best_score = score
            best_summary = current_summary
            best_impact = impact_analysis

        # Force a retry whenever the summary is too short (<80 chars), regardless of
        # score, or when a high-scoring item has formatting issues flagged by Agent B.
        summary_too_short = len(current_summary) < 80
        needs_retry = (summary_too_short or (score > RETRY_SCORE_THRESHOLD)) and fmt_feedback
        if needs_retry and attempt < MAX_RETRIES - 1:
            reason = 'short summary' if summary_too_short else f'score={score}'
            print(
                f'  [RETRY {attempt + 1}/{MAX_RETRIES - 1}] {reason}, '
                f'feedback: {fmt_feedback[:80]}'
            )
            feedback = fmt_feedback
            # Loop again with the feedback
        else:
            # Acceptable quality or last attempt — keep best result
            break

    item['summary'] = best_summary or '分析待ち'
    item['score'] = best_score
    item['impact_analysis'] = best_impact
    return True


# ============================================================
# TOP-3 HIGHLIGHTS (derived directly from scored items)
# ============================================================

def generate_highlights(items, api_key=None, excluded_urls=None, today_str=None):
    """Build Top-3 highlights from the already-scored items.

    Strict Daily Isolation: candidates are restricted to items published within
    the last 24–48 hours (today + yesterday, relative to *today_str*).  This prevents older high-
    scoring items from crowding out fresh news.  If the date-filtered pool has
    fewer than 3 scored items the window is relaxed to 7 days, and finally to
    the full item list — so the highlight block is always fully populated.

    Within the filtered pool items are sorted by score descending with the most
    recent publication date as a tie-breaker.  The impact_analysis field
    generated by Agent B is reused — no additional API call is needed.

    *excluded_urls* is an optional set of URL strings that were already
    featured in the Top-3 during the last 3 days.  Matching items are skipped
    so that each item appears in the Top-3 at most once across recent days.
    If excluding those URLs would leave fewer than 3 candidates in the daily
    pool the exclusion is relaxed so the highlight block is always fully
    populated.
    """
    excluded = excluded_urls or set()

    def _sorted_scored(pool):
        scored = [it for it in pool if it.get('score', 0) > 0]
        scored.sort(key=lambda x: (x.get('score', 0), x.get('date', '')), reverse=True)
        return scored

    # --- Strict Daily Isolation ---
    # Determine the reference date for freshness filtering.
    ref_date_str = today_str
    if not ref_date_str:
        jst = pytz.timezone('Asia/Tokyo')
        ref_date_str = datetime.now(jst).strftime('%Y-%m-%d')
    try:
        ref_date = datetime.strptime(ref_date_str, '%Y-%m-%d').date()
    except ValueError:
        ref_date = None

    def _within_days(item, n):
        """Return True if the item's date is within the last *n* days."""
        if ref_date is None:
            return True
        item_date_str = item.get('date', '')
        try:
            item_date = datetime.strptime(item_date_str[:10], '%Y-%m-%d').date()
            return (ref_date - item_date).days <= n
        except (ValueError, TypeError):
            return False

    # Try today+yesterday window first (window_days=1 → days_delta ≤ 1, i.e. 24–48h),
    # then a 7-day fallback, then the full pool.
    for window_days in (1, 7, None):
        if window_days is None:
            daily_pool = items
        else:
            daily_pool = [it for it in items if _within_days(it, window_days)]
        scored = _sorted_scored(daily_pool)
        if len(scored) >= 3:
            break
    if not scored:
        scored = _sorted_scored(items)

    # Exclude URLs already featured recently; fall back to full scored list if too few remain
    candidates = [it for it in scored if it.get('url', '') not in excluded]
    if len(candidates) < 3:
        candidates = scored
    top3 = (candidates if candidates else items)[:3]

    highlights = []
    for i, item in enumerate(top3):
        highlights.append({
            'rank': i + 1,
            'title': item.get('title', ''),
            'url': item.get('url', ''),
            'company': item.get('company', '不明'),
            'category': (
                (item.get('category_name') or '') + ' / ' +
                (item.get('info_type') or '')
            ),
            'date': item.get('date', ''),
            'impact': item.get('impact_analysis') or item.get('summary') or '',
            'score': item.get('score', 0),
            'is_patent': (
                item.get('permanent_record', False)
                or item.get('info_type') == '特許'
                or item.get('is_academic', False)
            ),
        })
    return highlights


def load_data(path):
    """Return (items, last_updated, highlights).

    Flattens both the date-bucket ``dates`` dict and the ``patents`` permanent
    archive into a single list so the processing pipeline can operate on all
    items uniformly.  The ``permanent_record`` flag on patent items is
    preserved so they are kept separate again at save time.
    """
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw, None, []
        # New date-bucket format
        if 'dates' in raw:
            items = []
            for date_items in raw.get('dates', {}).values():
                items.extend(date_items)
            items.extend(raw.get('patents', []))
            return items, raw.get('last_updated'), raw.get('highlights', [])
        # Legacy {last_updated, highlights, items} format
        return raw.get('items', []), raw.get('last_updated'), raw.get('highlights', [])
    return [], None, []


def save_data(path, items, highlights=None, last_updated=None):
    """Save items in date-bucket format.

    Items with ``permanent_record=True`` are stored in the ``patents``
    permanent archive and excluded from date buckets (which are subject to
    30-day pruning).
    """
    patents = [i for i in items if i.get('permanent_record')]
    regular = [i for i in items if not i.get('permanent_record')]
    dates = {}
    for item in regular:
        d = item.get('date', 'unknown')
        dates.setdefault(d, []).append(item)
    payload = {
        'last_updated': last_updated or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'highlights': highlights or [],
        'dates': dates,
        'patents': patents,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    # Verify OpenRouter API key is available
    if not os.environ.get('OPENROUTER_API_KEY', ''):
        print('WARNING: OPENROUTER_API_KEY not set. Summaries and scores will not be generated.')

    data, last_updated, existing_highlights = load_data(data_path)
    if not data:
        print('No data found. Run fetch_news.py first.')
        return

    # Deduplicate by URL before processing; keep the item with the highest score.
    # Only items with a real URL are deduplicated; URL-less items are kept as-is.
    url_map = {}
    no_url_items = []
    for item in data:
        url = item.get('url') or ''
        if not url:
            no_url_items.append(item)
        elif url not in url_map or (item.get('score') or 0) > (url_map[url].get('score') or 0):
            url_map[url] = item
    deduped = list(url_map.values()) + no_url_items
    if len(deduped) < len(data):
        print(f'Deduplication removed {len(data) - len(deduped)} duplicate items.')
    data = deduped

    updated = 0
    irrelevant_indices = []

    idx = 0
    while idx < len(data):
        item = data[idx]

        # Strip HTML from existing summary
        summary = strip_html(item.get('summary', ''))
        if item.get('summary', '') != summary:
            item['summary'] = summary

        # Skip if already has a quality summary, a score, and an impact analysis
        has_quality_summary = len(summary) >= 80 and '<' not in summary
        has_score = (item.get('score') is not None) and (item.get('score', 0) > 0)
        has_impact = bool(item.get('impact_analysis'))
        if has_quality_summary and has_score and has_impact:
            idx += 1
            continue
        # Skip items already marked pending in this run — all tiers failed earlier
        if summary == 'AI Summary Pending':
            idx += 1
            continue

        if not os.environ.get('OPENROUTER_API_KEY', ''):
            # No API key — assign defaults so all items have required fields
            if not has_score:
                item['score'] = 0
            if not has_impact:
                item['impact_analysis'] = ''
            idx += 1
            continue

        is_relevant = process_item_with_retry(item)

        if not is_relevant:
            irrelevant_indices.append(idx)
        else:
            updated += 1
        idx += 1

    # Remove items flagged as irrelevant (in reverse order to preserve indices)
    for idx in sorted(irrelevant_indices, reverse=True):
        removed_title = data[idx].get('title', '')[:60]
        print(f'  Removing irrelevant item: {removed_title}')
        data.pop(idx)

    if irrelevant_indices:
        print(f'Removed {len(irrelevant_indices)} irrelevant/paywall items.')

    # Ensure every item has the required schema fields
    for item in data:
        if item.get('score') is None:
            item['score'] = 0
        if not item.get('impact_analysis'):
            item['impact_analysis'] = ''

    # Tag patent items with permanent_record so they are archived permanently
    for item in data:
        if item.get('info_type') == '特許' and not item.get('permanent_record'):
            item['permanent_record'] = True
            item['category'] = 'patent'
            print(f'  [PATENT-SAVED] {item.get("title", "")[:60]}')

    # Sort by impact score descending
    data.sort(key=lambda x: x.get('score', 0), reverse=True)

    # ── 20+20+5 Bucket System ─────────────────────────────────────────────────
    # Bucket C: Academic / Patents (category_id='⑦' or is_academic flag)
    # Bucket B: Production / Machine R&D (③④ and machine companies) — excludes academic
    # Bucket A: Competitor / Market Intelligence (everything else)

    def is_bucket_c(item):
        return item.get('category_id') == '⑦' or bool(item.get('is_academic'))

    def is_bucket_b(item):
        if is_bucket_c(item):
            return False
        if item.get('category_id') in BUCKET_B_CATEGORY_IDS:
            return True
        if item.get('info_type') in BUCKET_B_INFO_TYPES:
            return True
        company_lower = (item.get('company') or '').lower()
        title_lower = (item.get('title') or '').lower()
        return any(kw in company_lower or kw in title_lower for kw in BUCKET_B_COMPANY_KEYWORDS)

    bucket_c = [it for it in data if is_bucket_c(it)]
    bucket_b = [it for it in data if is_bucket_b(it)]
    bucket_a = [it for it in data if not is_bucket_b(it) and not is_bucket_c(it)]

    # Take top quota from each bucket
    selected_c = bucket_c[:ACADEMIC_QUOTA]
    selected_b = bucket_b[:BUCKET_QUOTA]
    selected_a = bucket_a[:BUCKET_QUOTA]

    # Fill-in: if a bucket is short, pull from the others
    shortage_c = ACADEMIC_QUOTA - len(selected_c)
    shortage_b = BUCKET_QUOTA - len(selected_b)
    shortage_a = BUCKET_QUOTA - len(selected_a)

    if shortage_b > 0:
        selected_a = bucket_a[:BUCKET_QUOTA + shortage_b]
    if shortage_a > 0:
        selected_b = bucket_b[:BUCKET_QUOTA + shortage_a]
    if shortage_c > 0:
        # Pull academic shortfall from highest-scored remaining items
        used_urls = {it.get('url') for it in selected_a + selected_b + selected_c}
        fallback = [it for it in data if it.get('url') not in used_urls]
        selected_c = selected_c + fallback[:shortage_c]

    # Deduplicate across all three buckets (URL-based, keep highest score)
    seen_urls: set = set()
    final_data = []
    for item in selected_c + selected_a + selected_b:
        url = item.get('url', '')
        if url and url in seen_urls:
            continue
        seen_urls.add(url) if url else None
        final_data.append(item)
    data = final_data

    print(
        f'Bucket A (Competitor/Market): {len(selected_a)} items. '
        f'Bucket B (Machine/R&D): {len(selected_b)} items. '
        f'Bucket C (Academic/Patents): {len(selected_c)} items. '
        f'Total (after dedup): {len(data)}'
    )

    # Use JST (Asia/Tokyo) to ensure today's date matches the Japan business day
    jst = pytz.timezone('Asia/Tokyo')
    today = datetime.now(jst).strftime('%Y-%m-%d')
    today_dt = datetime.now(jst).date()
    data_dir = os.path.dirname(data_path)

    # ── Collect Top-3 URLs featured in the last 3 days to prevent repetition ──
    recent_top3_urls: set = set()
    for days_back in range(1, 4):
        past_date = (today_dt - timedelta(days=days_back)).strftime('%Y-%m-%d')
        past_file = os.path.join(data_dir, f'{past_date}.json')
        if os.path.exists(past_file):
            try:
                with open(past_file, 'r', encoding='utf-8') as f:
                    past_data = json.load(f)
                for h in past_data.get('highlights', []):
                    url = h.get('url', '')
                    if url:
                        recent_top3_urls.add(url)
            except Exception:
                pass
    if recent_top3_urls:
        print(f'  [TOP3-EXCL] Excluding {len(recent_top3_urls)} URL(s) featured in the last 3 days.')

    # Build Top-3 highlights with strict daily isolation — candidates are
    # restricted to items published within the last 24–48 hours (today +
    # yesterday) so that a fresh today item always outranks any older high-
    # scorer.  Selection still happens after the full 45-item deduplication
    # above to respect quotas.
    highlights = generate_highlights(data, excluded_urls=recent_top3_urls, today_str=today) if data else existing_highlights
    if not highlights:
        highlights = existing_highlights

    save_data(data_path, data, highlights=highlights)
    print(
        f'Updated {updated} items. Highlights: {len(highlights)}. '
        f'Total items saved: {len(data)}'
    )

    # ── Write per-date JSON files & update dates index ────────────────────────
    # (data_dir already defined above)

    # Group non-permanent items by date
    dates_for_files: dict = {}
    for item in data:
        if not item.get('permanent_record'):
            d = item.get('date', 'unknown')
            dates_for_files.setdefault(d, []).append(item)

    for date_str, date_items in dates_for_files.items():
        date_file = os.path.join(data_dir, f'{date_str}.json')
        date_payload = {
            'date': date_str,
            'items': date_items,
            'highlights': highlights if date_str == today else [],
        }
        with open(date_file, 'w', encoding='utf-8') as f:
            json.dump(date_payload, f, ensure_ascii=False, indent=2)
        print(f'  [DATE-FILE] Wrote {date_file} ({len(date_items)} items)')

    index_path = os.path.join(data_dir, 'dates_index.json')
    existing_index: list = []
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                existing_index = json.load(f)
        except Exception:
            existing_index = []
    merged_dates = sorted(
        set(existing_index + list(dates_for_files.keys())), reverse=True
    )
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(merged_dates, f, ensure_ascii=False, indent=2)
    print(f'  [INDEX] dates_index.json updated: {merged_dates}')

    # ── Update permanent_vault.json with all Bucket C items ───────────────────
    vault_path = os.path.join(data_dir, 'permanent_vault.json')
    existing_vault: list = []
    if os.path.exists(vault_path):
        try:
            with open(vault_path, 'r', encoding='utf-8') as f:
                existing_vault = json.load(f)
        except Exception:
            existing_vault = []
    vault_urls = {item.get('url') for item in existing_vault if item.get('url')}
    new_vault_items = [
        item for item in bucket_c
        if item.get('url') and item.get('url') not in vault_urls
    ]
    if new_vault_items:
        updated_vault = existing_vault + new_vault_items
        with open(vault_path, 'w', encoding='utf-8') as f:
            json.dump(updated_vault, f, ensure_ascii=False, indent=2)
        print(
            f'  [VAULT] Added {len(new_vault_items)} items to permanent_vault.json '
            f'(total: {len(updated_vault)})'
        )
    else:
        print(f'  [VAULT] No new Bucket C items (existing vault: {len(existing_vault)})')


if __name__ == '__main__':
    main()
