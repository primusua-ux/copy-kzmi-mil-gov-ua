import os
import re
import time
import urllib.parse
from collections import deque
import requests
from bs4 import BeautifulSoup
import undetected_chromedriver as uc

# Configuration
START_URL = "https://kzmi.mil.gov.ua/uk/"
DOMAIN = "kzmi.mil.gov.ua"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DELAY = 1.0  # Politeness delay
MAX_RETRIES = 3
CHROME_VERSION = 148  # Hardcoded based on user's current Chrome version

# Sets to keep track
crawled_pages = set()
pages_queue = deque([START_URL])
downloaded_assets = {}  # absolute_url -> local_rel_path
crawled_page_mappings = {} # absolute_url -> local_rel_path

# Global browser and session variables
driver = None
session = None
user_agent = ""

def init_browser():
    global driver, session, user_agent
    print("Initializing undetected-chromedriver...")
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    
    # Initialize driver
    driver = uc.Chrome(options=options, version_main=CHROME_VERSION)
    
    print(f"Navigating to {START_URL} to bypass Cloudflare...")
    driver.get(START_URL)
    time.sleep(8)  # Give it time to load and solve Cloudflare
    
    print("Extracting cookies and User-Agent...")
    user_agent = driver.execute_script("return navigator.userAgent")
    print(f"Browser User-Agent: {user_agent}")
    
    # Initialize requests session
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": START_URL
    })
    
    sync_cookies()

def sync_cookies():
    global driver, session
    cookies = driver.get_cookies()
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))
    # print(f"Synced {len(cookies)} cookies to session.")

def url_to_local_path(url, is_html=False):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    if path.startswith('/'):
        path = path[1:]
    
    # Normalize paths
    if not path or path == "uk" or path == "uk/":
        if path.startswith("uk"):
            path = "uk/"
        else:
            path = ""
            
    query_str = ""
    if is_html and parsed.query:
        query_str = "_" + parsed.query.replace('=', '_').replace('&', '_').replace('?', '_')
        if len(query_str) > 50:
            import hashlib
            query_str = "_" + hashlib.md5(parsed.query.encode()).hexdigest()[:10]
            
    if not path or path.endswith('/'):
        path = path + "index" + query_str + ".html"
    else:
        last_segment = path.split('/')[-1]
        if '.' not in last_segment:
            path = path + "/index" + query_str + ".html"
        elif is_html:
            if query_str:
                base, ext = os.path.splitext(path)
                path = base + query_str + ext
                
    parts = [p for p in path.split('/') if p]
    return os.path.join(*parts)

def fetch_asset(url):
    global session
    for attempt in range(MAX_RETRIES):
        try:
            # Sync cookies right before fetch to avoid expiration
            sync_cookies()
            response = session.get(url, timeout=15)
            if response.status_code == 200:
                return response
            elif response.status_code == 404:
                print(f"Asset 404 Not Found: {url}")
                return None
            else:
                print(f"Asset status code {response.status_code} for {url}")
        except Exception as e:
            print(f"Error fetching asset {url}: {e}")
        time.sleep(DELAY)
    return None

def download_asset(url, referrer_url):
    # Resolve relative URL
    abs_url = urllib.parse.urljoin(referrer_url, url)
    abs_url = abs_url.split('#')[0]
    
    parsed = urllib.parse.urlparse(abs_url)
    if parsed.netloc and parsed.netloc != DOMAIN:
        return None
        
    if abs_url in downloaded_assets:
        return downloaded_assets[abs_url]
        
    local_rel_path = url_to_local_path(abs_url, is_html=False)
    local_abs_path = os.path.join(BASE_DIR, local_rel_path)
    
    # If the file already exists, don't download it again
    if os.path.exists(local_abs_path):
        downloaded_assets[abs_url] = local_rel_path
        return local_rel_path
        
    print(f"Downloading asset: {abs_url} -> {local_rel_path}")
    response = fetch_asset(abs_url)
    if response:
        os.makedirs(os.path.dirname(local_abs_path), exist_ok=True)
        try:
            with open(local_abs_path, 'wb') as f:
                f.write(response.content)
            downloaded_assets[abs_url] = local_rel_path
            
            if local_rel_path.endswith('.css'):
                process_css_file(local_abs_path, abs_url)
                
            return local_rel_path
        except Exception as e:
            print(f"Failed to save asset {local_rel_path}: {e}")
    return None

