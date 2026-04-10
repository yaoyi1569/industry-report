import json
import os
import re
import requests
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

class DailyQuotaExhausted(Exception):
    """Raised when a Gemini API key has hit its daily quota (limit: 0)."""


# Maximum retry attempts for high-value items that fail the formatting check
MAX_RETRIES = 3
# Items with a score above this threshold are retried if formatting is poor
RETRY_SCORE_THRESHOLD = 80

# OpenRouter configuration
_OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'
_OPENROUTER_MODELS = ['deepseek/deepseek-chat', 'google/gemini-flash-1.5']

# 20+20 bucket quota — top 20 from each bucket → 40 items total
BUCKET_QUOTA = 20
TOP_N = BUCKET_QUOTA * 2  # 40

# Bucket B: Production / Machine R&D
# An item belongs to Bucket B when its category_id or info_type match any of these signals.
BUCKET_B_CATEGORY_IDS = {'③', '④'}
BUCKET_B_INFO_TYPES = {'加工機技術', '包装機技術', '研究開発', '特許'}
BUCKET_B_COMPANY_KEYWORDS = [
    'zuiko', '瑞光', 'gdm', 'fameccanica', 'optima', 'fanuc', 'ファナック',
]


def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text or '').strip()


def _gemini_generate(client, model, contents):
    """Call Gemini API once with no retries or delays.

    Raises DailyQuotaExhausted when the API confirms the daily cap has been hit.
    Any other error (including 404/503) is re-raised immediately so the caller
    can fall through to the next tier in the waterfall.
    """
    try:
        response = client.models.generate_content(model=model, contents=contents)
        return response
    except Exception as e:
        err_str = str(e)
        err_lower = err_str.lower()
        is_daily_quota = (
            'limit: 0' in err_str
            or '"limit":0' in err_str
            or '"limit": 0' in err_str
            or 'DAILY_LIMIT_EXCEEDED' in err_str
            or 'daily limit' in err_lower
            or 'per day' in err_lower
        )
        if is_daily_quota:
            raise DailyQuotaExhausted(err_str) from e
        raise


def _openrouter_generate(prompt):
    """Call OpenRouter API as Tier 3 fallback.

    Tries each model in _OPENROUTER_MODELS in order.  Returns the response
    text on success, or raises the last exception if all models fail.
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
    last_error = None
    for model in _OPENROUTER_MODELS:
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        try:
            resp = requests.post(_OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f'  [OPENROUTER] {model} failed: {e}')
            last_error = e
    raise last_error if last_error else RuntimeError('All OpenRouter models failed')


# ============================================================
# AGENT A — Summarizer
# ============================================================

def ai_summarize(title, snippet, company, api_key, retry_feedback=None):
    """Agent A: Generate a Japanese factual news summary using Gemini API.

    When *retry_feedback* is provided (a string with specific improvement instructions
    from Agent B), it is appended to the prompt so the model can correct the issues.

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
        client = google_genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})

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

        # ── Waterfall: Tier 1 → Tier 2 → Tier 3 ──────────────────────────
        text = None
        # Tier 1: Direct — Google Gemini 2.5 Flash
        try:
            response = _gemini_generate(client, 'gemini-2.5-flash', prompt)
            text = response.text.strip()
        except DailyQuotaExhausted:
            raise  # propagate so main() can rotate keys
        except Exception as e1:
            print(f'  [TIER1-FAIL] gemini-2.5-flash: {e1}. Trying Tier 2...')
            # Tier 2: Direct Fallback — Google Gemini 1.5-flash
            try:
                response = _gemini_generate(client, 'gemini-1.5-flash', prompt)
                text = response.text.strip()
            except DailyQuotaExhausted:
                raise
            except Exception as e2:
                print(f'  [TIER2-FAIL] gemini-1.5-flash: {e2}. Trying Tier 3 (OpenRouter)...')
                # Tier 3: OpenRouter
                try:
                    text = _openrouter_generate(prompt)
                except Exception as e3:
                    print(f'  [TIER3-FAIL] OpenRouter: {e3}. Marking as "AI Summary Pending".')
                    return True, 'AI Summary Pending'

        if text and text.strip().upper() == 'IRRELEVANT':
            print(f'  [AI-IRRELEVANT] {title[:60]}')
            return False, None
        return True, (text or '')[:300]
    except DailyQuotaExhausted:
        raise
    except Exception as e:
        print(f'  Gemini error for "{title[:40]}...": {e}')
        return True, None


