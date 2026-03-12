import asyncio
from playwright.async_api import async_playwright
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import shutil
import base64
from dotenv import load_dotenv
import re
import urllib.parse
load_dotenv()

# ── 統一的「真實瀏覽器偽裝」Header，對抗 Akamai / Cloudflare ──
REAL_BROWSER_HEADERS = {
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

# 圖片最小有效大小（bytes），低於此值視為 icon/placeholder
MIN_IMAGE_SIZE = 5000  # 5KB

def normalize_image_url(url):
    """
    移除網址中常見的尺寸後綴、Query Parameter 與 Fragment，
    確保同一張圖的不同尺寸變體都能被正確識別為重複。
    """
    try:
        parsed = urllib.parse.urlparse(url)
        # 移除 query string 和 fragment
        base = parsed._replace(query='', fragment='').geturl()
        # 移除檔名中的解析度後綴（多種格式）
        # 例如: -1024x576, _800w, @2x, @3x, -large, -medium, -small
        base = re.sub(r'([_-]\d+(?:x\d+)?w?)(@\d+x)?(\.[a-zA-Z0-9]+)$', r'\3', base, flags=re.IGNORECASE)
        # 移除 CDN 常見的 /w_1200/ 或 /q_80,w_1200/ 這種路徑參數（Cloudinary 風格）
        base = re.sub(r'/[a-z_,]+\d+[a-z_,]*/', '/', base, flags=re.IGNORECASE)
        return base.lower()
    except Exception:
        return url.lower()

def is_valid_image(file_path):
    """檢查圖片檔案是否有效（大小超過門檻）"""
    if not os.path.exists(file_path):
        return False
    size = os.path.getsize(file_path)
    if size < MIN_IMAGE_SIZE:
        print(f"  ⚠ Image too small ({size} bytes < {MIN_IMAGE_SIZE}), treating as invalid.")
        return False
    return True

def extract_best_src(img_tag):
    """從 img 標籤中依優先順序提取最佳 URL（支援 lazy-load 屬性與 srcset）"""
    for attr in ['data-lazy-src', 'data-original', 'data-src', 'src']:
        val = (img_tag.get(attr) or '').strip()
        if val and not val.startswith('data:'):
            return val
    # srcset: 取最後一項（通常是最高解析度）
    srcset = (img_tag.get('srcset') or '').strip()
    if srcset:
        parts = [p.strip().split(' ')[0] for p in srcset.split(',') if p.strip()]
        if parts:
            return parts[-1]
    return ''

async def download_image(url, save_path, section_type="", image_keywords=None, used_image_urls=None):
    if used_image_urls is None:
        used_image_urls = set()
    if image_keywords is None:
        image_keywords = []

    print(f"\n--- Processing {url} for Section: {section_type} ---")
    
    headers = REAL_BROWSER_HEADERS
    
    # Priority 1: requests + bs4 — 靜態分析，絕對優先
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        
        # Check for login wall in final URL
        final_url = r.url.lower()
        if "auth.epicgames.com" in final_url or "login" in final_url:
             print(f"  Priority 1: 🛑 Detected login redirect to {final_url}. Skipping.")
             return False
             
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Check for Cloudflare/Bot Validation in HTML Title
        if soup.title and any(t in soup.title.text.lower() for t in ['just a moment', 'cloudflare', 'attention required', 'checking your browser']):
             print(f"  Priority 1: 🛑 Detected Cloudflare/verification page '{soup.title.text}'. Skipping.")
             return False
             
        # === Strategy A: Try og:image first ===
        og_img = soup.find('meta', property='og:image')
        if og_img and og_img.get('content'):
            og_url = og_img['content']
            if any(skip in og_url.lower() for skip in ['logo', 'avatar', 'icon', 'default', 'placeholder']):
                print(f"  Priority 1A: og:image {og_url} appears to be a generic logo, skipping Strategy A.")
            else:
                print(f"  Priority 1A: Found og:image: {og_url}")
                try:
                    img_data = requests.get(og_url, headers=headers, timeout=10).content
                    with open(save_path, 'wb') as f:
                        f.write(img_data)
                    if is_valid_image(save_path):
                        if normalize_image_url(og_url) not in used_image_urls:
                            print(f"  Priority 1A: ✅ og:image saved successfully ({len(img_data)} bytes)")
                            used_image_urls.add(normalize_image_url(og_url))
                            return True
                        else:
                            print(f"  Priority 1A: og:image {og_url} already used in previous section, trying next strategy...")
                    else:
                        print(f"  Priority 1A: og:image too small, trying next strategy...")
                except Exception as e:
                    print(f"  Priority 1A: og:image download failed: {e}")

        # === Strategy A2: JSON-LD 結構化資料挖掘（新聞網站最準確的封面圖來源）===
        print("  Priority 1A2: Scanning JSON-LD blocks...")
        json_ld_img_url = None
        for script_tag in soup.find_all('script', type='application/ld+json'):
            try:
                ld_data = json.loads(script_tag.string or '')
                ld_items = ld_data if isinstance(ld_data, list) else [ld_data]
                for item in ld_items:
                    for field in ['thumbnailUrl', 'image']:
                        val = item.get(field)
                        if val:
                            if isinstance(val, dict):
                                val = val.get('url', '')
                            elif isinstance(val, list):
                                val = val[0] if val else ''
                                if isinstance(val, dict):
                                    val = val.get('url', '')
                            if val and isinstance(val, str) and val.startswith('http'):
                                if not any(s in val.lower() for s in ['logo', 'avatar', 'icon', 'default', 'placeholder']):
                                    json_ld_img_url = val
                                    break
                    if json_ld_img_url:
                        break
            except Exception:
                continue

        if json_ld_img_url:
            print(f"  Priority 1A2: Found JSON-LD image: {json_ld_img_url}")
            try:
                img_data = requests.get(json_ld_img_url, headers=headers, timeout=10).content
                with open(save_path, 'wb') as f:
                    f.write(img_data)
                if is_valid_image(save_path):
                    if normalize_image_url(json_ld_img_url) not in used_image_urls:
                        print(f"  Priority 1A2: ✅ JSON-LD image saved ({len(img_data)} bytes)")
                        used_image_urls.add(normalize_image_url(json_ld_img_url))
                        return True
                    else:
                        print(f"  Priority 1A2: Already used, continuing...")
                else:
                    print(f"  Priority 1A2: Too small, continuing...")
            except Exception as e:
                print(f"  Priority 1A2: Download failed: {e}")

        # === Strategy B: Scan <img> 標籤 — 全屬性掃描（含 lazy-load）===
        candidate_urls = []
        
        # 合併所有候選關鍵字
        all_keywords = list(image_keywords) if image_keywords else []
        
        # 通用高品質圖片關鍵字（適用於所有 section）
        generic_keywords = ['hero', 'cover', 'headline', 'banner', 'feature', 
                          'featured', 'thumbnail', 'preview', 'spotlight',
                          'screenshot', 'main-image', 'post-image', 'article-image']
        
        # TA 專用額外關鍵字
        ta_keywords = ['shader', 'wireframe', 'profiling', 'performance', 
                       'draw call', 'side-by-side', 'node', 'graph',
                       'render', 'viewport', 'engine', 'unreal', 'unity',
                       'godot', 'material', 'lighting', 'scene']
        
        if all_keywords:
            search_keywords = all_keywords + generic_keywords
        elif section_type == "TA":
            search_keywords = ta_keywords + generic_keywords
        else:
            search_keywords = generic_keywords
            
        for img in soup.find_all('img'):
            src = extract_best_src(img)   # ← 全屬性掃描：data-lazy-src / data-original / srcset
            if not src:
                continue
            src_url = src if src.startswith('http') else urljoin(url, src)
            alt_text = img.get('alt', '').lower()
            src_lower = src_url.lower()
            
            if src_lower.endswith('.svg') or src_lower.endswith('.gif'):
                continue
                
            combined_text = src_lower + ' ' + alt_text
            
            if any(kw.lower() in combined_text for kw in search_keywords):
                if normalize_image_url(src_url) not in used_image_urls:
                    candidate_urls.append(src_url)

        # === Strategy B2: CSS background-image 掃描（針對 80.lv 等把封面放在 div style 的網站）===
        if not candidate_urls:
            print("  Priority 1B2: Scanning CSS background-image on div/section/header tags...")
            bg_pattern = re.compile(r'background-image\s*:\s*url\(["\'\s]?([^"\')\s]+)["\'\s]?\)', re.IGNORECASE)
            for tag in soup.find_all(['div', 'section', 'figure', 'article', 'header']):
                style_val = tag.get('style', '')
                match = bg_pattern.search(style_val)
                if match:
                    bg_url = match.group(1).strip().strip('"\'')
                    if not bg_url.startswith('http'):
                        bg_url = urljoin(url, bg_url)
                    bg_lower = bg_url.lower()
                    if bg_lower.endswith('.svg') or bg_lower.endswith('.gif'):
                        continue
                    if any(s in bg_lower for s in ['logo', 'icon', 'avatar']):
                        continue
                    if normalize_image_url(bg_url) not in used_image_urls:
                        print(f"  Priority 1B2: Found CSS background-image: {bg_url[:100]}")
                        candidate_urls.append(bg_url)
                        if len(candidate_urls) >= 3:
                            break

        # === Strategy C: Fallback — 第一張「大圖」(寬度屬性 >= 400px，強化門檻) ===
        if not candidate_urls:
            print(f"  Priority 1C: No match, trying first large img (width>=400) fallback...")
            for img in soup.find_all('img'):
                src = extract_best_src(img)
                if not src:
                    continue
                src_url = src if src.startswith('http') else urljoin(url, src)
                src_lower = src_url.lower()
                if src_lower.endswith('.svg') or src_lower.endswith('.gif'):
                    continue
                width = img.get('width', '')
                height = img.get('height', '')
                try:
                    w = int(str(width).replace('px', ''))
                except (ValueError, TypeError):
                    w = 0
                try:
                    h = int(str(height).replace('px', ''))
                except (ValueError, TypeError):
                    h = 0
                # 強化：寬度門檻從 300 提升到 400，降低抓到小圖的機率
                if w >= 400 or h >= 200 or (w == 0 and h == 0):
                    if src_url not in used_image_urls:
                        candidate_urls.append(src_url)
                        if len(candidate_urls) >= 3:
                            break
                            
        # === Strategy D: Ultimate Fallback — 面積最大的 <img> ===
        if not candidate_urls:
            print(f"  Priority 1D: Still no matches, finding the absolute largest img tag...")
            largest_url = None
            max_area = 0
            for img in soup.find_all('img'):
                src = extract_best_src(img)
                if not src: continue
                src_url = src if src.startswith('http') else urljoin(url, src)
                if normalize_image_url(src_url) in used_image_urls: continue
                src_lower = src_url.lower()
                if src_lower.endswith('.svg') or src_lower.endswith('.gif'):
                    continue
                try: w = int(str(img.get('width', '0')).replace('px', ''))
                except: w = 0
                try: h = int(str(img.get('height', '0')).replace('px', ''))
                except: h = 0
                area = w * h
                if area > max_area:
                    max_area = area
                    largest_url = src_url
            if largest_url:
                print(f"  Priority 1D: Found largest candidate w*h={max_area}: {largest_url}")
                candidate_urls.append(largest_url)

        # 嘗試下載候選圖片
        for i, cand_url in enumerate(candidate_urls):
            try:
                print(f"  Priority 1: Trying candidate {i+1}/{len(candidate_urls)}: {cand_url[:100]}...")
                img_data = requests.get(cand_url, headers=headers, timeout=10).content
                with open(save_path, 'wb') as f:
                    f.write(img_data)
                if is_valid_image(save_path):
                    print(f"  Priority 1: ✅ Saved successfully ({len(img_data)} bytes)")
                    used_image_urls.add(normalize_image_url(cand_url))
                    return True
                else:
                    print(f"  Priority 1: Candidate too small, trying next...")
            except Exception as e:
                print(f"  Priority 1: Candidate download failed: {e}")
                continue
        
        print("  Priority 1: No valid images found via HTTP requests.")
            
    except Exception as e:
        print(f"  Priority 1 (Direct Download) failed: {e}")

    # ── 靜態優先閘門：走到這裡代表所有靜態手段失敗，才允許 Playwright ──
    print("  ⚠ All static strategies failed. Falling back to Playwright (Priority 2)...")
    try:
        async with async_playwright() as p:
            # ── 反偵測 Playwright 啟動參數 ──
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--disable-gpu',
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=REAL_BROWSER_HEADERS['User-Agent'],
                locale='zh-TW',
                extra_http_headers={
                    k: v for k, v in REAL_BROWSER_HEADERS.items()
                    if k != 'User-Agent'
                }
            )
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-TW', 'zh', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)

            try:
                # ── 強化：使用 networkidle，確保所有資源載入完畢，絕不截到 Loading 動畫 ──
                print("  Priority 2: Navigating with wait_until=networkidle...")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=45000)
                except Exception:
                    print("  Priority 2: networkidle timeout, trying domcontentloaded...")
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as e2:
                        print(f"  Priority 2: Navigation completely failed: {e2}")
                        await context.close()
                        await browser.close()
                        return False

                # ── 強制等待 Loading 動畫消失 ──
                print("  Priority 2: Waiting for loading spinners to disappear...")
                for sel in ['.loading', '.spinner', '.loader',
                            '[class*="loading"]', '[class*="spinner"]',
                            '[class*="skeleton"]', '[aria-busy="true"]']:
                    try:
                        await page.wait_for_selector(sel, state='hidden', timeout=4000)
                    except Exception:
                        pass  # 找不到或已消失皆可繼續

                current_url = page.url.lower()
                page_title = (await page.title()).lower()
                
                if "auth.epicgames.com" in current_url or "login" in current_url:
                    print(f"  Priority 2: 🛑 Login wall detected. Skipping.")
                    await context.close()
                    await browser.close()
                    return False
                    
                if any(t in page_title for t in ["captcha", "challenge", "human verification", "just a moment", "cloudflare"]):
                    print(f"  Priority 2: ⚠️ Verification page detected. Waiting 5s...")
                    await page.wait_for_timeout(5000)
                    page_title_retry = (await page.title()).lower()
                    if any(t in page_title_retry for t in ["captcha", "challenge", "human verification", "just a moment", "cloudflare"]):
                        print(f"  Priority 2: 🛑 Still stuck on verification. Skipping.")
                        await context.close()
                        await browser.close()
                        return False
                
                print("  Priority 2: Scrolling to trigger lazy images...")
                await page.evaluate("""
                    window.scrollTo(0, document.body.scrollHeight / 2);
                    setTimeout(() => window.scrollTo(0, 0), 1000);
                """)
                await page.wait_for_timeout(2000)
                    
            except Exception as e:
                print(f"  Playwright navigation failed: {e}")
                await context.close()
                await browser.close()
                return False

            captured = False

            # ── 驗證頁面攔截閘門：截圖前先確認頁面不是錯誤/驗證頁 ──
            try:
                page_content_text = await page.content()
                page_content_lower = page_content_text.lower()
                current_url_check = page.url.lower()

                BLOCK_URL_KEYWORDS = [
                    'auth.epicgames.com', 'captcha', 'recaptcha',
                    'sso.epicgames.com', '/login', '/signin'
                ]
                BLOCK_CONTENT_KEYWORDS = [
                    'access denied', '403 forbidden', '429 too many',
                    'security verification', 'human verification',
                    '安全驗證', '人機驗證', '瀏覽器擴充功能不相容',
                    'browser extension', 'verification required',
                    'prove you are human', 'are you human',
                    'enable javascript', 'please enable cookies',
                    'this page isn\'t available', 'page not found'
                ]

                url_blocked = any(kw in current_url_check for kw in BLOCK_URL_KEYWORDS)
                content_blocked = any(kw in page_content_lower for kw in BLOCK_CONTENT_KEYWORDS)

                if url_blocked or content_blocked:
                    reason = f'URL={current_url_check[:80]}' if url_blocked else 'content verification keywords'
                    print(f"  Priority 2: 🛑 Pre-screenshot blocker triggered ({reason}). Aborting screenshot.")
                    await context.close()
                    await browser.close()
                    return False

                print("  Priority 2: ✅ Pre-screenshot check passed. Page looks clean.")
            except Exception as e:
                print(f"  Priority 2: Pre-screenshot check failed (non-critical): {e}")
                page_content_text = ''

            # ── 嘗試 0: 從已渲染的 DOM 再提取靜態圖片 src（絕對優先於截圖，避免截到 loading 畫面）──
            try:
                print("  Priority 2-DOM: Extracting img srcs from rendered DOM...")
                rendered_soup = BeautifulSoup(page_content_text or await page.content(), 'html.parser')
                for img_el in rendered_soup.find_all('img'):
                    src = extract_best_src(img_el)
                    if not src:
                        continue
                    src_url = src if src.startswith('http') else urljoin(url, src)
                    src_lower = src_url.lower()
                    if src_lower.endswith('.svg') or src_lower.endswith('.gif'):
                        continue
                    try: w = int(str(img_el.get('width', '0')).replace('px', ''))
                    except: w = 0
                    try: h = int(str(img_el.get('height', '0')).replace('px', ''))
                    except: h = 0
                    if w >= 400 or h >= 200 or (w == 0 and h == 0):
                        if normalize_image_url(src_url) not in used_image_urls:
                            try:
                                img_data = requests.get(src_url, headers=REAL_BROWSER_HEADERS, timeout=10).content
                                with open(save_path, 'wb') as f:
                                    f.write(img_data)
                                if is_valid_image(save_path):
                                    print(f"  Priority 2-DOM: ✅ Downloaded rendered img ({len(img_data)} bytes)")
                                    used_image_urls.add(normalize_image_url(src_url))
                                    await context.close()
                                    await browser.close()
                                    return True
                            except Exception:
                                continue
            except Exception as e:
                print(f"  Priority 2-DOM extraction failed: {e}")

            captured = False
            
            # 嘗試 1: 找頁面中第一張大圖元素直接截圖（門檻提升至 400x200）
            try:
                images = await page.query_selector_all('img')
                for img_el in images:
                    box = await img_el.bounding_box()
                    if box and box['width'] >= 400 and box['height'] >= 200:
                        await img_el.screenshot(path=save_path)
                        if is_valid_image(save_path):
                            print(f"  Priority 2: ✅ Captured large image element ({box['width']}x{box['height']})")
                            captured = True
                            break
            except Exception as e:
                print(f"  Priority 2 image element capture failed: {e}")

            # 嘗試 2: 截取頁面上半部（通常包含主圖和標題）
            if not captured:
                try:
                    print("  Priority 2: Capturing top section of page...")
                    await page.screenshot(path=save_path, clip={
                        "x": 0, "y": 0,
                        "width": 1280, "height": 600
                    })
                    if is_valid_image(save_path):
                        print(f"  Priority 2: ✅ Top section captured")
                        captured = True
                except Exception as e:
                    print(f"  Priority 2 top section capture failed: {e}")

            # 嘗試 3: 全頁面截圖（最終 fallback）
            if not captured:
                print("  Priority 2: Capturing full viewport as absolute fallback...")
                await page.screenshot(path=save_path)
                print(f"  Priority 2: ✅ Saved viewport screenshot")

            await context.close()
            await browser.close()
            return True
            
    except Exception as e:
        print(f"  Playwright fallback absolutely failed: {e}")
        return False

