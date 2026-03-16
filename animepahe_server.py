import os
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

# Global browser instance
_playwright = None
_browser = None
_lock = threading.Lock()

# Simple In-Memory Cache to speed things up
_search_cache = {}
_stream_cache = {}

def get_browser():
    global _playwright, _browser
    with _lock:
        if _playwright is None:
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
        return _browser

def search_anime(query):
    if query in _search_cache:
        return _search_cache[query]
    
    browser = get_browser()
    # Create a fresh incognito context for every request
    context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    page = context.new_page()
    try:
        search_url = f"https://animepahe.si/api?m=search&q={query}"
        page.goto(search_url, timeout=30000)
        page.wait_for_selector("body", timeout=10000)
        data = json.loads(page.locator("body").inner_text())
        
        results = []
        for i in data.get('data', [])[:8]:
            results.append({
                'id': i['session'], 
                'title': i['title'], 
                'poster': i.get('poster', ''),
                'type': i.get('type', ''),
                'status': i.get('status', '')
            })
        _search_cache[query] = results
        return results
    except Exception as e:
        print(f"[Search Error] {e}")
        return []
    finally:
        page.close()
        context.close()

def get_episodes(anime_id, page_num=1):
    browser = get_browser()
    context = browser.new_context()
    page = context.new_page()
    try:
        rel_url = f"https://animepahe.si/api?m=release&id={anime_id}&sort=episode_asc&page={page_num}"
        page.goto(rel_url, timeout=20000)
        data = json.loads(page.locator("body").inner_text())
        return data
    except Exception as e:
        print(f"[Episodes Error] {e}")
        return {"data": []}
    finally:
        page.close()
        context.close()

def resolve_stream(anime_id, episode, stream_type="sub"):
    cache_key = (anime_id, episode, stream_type)
    if cache_key in _stream_cache:
        return _stream_cache[cache_key]
    
    browser = get_browser()
    context = browser.new_context()
    page = context.new_page()
    try:
        # Get episode session
        api_link = f"https://animepahe.si/api?m=release&id={anime_id}&sort=episode_asc"
        page.goto(api_link, timeout=20000)
        rel_data = json.loads(page.locator("body").inner_text())
        
        ep_session = next((item['session'] for item in rel_data.get('data', []) if str(item['episode']) == str(episode)), None)
        if not ep_session: return {"error": "Episode not found"}

        # Scrape play page
        page.goto(f"https://animepahe.si/play/{anime_id}/{ep_session}", timeout=30000)
        page.wait_for_selector("#fansubMenu", timeout=15000)
        page.click("#fansubMenu")
        
        items = page.query_selector_all(".dropdown-item")
        links = []
        for item in items:
            html = item.evaluate("el => el.outerHTML").lower()
            text = item.inner_text().lower()
            is_dub = "dub" in html or "eng" in text
            if (stream_type == "dub" and is_dub) or (stream_type == "sub" and not is_dub):
                url = item.get_attribute("data-src") or item.get_attribute("href")
                res = 1080 if "1080" in text else 720 if "720" in text else 360
                links.append({"res": res, "url": url})
        
        if not links: return {"error": "No links found"}
        links.sort(key=lambda x: x['res'], reverse=True)
        
        result = {"sources": [{"url": links[0]['url'], "quality": f"{links[0]['res']}p"}], "embed_url": links[0]['url']}
        _stream_cache[cache_key] = result
        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        page.close()
        context.close()

class AuraHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        result = {}

        # 1. Logic Processing
        if parsed.path == "/health":
            result = {"status": "ok", "server": "aura-animepahe"}
        elif parsed.path == "/search":
            query = params.get("q", [""])[0]
            result = {"results": search_anime(query)}
        elif parsed.path == "/episodes":
            aid = params.get("anime_id", [""])[0]
            p = params.get("page", ["1"])[0]
            result = get_episodes(aid, p)
        elif parsed.path == "/stream":
            aid = params.get("anime_id", [""])[0]
            ep = params.get("episode", ["1"])[0]
            st = params.get("type", ["sub"])[0]
            result = resolve_stream(aid, ep, st)
        else:
            result = {"error": "Not Found"}

        # 2. Response Delivery (Headers + Body together)
        response_data = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response_data)

    def log_message(self, format, *args):
        pass # Clean logs

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), AuraHandler)
    print(f"Aura Master LIVE on port {port}")
    server.serve_forever()
