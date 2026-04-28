"""PC工房の各カテゴリページから category_id (agg_category_id) を抽出するスクリプト"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json

CATEGORY_PAGES = {
    "cpu":         "/category/040101.html",
    "motherboard": "/category/040201.html",
    "memory":      "/category/040301.html",
    "storage":     "/category/040401.html",
    "gpu":         "/category/040501.html",
    "psu":         "/category/040601.html",
    "case":        "/category/040801.html",
    "cpu_cooler":  "/category/040901.html",
}

BASE_URL = "https://www.pc-koubou.jp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

def extract_category_id(page, url: str) -> int | None:
    """ページから agg_category_id を抽出"""
    try:
        page.goto(url, timeout=30000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # <input name="agg_category_id" type="hidden" value="XXXX">
        inp = soup.select_one('input[name="agg_category_id"]')
        if inp:
            value = inp.get("value", "0")
            return int(value)
    except Exception as e:
        print(f"  エラー: {e}")
    return None

def main():
    result = {}
    
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        
        for part_type, path in CATEGORY_PAGES.items():
            url = BASE_URL + path
            print(f"取得中: {part_type:15} ... {url}")
            cid = extract_category_id(page, url)
            if cid is not None:
                result[part_type] = cid
                print(f"  → category_id = {cid}")
            else:
                print(f"  → 未取得（0を設定）")
                result[part_type] = 0
        
        browser.close()
    
    print("\n=== 結果 ===")
    print("CATEGORY_IDS = {")
    for part_type in CATEGORY_PAGES.keys():
        cid = result.get(part_type, 0)
        print(f'    "{part_type:15}": {cid},')
    print("}")
    
    # JSON で保存
    with open("category_ids_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("\n category_ids_result.json に保存しました。")

if __name__ == "__main__":
    main()
