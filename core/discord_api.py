# -*- coding: utf-8 -*-
"""
core/discord_api.py — Discord API 共用工具

提供 Webhook 發送、Forum Thread 建立、Embed 發送等基礎操作，
供各頻道的 sender 腳本呼叫，避免重複實作 API 互動邏輯。
"""

import os
import json
import requests
import time

# Discord 允許的圖片 MIME 類型對應
MIME_TYPES = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}

DEFAULT_COLOR = 0x36393F  # Discord 預設深灰


def post_webhook_message(webhook_url: str, content: str,
                         file_path: str | None = None) -> bool:
    """透過 Webhook 發送一則訊息（可附帶一張圖片）。"""
    payload = {"content": content}
    files = {}
    if file_path and os.path.exists(file_path):
        fname = os.path.basename(file_path)
        files = {"file": (fname, open(file_path, "rb"))}

    resp = requests.post(webhook_url, data=payload, files=files)
    return resp.ok


def post_embed_to_thread(bot_token: str, thread_id: str, title: str,
                         description: str, color: int = DEFAULT_COLOR,
                         source: str = "", thumbnail_url: str = "",
                         dry_run: bool = False) -> bool:
    """在討論串中發送一則 Embed 訊息。"""
    if source:
        description += f"\n\n{source}"

    if dry_run:
        thumb_tag = " 🖼️" if thumbnail_url else ""
        print(f"  [DRY-RUN] 📰 {title[:60]}{thumb_tag}")
        print(f"  [DRY-RUN]    {description[:150]}")
        return True

    url = f"https://discord.com/api/v10/channels/{thread_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
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
    return resp.ok


def create_forum_thread(bot_token: str, forum_channel_id: str,
                        thread_name: str, display_title: str,
                        embed_description: str, tag_ids: list[str],
                        img_path: str | None = None,
                        color: int = DEFAULT_COLOR,
                        keywords: str = "",
                        img_url: str | None = None,
                        dry_run: bool = False) -> str | None:
    """
    在論壇頻道建立新討論串。
    回傳建立成功的 Thread ID，失敗則回傳 None。
    """
    if dry_run:
        print(f"[DRY-RUN] 串名 : {thread_name}")
        print(f"[DRY-RUN] 圖片 : {img_path or img_url or '(無)'}")
        print(f"[DRY-RUN] Embed 標題 : {display_title}")
        print(f"[DRY-RUN] Embed 內容 (前400字):\n{embed_description[:400]}")
        print("-" * 60)
        return "DRY_RUN_THREAD_ID"

    url = f"https://discord.com/api/v10/channels/{forum_channel_id}/threads"
    headers = {"Authorization": f"Bot {bot_token}"}

    embed: dict = {
        "title": display_title,
        "description": embed_description[:4096],
        "color": color,
    }
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
                url, headers=headers,
                files={
                    "payload_json": (None, json.dumps(payload), "application/json"),
                    "files[0]": (fname, img_file, mime),
                },
            )
    elif img_url:
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


def create_divider_thread(bot_token: str, forum_channel_id: str,
                          date_str: str, dry_run: bool = False) -> None:
    """建立每日分割線討論串。"""
    divider_name = f"━━━ {date_str} ━━━"
    if dry_run:
        print(f"[DRY-RUN] 分割線 : {divider_name}  (無標籤)")
        return

    url = f"https://discord.com/api/v10/channels/{forum_channel_id}/threads"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    divider_embed = {
        "description": f"━━━━━━━━━━━━━━━━━━━━\n📅 **{date_str}**\n━━━━━━━━━━━━━━━━━━━━",
        "color": 0x2C2F33,
    }
    payload = {
        "name": divider_name[:100],
        "message": {"content": "", "embeds": [divider_embed]},
    }
    resp = requests.post(url, headers=headers, json=payload)
    if resp.ok:
        print(f"📏 分割線已建立: {divider_name}")
    else:
        print(f"⚠️ 分割線建立失敗: {resp.status_code}")


def fetch_og_image(url: str, cache: dict | None = None) -> str:
    """從快取或網頁中取得 og:image URL。"""
    if not url:
        return ""
    if cache and url in cache:
        og = cache[url].get("og_image", "")
        if og:
            return og
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        import re
        resp = requests.get(url, timeout=5, headers=headers)
        if resp.ok:
            pat1 = r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\'<>]+)["\']'
            pat2 = r'<meta[^>]+content=["\']([^"\'<>]+)["\'][^>]+property=["\']og:image["\']'
            m = re.search(pat1, resp.text) or re.search(pat2, resp.text)
            if m:
                img_url = m.group(1)
                if not any(skip in img_url.lower() for skip in
                           ["logo", "avatar", "icon", "default", "placeholder"]):
                    return img_url
    except Exception:
        pass
    return ""
