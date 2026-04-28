"""AMD マザーボードと SSD/NVMe カテゴリを探す"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
BASE = "https://www.pc-koubou.jp"

# AMD MB と SSD/NVMe の候補を絞り込む
TARGETS = [
    "/category/040302.html",   # 内蔵2.5HDD
]

def get_info(page, url):
    try:
        page.goto(url, timeout=15000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        inp = soup.select_one('input[name="agg_category_id"]')
        h1 = soup.select_one("h1")
        title_text = h1.get_text(strip=True)[:50] if h1 else "?"
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
    for path in TARGETS:
        cid, title = get_info(page, BASE + path)
        if cid:
            print(f"{path:30} | {cid:8} | {title}")
        else:
            print(f"{path:30} | N/A      | (カテゴリなし)")
    browser.close()
