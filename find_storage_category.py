"""カテゴリ URL の category_id を確認 + storage の正しいカテゴリを探す"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

def get_category_id(page, url: str):
    page.goto(url, timeout=30000)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.select_one('input[name="agg_category_id"]')
    title = soup.select_one("h1") or soup.select_one("title")
    title_text = title.get_text(strip=True)[:60] if title else "?"
    cid = inp.get("value") if inp else "未取得"
    return cid, title_text

# 確認対象
CHECK_URLS = {
    "040401.html (Intel MB?)":  "/category/040401.html",
    "040202.html (Intel MB?)":  "/category/040202.html",
    "040301.html (memory)":     "/category/040301.html",
    "040501.html (storage?)":   "/category/040501.html",
    "040601.html (psu)":        "/category/040601.html",
    # ストレージ候補を幅広く探す
    "040701.html":              "/category/040701.html",
    "041001.html":              "/category/041001.html",
    "041101.html":              "/category/041101.html",
    "041201.html":              "/category/041201.html",
    "041301.html":              "/category/041301.html",
}

BASE = "https://www.pc-koubou.jp"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()
    for label, path in CHECK_URLS.items():
        try:
            cid, title = get_category_id(page, BASE + path)
            print(f"{label:30} | category_id={cid:6} | {title}")
        except Exception as e:
            print(f"{label:30} | ERROR: {e}")
    browser.close()
