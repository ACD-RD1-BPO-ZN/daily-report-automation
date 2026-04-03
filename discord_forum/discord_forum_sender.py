import requests
import os
import glob
import json
import re
import time
from dotenv import load_dotenv

load_dotenv()

# DRY_RUN=true → 只印預覽，不呼叫 Discord API（用於本地確認內容）
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
FORUM_CHANNEL_ID = os.getenv("DISCORD_FORUM_CHANNEL_ID")

# ============================================================
# 論壇標籤 ID 設定
# ID 已預設為實際值，可透過 GitHub Secrets 或 .env 覆蓋。
# ============================================================
TAG_IDS = {
    "headline": os.getenv("FORUM_TAG_HEADLINE_ID", "1489205702886031500"),  # 頭條
    "indie":    os.getenv("FORUM_TAG_INDIE_ID",    "1489200534941597828"),  # 遊戲 | 獨立遊戲
    "global":   os.getenv("FORUM_TAG_GLOBAL_ID",   "1489200786427875519"),  # 遊戲 | 全球
    "ai":       os.getenv("FORUM_TAG_AI_ID",        "1489198768883630114"),  # AI | 資訊
    "ue":       os.getenv("FORUM_TAG_UE_ID",        "1489178833004265614"),  # 🟦引擎 | UE
    "unity":    os.getenv("FORUM_TAG_UNITY_ID",     "1489189619210911825"),  # 🟩引擎 | Unity
    "ta":       os.getenv("FORUM_TAG_TA_ID",        "1489189750874308780"),  # 🛠 技術 | TA
    "3d":       os.getenv("FORUM_TAG_3D_ID",        "1489198718258380913"),  # 🛠 技術 | 3D
}

# ============================================================
# 段落 Emoji → 論壇標籤鍵值對應表
# 新增報告段落時，在此處補充對應關係即可。
# Discord 論壇頻道每串最多只能套用 5 個標籤。
# ============================================================
SECTION_TAG_MAP = {
    "📢": ["headline"],   # 今日頭條     → 頭條
    "🎮": ["indie"],      # 獨立遊戲     → 遊戲|獨立遊戲
    "🤝": ["indie"],      # 在地社群     → 遊戲|獨立遊戲
    "💼": ["global"],     # 製作人週記   → 遊戲|全球 (引擎相關自動分流)
    "🌌": ["ai"],         # 深度總結     → AI|資訊
}

# ============================================================
# 標籤顯示名稱（用於討論串標題）
# ============================================================
TAG_DISPLAY_NAMES = {
    "headline": "🔥 頭條",
    "indie":    "💗 遊戲｜獨立遊戲",
    "global":   "🌍 遊戲｜全球",
    "ue":       "📘 引擎｜UE",
    "unity":    "📙 引擎｜Unity",
    "ta":       "🛠️ 技術｜TA",
    "3d":       "🛠️ 技術｜3D",
    "ai":       "🤖 AI｜資訊",
}

# 引擎關鍵字路由（用於將製作人週記中的條目分流到引擎標籤）
ENGINE_ROUTE_KEYWORDS: dict[str, list[str]] = {
    "unity": ["unity"],
    "ue":    ["unreal", "ue5", "ue4"],
}

# ============================================================
# 🎨 引擎段落的子標題拆分設定
# 每個引擎子區塊將被發成獨立的討論串，並掛上精確的單一標籤。
# 格式：(用於比對子標題的 regex, 討論串顯示名稱, tag_keys)
# ============================================================
ENGINE_SUBSECTION_DEFS = [
    # (regex, 串名, tag_keys, 來源連結前綴清單)
    (re.compile(r"\*\*Unreal Engine", re.IGNORECASE), "🟦 Unreal Engine", ["ue"],    ["[Unreal"]),
    (re.compile(r"\*\*Unity",         re.IGNORECASE), "🟩 Unity",         ["unity"], ["[Unity"]),
    (re.compile(r"\*\*Godot",         re.IGNORECASE), "🎮 Godot Engine",  ["ta"],    ["[Godot"]),
    (re.compile(r"\*\*80\.lv",        re.IGNORECASE), "80.lv (TA/Tech)", ["ta"],    ["[80.lv"]),
    (re.compile(r"\*\*映CG|InCG",     re.IGNORECASE), "映CG (3D/CG)",    ["3d"],    ["[映CG", "[InCG"]),
]

