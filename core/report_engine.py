# -*- coding: utf-8 -*-
"""
core/report_engine.py — 多頻道共用的報告生成引擎

職責：
  1. 讀取 channel_config.json 設定
  2. 依設定爬取 RSS/網頁來源
  3. 管理頻道獨立的歷史去重
  4. 組裝 Prompt → 呼叫 Gemini API
  5. 解析 JSON 回應 → 產出 Markdown + daily_targets.json
"""

import os
import json
import re
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import google.genai as genai
from google.genai import types as genai_types
from dotenv import load_dotenv
import feedparser

load_dotenv()

# 台灣時間 (UTC+8)
TZ = timezone(timedelta(hours=8))


# ============================================================
# 通用 Header（對抗 Akamai / Cloudflare）
# ============================================================
BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}


# ============================================================
# 頻道設定讀取
# ============================================================

def load_channel_config(channel_dir: str) -> dict:
    """讀取並回傳頻道設定 (channel_config.json)。"""
    config_path = os.path.join(channel_dir, "channel_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 歷史去重管理
# ============================================================

def load_history(channel_dir: str, config: dict, today: datetime) -> tuple[dict, set]:
    """
    載入頻道專屬歷史紀錄，清除過期條目。
    回傳 (history_dict, recent_urls_set)。
    """
    history_file = os.path.join(channel_dir, "history.json")
    window_days = config.get("history_window_days", 7)
    global_history: dict = {}

    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 向下相容：舊格式為 list-of-lists，自動轉換為 dict
            if isinstance(raw, list):
                print(f"Migrating old array-format history ({len(raw)} days) to dict format...")
                for i, daily_urls in enumerate(raw):
                    backdate = (today - timedelta(days=len(raw) - i)).strftime("%Y-%m-%d")
                    global_history[backdate] = daily_urls
            else:
                global_history = raw
            print(f"Loaded history ({len(global_history)} days)")
        except json.JSONDecodeError:
            print("History file exists but is invalid JSON. Starting fresh.")

    # 清除過期
    cutoff = (today - timedelta(days=window_days)).strftime("%Y-%m-%d")
    expired = [k for k in global_history if k < cutoff]
    for k in expired:
        del global_history[k]
    if expired:
        print(f"Pruned {len(expired)} expired history entries (older than {cutoff})")

    recent_urls = set()
    for daily_urls in global_history.values():
        recent_urls.update(daily_urls)
    print(f"Total unique URLs in history to filter: {len(recent_urls)}")
    return global_history, recent_urls


def save_history(channel_dir: str, config: dict, history: dict,
                 today_urls: list[str], today: datetime) -> None:
    """儲存今日新聞 URL 至歷史紀錄。"""
    if not today_urls:
        return
    history_file = os.path.join(channel_dir, "history.json")
    window_days = config.get("history_window_days", 7)
    today_str = today.strftime("%Y-%m-%d")

    history[today_str] = today_urls
    cutoff = (today - timedelta(days=window_days)).strftime("%Y-%m-%d")
    history = {k: v for k, v in history.items() if k >= cutoff}

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(today_urls)} today's urls to history. Current history days: {len(history)}")


# ============================================================
# RSS 與網頁爬取（依據 channel_config）
# ============================================================

def _extract_incg_title(anchor, article_url: str, headers: dict) -> str:
    """映CG 卡片標題萃取（多路徑 fallback）。"""
    title_text = anchor.get_text(strip=True)
    if title_text:
        return title_text
    for attr in ("title", "aria-label"):
        v = (anchor.get(attr) or "").strip()
        if v:
            return v
    img = anchor.select_one("img[alt]")
    if img:
        alt = (img.get("alt") or "").strip()
        if alt:
            return alt
    try:
        r = requests.get(article_url, headers=headers, timeout=10)
        s = BeautifulSoup(r.text, "html.parser")
        page_title = (s.title.string or "").strip() if s.title else ""
        if page_title:
            return re.sub(r"\s*[-|｜]\s*映CG.*$", "", page_title).strip()
    except Exception:
        pass
    return ""


