"""category_id の空白番号を試して SSD・AMD MB を見つける"""
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
}

BASE = "https://www.pc-koubou.jp/products/list.php"

# 確認済みIDの空白を試す: 1893-1935 の範囲で未確認のもの
KNOWN = {1896, 1897, 1899, 1900, 1902, 1904, 1911, 1912, 1915, 1920, 1921, 1922, 1923, 1924, 1931, 1932, 1933}
CANDIDATES = [i for i in range(1893, 1950) if i not in KNOWN]

def check_category(cid):
    url = f"{BASE}?category_id={cid}&disp_number=5&pageno=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True)[:50] if h1 else ""
        # 商品数を確認
        count_el = soup.select_one(".search-result-total-count") or soup.select_one(".total-count")
        count = count_el.get_text(strip=True) if count_el else ""
        if title and title != "PC-Koubou" and "ページが見つかりません" not in title:
            return title, count
    except Exception:
        pass
    return None

print(f"{'category_id':12} | {'タイトル':45} | 商品数")
print("-" * 80)
for cid in CANDIDATES:
    result = check_category(cid)
    if result:
        title, count = result
        print(f"{cid:12} | {title:45} | {count}")