# 段落 Emoji → daily_targets.json 中的 section_name 對應
# 用於不依賴 md 內的日期字串就能正確對到圖片
SECTION_IMG_KEY: dict[str, str] = {
    "📢": "Headline",
    "🎨": "TA",
    "🎮": "Indie",
    "🤝": "Local",
    "💼": "Producer",
}

# Discord 允許的圖片 MIME 類型對應
MIME_TYPES = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}

# ============================================================
# Embed 左側顏色邊框對應表（十進位整數，Discord API 格式）
# ============================================================
EMBED_COLORS = {
    "headline": 0xE67E22,  # 橙金  — 今日頭條
    "ue":       0x0E4DA4,  # 深藍  — Unreal Engine
    "unity":    0x48A14D,  # 草綠  — Unity
    "ta":       0x9B59B6,  # 紫    — TA / Godot / 80.lv
    "3d":       0x7F8C8D,  # 灰鋼  — 技術 3D
    "indie":    0x2ECC71,  # 翠綠  — 獨立遊戲
    "global":   0x3498DB,  # 天藍  — 全球 / 在地社群
    "ai":       0x7B68EE,  # 靛紫  — AI 資訊
}
DEFAULT_COLOR = 0x36393F  # Discord 預設深灰


def _resolve_tag_ids(section_text: str) -> list[str]:
    """根據段落開頭的 Emoji，回傳對應的有效 Tag ID 清單。"""
    heading = section_text[:80]
    for emoji, keys in SECTION_TAG_MAP.items():
        if emoji in heading:
            return [TAG_IDS[k] for k in keys if TAG_IDS.get(k)]
    return []


def _resolve_color(tag_keys: list[str]) -> int:
    """依照 tag_keys 回傳第一個命中的 Embed 顏色，無命中則回傳預設色。"""
    for k in tag_keys:
        if k in EMBED_COLORS:
            return EMBED_COLORS[k]
    return DEFAULT_COLOR


def _split_engine_section(section_text: str) -> list[tuple[str, list[str], str]]:
    """
    將 🎨 引擎段落依照引擎子標題（Unreal/Unity/Godot/80.lv）切割為多份，
    並將 [資料來源] 區塊中的連結依引擎名稱前綴分配至對應子串。
    回傳 list of (content, tag_keys, sub_title)。
    """
    # 1. 分離 [資料來源] 區塊與主體內容
    sources_lines: list[str] = []
    body = section_text
    sources_match = re.search(r"\[資料來源\]", section_text)
    if sources_match:
        body = section_text[:sources_match.start()]
        sources_lines = section_text[sources_match.start():].split("\n")

    # 2. 在主體內找各引擎子標題起始位置
    split_points: list[tuple[int, str, list[str], list[str]]] = []
    for pattern, display_name, tag_keys, src_prefixes in ENGINE_SUBSECTION_DEFS:
        for m in pattern.finditer(body):
            line_start = body.rfind("\n", 0, m.start()) + 1
            split_points.append((line_start, display_name, tag_keys, src_prefixes))

    if not split_points:
        return []

    split_points.sort(key=lambda x: x[0])
    results = []
    for i, (start, display_name, tag_keys, src_prefixes) in enumerate(split_points):
        end = split_points[i + 1][0] if i + 1 < len(split_points) else len(body)
        content = body[start:end].strip()
        # 3. 篩選屬於此引擎的來源連結並附加
        if sources_lines and src_prefixes:
            matched = [ln for ln in sources_lines
                       if any(p in ln for p in src_prefixes)]
            if matched:
                content += "\n[資料來源]\n" + "\n".join(matched)
        if content:
            results.append((content, tag_keys, display_name))
    return results


