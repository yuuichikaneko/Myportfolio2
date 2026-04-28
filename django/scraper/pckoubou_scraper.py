"""
PC工房（pc-koubou.jp）スクレイパー

商品一覧が JavaScript (AJAX) で動的にレンダリングされるため、
Playwright (ヘッドレス Chromium) を使用します。

取得構造:
  コンテナ : .itemlist--1 .search-result
  商品ID  : div#search-result--{product_id}
  商品名  : p.name
  価格    : .price--num（カンマ除去して int）
  URL     : a[href*='product_id']
  スペック: .spec
  ページ  : pageno パラメータをインクリメント（重複IDで終了）
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Union

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PCKOUBOU_BASE_URL = "https://www.pc-koubou.jp"
PCKOUBOU_LIST_URL = f"{PCKOUBOU_BASE_URL}/products/list.php"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# ------------------------------------------------------------------
# カテゴリID対応表
# products/list.php?category_id=XXXX の XXXX に相当
# CPU・マザーボードは Intel と AMD に分かれているため List[int] で指定
# str を指定した場合は固定URL（ページネーションなし）として直接取得
# ------------------------------------------------------------------
CATEGORY_IDS: Dict[str, Union[int, List[int], str]] = {
    "cpu":         [1899, 1900],  # Intel CPU (1899) + AMD CPU (1900)
    "motherboard": [1902, 1903],  # Intel対応MB (1902) + AMD対応MB (1903)
    "memory":      1895,          # PC用メモリ
    "ssd":         2001,          # SSD (/category/040310.html)
    "hdd":         1911,          # 内蔵3.5HDD (/category/040301.html)
    "hdd25":       1912,          # 内蔵2.5HDD (/category/040302.html)
    "gpu":         1904,          # グラフィックカード
    "psu":         1943,          # PC電源
    "case":        1921,          # ミドルタワーケース（代表）
    "cpu_cooler":  1931,          # CPUクーラー (/category/040901.html)
    "case_fan":    1932,          # ケースファン (/category/040902.html)
    "os":          f"{PCKOUBOU_BASE_URL}/goods/windows11_package.php",  # Windows 11 パッケージ版
}

# スクレイパー内部キー → DB保存時の part_type マッピング
# ssd/hdd/hdd25 は DB では storage として保存し、specs['storage_type'] で区別する
DB_PART_TYPE_MAP: Dict[str, str] = {
    "ssd":   "storage",
    "hdd":   "storage",
    "hdd25": "storage",
}

# カテゴリ判定キーワード（dospara_scraper の流用）
CATEGORY_RULES: Dict[str, Dict[str, List[str]]] = {
    "cpu": {
        "include": ["ryzen", "core i", "core ultra", "xeon", "cpu box", "pentium", "celeron"],
        "exclude": ["グリス", "cooler", "クーラー", "cpuクーラー", "water block"],
    },
    "gpu": {
        "include": ["geforce", "rtx ", "radeon", "graphics", "arc ", "rx "],
        "exclude": ["monitor", "モニター", "gt 710", "gt 1030"],
    },
    "motherboard": {
        "include": [
            "motherboard", "マザーボード", "lga1700", "lga1851",
            "am4", "am5", "b550", "b650", "b760", "z690", "z790", "x670",
        ],
        "exclude": ["クーラー", "cooler", "グリス"],
    },
    "memory": {
        "include": ["ddr4", "ddr5", "sodimm", "メモリ", "memory", "pc5-", "pc4-"],
        "exclude": ["ssd", "hdd", "nvme"],
    },
    "ssd": {
        "include": ["ssd", "nvme", "m.2"],
        "exclude": ["マザーボード", "motherboard", "hdd"],
    },
    "hdd": {
        "include": ["hdd", "ハードディスク", "hard disk"],
        "exclude": ["ssd", "マザーボード", "motherboard"],
    },
    "hdd25": {
        "include": ["hdd", "ハードディスク", "hard disk", "2.5"],
        "exclude": ["ssd", "マザーボード", "motherboard"],
    },
    "psu": {
        "include": ["power", "psu", "電源", "80 plus", "80plus", "atx3.0"],
        "exclude": ["ケーブル"],
    },
    "case": {
        "include": ["pcケース", "case", "chassis", "ミドルタワー", "フルタワー"],
        "exclude": ["ケースファン"],
    },
    "cpu_cooler": {
        "include": ["cpuクーラー", "cpu cooler", "aio", "簡易水冷", "水冷", "空冷"],
        "exclude": ["グリス", "マザーボード"],
    },
    "case_fan": {
        "include": ["ケースファン", "case fan", "pcファン", "120mm", "140mm"],
        "exclude": ["cpuクーラー", "cpu cooler"],
    },
    "os": {
        "include": ["windows 11", "windows 10", "microsoft windows"],
        "exclude": ["office", "word", "excel"],
    },
}


def _infer_part_type(name: str) -> Optional[str]:
    """商品名からカテゴリを推定する"""
    name_lower = name.lower()
    for part_type, rules in CATEGORY_RULES.items():
        includes = rules.get("include", [])
        excludes = rules.get("exclude", [])
        if any(kw in name_lower for kw in includes):
            if not any(kw in name_lower for kw in excludes):
                return part_type
    return None


def _parse_price(text: str) -> Optional[int]:
    """価格テキストから数値を抽出する（例: '249,800' → 249800）"""
    if not text:
        return None
    cleaned = text.replace(",", "").strip()
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


def _extract_items_from_html(html: str, part_type: str) -> List[Dict]:
    """レンダリング済みHTMLから商品リストを構築する"""
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    for item in soup.select(".itemlist--1 .search-result"):
        # 商品ID
        product_id = item.get("id", "").replace("search-result--", "").strip()

        # 商品名
        name_el = item.select_one("p.name")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        # 価格
        price_el = item.select_one(".price--num")
        if not price_el:
            # 価格なし = 取扱終了 or 売り切れの可能性があるためスキップ
            continue
        price = _parse_price(price_el.get_text(strip=True))
        if price is None or price <= 0:
            continue

        # URL
        link_el = item.select_one("a[href*='product_id']")
        href = link_el["href"] if link_el else f"/products/detail.php?product_id={product_id}"
        if href and not href.startswith("http"):
            href = PCKOUBOU_BASE_URL + href

        # スペック文字列
        spec_el = item.select_one(".spec")
        spec_text = spec_el.get_text(strip=True) if spec_el else ""

        # コメント
        comment_el = item.select_one(".item-comment")
        comment = comment_el.get_text(strip=True) if comment_el else ""

        # part_type は呼び出し元から渡す（カテゴリID単位でスクレイピングするため）
        # ただし渡された part_type が None のときはキーワード推定を使う
        effective_part_type = part_type or _infer_part_type(name)
        if not effective_part_type:
            logger.debug("part_type 判定不能 スキップ: %s", name)
            continue

        # ssd/hdd/hdd25 は DB 保存時に storage へ変換。元のキーは specs に保持
        db_part_type = DB_PART_TYPE_MAP.get(effective_part_type, effective_part_type)
        specs_dict: Dict = {
            "code":         product_id,
            "spec_text":    spec_text,
            "comment":      comment,
        }
        if effective_part_type != db_part_type:
            specs_dict["storage_type"] = effective_part_type  # "ssd" / "hdd" / "hdd25"

        items.append({
            "part_type":    db_part_type,
            "name":         name,
            "price":        price,
            "url":          href,
            "stock_status": "in_stock",   # 価格が存在する = 販売中とみなす
            "specs":        specs_dict,
        })

    return items


def scrape_pckoubou_category(
    part_type: str,
    max_items: int = 500,
    timeout_ms: int = 30000,
    headless: bool = True,
) -> List[Dict]:
    """
    指定カテゴリの全ページをスクレイピングして商品リストを返す。
    複数の category_id を持つカテゴリの場合は全て取得する。

    Args:
        part_type: 'gpu', 'cpu' など CATEGORY_IDS のキー
        max_items: 最大取得件数
        timeout_ms: Playwright のタイムアウト（ミリ秒）
        headless: ヘッドレスモードで実行するか

    Returns:
        商品辞書のリスト
    """
    from playwright.sync_api import sync_playwright

    category_ids_raw = CATEGORY_IDS.get(part_type)
    if category_ids_raw is None:
        logger.error("カテゴリが未定義です: %s", part_type)
        return []

    all_items: List[Dict] = []
    seen_ids: set = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
        page = context.new_page()

        # str の場合は固定URLを直接取得（ページネーションなし）
        if isinstance(category_ids_raw, str):
            fixed_url = category_ids_raw
            logger.info("カテゴリ=%s, 固定URL=%s のスクレイピング開始", part_type, fixed_url)
            try:
                page.goto(fixed_url, timeout=timeout_ms)
                page.wait_for_selector(".itemlist--1", timeout=timeout_ms)
            except Exception as e:
                logger.warning("ページロード失敗 url=%s err=%s", fixed_url, e)
                browser.close()
                return []
            html = page.content()
            all_items = _extract_items_from_html(html, part_type)
            browser.close()
            return all_items[:max_items]

        # category_ids_raw が int なら [int] に統一、List なら そのまま使用
        if isinstance(category_ids_raw, int):
            category_ids = [category_ids_raw]
        else:
            category_ids = category_ids_raw

        for category_id in category_ids:
            pageno = 1
            logger.info("カテゴリ=%s, category_id=%d のスクレイピング開始", part_type, category_id)

            while len(all_items) < max_items:
                url = (
                    f"{PCKOUBOU_LIST_URL}"
                    f"?category_id={category_id}"
                    f"&disp_number=30"
                    f"&pageno={pageno}"
                )
                logger.info("スクレイピング中: %s", url)

                try:
                    page.goto(url, timeout=timeout_ms)
                    # まず商品コンテナ本体を待機（在庫ゼロでも表示される）
                    page.wait_for_selector(".itemlist--1", timeout=timeout_ms)
                except Exception as e:
                    logger.warning("ページロード失敗 category_id=%d pageno=%d err=%s", category_id, pageno, e)
                    break

                html = page.content()

                # 在庫ゼロ判定（商品リスト要素が存在しない場合はスキップ）
                from bs4 import BeautifulSoup as _BS
                _soup = _BS(html, "html.parser")
                if not _soup.select(".itemlist--1 .search-result"):
                    logger.info("category_id=%d pageno=%d: 商品なし（在庫ゼロ）。終了。", category_id, pageno)
                    break

                page_items = _extract_items_from_html(html, part_type)

                if not page_items:
                    logger.info("category_id=%d pageno=%d で商品なし。次へ。", category_id, pageno)
                    break

                # 重複チェック（ページネーション終端の検出）
                new_ids = {
                    item["specs"]["code"]
                    for item in page_items
                    if item["specs"].get("code")
                }
                if new_ids and new_ids.issubset(seen_ids):
                    logger.info("category_id=%d pageno=%d で重複ID検出。最終ページ到達。", category_id, pageno)
                    break

                for item in page_items:
                    code = item["specs"].get("code", "")
                    if code not in seen_ids:
                        seen_ids.add(code)
                        all_items.append(item)

                logger.info(
                    "category_id=%d pageno=%d: %d件取得（累計: %d件）",
                    category_id, pageno, len(page_items), len(all_items),
                )
                pageno += 1

        browser.close()

    return all_items[:max_items]


def scrape_pckoubou_all(
    max_items_per_category: int = 500,
    timeout_ms: int = 30000,
    headless: bool = True,
) -> List[Dict]:
    """
    CATEGORY_IDS に登録された全カテゴリを一括スクレイピングする。
    """
    all_parts: List[Dict] = []
    for part_type in CATEGORY_IDS.keys():
        parts = scrape_pckoubou_category(
            part_type,
            max_items=max_items_per_category,
            timeout_ms=timeout_ms,
            headless=headless,
        )
        logger.info("カテゴリ=%s 取得件数=%d", part_type, len(parts))
        all_parts.extend(parts)
    return all_parts
