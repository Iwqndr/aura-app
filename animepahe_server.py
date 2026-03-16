"""
Aura AnimePahe Bypass Server
Cloud-Ready version for Render & UptimeRobot
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
import json
import threading
import time
import os

# Global browser instance
_browser = None
_context = None
_lock = threading.Lock()

# Simple In-Memory Cache
_search_cache = {}
_stream_cache = {}
_ep_session_cache = {}

def get_browser():
    global _browser, _context
    if _browser is None:
        pw = sync_playwright().start()
        _browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        _context = _browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
    return _context

def extract_kwik(ctx, kwik_url):
    page = ctx.new_page()
    page.route("**/*", lambda route: route.abort() 
               if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
               else route.continue_())
    try:
        page.goto(kwik_url, wait_until="domcontentloaded")
        time.sleep(1)
        video = page.query_selector("video")
        if video:
            src = video.get_attribute("src")
            if src and (".m3u8" in src or ".mp4" in src):
                return src
        return kwik_url
    except Exception as e:
        print(f"[Kwik Error] {e}")
        return kwik_url
    finally:
        page.close()

def search_anime(query):
    if query in _search_cache:
        return _search_cache[query]
    ctx = get_browser()
    page = ctx.new_page()
    try:
        search_url = f"https://animepahe.si/api?m=search&q={query}"
        page.goto(search_url)
        page.wait_for_selector("body:has-text('total')", timeout=30000)
        data = json.loads(page.locator("body").inner_text())
        results = [{'id': i['session'], 'title': i['title'], 'poster': i.get('poster', '')} for i in data.get('data', [])[:8]]
        _search_cache[query] = results
        return results
    except: return []
    finally: page.close()

class AuraHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        """FIX: Handles UptimeRobot Free Tier pings to prevent 501 errors"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if parsed.path == "/health":
            self.wfile.write(json.dumps({"status": "ok", "server": "aura-animepahe"}).encode())
        
        elif parsed.path == "/search":
            query = params.get("q", [""])[0]
            result = search_anime(query)
            self.wfile.write(json.dumps({"results": result}).encode())

        # ... (Add other endpoints like /stream or /episodes here if needed) ...

        else:
            self.wfile.write(json.dumps({"error": "Unknown endpoint"}).encode())

    def log_message(self, format, *args):
        pass # Keeps Render logs clean

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), AuraHandler)
    print(f"Aura Server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    run_server()
