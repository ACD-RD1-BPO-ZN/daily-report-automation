import urllib.request
import urllib.error
import mimetypes
import uuid
import os
import time
import json
import re
import glob

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def send_message(content=None, image_path=None):
    if not WEBHOOK_URL:
        print("Error: DISCORD_WEBHOOK_URL is not set. Skipping Discord push.")
        return

    boundary = uuid.uuid4().hex
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    body = bytearray()
    payload = {}
    
    # Discord limitation: content max 2000 chars. Truncating if necessary.
    if content:
        if len(content) > 1950:
            content = content[:1950] + "...(truncated)"
        payload["content"] = content
    
    if image_path and os.path.exists(image_path):
        filename = os.path.basename(image_path)
        payload["embeds"] = [{
            "image": {"url": f"attachment://{filename}"},
            "color": 3447003
        }]
        
    body.extend(f"--{boundary}\r\n".encode('utf-8'))
    body.extend(b'Content-Disposition: form-data; name="payload_json"\r\n\r\n')
    body.extend(json.dumps(payload).encode('utf-8'))
    body.extend(b'\r\n')
    
    if image_path and os.path.exists(image_path):
        ext = os.path.splitext(image_path)[1].lower()
        if ext == '.png': mime_type = 'image/png'
        elif ext in ['.jpg', '.jpeg']: mime_type = 'image/jpeg'
        elif ext == '.gif': mime_type = 'image/gif'
        elif ext == '.webp': mime_type = 'image/webp'
        else: mime_type = 'application/octet-stream'
        
        body.extend(f"--{boundary}\r\n".encode('utf-8'))
        body.extend(f'Content-Disposition: form-data; name="file0"; filename="{os.path.basename(image_path)}"\r\n'.encode('utf-8'))
        body.extend(f'Content-Type: {mime_type}\r\n\r\n'.encode('utf-8'))
        with open(image_path, 'rb') as f:
            body.extend(f.read())
        body.extend(b'\r\n')
        
    body.extend(f"--{boundary}--\r\n".encode('utf-8'))
    
    req = urllib.request.Request(WEBHOOK_URL, data=body, headers=headers, method='POST')
    try:
        response = urllib.request.urlopen(req, timeout=30)
        print(f"Sent successfully! (Status: {response.status})")
        time.sleep(2.0) # discord rate limit safe sleep
    except urllib.error.HTTPError as e:
        print(f"Failed to send (HTTP Error): {e.code} - {e.reason}")
        try:
            print(e.read().decode('utf-8'))
        except:
            pass
        time.sleep(2.0)
    except Exception as e:
        print(f"Failed to send (URL/Network Error): {e}")
        time.sleep(2.0)

def main():
    base_dir = os.getcwd()
    
    # Get current date in UTC+8 (Taiwan Time)
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    today_str = datetime.now(tz).strftime("%Y%m%d")
    expected_filename = f"Daily_Full_Report_{today_str}.md"
    latest_report = os.path.join(base_dir, expected_filename)
    
    if not os.path.exists(latest_report):
        print(f"Error: Today's report ({expected_filename}) not found. Skipping Discord push to avoid sending old reports.")
        return
        
    print(f"Pushing segment-by-segment report: {latest_report}")
    
    with open(latest_report, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex to match Markdown images ![alt](rel_path)
    pattern = re.compile(r'(!\[.*?\]\((.*?)\))')
    
    # Split content by H2 headers (## )
    sections = re.split(r'(?m)^## ', content)
    
    for i, section_text in enumerate(sections):
        if not section_text.strip():
            continue
            
        # Re-add the header syntax that was stripped, except for the first chunk (H1)
        if i > 0:
            section_text = "## " + section_text
            
        # Identify all images in this section
        images_in_section = list(pattern.finditer(section_text))
        
        target_image_abs_path = None
        
        # We only keep the FIRST image in the section due to the 1-to-1 strict mapping rule
        if images_in_section:
            first_image_match = images_in_section[0]
            image_rel_path = first_image_match.group(2)
            abs_path = os.path.normpath(os.path.join(os.path.dirname(latest_report), image_rel_path))
            
            if os.path.exists(abs_path):
                target_image_abs_path = abs_path
                print(f"Selected image attachment for section: {target_image_abs_path}")
            else:
                print(f"Image not found locally for section: {abs_path}")
        
        # Remove ALL image markdown links from the text so it's clean, since the image is attached via File Embed
        clean_text = pattern.sub('', section_text).strip()
        
        if clean_text or target_image_abs_path:
            try:
                # Send EXACTLY one pair of text + attachment per section
                print("Sending single payload with Strict 1-to-1 Mapping to Discord...")
                send_message(content=clean_text, image_path=target_image_abs_path)
            except Exception as e:
                print(f"Unexpected error sending section: {e}")

if __name__ == "__main__":
    main()
