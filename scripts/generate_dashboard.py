import json
import os
import re
import time
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

# Maximum retry attempts for high-value items that fail the formatting check
MAX_RETRIES = 3
# Items with a score above this threshold are retried if formatting is poor
RETRY_SCORE_THRESHOLD = 80
# Maximum retry attempts when a 429 RESOURCE_EXHAUSTED error is received
MAX_429_RETRIES = 3
# Seconds to sleep between every Gemini API call (15 RPM free-tier limit)
GEMINI_THROTTLE_SECONDS = 12
# Seconds to wait before retrying after a 429 response
GEMINI_429_WAIT_SECONDS = 30

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
    """Call Gemini API with rate limiting and 429 retry logic.

    Sleeps GEMINI_THROTTLE_SECONDS *after* every successful call to stay within
    the free-tier 15 RPM limit.  On a 429 / RESOURCE_EXHAUSTED error, waits
    GEMINI_429_WAIT_SECONDS (which already exceeds the throttle interval) and
    retries up to MAX_429_RETRIES times.  Raises the last exception if all
    retries are exhausted.
    """
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            response = client.models.generate_content(model=model, contents=contents)
            time.sleep(GEMINI_THROTTLE_SECONDS)  # throttle between calls
            return response
        except Exception as e:
            err_str = str(e)
            is_rate_limit = (
                '429' in err_str
                or 'RESOURCE_EXHAUSTED' in err_str
                or 'quota' in err_str.lower()
            )
            if is_rate_limit and attempt < MAX_429_RETRIES:
                print(
                    f'  [429] Rate limited. Waiting {GEMINI_429_WAIT_SECONDS}s '
                    f'before retry {attempt + 1}/{MAX_429_RETRIES}...'
                )
                time.sleep(GEMINI_429_WAIT_SECONDS)
            else:
                raise


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
        client = google_genai.Client(api_key=api_key)

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
        response = _gemini_generate(client, 'gemini-2.0-flash', prompt)
        text = response.text.strip()
        if text.strip().upper() == 'IRRELEVANT':
            print(f'  [AI-IRRELEVANT] {title[:60]}')
            return False, None
        return True, text[:300]
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
        client = google_genai.Client(api_key=api_key)
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
        response = _gemini_generate(client, 'gemini-2.0-flash', prompt)
        text = response.text.strip()
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

        if score > RETRY_SCORE_THRESHOLD and fmt_feedback:
            print(
                f'  [RETRY {attempt + 1}/{MAX_RETRIES}] score={score}, '
                f'feedback: {fmt_feedback[:80]}'
            )
            feedback = fmt_feedback
            # Loop again with the feedback
        else:
            # Acceptable quality or low score — no retry needed
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

    updated = 0
    irrelevant_indices = []
    for idx, item in enumerate(data):
        # Strip HTML from existing summary
        summary = strip_html(item.get('summary', ''))
        if item.get('summary', '') != summary:
            item['summary'] = summary

        # Skip if already has a quality summary, a score, and an impact analysis
        has_quality_summary = len(summary) >= 80 and '<' not in summary
        has_score = (item.get('score') is not None) and (item.get('score', 0) > 0)
        has_impact = bool(item.get('impact_analysis'))
        if has_quality_summary and has_score and has_impact:
            continue

        if not api_key:
            # No API key — assign defaults so all items have required fields
            if not has_score:
                item['score'] = 0
            if not has_impact:
                item['impact_analysis'] = ''
            continue

        is_relevant = process_item_with_retry(item, api_key)
        if not is_relevant:
            irrelevant_indices.append(idx)
        else:
            updated += 1

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
    highlights = generate_highlights(source_items, api_key) if source_items else existing_highlights
    if not highlights:
        highlights = existing_highlights

    save_data(data_path, data, highlights=highlights)
    print(
        f'Updated {updated} items. Highlights: {len(highlights)}. '
        f'Total items saved: {len(data)}'
    )


if __name__ == '__main__':
    main()
