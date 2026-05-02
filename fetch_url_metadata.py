import asyncio
import os
import glob
import re
import json
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

CACHE_FILE = "url_metadata_cache.json"
JS_RENDERED_DOMAINS = ["unrealengine.com", "80.lv"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
}

def get_latest_report():
    report_files = glob.glob(os.path.join("Daily_Report", "Daily_Full_Report_*.md"))
    if not report_files:
        return None
    report_files.sort(reverse=True)
    return report_files[0]

def extract_all_urls():
    urls = set()
    # 1. From latest markdown
    md_path = get_latest_report()
    if md_path:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pat = re.compile(r'\[.*?\]\(<?(https?://[^>)]+)>?\)')
        for m in pat.finditer(content):
            urls.add(m.group(1))
            
    # 2. From daily_targets.json
    if os.path.exists("daily_targets.json"):
        with open("daily_targets.json", "r", encoding="utf-8") as f:
            targets = json.load(f)
            for item in targets:
                for url in item.get("source_urls", []):
                    if url and url != "GENERATE_AI_IMAGE":
                        urls.add(url)
                if "source_url" in item and item["source_url"] != "GENERATE_AI_IMAGE":
                    urls.add(item["source_url"])
    return list(urls)

def parse_og_image(html):
    soup = BeautifulSoup(html, 'html.parser')
    og_img = soup.find('meta', property='og:image')
    if not og_img:
        og_img = soup.find('meta', attrs={'name': 'og:image'})
    if og_img and og_img.get('content'):
        url = og_img['content']
        if not any(skip in url.lower() for skip in ["logo", "avatar", "icon", "default", "placeholder"]):
            return url
    return ""

async def fetch_with_playwright(browser, url):
    print(f"    [Playwright] Fetching: {url}")
    try:
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        # Bypass webdriver checks
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        
        # Give a short wait for JS rendered meta tags
        try:
            await page.wait_for_selector('meta[property="og:image"]', timeout=3000)
        except:
            pass
            
        html = await page.content()
        await context.close()
        return parse_og_image(html)
    except Exception as e:
        print(f"    [Playwright] Error fetching {url}: {e}")
        return ""

async def get_metadata_for_urls(urls):
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except:
            pass

    urls_to_fetch_pw = []
    
    # 1. First pass with requests
    for url in urls:
        if url in cache and cache[url].get("og_image"):
            print(f"[Cache Hit] {url}")
            continue
            
        if any(domain in url for domain in JS_RENDERED_DOMAINS):
            urls_to_fetch_pw.append(url)
            continue
            
        print(f"[Requests] Fetching: {url}")
        try:
            resp = await asyncio.to_thread(
                requests.get, url, headers=HEADERS, timeout=10
            )
            if not resp.ok or any(t in resp.text[:500].lower() for t in ["just a moment", "cloudflare", "checking your browser"]):
                urls_to_fetch_pw.append(url)
                continue
            
            og_img = parse_og_image(resp.text)
            if og_img:
                cache[url] = {
                    "og_image": og_img,
                    "last_seen": datetime.now().strftime("%Y-%m-%d")
                }
            else:
                urls_to_fetch_pw.append(url) # Fallback to playwright if not found
        except Exception as e:
            print(f"    [Requests] Failed {url}: {e}")
            urls_to_fetch_pw.append(url)
            
    # 2. Second pass with Playwright for JS domains or failed requests
    if urls_to_fetch_pw:
        print(f"Starting Playwright for {len(urls_to_fetch_pw)} URLs...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            # Process sequentially to avoid memory spikes / aggressive bot blocking
            for url in urls_to_fetch_pw:
                og_img = await fetch_with_playwright(browser, url)
                cache[url] = {
                    "og_image": og_img,
                    "last_seen": datetime.now().strftime("%Y-%m-%d")
                }
            
            await browser.close()
            
    # 3. Pruning logic: Only keep entries seen in the last 7 days
    today = datetime.now()
    expiry_limit = today - timedelta(days=30)  # og:image 基本是不可變資源，延長 TTL 減少 Playwright 重複呼叫
    
    pruned_cache = {}
    pruned_count = 0
    now_str = today.strftime("%Y-%m-%d")

    # Update last_seen for current URLs
    for url in urls:
        if url in cache:
            cache[url]["last_seen"] = now_str

    for url, data in cache.items():
        last_seen_str = data.get("last_seen", "2000-01-01")
        try:
            last_seen_dt = datetime.strptime(last_seen_str, "%Y-%m-%d")
            if last_seen_dt > expiry_limit:
                pruned_cache[url] = data
            else:
                pruned_count += 1
        except:
            # If for some reason the date format is wrong, keep it to be safe
            pruned_cache[url] = data

    if pruned_count > 0:
        print(f"🗑  Pruned {pruned_count} expired entries from cache.")

    # Save cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned_cache, f, indent=2, ensure_ascii=False)
        
    print(f"Metadata fetch complete. Updated {CACHE_FILE} (Total {len(pruned_cache)} entries)")

def main():
    urls = extract_all_urls()
    print(f"Found {len(urls)} unique URLs to process.")
    asyncio.run(get_metadata_for_urls(urls))

if __name__ == "__main__":
    main()