async def main():
    assets_dir = os.path.join(os.getcwd(), "assets")
    os.makedirs(assets_dir, exist_ok=True)
    
    # Ensure a default cover exists for AI stub fallback
    default_cover_path = os.path.join(assets_dir, "default_cover.png")
    if not os.path.exists(default_cover_path):
        print("Creating placeholder default_cover.png")
        try:
            placeholder = requests.get('https://placehold.co/1200x630/2C2F33/FFFFFF.png?text=AI+Synthesis+Placeholder', timeout=5).content
            with open(default_cover_path, 'wb') as f:
                f.write(placeholder)
        except Exception:
            # Create dummy bytes if network fails
            with open(default_cover_path, 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

    targets_file = os.path.join(os.getcwd(), "daily_targets.json")
    targets_data = []
    if not os.path.exists(targets_file):
        print(f"Warning: Targets file not found at {targets_file}. Skipping image fetch.")
    else:
        try:
            with open(targets_file, "r", encoding="utf-8") as f:
                targets_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error parsing {targets_file}: {e}")
            print("Skipping image fetch due to invalid JSON.")
            
    used_image_urls = set()
    
    # 建立 global fallback URLs
    global_candidate_urls = []
    for item in targets_data:
        srcs = item.get("source_urls", [])
        if "source_url" in item and not srcs:
            srcs = [item.get("source_url")]
        for s in srcs:
            if s and s != "GENERATE_AI_IMAGE":
                global_candidate_urls.append(s)
    
    for item in targets_data:
        try:
            source_urls = item.get("source_urls", [])
            # Fallback for old schema
            if "source_url" in item and not source_urls:
                source_urls = [item.get("source_url")]
                
            filename = item.get("image_filename")
            sec_type = item.get("section_name")
            ai_prompt = item.get("ai_prompt")
            image_keywords = item.get("image_keywords", [])
            
            # For AI Synthesis
            if sec_type == "Synthesis" or (source_urls and source_urls[0] == "GENERATE_AI_IMAGE"):
                print(f"\n--- Generating AI Synthesis Art for Section: {sec_type} ---")
                print(f"Prompt: {ai_prompt}")
                success = False
                
                try:
                    from google import genai as new_genai
                    from google.genai import types as genai_types
                    
                    API_KEY = os.getenv("GEMINI_API_KEY")
                    if not API_KEY:
                        print("Error: GEMINI_API_KEY not found in environment for Image Generation.")
                        success = False
                        break
                    
                    client = new_genai.Client(api_key=API_KEY)
                    
                    target_path = os.path.join(assets_dir, filename)
                    print(f"AI Image: Requesting imagen-4.0-generate-001...")
                    result = client.models.generate_images(
                        model='imagen-4.0-generate-001',
                        prompt=ai_prompt,
                        config=genai_types.GenerateImagesConfig(
                            number_of_images=1,
                            output_mime_type="image/jpeg",
                            aspect_ratio="16:9",
                        )
                    )
                    
                    if result.generated_images:
                        generated_image = result.generated_images[0]
                        image_bytes = generated_image.image.image_bytes
                        with open(target_path, 'wb') as f:
                            f.write(image_bytes)
                        file_size = os.path.getsize(target_path)
                        print(f"AI Image: ✅ Successfully saved ({file_size} bytes) to {target_path} using Gemini API")
                        success = True
                    else:
                        print("AI Image: ❌ Failed to generate from Gemini API, no images returned.")
                        
                except Exception as e:
                    print(f"AI Image Fallback: ❌ Error during Gemini Image generation: {e}")
                
                if not success:
                    target_path = os.path.join(assets_dir, filename)
                    print(f"AI Image absolute fallback: Copying default_cover.png to {target_path}")
                    if os.path.exists(default_cover_path):
                        shutil.copy(default_cover_path, target_path)
            else:
                if not source_urls:
                    print(f"No URLs provided for target: {filename}. Skipping.")
                    continue
                    
                target_path = os.path.join(assets_dir, filename)
                print(f"Target path: {target_path}")
                
                success = False
                for target_url in source_urls:
                    print(f"\nEvaluating URL for {sec_type}: {target_url}")
                    is_downloaded = await download_image(target_url, target_path, sec_type, image_keywords, used_image_urls)
                    if is_downloaded:
                        print(f"✅ Successfully acquired image for {sec_type} from {target_url}")
                        success = True
                        break
                    else:
                        print(f"❌ Failed to acquire image from {target_url}, trying next URL...")
                
                # 執行 Global Fallback
                if not success:
                    print(f"❌ Failed to acquire image from primary URLs, attempting Global Fallback for {sec_type}...")
                    for fallback_url in global_candidate_urls:
                        # 避免重複抓取剛剛已經失敗的 URL 或已經使用的 URL
                        if fallback_url in source_urls or normalize_image_url(fallback_url) in used_image_urls:
                            continue
                        print(f"  Attempting Global Fallback URL: {fallback_url}")
                        is_downloaded = await download_image(fallback_url, target_path, sec_type, [], used_image_urls)
                        if is_downloaded:
                            print(f"✅ Successfully acquired image for {sec_type} via Global Fallback from {fallback_url}")
                            success = True
                            break

                if not success:
                    print(f"⚠ Absolute failure for {sec_type}. Using AI Synthesis Placeholder fallback cover.")
                    if os.path.exists(default_cover_path):
                        shutil.copy(default_cover_path, target_path)
                
        except Exception as e:
            # 強化容錯處理 (Error Handling)，確保不會 Crash 整個迴圈
            print(f"Error processing item {item}: {e}")
            continue

if __name__ == "__main__":
    asyncio.run(main())
