"""PC工房のカテゴリ構造を正しくスキャン (/category/04AABB.html 形式)"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

BASE = "https://www.pc-koubou.jp"

# /category/04{AA}{BB}.html の形式 (AA: 01-15, BB: 01-05)
SCAN_PATHS = [
    f"/category/04{a:02d}{b:02d}.html"
    for a in range(1, 16)
    for b in range(1, 6)
]

def get_info(page, url: str):
    try:
        page.goto(url, timeout=15000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        inp = soup.select_one('input[name="agg_category_id"]')
        h1 = soup.select_one("h1")
        title_text = h1.get_text(strip=True)[:40] if h1 else "?"
        cid = inp.get("value") if inp else None
        if cid is None:
            return None, None
        return cid, title_text
    except Exception:
        return None, None

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    print(f"{'URL':30} | {'category_id':12} | タイトル")
    print("-" * 80)
    for path in SCAN_PATHS:
        cid, title = get_info(page, BASE + path)
        if cid:
            print(f"{path:30} | {cid:12} | {title}")

    browser.close()
