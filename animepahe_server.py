"""
Aura AnimePahe Bypass Server
Converts bypass.py into a local HTTP server for the Flutter app.
Run: python animepahe_server.py
Listens on: http://localhost:8888
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright
import json
import threading
import time
import os

# Global browser instance (reused across requests)
_browser = None
_context = None
_lock = threading.Lock()

# Simple In-Memory Cache
_search_cache = {}  # query -> results
_stream_cache = {}  # (anime_id, episode, type) -> stream_data
_ep_session_cache = {} # (anime_id, episode) -> ep_session


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
    """Deeply extract the direct video URL from a Kwik link."""
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    try:
        page.goto(kwik_url, wait_until="domcontentloaded")
        # Kwik often uses a button to 'reveal' the link or has it in a script.
        # We'll wait for the video tag or a specific script variable.
        time.sleep(1) # Give it a second to run its scripts
        
        # Method 1: Check for <video> source
        video = page.query_selector("video")
        if video:
            src = video.get_attribute("src")
            if src and ".m3u8" in src or ".mp4" in src:
                return src
        
        # Method 2: Extract from evaluation (common in Kwik)
        script_content = page.evaluate('''() => {
            const scripts = Array.from(document.querySelectorAll('script'));
            for (const s of scripts) {
                if (s.innerText.includes('pX(')) { // Common Kwik obfuscation
                    return s.innerText;
                }
            }
            return null;
        }''')
        
        # If we can't find it easily, try to wait and see if it appears
        page.wait_for_timeout(2000)
        video = page.query_selector("video")
        if video:
            return video.get_attribute("src")
            
        return kwik_url # Fallback to original if extraction fails
    except Exception as e:
        print(f"[Kwik Error] {e}")
        return kwik_url
    finally:
        page.close()


def get_episodes(anime_id, page=1):
    """Fetch the episode list for an anime."""
    ctx = get_browser()
    browser_page = ctx.new_page()
    try:
        rel_url = f"https://animepahe.si/api?m=release&id={anime_id}&sort=episode_asc&page={page}"
        browser_page.goto(rel_url)
        browser_page.wait_for_selector("body:has-text('total')", timeout=15000)
        data = json.loads(browser_page.locator("body").inner_text())
        
        episodes = []
        for item in data.get('data', []):
            episodes.append({
                'episode': item['episode'],
                'session': item['session'],
                'snapshot': item.get('snapshot', ''),
                'disc': item.get('disc', 0),
                'duration': item.get('duration', '00:00:00'),
                'created_at': item.get('created_at', '')
            })
        
        return {
            "total": data.get("total", 0),
            "per_page": data.get("per_page", 0),
            "current_page": data.get("current_page", 0),
            "last_page": data.get("last_page", 0),
            "data": episodes
        }
    except Exception as e:
        print(f"[Episodes Error] {e}")
        return {"error": str(e)}
    finally:
        browser_page.close()


def search_anime(query):
    """Search AnimePahe for an anime with caching."""
    if query in _search_cache:
        print(f"[Cache Hit] Search: {query}")
        return _search_cache[query]

    ctx = get_browser()
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    try:
        search_url = f"https://animepahe.si/api?m=search&q={query}"
        page.goto(search_url)
        page.wait_for_selector("body:has-text('total')", timeout=30000)
        data = json.loads(page.locator("body").inner_text())
        results = []
        for item in data.get('data', [])[:8]:
            results.append({
                'id': item['session'],
                'title': item['title'],
                'type': item['type'],
                'status': item['status'],
                'episodes': item.get('episodes', 0),
                'poster': item.get('poster', ''),
            })
        _search_cache[query] = results
        return results
    except Exception as e:
        print(f"[Search Error] {e}")
        return []
    finally:
        page.close()


def get_stream(anime_id, episode, stream_type="sub"):
    """Get a stream URL for a specific episode and type (sub/dub) with caching."""
    cache_key = (anime_id, episode, stream_type)
    if cache_key in _stream_cache:
        print(f"[Cache Hit] Stream: {cache_key}")
        return _stream_cache[cache_key]

    ctx = get_browser()
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    try:
        # Step 1: Find the episode session (check cache first)
        ep_session = _ep_session_cache.get((anime_id, episode))
        
        if not ep_session:
            current_page = 1
            while True:
                rel_url = f"https://animepahe.si/api?m=release&id={anime_id}&sort=episode_asc&page={current_page}"
                page.goto(rel_url)
                page.wait_for_selector("body:has-text('total')", timeout=15000)
                rel_data = json.loads(page.locator("body").inner_text())

                for item in rel_data.get('data', []):
                    ep_num = str(item['episode']).split('.')[0].lstrip('0')
                    if ep_num == str(episode).lstrip('0'):
                        ep_session = item['session']
                        _ep_session_cache[(anime_id, episode)] = ep_session
                        break

                if ep_session or not rel_data.get('next_page_url'):
                    break
                current_page += 1

        if not ep_session:
            return {"error": f"Episode {episode} not found"}

        # Step 2: Navigate to the play page and extract links
        play_url = f"https://animepahe.si/play/{anime_id}/{ep_session}"
        page.goto(play_url, wait_until="domcontentloaded")
        page.wait_for_selector("#fansubMenu", timeout=20000)
        page.click("#fansubMenu")
        time.sleep(1)

        items = page.query_selector_all(".dropdown-item")
        available_links = []

        for item in items:
            html = item.evaluate("el => el.outerHTML").lower()
            text = item.inner_text().lower()
            is_dub = any(x in html or x in text for x in ["eng", "dub"])
            res = 1080 if "1080" in text else 720 if "720" in text else 360 if "360" in text else 0
            url = item.get_attribute("data-src") or item.get_attribute("href")

            if (stream_type == "dub" and is_dub) or (stream_type == "sub" and not is_dub):
                available_links.append({'res': res, 'url': url, 'type': stream_type})

        if not available_links:
            # Try to see if ANY exist for error reporting
            return {"error": f"No {stream_type.upper()} version found", "available": []}

        # Sort by resolution (highest first)
        available_links.sort(key=lambda x: x['res'], reverse=True)
        top = available_links[0]

        # Step 3: Extract the direct link from Kwik
        final_url = top['url']
        if final_url and "kwik" in final_url:
            print(f"[Aura] Resolving Kwik: {final_url}")
            final_url = extract_kwik(ctx, final_url)

        # Step 4: If URL is still missing, try the API fallback
        if not final_url or final_url == "#":
            api_link = f"https://animepahe.si/api?m=links&id={anime_id}&session={ep_session}&p=kwik"
            time.sleep(2)
            res_data = page.evaluate(f'async () => {{ const r = await fetch("{api_link}"); return r.json(); }}')
            for entry in res_data.get('data', []):
                for r_key, info in entry.items():
                    is_d = "eng" in info.get('audio', '') or "dub" in info.get('fansub', '').lower()
                    if int(r_key) == top['res'] and ((stream_type == "dub" and is_d) or (stream_type == "sub" and not is_d)):
                        final_url = extract_kwik(ctx, info['kwik'])
                        break

        result = {
            "sources": [{"url": final_url, "quality": f"{top['res']}p", "type": stream_type}],
            "all_sources": available_links,
            "headers": {
                "Referer": "https://kwik.cx/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            }
        }
        _stream_cache[cache_key] = result
        return result

    except Exception as e:
        print(f"[Stream Error] {e}")
        return {"error": str(e)}
    finally:
        page.close()


class AuraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if parsed.path == "/search":
            query = params.get("q", [""])[0]
            result = search_anime(query)
            self.wfile.write(json.dumps({"results": result}).encode())

        elif parsed.path == "/episodes":
            anime_id = params.get("anime_id", [""])[0]
            page = params.get("page", ["1"])[0]
            result = get_episodes(anime_id, page)
            self.wfile.write(json.dumps(result).encode())

        elif parsed.path == "/stream":
            anime_id = params.get("anime_id", [""])[0]
            episode = params.get("episode", ["1"])[0]
            stream_type = params.get("type", ["sub"])[0]
            with _lock:
                result = get_stream(anime_id, episode, stream_type)
            self.wfile.write(json.dumps(result).encode())

        elif parsed.path == "/health":
            self.wfile.write(json.dumps({"status": "ok", "server": "aura-animepahe"}).encode())

        else:
            self.wfile.write(json.dumps({"error": "Unknown endpoint"}).encode())

    def log_message(self, format, *args):
        # Suppress logging every single GET request to keep terminal clean
        pass


def run_server():
    # 1. Look for the port Render gave us, default to 10000
    port = int(os.environ.get("PORT", 10000)) 
    
    # 2. MUST use "0.0.0.0" (not "localhost" or "127.0.0.1")
    server = HTTPServer(("0.0.0.0", port), AuraHandler)
    
    print(f"Aura Server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    run_server()