def _route_content_by_engine(content: str, default_tag: str = "global") -> dict[str, str]:
    """
    將段落的 bullet 依引擎關鍵字分流。
    回傳 {tag_key: content_str}，非引擎相關的放在 default_tag。
    來源連結跟隨對應的 bullet 路由，而非獨立比對關鍵字。
    """
    # 移除開頭粗體標題行
    content = re.sub(r"^\*\*[^\n]+\*\*\s*\n*", "", content, count=1).strip()

    # 分離 [資料來源] 區塊
    body = content
    sources_text = ""
    src_match = re.search(r"\[資料來源\]", content)
    if src_match:
        body = content[:src_match.start()].strip()
        sources_text = content[src_match.start():]
    source_lines = [ln for ln in sources_text.split("\n") if ln.strip() and ln.strip() not in ("[資料來源]", "---")]

    # 路由 bullet
    routed_bullets: dict[str, list[str]] = {}
    routed_tags: set[str] = set()        # 被分流到哪些引擎 tag
    for line in body.split("\n"):
        s = line.strip()
        if not s or s == "---":
            continue
        target = default_tag
        lower = s.lower()
        for tag_key, keywords in ENGINE_ROUTE_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                target = tag_key
                break
        routed_bullets.setdefault(target, []).append(line)
        if target != default_tag:
            routed_tags.add(target)

    # 路由來源連結：只有 bullet 確實被分流的引擎 tag 才會收對應來源
    routed_sources: dict[str, list[str]] = {}
    for line in source_lines:
        target = default_tag
        if routed_tags:
            lower = line.strip().lower()
            for tag_key in routed_tags:
                kws = ENGINE_ROUTE_KEYWORDS.get(tag_key, [])
                if any(kw in lower for kw in kws):
                    target = tag_key
                    break
        routed_sources.setdefault(target, []).append(line)

    # 組合結果
    result: dict[str, str] = {}
    all_keys = set(list(routed_bullets.keys()) + list(routed_sources.keys()))
    for tag_key in all_keys:
        parts = []
        if tag_key in routed_bullets:
            parts.append("\n".join(routed_bullets[tag_key]))
        if tag_key in routed_sources:
            parts.append("[資料來源]\n" + "\n".join(routed_sources[tag_key]))
        result[tag_key] = "\n".join(parts).strip()
    return result


def _has_cjk(text: str) -> bool:
    """檢查字串是否包含中日韓文字。"""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))


def _extract_bullet_summary(content: str, max_items: int = 3) -> list[str]:
    """
    從 bullet 正文中提取簡短的中文摘要片段。
    用於沒有粗體/書名號/中文連結時的 fallback。
    """
    summaries: list[str] = []
    for line in content.split("\n"):
        s = line.strip()
        # 跳過非 bullet、來源連結行、純英文行
        if not s.startswith("- ") or s.startswith("- [") or not _has_cjk(s):
            continue
        text = s[2:].strip()
        # 如果開頭是英文，跳到第一個有意義的中文詞開始提取
        cjk_match = re.search(r'[\u4e00-\u9fff]', text)
        if cjk_match and cjk_match.start() > 3:
            text = text[cjk_match.start():]
            # 跳過開頭的單字虛詞（的、了、在、於、為、正、和、與）
            text = re.sub(r'^[的了在於為正和與]\s*', '', text)
        # 取第一個逗號/句號前的片段
        for sep in ("，", "、", "。", "；"):
            idx = text.find(sep)
            if 4 < idx < 22:
                text = text[:idx]
                break
        if len(text) > 20:
            text = text[:20]
            # 如果截斷在英文單字中間，回退到最後一個中文字
            while text and not re.search(r'[\u4e00-\u9fff\u3400-\u4dbf》）]$', text):
                text = text[:-1]
        text = text.strip()
        # 跳過含有《》的摘要（書名號已在 step 2 提取）
        if "《" in text:
            continue
        if len(text) > 4 and text not in summaries:
            summaries.append(text)
        if len(summaries) >= max_items:
            break
    return summaries


def _extract_keywords(content: str, max_keywords: int = 6) -> str:
    """
    從段落內容提取關鍵詞，用於外層預覽。
    優先順序：粗體 → 書名號 → 中文來源連結 → bullet 正文摘要。
    回傳以 ｜ 分隔的關鍵詞字串。
    """
    keywords: list[str] = []
    seen = set()
    # 1. 提取粗體 **xxx** 中的文字
    for m in re.finditer(r"\*\*(.+?)\*\*", content):
        kw = m.group(1).strip().rstrip("：:")
        lower = kw.lower()
        # 過濾掉段落標題類的粗體（太長或含 emoji）
        if len(kw) > 25 or any(ord(c) > 0xFFFF for c in kw):
            continue
        if lower not in seen and len(kw) > 1:
            seen.add(lower)
            keywords.append(kw)
    # 2. 提取書名號《xxx》中的內容
    for m in re.finditer(r"《(.+?)》", content):
        kw = m.group(1).strip()
        lower = kw.lower()
        if lower not in seen and len(kw) > 1:
            seen.add(lower)
            keywords.append(kw)
    # 3. 從中文來源連結 [站名 - 關鍵詞](<url>) 提取關鍵詞部分（僅限含中文的）
    if len(keywords) < max_keywords:
        for m in re.finditer(r"\[([^\]]+)\]\(<https?://[^>]+>\)", content):
            display = m.group(1).strip()
            if " - " in display:
                kw = display.split(" - ", 1)[1].strip()
            else:
                kw = display
            if not _has_cjk(kw):
                continue
            if len(kw) > 32:
                kw = kw[:32].strip()
            lower = kw.lower()
            if lower not in seen and len(kw) > 3:
                seen.add(lower)
                keywords.append(kw)
            if len(keywords) >= max_keywords:
                break
    # 4. Fallback：從 bullet 正文提取中文摘要片段
    if len(keywords) < max_keywords:
        for summary in _extract_bullet_summary(content, max_items=max_keywords - len(keywords)):
            lower = summary.lower()
            if lower not in seen:
                seen.add(lower)
                keywords.append(summary)
    return " ｜ ".join(keywords[:max_keywords])