# ============================================================
# AGENT B — Auditor
# ============================================================

def audit_item(title, summary, company, api_key):
    """Agent B: Critically evaluate a news summary as a senior R&D director at Daio Paper.

    Assigns a unique 1–100 impact score with heavy weight on strategic R&D relevance.
    Returns a 3-tuple: (score: int, impact_analysis: str, formatting_feedback: str | None).
    *formatting_feedback* is non-None only when there are correctable formatting issues.
    """
    if not GENAI_AVAILABLE or not api_key:
        return 0, '', None
    try:
        client = google_genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
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

        # ── Waterfall: Tier 1 → Tier 2 → Tier 3 ──────────────────────────
        text = None
        # Tier 1: Direct — Google Gemini 2.5 Flash
        try:
            response = _gemini_generate(client, 'gemini-2.5-flash', prompt)
            text = response.text.strip()
        except DailyQuotaExhausted:
            raise
        except Exception as e1:
            print(f'  [TIER1-FAIL] gemini-2.5-flash: {e1}. Trying Tier 2...')
            # Tier 2: Direct Fallback — Google Gemini 1.5-flash
            try:
                response = _gemini_generate(client, 'gemini-1.5-flash', prompt)
                text = response.text.strip()
            except DailyQuotaExhausted:
                raise
            except Exception as e2:
                print(f'  [TIER2-FAIL] gemini-1.5-flash: {e2}. Trying Tier 3 (OpenRouter)...')
                # Tier 3: OpenRouter
                try:
                    text = _openrouter_generate(prompt)
                except Exception as e3:
                    print(f'  [TIER3-FAIL] OpenRouter: {e3}. Skipping audit.')
                    return 0, '', None

        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text)
        score = int(result.get('score', 0))
        score = max(1, min(100, score))
        impact_analysis = (result.get('impact_analysis') or '')[:300]
        formatting_feedback = result.get('formatting_feedback') or None
        return score, impact_analysis, formatting_feedback
    except DailyQuotaExhausted:
        raise
    except Exception as e:
        print(f'  Audit error for "{title[:40]}...": {e}')
        return 0, '', None


# ============================================================
# DUAL-AGENT PIPELINE WITH RETRY
# ============================================================

