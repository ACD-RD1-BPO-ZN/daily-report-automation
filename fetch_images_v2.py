import asyncio
from playwright.async_api import async_playwright
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import shutil
import base64
import io
from dotenv import load_dotenv
import re
import urllib.parse
from PIL import Image
load_dotenv()

# ── 開發用：設為 True 時，所有圖片都改用 assets/Test.jpg 暫代，跳過抓圖 ──
USE_LOCAL_TEST_IMAGE = False

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

def convert_to_png(img_data: bytes) -> bytes:
    """
    將任意格式的圖片 bytes 轉碼為真實 PNG 格式。
    """
    img = Image.open(io.BytesIO(img_data))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def normalize_image_url(url):
    """
    移除網址中常見的尺寸後綴或 Query Parameter，避免同一張圖因為不同尺寸檔名造成重複。
    """
    try:
        # 移除 URL 參數 (例如 ?width=1200)
        base = urllib.parse.urlparse(url)._replace(query="").geturl()
        # 移除檔名中像是 _1024x576, -800w 這樣的解析度後綴
        base = re.sub(r'([_-]\d+(?:x\d+|w))(\.[a-zA-Z0-9]+)$', r'\2', base, flags=re.IGNORECASE)
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

async def download_image(url, save_path, section_type="", image_keywords=None, used_image_urls=None):
    if used_image_urls is None:
        used_image_urls = set()
    if image_keywords is None:
        image_keywords = []

    print(f"\n--- Processing {url} for Section: {section_type} ---")
    
    headers = REAL_BROWSER_HEADERS
    
    # Priority 1: requests + bs4 for og:image or direct img src
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
                    png_data = convert_to_png(img_data)
                    with open(save_path, 'wb') as f:
                        f.write(png_data)
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

        # === Strategy B: Scan <img> tags with keywords ===
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
            src = img.get('src') or img.get('data-src') or ''
            if not src:
                continue
            src_url = src if src.startswith('http') else urljoin(url, src)
            alt_text = img.get('alt', '').lower()
            src_lower = src_url.lower()
            
            # 排除 SVG 和 GIF (Discord 支援不佳)
            if src_lower.endswith('.svg') or src_lower.endswith('.gif'):
                continue
                
            combined_text = src_lower + ' ' + alt_text
            
            # --- 新增：排除載入動畫圖 (Loading Spinner) ---
            if any(skip in combined_text for skip in ['loading', 'spinner', 'loader', 'placeholder']):
                continue
            # --------------------------------------------
            
            if any(kw.lower() in combined_text for kw in search_keywords):
                # 如果這張圖還沒被其他段落用過，就加進候選名單
                if normalize_image_url(src_url) not in used_image_urls:
                    candidate_urls.append(src_url)
                    if len(candidate_urls) >= 5: # 找到足夠多大圖就先停，節省效能
                        break

        # === Strategy C: Fallback — 第一張「大圖」(寬度/高度屬性 > 300) ===
        if not candidate_urls:
            print(f"  Priority 1B: No keyword match, trying first large image fallback...")
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src') or ''
                if not src:
                    continue
                src_url = src if src.startswith('http') else urljoin(url, src)
                
                # 排除明顯的 icon/logo
                src_lower = src_url.lower()
                if src_lower.endswith('.svg') or src_lower.endswith('.gif'):
                    continue
                
                # 嘗試從 HTML 屬性判斷圖片大小
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
                    
                # 若有明確的尺寸屬性且夠大，或沒有尺寸屬性（可能是 CSS 控制的大圖）
                if w >= 300 or h >= 200 or (w == 0 and h == 0):
                    if src_url not in used_image_urls:
                        candidate_urls.append(src_url)
                        if len(candidate_urls) >= 3:
                            break
                            
        # === Strategy D: Ultimate Fallback — 如果上面的規則都沒找到圖，直接抓面積最大的 <img> ===
        if not candidate_urls:
            print(f"  Priority 1C: Still no matches, finding the absolute largest image tag on the page...")
            largest_url = None
            max_area = 0
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src') or ''
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
                print(f"  Priority 1C: Found largest candidate with w*h={max_area}: {largest_url}")
                candidate_urls.append(largest_url)

        # 嘗試下載候選圖片
        for i, cand_url in enumerate(candidate_urls):
            try:
                print(f"  Priority 1: Trying candidate {i+1}/{len(candidate_urls)}: {cand_url[:100]}...")
                img_data = requests.get(cand_url, headers=headers, timeout=10).content
                png_data = convert_to_png(img_data)
                with open(save_path, 'wb') as f:
                    f.write(png_data)
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

    # Priority 2: Playwright fallback — 截取頁面上半部（包含主圖區域）
    print("  Falling back to Playwright for Priority 2...")
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
            # 使用完整 UA 建立 context，並隱藏 webdriver 特徵
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
            # 注入 JS 讓 navigator.webdriver 回傳 undefined
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-TW', 'zh', 'en-US', 'en']
                });
                window.chrome = { runtime: {} };
            """)

            try:
                # 調整為 networkidle，等待網路請求初步穩定
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # --- 新增：針對動態網頁內容的偵測機制 ---
                # .cooked 是 Unreal 論壇的核心內容 class
                content_selectors = [".cooked", "article", ".post-content", ".main-content"]
                found_content = False
                for selector in content_selectors:
                    try:
                        # 只要內容容器出現，就停止等待
                        await page.wait_for_selector(selector, timeout=5000)
                        found_content = True
                        break
                    except:
                        continue
                
                if not found_content:
                    await page.wait_for_timeout(3000) # 沒找到容器才用保底等待
                # --------------------------------------
                
                # Check for login/verification walls
                current_url = page.url.lower()
                page_title = await page.title()
                page_title = page_title.lower()
                
                # 新增獲取網頁純文字內容，用來檢查 Akamai 特徵 (加上 try-except 避免 body 沒載入報錯)
                try:
                    page_text = (await page.inner_text("body")).lower() if await page.query_selector("body") else ""
                except Exception:
                    page_text = ""
                
                if "auth.epicgames.com" in current_url or "login" in current_url:
                    print(f"  Priority 2: 🛑 Detected strict login wall at {current_url} | Title: {page_title}. Skipping.")
                    await browser.close()
                    return False
                    
                # 1. 針對絕對不會通過的 CDN 阻擋 (Akamai 403 / Access Denied)，直接放棄
                if "access denied" in page_title or "403 forbidden" in page_title or "reference #" in page_text:
                    print(f"  Priority 2: 🛑 Detected Akamai/CDN block at {current_url}. Skipping.")
                    await browser.close()
                    return False

                # 2. 針對「可能」可以繞過的 Cloudflare 驗證，保留你原本的等待 5 秒重試機制
                if "captcha" in page_title or "challenge" in page_title or "human verification" in page_title or "just a moment" in page_title or "cloudflare" in page_title:
                    print(f"  Priority 2: ⚠️ Detected Cloudflare/verification at {current_url}. Waiting extra 5 seconds to bypass...")
                    await page.wait_for_timeout(5000)
                    
                    # 再檢查一次，如果還是卡在驗證畫面就放棄，避免截下一張盾牌圖
                    page_title_retry = (await page.title()).lower()
                    if "captcha" in page_title_retry or "challenge" in page_title_retry or "human verification" in page_title_retry or "just a moment" in page_title_retry or "cloudflare" in page_title_retry:
                        print(f"  Priority 2: 🛑 Still stuck on Cloudflare/verification | Title: {page_title_retry}. Skipping.")
                        await browser.close()
                        return False
                
                print("  Priority 2: Scrolling down to trigger lazy-loaded images...")
                # Scroll down and up to trigger lazy-loaded images
                await page.evaluate("""
                    window.scrollTo(0, document.body.scrollHeight / 2);
                    setTimeout(() => window.scrollTo(0, 0), 1000);
                """)
                await page.wait_for_timeout(2000)
                
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    print("  Priority 2: Networkidle timeout, proceeding anyway...")
                # --- 💡 就在這裡插入掃除代碼！ ---
                print("  Priority 2: Cleaning up popups and overlays...")
                await page.evaluate("""
                    () => {
                        const selectors = [
                            '[class*="modal"]', '[id*="modal"]', 
                            '[class*="popup"]', '[id*="popup"]', 
                            '[class*="newsletter"]', '[class*="subscription"]',
                            '.overlay', '.backdrop', '.tp-backdrop', '.tp-modal',
                            '[id*="newsletter"]', '#spu-main'
                        ];
                        selectors.forEach(s => {
                            document.querySelectorAll(s).forEach(el => {
                                el.style.display = 'none';
                                el.style.visibility = 'hidden';
                                el.style.opacity = '0';
                            });
                        });
                        document.body.style.overflow = 'auto';
                    }
                """)
                await page.wait_for_timeout(500) 
                # --------------------------------------        
            except Exception as e:
                print(f"  Playwright navigation failed: {e}")
                await browser.close()
                return False

            captured = False
            
            # 嘗試 1: 找頁面中第一張大圖元素直接截圖
            try:
                images = await page.query_selector_all('img')
                for img_el in images:
                    box = await img_el.bounding_box()
                    if box and box['width'] >= 300 and box['height'] >= 150:
                        await img_el.screenshot(path=save_path)
                        if is_valid_image(save_path):
                            print(f"  Priority 2: ✅ Captured large image element")
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
    
    # ── 開發用：直接用 Test.jpg 暫代所有圖片 ──
    if USE_LOCAL_TEST_IMAGE:
        test_image_path = os.path.join(assets_dir, "Test.jpg")
        if not os.path.exists(test_image_path):
            print(f"⚠ Test image not found at {test_image_path}")
        else:
            for item in targets_data:
                filename = item.get("image_filename")
                if filename:
                    target_path = os.path.join(assets_dir, filename)
                    shutil.copy(test_image_path, target_path)
                    print(f"🧪 [TEST MODE] Copied Test.jpg → {filename}")
            print("🧪 [TEST MODE] All images substituted with Test.jpg. Done.")
            return
    
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
                            output_mime_type="image/png",
                            aspect_ratio="16:9",
                        )
                    )
                    
                    if result.generated_images:
                        generated_image = result.generated_images[0]
                        image_bytes = generated_image.image.image_bytes
                        png_bytes = convert_to_png(image_bytes)
                        with open(target_path, 'wb') as f:
                            f.write(png_bytes)
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
