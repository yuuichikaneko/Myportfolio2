"""AMD CPU の category_id を取得"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

url = 'https://www.pc-koubou.jp/category/040102.html'
user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=user_agent)
    page = context.new_page()
    page.goto(url, timeout=30000)
    html = page.content()
    soup = BeautifulSoup(html, 'html.parser')
    inp = soup.select_one('input[name="agg_category_id"]')
    if inp:
        print(f'AMD CPU category_id = {inp.get("value")}')
    else:
        print('category_id not found')
    browser.close()