def _split_into_news_items(content: str) -> list[dict]:
    """
    將合併後的段落內容拆分為獨立新聞條目，每條配對一個來源連結。
    採用線性掃描：收集所有 bullet 和來源連結，按位置配對。
    回傳 [{"text": "...", "source": "..."}, ...]
    """
    all_bullets: list[str] = []
    all_sources: list[str] = []
    current_bullet: list[str] = []

    def _flush():
        if current_bullet:
            all_bullets.append("\n".join(current_bullet))
            current_bullet.clear()

    for line in content.split("\n"):
        s = line.strip()
        if not s or s == "---" or s == "[資料來源]":
            continue
        # 跳過獨立子標題行（**Unreal Engine 相關：**）
        if re.match(r"^\*\*[^*]+\*\*[\uff1a:]?\s*$", s):
            _flush()
            continue
        # 來源連結行：以 "- [" 開頭且含 URL
        if s.startswith("- [") and re.search(r"\(<https?://|\(https?://", s):
            _flush()
            all_sources.append(s)
            continue
        # Bullet 行
        if s.startswith("- "):
            # 跳過引擎子標題 bullet（- **Unity**：）
            stripped = re.sub(r"^-\s*", "", s)
            if re.match(r"^\*\*[^*]+\*\*[\uff1a:]?\s*$", stripped):
                _flush()
                continue
            _flush()
            current_bullet.append(s)
            continue
        # 其他文字：追加到當前 bullet 或作為新的段落
        current_bullet.append(s)

    _flush()

    # 位置配對
    n = max(len(all_bullets), len(all_sources))
    if n == 0 and content.strip():
        return [{"text": content.strip(), "source": ""}]

    items: list[dict] = []
    for i in range(n):
        text = all_bullets[i] if i < len(all_bullets) else ""
        source = all_sources[i] if i < len(all_sources) else ""
        if text or source:
            items.append({"text": text, "source": source})

    return items if items else [{"text": content.strip(), "source": ""}]


def _extract_url_from_source(source: str) -> str:
    """從來源連結文字中提取第一個 URL。"""
    m = re.search(r"<(https?://[^>]+)>", source) or re.search(r"\((https?://[^)]+)\)", source)
    return m.group(1) if m else ""


def _fetch_og_image(url: str) -> str:
    """抓取網頁的 og:image，回傳圖片 URL；失敗則回傳空字串。
    使用完整的瀏覽器 Headers 來避開 Cloudflare / Akamai 等反爬蟲機制。
    對 JS 渲染網站（如 unrealengine.com）自動使用 Playwright fallback。
    """
    if not url:
        return ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
    }
    try:
        resp = requests.get(url, timeout=8, headers=headers)
        if not resp.ok:
            return ""
        # 偵測 Cloudflare 攔截頁
        if any(t in resp.text[:500].lower() for t in ["just a moment", "cloudflare", "checking your browser"]):
            return ""
        og = _parse_og_image(resp.text)
        if og:
            return og
        # 靜態 HTML 找不到 og:image → 對 JS 渲染網站嘗試 Playwright
        if any(domain in url for domain in _JS_RENDERED_DOMAINS):
            return _fetch_og_image_playwright(url)
        return ""
    except Exception:
        return ""


# 需要 Playwright 支援的 JS 渲染網站
_JS_RENDERED_DOMAINS = ["unrealengine.com"]