def fetch_sources(config: dict, recent_urls: set) -> str:
    """
    依據 channel_config 爬取所有來源，回傳 scraped_data 純文字。
    此函式取代原本 generate_report.py 中硬編碼的 fetch_rss_feeds()。
    """
    print("Fetching sources for channel:", config.get("channel_id", "unknown"))
    scraped_data = ""
    recent_news_count = 0
    max_entries = config.get("rss_max_entries", 6)

    feedparser.USER_AGENT = BROWSER_HEADERS['User-Agent']

    # ── 1. RSS feeds ──
    for source_name, url in config.get("rss_feeds", {}).items():
        try:
            parsed = feedparser.parse(url)
            if not parsed.entries:
                print(f"  [Warning] No entries found for {source_name}")
                continue
            scraped_data += f"\n### 來源資訊: {source_name}\n"
            for entry in parsed.entries[:max_entries]:
                link = entry.get('link', '')
                if link in recent_urls:
                    print(f"  [Skip] Recently reported: {link}")
                    continue
                title = entry.get('title', 'No Title')
                summary_raw = entry.get('summary', '') or entry.get('description', '')
                summary_clean = re.sub(r'<[^>]+>', '', summary_raw)[:250].strip()
                scraped_data += f"- 【標題】: {title}\n  【網址 URL】: {link}\n  【摘要前言】: {summary_clean}...\n"
                recent_news_count += 1
        except Exception as e:
            print(f"  [Error] Failed to fetch {source_name}: {e}")

    # ── 2. Scrape fallbacks ──
    for fb in config.get("scrape_fallbacks", []):
        check_key = fb.get("check_key", fb["name"])
        if check_key in scraped_data:
            continue  # RSS 已有資料，跳過 fallback
        try:
            r = requests.get(fb["url"], headers=BROWSER_HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            scraped_data += f'\n### 來源資訊: {fb["name"]}\n'
            count = 0
            exclude_exact = set(fb.get("exclude_href_exact", []))
            for a in soup.select(fb["selector"]):
                href = a.get('href', '')
                if not href.startswith('http'):
                    href = fb.get("base_url", "") + href
                title_text = a.get_text(strip=True)
                min_len = fb.get("min_title_len", 10)
                # 排除首頁連結
                if any(href.rstrip('/').endswith(ex.rstrip('/')) for ex in exclude_exact):
                    continue
                if title_text and len(title_text) > min_len and href not in scraped_data:
                    if href in recent_urls:
                        continue
                    hint = fb.get("summary_hint", "(article)")
                    scraped_data += f'- [標題]: {title_text}\n  [網址 URL]: {href}\n  [摘要前言]: {hint}...\n'
                    count += 1
                    recent_news_count += 1
                    if count >= fb.get("max_items", 5):
                        break
            if count == 0:
                print(f'  [Warning] {fb["name"]} direct scrape returned nothing')
        except Exception as e:
            print(f'  [Error] {fb["name"]} direct scrape failed: {e}')

    # ── 3. Custom scrapers (映CG 等) ──
    for custom in config.get("scrape_custom", []):
        if custom.get("type") == "incg":
            try:
                scraped_data += f'\n### 來源資訊: {custom["name"]}\n'
                count = 0
                seen = set()
                for cat in custom.get("categories", []):
                    r = requests.get(cat["url"], headers=BROWSER_HEADERS, timeout=10)
                    soup = BeautifulSoup(r.text, 'html.parser')
                    for a in soup.select('a[href]'):
                        href = a.get('href', '')
                        slug_prefix = cat["slug_prefix"]
                        if not href.startswith(slug_prefix) or href.rstrip('/') == slug_prefix.rstrip('/'):
                            continue
                        full_url = custom["base_url"] + href
                        title_text = _extract_incg_title(a, full_url, BROWSER_HEADERS)
                        min_len = custom.get("min_title_len", 4)
                        if title_text and len(title_text) > min_len and full_url not in seen:
                            if full_url in recent_urls:
                                continue
                            seen.add(full_url)
                            hint = custom.get("summary_hint", "(article)")
                            scraped_data += f'- [標題]: {title_text}\n  [網址 URL]: {full_url}\n  [摘要前言]: {hint}...\n'
                            count += 1
                            recent_news_count += 1
                            if count >= custom.get("max_items", 6):
                                break
                    if count >= custom.get("max_items", 6):
                        break
                if count == 0:
                    print(f'  [Warning] {custom["name"]} scrape returned nothing')
                else:
                    print(f'  {custom["name"]}: scraped {count} articles')
            except Exception as e:
                print(f'  [Error] {custom["name"]} scrape failed: {e}')

    print(f"Scraped {recent_news_count} news items for context.")
    return scraped_data


# ============================================================
# 語意級去重：擷取近期頭條標題
# ============================================================

def _extract_recent_headlines(today: datetime, lookback_days: int = 3) -> str:
    """
    從過去 N 天的日報 Markdown 中擷取【今日頭條】段落的標題文字。
    回傳供 Prompt 注入的純文字摘要，讓 LLM 避免選擇相同事件作為頭條。
    """
    headlines = []
    report_dir = "Daily_Report"
    if not os.path.isdir(report_dir):
        return ""

    for i in range(1, lookback_days + 1):
        past_date = today - timedelta(days=i)
        filename = os.path.join(report_dir, f"Daily_Full_Report_{past_date.strftime('%Y%m%d')}.md")
        if not os.path.exists(filename):
            continue
        try:
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()
            # 擷取【今日頭條】到下一個段落之間的內容
            headline_match = re.search(
                r'\*\*📢\s*【今日頭條】\*\*.*?\n!\[.*?\]\(.*?\)\n(.*?)(?=\n\*\*[^\n]*【|\n---|\Z)',
                content, re.DOTALL
            )
            if headline_match:
                headline_text = headline_match.group(1).strip()
                # 只取第一行（主要摘要），去除 Markdown 連結
                first_line = headline_text.split('\n')[0].strip()
                first_line = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', first_line)
                first_line = re.sub(r'[<>]', '', first_line)
                if first_line:
                    headlines.append(f"- {past_date.strftime('%m/%d')}: {first_line}")
        except Exception as e:
            print(f"  [Warning] Failed to extract headline from {filename}: {e}")

    if not headlines:
        return ""

    result = "【近期已報導頭條（禁止重複選用相同事件）】\n" + "\n".join(headlines)
    print(f"Injected {len(headlines)} recent headline(s) for semantic dedup")
    return result


# ============================================================
# Prompt 組裝
# ============================================================

def build_prompt(config: dict, channel_dir: str,
                 scraped_context: str, today: datetime) -> str:
    """
    讀取 prompt_template.md + section_rules.json，
    動態組裝完整 Prompt 字串。
    """
    today_str_display = today.strftime("%Y-%m-%d")
    today_str_file = today.strftime("%Y%m%d")
    sections = config.get("sections", [])

    # 讀取模板
    template_path = os.path.join(channel_dir, "prompt_template.md")
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    # 讀取板塊規則
    rules_path = os.path.join(channel_dir, "section_rules.json")
    section_rules = {}
    if os.path.exists(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            section_rules = json.load(f)

    # 組裝各段落的 Prompt 片段
    sections_prompt_parts = []
    image_targets_schema = []

    for sec in sections:
        emoji = sec["emoji"]
        title = sec["title"]
        section_key = sec["section_key"]
        img_tag = f"![{title}](../assets/{section_key.lower()}_{today_str_file}.png)"
        rules = section_rules.get(emoji, {}).get("rules", "(內容)")

        # 建構段落 Prompt
        part = f"**{emoji} 【{title}】**\n{img_tag}\n{rules}\n---"
        sections_prompt_parts.append(part)

        # 建構 JSON Schema 中的 image_targets（精簡版：只要求 LLM 輸出 section_name + source_urls）
        # image_filename / image_keywords 由 Python 在 parse_response 中自動補齊，節省 output tokens
        if sec.get("skip_forum") and section_key == "Synthesis":
            continue  # 深度總結不需要圖片
        keywords = sec.get("image_keywords", [])
        if keywords:
            image_targets_schema.append({
                "section_name": section_key,
                "source_urls": [f"(請挑選相關的{title}網址)"],
            })

    sections_prompt = "\n\n".join(sections_prompt_parts)

    # JSON Schema 範例
    json_schema = json.dumps({
        "markdown_content": "完整的 Markdown 報告字串...",
        "image_targets": image_targets_schema,
    }, ensure_ascii=False, indent=2)

    # 語意級去重：注入近期頭條摘要
    recent_headlines = _extract_recent_headlines(today)

    # 替換模板中的佔位符
    prompt = template.replace("{scraped_context}", scraped_context)
    prompt = prompt.replace("{today_str_display}", today_str_display)
    prompt = prompt.replace("{section_count}", str(len(sections)))
    prompt = prompt.replace("{sections_prompt}", sections_prompt)
    prompt = prompt.replace("{json_schema}", json_schema)
    prompt = prompt.replace("{recent_headlines}", recent_headlines)

    # 向下相容：若模板中沒有 {recent_headlines} 佔位符，則附加在 scraped_context 後方
    if "{recent_headlines}" not in template and recent_headlines:
        prompt += "\n\n" + recent_headlines

    return prompt


# ============================================================
# Gemini API 呼叫與回應解析
# ============================================================

def call_gemini(config: dict, prompt: str) -> str:
    """呼叫 Gemini API 並回傳原始回應文字。"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    client = genai.Client(api_key=api_key)
    model = config.get("gemini_model", "gemini-2.5-flash")
    temperature = config.get("gemini_temperature", 0.2)

    print(f"Requesting content from Gemini ({model}, temp={temperature})...")
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=temperature,
            thinking_config=genai_types.ThinkingConfig(
                thinking_budget=0  # 關閉思考鏈，節省 ~90% API 成本
            ),
        )
    )
    res_text = response.text.strip()

    # 💰 Token 用量監控（用於確認成本優化效果）
    usage = response.usage_metadata
    if usage:
        print(f"💰 Token 用量: input={usage.prompt_token_count}, "
              f"output={usage.candidates_token_count}, "
              f"thinking={getattr(usage, 'thoughts_token_count', 0) or 0}")
        total_output = (usage.candidates_token_count or 0) + (getattr(usage, 'thoughts_token_count', 0) or 0)
        est_cost = (usage.prompt_token_count or 0) * 0.15 / 1e6 + total_output * 2.50 / 1e6
        print(f"💰 預估本次成本: ~${est_cost:.4f}")

    print("--- RAW GEMINI RESPONSE PREVIEW ---")
    print(res_text[:500] + "\n...\n" + res_text[-500:])
    print("-----------------------------------")

    # 移除可能的 Markdown 標記
    json_match = re.search(r'```(?:json)?(.*?)```', res_text, re.DOTALL)
    if json_match:
        res_text = json_match.group(1).strip()
    return res_text


def parse_response(res_text: str, today_str_file: str, config: dict = None) -> dict | None:
    """解析 Gemini 回應的 JSON，執行防呆驗證，並自動補齊 image_filename / image_keywords。"""
    try:
        report_data = json.loads(res_text, strict=False)
    except json.JSONDecodeError as e:
        print(f"Error parsing Gemini response as JSON: {e}")
        return None

    # 從 config 建立 section_key → {filename, keywords} 的查找表
    section_meta: dict[str, dict] = {}
    if config:
        for sec in config.get("sections", []):
            sk = sec.get("section_key", "")
            if sk:
                section_meta[sk] = {
                    "image_filename": f"{sk.lower()}_{today_str_file}.png",
                    "image_keywords": sec.get("image_keywords", []),
                }

    # 防呆：檢查 source_urls，並補齊 LLM 不再輸出的欄位
    valid_targets = []
    for target in report_data.get("image_targets", []):
        urls = target.get("source_urls", [])
        if "source_url" in target and not urls:
            urls = [target["source_url"]]
        formatted = [u for u in urls if u and (u.startswith("http") or u == "GENERATE_AI_IMAGE")]
        if formatted:
            target["source_urls"] = formatted
            # 自動補齊 image_filename / image_keywords（若 LLM 未輸出）
            sec_name = target.get("section_name", "")
            meta = section_meta.get(sec_name, {})
            if "image_filename" not in target and meta:
                target["image_filename"] = meta["image_filename"]
            if "image_keywords" not in target and meta:
                target["image_keywords"] = meta["image_keywords"]
            # 最終保底：若仍無 filename，用 section_name 推導
            if "image_filename" not in target:
                target["image_filename"] = f"{sec_name.lower()}_{today_str_file}.png"
            valid_targets.append(target)
        else:
            print(f"Warning: Discarding invalid URLs for section {target.get('section_name')}")

    report_data["image_targets"] = valid_targets
    return report_data


# ============================================================
# 輸出
# ============================================================

def save_outputs(report_data: dict, today_str_file: str) -> list[str]:
    """
    儲存 Markdown 報告與 daily_targets.json。
    回傳今日所有 URL 的列表（供歷史紀錄）。
    """
    # 1. Markdown
    os.makedirs("Daily_Report", exist_ok=True)
    md_filename = os.path.join("Daily_Report", f"Daily_Full_Report_{today_str_file}.md")
    with open(md_filename, "w", encoding="utf-8") as f:
        f.write(report_data.get("markdown_content", "No markdown content returned."))
    print(f"Generated Markdown report: {md_filename}")

    # 2. daily_targets.json
    image_targets = report_data.get("image_targets", [])
    with open("daily_targets.json", "w", encoding="utf-8") as f:
        json.dump(image_targets, f, ensure_ascii=False, indent=2)
    print(f"Generated targets file: daily_targets.json with {len(image_targets)} valid targets.")

    # 3. 萃取今日所有 URL
    today_urls = []
    md_content = report_data.get("markdown_content", "")
    for m in re.finditer(r"\((https?://[^)]+)\)|<(https?://[^>]+)>", md_content):
        url = m.group(1) or m.group(2)
        if url and url != "GENERATE_AI_IMAGE" and url not in today_urls:
            today_urls.append(url)
    return today_urls


def save_fallback(res_text: str, today: datetime) -> None:
    """當 JSON 解析失敗時，儲存 Fallback Markdown。"""
    today_str_file = today.strftime("%Y%m%d")
    today_str_display = today.strftime("%Y-%m-%d")
    os.makedirs("Daily_Report", exist_ok=True)
    md_filename = os.path.join("Daily_Report", f"Daily_Full_Report_{today_str_file}.md")
    with open(md_filename, "w", encoding="utf-8") as f:
        f.write(f"# Ultimate Daily Full Report ({today_str_display})\n\n")
        f.write("Error generating formatted JSON. Raw output from Gemini:\n\n")
        f.write(res_text)
    print(f"Fallback Markdown saved to: {md_filename}")


# ============================================================
# 主流程入口
# ============================================================

async def generate_report(channel_dir: str) -> None:
    """
    完整的報告生成流程。
    channel_dir: 頻道設定目錄（例如 'channels/gamedev'）
    """
    today = datetime.now(TZ)
    today_str_file = today.strftime("%Y%m%d")

    print(f"=== Report Engine: {channel_dir} ===")
    print(f"Generating report for: {today.strftime('%Y-%m-%d')}")

    # 1. 讀取設定
    config = load_channel_config(channel_dir)
    print(f"Channel: {config.get('display_name', config.get('channel_id'))}")

    # 2. 載入歷史 + 爬取來源
    history, recent_urls = load_history(channel_dir, config, today)
    scraped_context = fetch_sources(config, recent_urls)

    if not scraped_context.strip():
        scraped_context = "無法取得即時新聞，請以過去 48 小時內廣為人知的新聞進行撰寫，但嚴格標示網址。"
        print("Warning: Scraped context is empty.")

    # 3. 組裝 Prompt + 呼叫 LLM
    prompt = build_prompt(config, channel_dir, scraped_context, today)
    res_text = call_gemini(config, prompt)

    # 4. 解析回應 + 輸出
    report_data = parse_response(res_text, today_str_file, config=config)
    if report_data is None:
        save_fallback(res_text, today)
        return

    today_urls = save_outputs(report_data, today_str_file)

    # 5. 更新歷史
    save_history(channel_dir, config, history, today_urls, today)

    print(f"=== Report generation complete for {config.get('channel_id')} ===")
