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
load_dotenv()

# 圖片最小有效大小（bytes），低於此值視為 icon/placeholder
MIN_IMAGE_SIZE = 5000  # 5KB

def is_valid_image(file_path):
    """檢查圖片檔案是否有效（大小超過門檻）"""
    if not os.path.exists(file_path):
        return False
    size = os.path.getsize(file_path)
    if size < MIN_IMAGE_SIZE:
        print(f"  ⚠ Image too small ({size} bytes < {MIN_IMAGE_SIZE}), treating as invalid.")
        return False
    return True

async def download_image(url, save_path, section_type="", image_keywords=None):
    if image_keywords is None:
        image_keywords = []

    print(f"\n--- Processing {url} for Section: {section_type} ---")
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
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
                    with open(save_path, 'wb') as f:
                        f.write(img_data)
                    if is_valid_image(save_path):
                        print(f"  Priority 1A: ✅ og:image saved successfully ({len(img_data)} bytes)")
                        return True
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
            combined_text = src_lower + ' ' + alt_text
            
            if any(kw.lower() in combined_text for kw in search_keywords):
                candidate_urls.append(src_url)

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
                    candidate_urls.append(src_url)
                    if len(candidate_urls) >= 3:
                        break

        # 嘗試下載候選圖片
        for i, cand_url in enumerate(candidate_urls):
            try:
                print(f"  Priority 1: Trying candidate {i+1}/{len(candidate_urls)}: {cand_url[:100]}...")
                img_data = requests.get(cand_url, headers=headers, timeout=10).content
                with open(save_path, 'wb') as f:
                    f.write(img_data)
                if is_valid_image(save_path):
                    print(f"  Priority 1: ✅ Saved successfully ({len(img_data)} bytes)")
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
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_extra_http_headers({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                
                # Check for login/verification walls
                current_url = page.url.lower()
                page_title = await page.title()
                page_title = page_title.lower()
                
                if "auth.epicgames.com" in current_url or "login" in current_url or "captcha" in page_title or "challenge" in page_title or "human verification" in page_title or "just a moment" in page_title or "cloudflare" in page_title:
                    print(f"  Priority 2: 🛑 Detected login/captcha wall at {current_url} | Title: {page_title}. Skipping this URL.")
                    await browser.close()
                    return False
                    
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
                
                import base64
                
                try:
                    from google import genai as new_genai
                    from google.genai import types as genai_types
                    
                    # 使用使用者提供的 API KEY 或環境變數
                    API_KEY = "AIzaSyA9JS2ZU4RW7L4C2AkMROUa9Tta4hiHzcs"
                    os.environ["GEMINI_API_KEY"] = API_KEY
                    client = new_genai.Client(api_key=API_KEY)
                    
                    target_path = os.path.join(assets_dir, filename)
                    # 依據最新 SDK 使用 imagen-4.0-generate-001 產圖
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
                        
                except ImportError as imp_e:
                    print(f"AI Image: ❌ Import failed — 'google-genai' package not installed: {imp_e}")
                    print("  Fix: pip install google-genai")
                except Exception as e:
                    print(f"AI Image: ❌ Error during Gemini Image generation: {e}")
                
                if not success:
                    target_path = os.path.join(assets_dir, filename)
                    print(f"AI Image fallback: Copying default_cover.png to {target_path}")
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
                    is_downloaded = await download_image(target_url, target_path, sec_type, image_keywords)
                    if is_downloaded:
                        print(f"✅ Successfully acquired image for {sec_type} from {target_url}")
                        success = True
                        break
                    else:
                        print(f"❌ Failed to acquire image from {target_url}, trying next URL...")
                
                if not success:
                    print(f"⚠ Exhausted all source URLs for {sec_type}. Using fallback cover.")
                    if os.path.exists(default_cover_path):
                        shutil.copy(default_cover_path, target_path)
                
        except Exception as e:
            # 強化容錯處理 (Error Handling)，確保不會 Crash 整個迴圈
            print(f"Error processing item {item}: {e}")
            continue

if __name__ == "__main__":
    asyncio.run(main())