def _parse_og_image(html: str) -> str:
    """從 HTML 中提取 og:image URL，過濾 logo/placeholder。"""
    pat1 = r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\'>]+)["\']'
    pat2 = r'<meta[^>]+content=["\']([^"\'>]+)["\'][^>]+property=["\']og:image["\']'
    m = re.search(pat1, html) or re.search(pat2, html)
    if not m:
        return ""
    img_url = m.group(1)
    if any(skip in img_url.lower() for skip in ["logo", "avatar", "icon", "default", "placeholder"]):
        return ""
    return img_url


def _fetch_og_image_playwright(url: str) -> str:
    """用 Playwright（反偵測模式）載入 JS 渲染頁面後提取 og:image。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="zh-TW",
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                window.chrome = { runtime: {} };
            """)
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector('meta[property="og:image"]', timeout=5000)
            except Exception:
                pass
            html = page.content()
            browser.close()
        return _parse_og_image(html)
    except Exception:
        return ""


def _post_embed_to_thread(thread_id: str, title: str, description: str,
                          color: int = DEFAULT_COLOR, source: str = "",
                          thumbnail_url: str = "") -> None:
    """在討論串中發送一則獨立的 Embed 訊息（用於單條新聞展示）。"""
    if source:
        description += f"\n\n{source}"

    if DRY_RUN:
        thumb_tag = f" 🖼️" if thumbnail_url else ""
        print(f"  [DRY-RUN] 📰 {title[:60]}{thumb_tag}")
        print(f"  [DRY-RUN]    {description[:150]}")
        return

    url = f"https://discord.com/api/v10/channels/{thread_id}/messages"
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    embed: dict = {"description": description[:4096], "color": color}
    if title:
        embed["title"] = title[:256]
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    resp = requests.post(url, headers=headers, json={"embeds": [embed]})
    if not resp.ok:
        print(f"  ❌ Embed 發送失敗 [{resp.status_code}]: {resp.text[:200]}")
    else:
        print(f"  📰 已發送: {title[:50]}")





def _create_forum_thread(thread_name: str, display_title: str, embed_description: str,
                         tag_ids: list[str], img_path: str | None, color: int = DEFAULT_COLOR,
                         keywords: str = "", img_url: str | None = None) -> str | None:
    """
    在論壇頻道建立新討論串。
    - display_title: Embed 的 title 欄位，顯示較大字體。
    - embed_description: Embed 的 description，包含完整詳細內容。
    - keywords: 外層預覽關鍵詞，放入 message.content。
    - img_url: 外部圖片 URL（當沒有本地圖片時使用，用於避免重複縮圖）。
    回傳建立成功的討論串 Channel ID，失敗則回傳 None。
    DRY_RUN 模式下只印預覽。
    """
    if DRY_RUN:
        tag_names = [k for k, v in TAG_IDS.items() if v in tag_ids]
        print(f"[DRY-RUN] 串名 : {thread_name}")
        print(f"[DRY-RUN] 標籤 : {tag_names}  顏色: #{color:06X}")
        print(f"[DRY-RUN] 圖片 : {img_path or img_url or '(無)'}")
        print(f"[DRY-RUN] 關鍵詞 : {keywords}")
        print(f"[DRY-RUN] Embed 標題 : {display_title}")
        print(f"[DRY-RUN] Embed 內容 (前400字):\n{embed_description[:400]}")
        print("-" * 60)
        return "DRY_RUN_THREAD_ID"

    url = f"https://discord.com/api/v10/channels/{FORUM_CHANNEL_ID}/threads"
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}

    # Embed: title 顯示較大字體，description 上限 4096 字元
    embed: dict = {"title": display_title, "description": embed_description[:4096], "color": color}
    payload: dict = {
        "name": thread_name,
        "message": {
            "content": keywords[:2000] if keywords else "",
            "embeds": [embed],
        },
    }
    if tag_ids:
        payload["applied_tags"] = tag_ids

    if img_path and os.path.exists(img_path):
        ext = os.path.splitext(img_path)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        fname = os.path.basename(img_path)
        embed["image"] = {"url": f"attachment://{fname}"}
        payload["message"]["attachments"] = [{"id": 0, "filename": fname}]
        with open(img_path, "rb") as img_file:
            resp = requests.post(
                url,
                headers=headers,
                files={
                    "payload_json": (None, json.dumps(payload), "application/json"),
                    "files[0]":     (fname, img_file, mime),
                },
            )
    elif img_url:
        # 使用外部圖片 URL（避免重複縮圖時使用）
        embed["image"] = {"url": img_url}
        headers["Content-Type"] = "application/json"
        resp = requests.post(url, headers=headers, json=payload)
    else:
        headers["Content-Type"] = "application/json"
        resp = requests.post(url, headers=headers, json=payload)

    if not resp.ok:
        print(f"❌ 建立討論串失敗 [{resp.status_code}]: {resp.text[:300]}")
        return None

    thread_id = resp.json().get("id")
    print(f"✅ 討論串已建立: 「{thread_name}」 (id={thread_id})")
    return thread_id


