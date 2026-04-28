"""2.5HDD ページ構造確認"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
url = "https://www.pc-koubou.jp/products/list.php?category_id=1912&disp_number=10&pageno=1"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=UA)
    page = context.new_page()
    page.goto(url, timeout=30000)
    page.wait_for_timeout(5000)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # 商品数テキストを探す
    for sel in [".search-result-total", ".item-total", ".total-count", ".search-total"]:
        el = soup.select_one(sel)
        if el:
            print(f"{sel}: {el.get_text(strip=True)}")

    # .item-list 配下を確認
    item_list = soup.select_one(".item-list")
    if item_list:
        children = [c for c in item_list.children if hasattr(c, "name") and c.name]
        print(f".item-list 直下の要素数: {len(children)}")
        for c in children[:5]:
            cls = c.get("class", [])
            print(f"  <{c.name} class={cls}>")

    # "在庫なし" や "商品がありません" を探す
    text = soup.get_text()
    if "商品がありません" in text or "該当する商品が" in text:
        print("→ 商品なし表示あり（在庫ゼロ）")
    else:
        print("→ 商品なし表示なし")

    browser.close()