def process_item_with_retry(item, api_key):
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
            title, snippet, company, api_key, retry_feedback=feedback
        )
        if not is_relevant:
            return False

        current_summary = new_summary or best_summary
        if not current_summary:
            # Cannot obtain a usable summary; stop retrying
            break

        score, impact_analysis, fmt_feedback = audit_item(
            title, current_summary, company, api_key
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

def generate_highlights(items, api_key):
    """Build Top-3 highlights from the already-scored items.

    Items are expected to be sorted by score descending.  The impact_analysis
    field generated by Agent B is reused — no additional API call is needed.
    Falls back to a Gemini-generated strategic summary when items lack scores.
    """
    scored = [it for it in items if it.get('score', 0) > 0]
    top3 = (scored if scored else items)[:3]

    highlights = []
    for i, item in enumerate(top3):
        highlights.append({
            'rank': i + 1,
            'title': item.get('title', ''),
            'company': item.get('company', '不明'),
            'category': (
                (item.get('category_name') or '') + ' / ' +
                (item.get('info_type') or '')
            ),
            'date': item.get('date', ''),
            'impact': item.get('impact_analysis') or item.get('summary') or '',
            'score': item.get('score', 0),
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

    api_key_1 = os.environ.get('GEMINI_API_KEY', '')
    api_key_2 = os.environ.get('GEMINI_API_KEY_2', '')
    api_keys = [k for k in [api_key_1, api_key_2] if k]

    if not api_keys:
        print('WARNING: GEMINI_API_KEY not set. Summaries and scores will not be updated.')

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

    # Active API key management
    key_idx = 0
    active_key = api_keys[key_idx] if api_keys else ''

    updated = 0
    irrelevant_indices = []
    both_keys_exhausted = False

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

        if not active_key:
            # No API key — assign defaults so all items have required fields
            if not has_score:
                item['score'] = 0
            if not has_impact:
                item['impact_analysis'] = ''
            idx += 1
            continue

        try:
            is_relevant = process_item_with_retry(item, active_key)
        except DailyQuotaExhausted:
            exhausted_idx = key_idx
            key_idx += 1
            if key_idx >= len(api_keys):
                print(
                    f'  [QUOTA] All {len(api_keys)} API key(s) have hit the daily quota. '
                    'Saving progress and exiting.'
                )
                both_keys_exhausted = True
                break
            active_key = api_keys[key_idx]
            print(
                f'  [QUOTA] Key index {exhausted_idx} exhausted (daily limit reached). '
                f'Switching to key index {key_idx}. Retrying current item (idx={idx})...'
            )
            # Retry the same item with the new key (do not advance idx)
            continue

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

    # ── 20+20 Bucket System ───────────────────────────────────────────────
    # Bucket B: Production / Machine R&D
    # Bucket A: Competitor / Market Intelligence (everything else)

    def is_bucket_b(item):
        if item.get('category_id') in BUCKET_B_CATEGORY_IDS:
            return True
        if item.get('info_type') in BUCKET_B_INFO_TYPES:
            return True
        company_lower = (item.get('company') or '').lower()
        title_lower = (item.get('title') or '').lower()
        return any(kw in company_lower or kw in title_lower for kw in BUCKET_B_COMPANY_KEYWORDS)

    bucket_b = [it for it in data if is_bucket_b(it)]
    bucket_a = [it for it in data if not is_bucket_b(it)]

    # Take top BUCKET_QUOTA from each; if one bucket is short, fill from the other
    selected_b = bucket_b[:BUCKET_QUOTA]
    selected_a = bucket_a[:BUCKET_QUOTA]

    shortage_b = BUCKET_QUOTA - len(selected_b)
    shortage_a = BUCKET_QUOTA - len(selected_a)

    if shortage_b > 0:
        # Bucket B is short — pull extras from A beyond its own quota
        selected_a = bucket_a[:BUCKET_QUOTA + shortage_b]
    if shortage_a > 0:
        # Bucket A is short — pull extras from B beyond its own quota
        selected_b = bucket_b[:BUCKET_QUOTA + shortage_a]

    data = selected_a + selected_b

    print(
        f'Bucket A (Competitor/Market): {len(selected_a)} items. '
        f'Bucket B (Machine/R&D): {len(selected_b)} items. '
        f'Total kept: {len(data)}'
    )

    # Build Top-3 highlights from best-scored items
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    today_items = [item for item in data if item.get('date', '') == today]
    source_items = today_items if today_items else data
    highlights = generate_highlights(source_items, active_key) if source_items else existing_highlights
    if not highlights:
        highlights = existing_highlights

    save_data(data_path, data, highlights=highlights)
    print(
        f'Updated {updated} items. Highlights: {len(highlights)}. '
        f'Total items saved: {len(data)}'
    )
    if both_keys_exhausted:
        print('NOTE: Saved partial progress due to daily quota exhaustion on all API keys.')


if __name__ == '__main__':
    main()