def send_to_discord_forum() -> None:
    if DRY_RUN:
        print("🔍 DRY-RUN 模式：只預覽，不發送至 Discord\n" + "=" * 60)
    elif not BOT_TOKEN or not FORUM_CHANNEL_ID:
        print("Error: DISCORD_BOT_TOKEN 或 DISCORD_FORUM_CHANNEL_ID 尚未設定，跳過論壇發布。")
        return

    # 1. 取得最新報告檔案
    report_files = glob.glob(os.path.join("Daily_Report", "Daily_Full_Report_*.md"))
    if not report_files:
        print("找不到報告檔案，中止執行。")
        return
    report_files.sort(key=os.path.getmtime, reverse=True)
    latest_report = report_files[0]
    print(f"发布報告: {latest_report}")

    # 2. 從檔名解析日期字串 (YYYYMMDD → YYYY-MM-DD)
    date_match = re.search(r"(\d{8})", os.path.basename(latest_report))
    raw_date = date_match.group(1) if date_match else "00000000"
    date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"

    with open(latest_report, "r", encoding="utf-8") as f:
        full_text = f.read()

    # 3. 依照大標題切割段落（與 discord_webhook_sender.py 邏輯一致）
    section_pattern = re.compile(
        r"(?=# 📅|\*\*[📢🎨🎮🇨🇳🇹🇼📍💼🤝🌌])",
        re.UNICODE,
    )
    raw_sections = section_pattern.split(full_text)
    sections = [s.strip() for s in raw_sections if s.strip()]

    if not sections:
        # 舊格式 fallback
        raw_sections = full_text.split("## ")
        sections = [("## " + s.strip() if i > 0 else s.strip())
                    for i, s in enumerate(raw_sections) if s.strip()]

    # 4. 讀取圖片路徑映射（依 section_name 匹配，與日期無關）
    section_name_to_img: dict[str, str] = {}
    if os.path.exists("daily_targets.json"):
        with open("daily_targets.json", "r", encoding="utf-8") as f:
            targets_data = json.load(f)
        for item in targets_data:
            sname = item.get("section_name", "").strip()  # e.g. "Headline", "TA"
            fpath = os.path.join("assets", item["image_filename"])
            if os.path.exists(fpath):
                section_name_to_img[sname] = fpath
                print(f"🖼  圖片對應: {sname} → {fpath}")
            else:
                print(f"⚠️  圖片不存在: {fpath}")

    # 5. 依標籤分類聚合內容（每個標籤建立一個討論串）
    buckets: dict[str, list[str]] = {}          # tag_key → content blocks
    bucket_imgs: dict[str, str | None] = {}     # tag_key → first image

    for section in sections:
        # 跳過純日期標題行與深度總結
        if section.startswith("# 📅") or "🌌" in section[:80]:
            print(f"⏭  跳過: {section[:30].strip()}")
            continue

        # 清除 Markdown 圖片標籤
        message_content = re.sub(r"!\[.*?\]\(.*?\)", "", section).strip()
        if not message_content:
            continue

        # 依 section emoji 查找對應圖片
        img_path: str | None = None
        emoji_found = next((e for e in SECTION_IMG_KEY if e in section[:80]), None)
        if emoji_found:
            img_path = section_name_to_img.get(SECTION_IMG_KEY[emoji_found])

        # 🎨 引擎段落：拆分後路由到各引擎標籤桶
        if "🎨" in section[:80]:
            engine_parts = _split_engine_section(message_content)
            for eng_content, eng_tag_keys, _eng_title in engine_parts:
                tag_key = eng_tag_keys[0]
                buckets.setdefault(tag_key, []).append(eng_content)
                if tag_key not in bucket_imgs and img_path:
                    bucket_imgs[tag_key] = img_path
            continue

        # 💼 製作人週記：依引擎關鍵字分流
        if "💼" in section[:80]:
            routed = _route_content_by_engine(message_content, default_tag="global")
            for tag_key, routed_content in routed.items():
                buckets.setdefault(tag_key, []).append(routed_content)
            if "global" not in bucket_imgs and img_path:
                bucket_imgs["global"] = img_path
            continue

        # 一般段落：路由到對應標籤，並將引擎相關條目分流
        emoji_key = next((e for e in SECTION_TAG_MAP if e in section[:80]), None)
        if not emoji_key:
            continue
        tag_key = SECTION_TAG_MAP[emoji_key][0]
        content = re.sub(r"^\*\*[^\n]+\*\*\s*\n*", "", message_content, count=1).strip()
        routed = _route_content_by_engine(content, default_tag=tag_key)
        for rk, rc in routed.items():
            buckets.setdefault(rk, []).append(rc)
        if tag_key not in bucket_imgs and img_path:
            bucket_imgs[tag_key] = img_path

    # 6. 依標籤建立討論串，每條新聞獨立 Embed
    TAG_ORDER = ["ai", "3d", "unity", "ue", "ta", "global", "indie", "headline"]
    used_img_paths: set[str] = set()  # 追蹤已使用的圖片路徑，避免論壇列表縮圖重複
    for tag_key in TAG_ORDER:
        if tag_key not in buckets:
            continue
        merged = "\n\n".join(buckets[tag_key])
        tag_ids = [TAG_IDS[tag_key]] if TAG_IDS.get(tag_key) else []
        color = _resolve_color([tag_key])
        display_name = TAG_DISPLAY_NAMES.get(tag_key, tag_key)
        thread_name = f"{date_str} | {display_name}"[:100]
        img = bucket_imgs.get(tag_key)
        kw = _extract_keywords(merged)

        # 預先拆分新聞，後面建立串和發送 embed 都會用到
        news_items = _split_into_news_items(merged)

        # 判斷是否需要用 og:image 替代重複的本地圖片
        og_fallback_url: str | None = None
        if img and img in used_img_paths:
            # 此圖片已被前一個討論串使用，改用第一條新聞的 og:image
            print(f"🔄 {tag_key}: 本地圖片已被其他串使用，嘗試抓取 og:image 替代...")
            if news_items:
                src_url = _extract_url_from_source(news_items[0].get("source", ""))
                og_fallback_url = _fetch_og_image(src_url)
            if og_fallback_url:
                print(f"✅ {tag_key}: 使用 og:image 作為串縮圖: {og_fallback_url[:80]}")
                img = None  # 不使用本地檔案，改用 og:image URL
            else:
                print(f"⚠️ {tag_key}: 無法取得 og:image，仍使用原圖")
        if img:
            used_img_paths.add(img)

        # 建立討論串（首則訊息為概覽：標題 + 關鍵詞 + 圖片）
        thread_id = _create_forum_thread(
            thread_name, display_name,
            f"今日 {display_name} 共 {len(news_items)} 則新聞，請往下瀏覽各則詳情。",
            tag_ids, img, color, keywords=kw, img_url=og_fallback_url,
        )
        if not thread_id:
            continue

        # 逐條發送：文章 Embed → 資料來源 Embed 交替
        for idx, item in enumerate(news_items, 1):
            # 文章 Embed
            if item["text"]:
                raw = re.sub(r"^\-\s*", "", item["text"].split("\n")[0]).strip()
                title_match = re.search(r"\*\*(.+?)\*\*", raw) or re.search(r"《(.+?)》", raw)
                embed_title = title_match.group(1)[:80] if title_match else raw[:50]
                _post_embed_to_thread(thread_id, embed_title, item["text"], color)
                time.sleep(0.5)
            # 資料來源 Embed（獨立顯示，帶 og:image 縮圖）
            if item["source"]:
                src_url = _extract_url_from_source(item["source"])
                thumb = _fetch_og_image(src_url)
                _post_embed_to_thread(thread_id, "📎 資料來源", item["source"], color, thumbnail_url=thumb)
                time.sleep(0.5)

        time.sleep(1.5)

    print("✅ 所有段落已成功發布至 Discord 論壇頻道！")


if __name__ == "__main__":
    send_to_discord_forum()