def process_css_file(css_abs_path, css_url):
    try:
        with open(css_abs_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        urls = re.findall(r'url\s*\(\s*[\'"]?([^\'"\)]+)[\'"]?\s*\)', content)
        modified = False
        for css_asset_url in urls:
            css_asset_url = css_asset_url.strip()
            if css_asset_url.startswith('data:') or css_asset_url.startswith('#') or not css_asset_url:
                continue
                
            asset_rel_path = download_asset(css_asset_url, css_url)
            if asset_rel_path:
                css_dir_rel = os.path.dirname(os.path.relpath(css_abs_path, BASE_DIR))
                rel_url = os.path.relpath(asset_rel_path, css_dir_rel).replace(os.sep, '/')
                content = content.replace(css_asset_url, rel_url)
                modified = True
                
        if modified:
            with open(css_abs_path, 'w', encoding='utf-8') as f:
                f.write(content)
    except Exception as e:
        print(f"Error processing CSS {css_abs_path}: {e}")

def process_page(url):
    global driver
    print(f"\nProcessing page via browser: {url}")
    
    # Politeness delay
    time.sleep(DELAY)
    
    try:
        driver.get(url)
        time.sleep(3)  # Wait for page to render and JS to complete
    except Exception as e:
        print(f"Error loading page in browser {url}: {e}")
        return
        
    local_rel_path = url_to_local_path(url, is_html=True)
    local_abs_path = os.path.join(BASE_DIR, local_rel_path)
    
    # Save the mapping
    crawled_page_mappings[url] = local_rel_path
    
    # Extract source HTML
    html_source = driver.page_source
    soup = BeautifulSoup(html_source, 'html.parser')
    
    # Sync cookies in case they changed during page render
    sync_cookies()
    
    # 1. Process stylesheet links (<link rel="stylesheet">)
    for link in soup.find_all('link', rel=lambda x: x and 'stylesheet' in x):
        href = link.get('href')
        if href:
            asset_path = download_asset(href, url)
            if asset_path:
                rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                link['href'] = rel_url
                
    # 2. Process scripts (<script src="...">)
    for script in soup.find_all('script', src=True):
        src = script.get('src')
        if src:
            asset_path = download_asset(src, url)
            if asset_path:
                rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                script['src'] = rel_url
                
    # 3. Process images (<img src="..."> and <img srcset="...">)
    for img in soup.find_all('img', src=True):
        src = img.get('src')
        if src:
            asset_path = download_asset(src, url)
            if asset_path:
                rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                img['src'] = rel_url
        
        # Handle srcset
        srcset = img.get('srcset')
        if srcset:
            new_srcset = []
            for item in srcset.split(','):
                item = item.strip()
                if not item:
                    continue
                subparts = item.split()
                if subparts:
                    img_url = subparts[0]
                    asset_path = download_asset(img_url, url)
                    if asset_path:
                        rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                        subparts[0] = rel_url
                    new_srcset.append(" ".join(subparts))
            img['srcset'] = ", ".join(new_srcset)

    # 4. Process <source> elements (usually inside <picture>)
    for source in soup.find_all('source'):
        src = source.get('src')
        if src:
            asset_path = download_asset(src, url)
            if asset_path:
                rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                source['src'] = rel_url
                
        srcset = source.get('srcset')
        if srcset:
            new_srcset = []
            for item in srcset.split(','):
                item = item.strip()
                if not item:
                    continue
                subparts = item.split()
                if subparts:
                    img_url = subparts[0]
                    asset_path = download_asset(img_url, url)
                    if asset_path:
                        rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                        subparts[0] = rel_url
                    new_srcset.append(" ".join(subparts))
            source['srcset'] = ", ".join(new_srcset)

    # 5. Process general inline styles with url(...)
    for tag in soup.find_all(style=True):
        style_attr = tag['style']
        urls = re.findall(r'url\s*\(\s*[\'"]?([^\'"\)]+)[\'"]?\s*\)', style_attr)
        for inline_url in urls:
            inline_url = inline_url.strip()
            if inline_url.startswith('data:') or inline_url.startswith('#'):
                continue
            asset_path = download_asset(inline_url, url)
            if asset_path:
                rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                tag['style'] = tag['style'].replace(inline_url, rel_url)

    # 6. Process anchors (<a href="...">)
    for a in soup.find_all('a', href=True):
        href = a.get('href')
        if href.startswith(('mailto:', 'tel:', '#', 'javascript:')):
            continue
            
        abs_href = urllib.parse.urljoin(url, href)
        parsed_href = urllib.parse.urlparse(abs_href)
        
        if parsed_href.netloc == DOMAIN:
            if parsed_href.path.startswith('/uk/') or parsed_href.path == '/uk':
                clean_abs_href = abs_href.split('#')[0]
                
                last_seg = parsed_href.path.split('/')[-1]
                is_file_asset = False
                if '.' in last_seg:
                    ext = last_seg.split('.')[-1].lower()
                    if ext not in ('html', 'php', 'aspx', 'jsp', 'htm'):
                        is_file_asset = True
                        
                if is_file_asset:
                    asset_path = download_asset(clean_abs_href, url)
                    if asset_path:
                        rel_url = os.path.relpath(asset_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                        if parsed_href.fragment:
                            rel_url += f"#{parsed_href.fragment}"
                        a['href'] = rel_url
                else:
                    if clean_abs_href not in crawled_pages and clean_abs_href not in pages_queue:
                        pages_queue.append(clean_abs_href)
                        
                    target_local_rel_path = url_to_local_path(clean_abs_href, is_html=True)
                    rel_url = os.path.relpath(target_local_rel_path, os.path.dirname(local_rel_path)).replace(os.sep, '/')
                    if parsed_href.fragment:
                        rel_url += f"#{parsed_href.fragment}"
                    a['href'] = rel_url

    # Save the parsed HTML back
    os.makedirs(os.path.dirname(local_abs_path), exist_ok=True)
    with open(local_abs_path, 'wb') as f:
        f.write(soup.prettify('utf-8'))
        
    print(f"Saved page to {local_rel_path}")

def create_root_redirect():
    root_index = os.path.join(BASE_DIR, "index.html")
    content = """<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="refresh" content="0; url=uk/index.html">
    <script>window.location.replace("uk/index.html");</script>
</head>
<body>
    <p>Redirecting to <a href="uk/index.html">uk/index.html</a>...</p>
</body>
</html>"""
    with open(root_index, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Created root index.html redirect to uk/index.html")

def main():
    try:
        init_browser()
        print(f"Starting crawl of {START_URL}")
        
        while pages_queue:
            current_url = pages_queue.popleft()
            if current_url in crawled_pages:
                continue
                
            try:
                process_page(current_url)
                crawled_pages.add(current_url)
            except Exception as e:
                print(f"Failed to process {current_url}: {e}")
                
        create_root_redirect()
        print("\nCrawl complete!")
        print(f"Total HTML pages crawled: {len(crawled_pages)}")
        print(f"Total assets downloaded: {len(downloaded_assets)}")
        
    finally:
        if driver:
            print("Closing browser...")
            driver.quit()

if __name__ == "__main__":
    main()
