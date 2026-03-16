import os
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

# --- LOGGING SYSTEM ---
# This keeps the last 50 lines in memory so you can see them at /logs
logs = ["Aura Server initialized... waiting for first request."]

def log_print(message):
    timestamp = time.strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {message}"
    print(formatted)  # Shows in Render Dashboard
    logs.append(formatted)
    if len(logs) > 50:
        logs.pop(0)

# --- THE SCRAPER LOGIC ---
def get_anime_stream(query, ep_num, choice_type):
    log_print(f"🚀 New Request: {query} | Ep: {ep_num} | Mode: {choice_type}")
    
    with sync_playwright() as p:
        # 1. Stealth Launch
        log_print("Launching Stealth Browser...")
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        # Hide Webdriver
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            # 2. Search Anime
            log_print(f"Searching for '{query}'...")
            search_url = f"https://animepahe.si/api?m=search&q={query}"
            page.goto(search_url, timeout=60000)
            
            page.wait_for_selector("body:has-text('total')", timeout=20000)
            search_data = json.loads(page.locator("body").inner_text())
            
            if not search_data.get('data'):
                log_print("❌ No results found for that name.")
                return None

            selected = search_data['data'][0]
            anime_id = selected['session']
            log_print(f"Matched Anime: {selected['title']}")

            # 3. Find Episode
            log_print(f"Finding Episode {ep_num}...")
            current_page = 1
            episode_data = None
            
            while True:
                rel_url = f"https://animepahe.si/api?m=release&id={anime_id}&sort=episode_asc&page={current_page}"
                page.goto(rel_url, timeout=30000)
                page.wait_for_selector("body:has-text('total')", timeout=15000)
                rel_data = json.loads(page.locator("body").inner_text())
                
                # Check for the episode (handle 0-padding like '01' vs '1')
                for item in rel_data['data']:
                    if str(item['episode']).split('.')[0].lstrip('0') == ep_num.lstrip('0'):
                        episode_data = item
                        break
                
                if episode_data or not rel_data.get('next_page_url') or current_page > 15:
                    break
                current_page += 1

            if not episode_data:
                log_print(f"❌ Episode {ep_num} not found.")
                return None

            # 4. Extract Kwik Link
            play_url = f"https://animepahe.si/play/{anime_id}/{episode_data['session']}"
            log_print(f"Opening content page: {play_url}")
            page.goto(play_url, wait_until="domcontentloaded")
            
            page.wait_for_selector("#fansubMenu", timeout=20000)
            page.click("#fansubMenu")
            time.sleep(1) # Wait for dropdown animation
            
            items = page.query_selector_all(".dropdown-item")
            available_links = []

            for item in items:
                text = item.inner_text().lower()
                is_dub = any(x in text for x in ["eng", "dub"])
                res = 1080 if "1080" in text else 720 if "720" in text else 360 if "360" in text else 0
                url = item.get_attribute("data-src") or item.get_attribute("href")

                if (choice_type == "dub" and is_dub) or (choice_type == "sub" and not is_dub):
                    available_links.append({'res': res, 'url': url})

            if not available_links:
                log_print(f"❌ No {choice_type.upper()} version found.")
                return None

            # Return the highest resolution found
            available_links.sort(key=lambda x: x['res'], reverse=True)
            log_print(f"✅ Success! Found {available_links[0]['res']}p link.")
            return available_links[0]['url']

        except Exception as e:
            log_print(f"❌ Error during scrape: {str(e)}")
            return None
        finally:
            browser.close()

# --- WEB SERVER INTERFACE ---
class AuraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Endpoint 1: LIVE LOGS
        if parsed.path == "/logs":
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            log_html = "<br>".join(reversed(logs)) # Newest logs at top
            html = f"""
            <html><body style="background:#111; color:#0f0; font-family:monospace; padding:20px;">
                <h1 style="color:white;">Aura Pro Live Feed</h1>
                <p>Status: Running</p><hr>
                <div id="logs">{log_html}</div>
                <script>setTimeout(() => location.reload(), 3000);</script>
            </body></html>
            """
            self.wfile.write(html.encode())
            return

        # Endpoint 2: SEARCH (The API for your Flutter App)
        if parsed.path == "/search":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            q = params.get('q', [''])[0]
            ep = params.get('ep', ['1'])[0]
            mode = params.get('mode', ['sub'])[0]
            
            stream_url = get_anime_stream(q, ep, mode)
            
            response = {"url": stream_url} if stream_url else {"error": "No stream found"}
            self.wfile.write(json.dumps(response).encode())

# --- LAUNCH ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), AuraHandler)
    log_print(f"Aura Server listening on port {port}")
    server.serve_forever()
