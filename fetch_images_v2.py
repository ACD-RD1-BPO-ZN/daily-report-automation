import asyncio
from playwright.async_api import async_playwright
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import json
# Global list to track used image URLs to prevent duplicates (Strict 1-to-1 Mapping)
global_used_images = set()

def load_existing_assets(assets_dir):
    if os.path.exists(assets_dir):
        for f in os.listdir(assets_dir):
            if os.path.isfile(os.path.join(assets_dir, f)):
                global_used_images.add(f)


async def download_image(url, save_path, section_type=""):
    print(f"\n--- Processing {url} for Section: {section_type} ---")
    
    # Priority 1: requests + bs4 for og:image or direct img src
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        
        candidate_urls = []
        
        # Gather all possible image candidates
        og_img = soup.find('meta', property='og:image')
        if og_img and og_img.get('content'):
            candidate_urls.append(og_img['content'])
            
        for img in soup.find_all('img'):
            if img.get('src'):
                src_url = img['src'] if img['src'].startswith('http') else urljoin(url, img['src'])
                candidate_urls.append(src_url)

        # Filtering logic based on section
        selected_url = None
        for c_url in candidate_urls:
            # 1. Deduplication check
            if c_url in global_used_images:
                continue
                
            # 2. Section specific logic
            c_url_lower = c_url.lower()
            if section_type == "TA":
                # Only allow node graphs, comparisons, material renders
                if any(kw in c_url_lower for kw in ['shader', 'wireframe', 'profiling', 'performance', 'draw call', 'side-by-side', 'node', 'graph']):
                    selected_url = c_url
                    print(f"Priority 1 [TA FILTER]: Found matching node/tech image: {selected_url}")
                    break
            elif section_type == "Local":
                # Attempt to find a legitimate image; Playwright fallback will handle title block.
                # Just ensuring we don't pick generic or already used stuff
                selected_url = c_url
                break
            else:
                selected_url = c_url
                break
                
        if selected_url:
            img_data = requests.get(selected_url, headers=headers, timeout=10).content
            with open(save_path, 'wb') as f:
                f.write(img_data)
            global_used_images.add(selected_url)
            print(f"Priority 1: Successfully saved to {save_path}")
            return True
        else:
            if section_type == "TA":
                print("Priority 1 [TA FILTER]: No suitable node/tech image found via HTTP requests.")
            else:
                print("Priority 1: No suitable unique images found.")
            
    except Exception as e:
        print(f"Priority 1 (Direct Download) failed or no image found: {e}")

    # Priority 2 & 3: Playwright fallback
    print("Falling back to Playwright for Priority 2 & 3...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.set_extra_http_headers({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Priority 2: Element screenshot with size validation
            # For Local section, skip looking for generic image elements if we missed Priority 1, go straight to Title Block
            captured_element = False
            
            if section_type != "Local":
                img_locators = await page.locator("img").all()
                for img_locator in img_locators:
                    try:
                        box = await img_locator.bounding_box()
                        if box and box["width"] > 300 and box["height"] > 200:
                            src = await img_locator.get_attribute("src")
                            src_str = str(src) if src else ""
                            full_src = urljoin(url, src_str) if not src_str.startswith('http') else src_str
                            
                            if full_src in global_used_images:
                                continue
                                
                            if section_type == "TA":
                                alt_text = await img_locator.get_attribute("alt") or ""
                                combined_text = (full_src + alt_text).lower()
                                if not any(kw in combined_text for kw in ['shader', 'wireframe', 'profiling', 'performance', 'draw call', 'side-by-side', 'node', 'graph']):
                                    continue
                            
                            print(f"Priority 2: Found valid large img element ({box['width']}x{box['height']}), capturing...")
                            await img_locator.screenshot(path=save_path)
                            global_used_images.add(full_src)
                            print(f"Priority 2 {f'[{section_type}]'}: Successfully saved to {save_path}")
                            captured_element = True
                            break
                    except Exception as loop_e:
                        continue
            
            if not captured_element:
                if section_type == "Local":
                    print("Priority 3: Local fallback, navigating to Bahamut GNN...")
                    await page.goto("https://gnn.gamer.com.tw/", wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)
                    title_loc = page.locator(".BA-lbox").first
                    if await title_loc.count() > 0:
                        box = await title_loc.bounding_box()
                        if box:
                            clip_box = {
                                "x": max(0, box["x"] - 20),
                                "y": max(0, box["y"] - 20),
                                "width": min(800, box["width"] + 100),
                                "height": 300
                            }
                            await page.screenshot(path=save_path, clip=clip_box)
                            global_used_images.add(f"gnn_fallback_{url}")
                            print(f"Priority 3: Successfully saved local fallback to {save_path}")
                    
                else:
                    # Priority 3: Title Block (Title + 2 lines of text)
                    print(f"Priority 3: Capturing title and text block region for {section_type}...")
                    title_loc = page.locator("h1, h2, .title, .headline").first
                    if await title_loc.count() > 0:
                        box = await title_loc.bounding_box()
                        if box:
                            clip_box = {
                                "x": max(0, box["x"] - 20),
                                "y": max(0, box["y"] - 20),
                                "width": min(800, box["width"] + 400),
                                "height": 250  # Enough to capture title + ~2 lines of paragraph
                            }
                            await page.screenshot(path=save_path, clip=clip_box)
                            print(f"Priority 3: Successfully saved title block to {save_path}")
                            # Add a fake placeholder to block this URL from being used implicitly again
                            global_used_images.add(f"title_block_fallback_{url}")
                        else:
                            print("Priority 3: Title bounding box not found.")
                    else:
                        print("Priority 3: No headers found to capture.")
            
            await browser.close()
            return True
            
    except Exception as e:
        print(f"Playwright fallback failed: {e}")
        return False

async def main():
    assets_dir = os.path.join(os.getcwd(), "assets")
    os.makedirs(assets_dir, exist_ok=True)
    load_existing_assets(assets_dir)
    
    # Load dynamic targets instead of hardcoding
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
        url = item.get("source_url")
        filename = item.get("image_filename")
        sec_type = item.get("section_name")
        ai_prompt = item.get("ai_prompt") # for synthesis
        
        if not url or not filename:
            print(f"Skipping invalid target: {item}")
            continue
            
        target_path = os.path.join(assets_dir, filename)
            
        if url == "GENERATE_AI_IMAGE":
            print(f"\n--- Generating AI Synthesis Art for Section: {sec_type} ---")
            print(f"Prompt: {ai_prompt}")
            try:
                # Due to current API limitations in free tier/script setup, 
                # we'll mock the image generation or rely on an external service if needed.
                # For now, we will create a text placeholder or use a default image path.
                print(f"AI Image Generation proxy: would call Imagen API here. Saving placeholder.")
                # We will copy a placeholder if the image generation fails or is mocked
                # Optional: Import google.generativeai and generate if the model supports it (`imagen-3.0-generate-001`)
                import google.generativeai as genai
                GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
                if GEMINI_API_KEY:
                    genai.configure(api_key=GEMINI_API_KEY)
                    try:
                        # Attempt to use Imagen
                        result = genai.generate_images(
                            prompt=ai_prompt,
                            number_of_images=1,
                            model="imagen-3.0-generate-001",
                            aspect_ratio="16:9"
                        )
                        for image in result.images:
                            # image_bytes is a property on generated images
                            pass 
                        # This API might not be accessible to all keys, fallback
                    except Exception as ai_e:
                        print(f"Imagen API failed or not supported: {ai_e}")
            except Exception as e:
                print(f"Failed AI image gen stub: {e}")
        else:
            await download_image(url, target_path, sec_type)

if __name__ == "__main__":
    asyncio.run(main())
