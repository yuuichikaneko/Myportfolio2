import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings


logger = logging.getLogger(__name__)

DOSPARA_PARTS_URL = "https://www.dospara.co.jp/parts"
DOSPARA_PRODUCTS_API_URL = "https://www.dospara.co.jp/s/dospara/api/getProducts"
DOSPARA_UPDATE_GRID_URL = (
    "https://www.dospara.co.jp/on/demandware.store/"
    "Sites-dospara-Site/ja_JP/Search-UpdateGrid"
)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}

# DOM差分に追随しやすいよう、抽出セレクタを設定化する。
SCRAPER_SELECTORS = {
    "item_roots": [
        "article",
        "li",
        "div[class*='item']",
        "div[class*='product']",
        "div[class*='card']",
    ],
    "name": [
        "a[title]",
        "h1",
        "h2",
        "h3",
        "a[href]",
    ],
    "price": [
        "[data-price]",
        "span[class*='price']",
        "div[class*='price']",
        "p[class*='price']",
    ],
    "link": [
        "a[href]",
    ],
}

SCRAPER_DEFAULT_CONFIG = {
    "url": DOSPARA_PARTS_URL,
    "products_api_url": DOSPARA_PRODUCTS_API_URL,
    "timeout": 20,
    "max_items": 500,
    "batch_size": 100,
    "headers": DEFAULT_HEADERS,
    "selectors": SCRAPER_SELECTORS,
}

PRICE_PATTERNS = [
    re.compile(r"([0-9][0-9,]{2,})\s*円"),
    re.compile(r"¥\s*([0-9][0-9,]{2,})"),
]

PRODUCT_LINK_PATTERN = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*(?:IC\d+\.html|/SBR\d+/IC\d+\.html)[^"]*)"[^>]*>(?P<name>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
IC_CODE_PATTERN = re.compile(r"IC\d{6,}")
PRODUCT_PAGE_HREF_PATTERN = re.compile(r"/(?:SBR\d+/)?(IC\d{6,})\.html", re.IGNORECASE)

# ドスパラの一覧に混在する汎用カテゴリ名を、アプリの part_type へ寄せる。
CATEGORY_RULES = {
    "cpu_cooler": {
        "include": [
            "cpuクーラー",
            "cpu cooler",
            "air cooler",
            "water cooler",
            "aio",
            "簡易水冷",
            "水冷",
            "空冷",
            "radiator",
            "lga1700対応",
            "am5対応",
            "noctua",
            "deepcool",
            "corsair icue link h",
            "arctic p12",
            "arctic p14",
            "arctic f12",
            "arctic f14",
            "arctic s8",
            "arctic s12",
            "kaze flex",
            "wonder snail",
            "uni fan",
            "icue link lx",
            "icue link qx",
            "momentum 12",
            "momentum 14",
            "fractal aspect",
            "be quiet! pure wings",
            "be quiet! pro wings",
            "phanteks d30",
            "phanteks t30",
            "nzxt f120",
            "nzxt f140",
            "nzxt f360",
        ],
        "exclude": ["グリス", "thermal paste", "マザーボード", "motherboard", "pcケース"],
    },
    "cpu": {
        "include": ["ryzen", "core i", "pentium", "celeron", "core ultra", "cpu box"],
        "exclude": ["グリス", "cooler", "クーラー", "ファン", "cpuクーラー", "water block"],
    },
    "gpu": {
        "include": ["geforce", "rtx", "radeon", "graphics", "arc ", "rx "],
        "exclude": ["monitor", "モニター", "gt 710", "gt710", "gt 1030", "gt1030"],
    },
    "motherboard": {
        "include": [
            "motherboard",
            "マザーボード",
            "chipset",
            "lga1700",
            "lga1851",
            "am4",
            "am5",
            "microatx",
            "mini-itx",
            "h610",
            "h670",
            "b550",
            "b650",
            "b760",
            "z690",
            "z790",
            "x670",
        ],
        "exclude": [
            "noctua",
            "nh-",
            "クーラー",
            "cooler",
            "ガラス",
            "グリス",
            "thermal paste",
            "phase change material",
            "thermal pad",
            "perihelion",
            "mnm-ptmp",
        ],
    },
    "memory": {
        "include": ["ddr4", "ddr5", "sodimm", "メモリ", "memory", "pc5-", "pc4-"],
        "exclude": ["ssd", "hdd", "nvme"],
    },
    "storage": {
        "include": [
            "ssd",
            "hdd",
            "nvme",
            "m.2",
            "storage",
            "ストレージ",
            "wd black",
            "wd blue",
            "wd red",
            "wds",
            "barracuda",
            "ironwolf",
            "mq04",
            "dt02",
            "n300",
            "mg10",
            "mg11",
            "hat3300",
        ],
        "exclude": ["microatx", "mini-itx", "am5", "am4", "lga1700", "lga1851", "b650", "b760", "z790", "h610"],
    },
    "os": {
        "include": ["windows 11", "windows 10", "microsoft windows", "operating system", "os 日本語"],
        "exclude": ["office", "word", "excel", "outlook", "antivirus", "security"],
    },
    "psu": {
        "include": ["power", "psu", "電源", "80 plus", "80plus", "pcie5", "atx3.0", "atx 3.0"],
        "exclude": ["ケーブル", "fan", "ファン"],
    },
    "case": {
        "include": ["pcケース", "case", "chassis", "ミドルタワー", "フルタワー", "ピラーレス", "ガラス"],
        "exclude": ["ケースファン", "ファン"],
    },
}

URL_CATEGORY_HINTS = {
    "cpu_cooler": ["/sbr95/", "/br95", "/cpu-cooler", "/sbr738/", "/sbr739/", "/br116"],
    "cpu": ["/sbr2/", "/sbr8/", "/TC2/"],
    "gpu": ["/sbr4/", "/sbr1853/", "/TC8/"],
    "memory": ["/sbr5/", "/sbr1716/"],
    "storage": ["/br13", "/sbr7/", "/sbr12/", "/sbr13/", "/sbr6/", "/hdd-35sata"],
    "os": ["/br161", "/sbr170/"],
    "psu": ["/sbr83/"],
    "case": ["/sbr79/", "/sbr447/", "/sbr143/", "/sbr1959/", "/sbr448/"],
    "motherboard": ["/sbr1739/", "/sbr1798/", "/sbr1297/", "/sbr21/"],
}

# パーツ種別ごとの価格帯取得用カテゴリページ URL
# ドスパラはURLパスの大文字小文字が厳密なため、小文字に統一する。
PART_CATEGORY_URLS: Dict[str, List[str]] = {
    "cpu":         ["https://www.dospara.co.jp/cpu", "https://www.dospara.co.jp/BR11", "https://www.dospara.co.jp/BR10?srule=03&includeNotInventory=false"],
    "cpu_cooler":  ["https://www.dospara.co.jp/BR95"],
    "gpu":         [
        "https://www.dospara.co.jp/BR31",
        "https://www.dospara.co.jp/nvidia-geforce?prefn1=txChipFilter&prefv1=GeForce%20RTX%203050%7cGeForce%20GTX%201660%20SUPER%7cGeForce%20GTX%201660&srule=01&includeNotInventory=false",
        "https://www.dospara.co.jp/BR31?prefn1=txChipFilter&prefv1=Radeon%20RX%207600%7cRadeon%20RX%206600%7cRadeon%20RX%206400&srule=01&includeNotInventory=false",
    ],
    "motherboard": ["https://www.dospara.co.jp/BR21", "https://www.dospara.co.jp/mb-intel", "https://www.dospara.co.jp/mb-amd"],
    "memory":      [
        "https://www.dospara.co.jp/mem-desktop?srule=03&includeNotInventory=false",
        "https://www.dospara.co.jp/BR12",
        "https://www.dospara.co.jp/mem-note",
    ],
    "storage":     ["https://www.dospara.co.jp/BR115", "https://www.dospara.co.jp/BR13", "https://www.dospara.co.jp/m2ssd"],
    "os":          ["https://www.dospara.co.jp/BR161"],
    "psu":         ["https://www.dospara.co.jp/BR83", "https://www.dospara.co.jp/SBR755"],
    "case":        ["https://www.dospara.co.jp/BR72", "https://www.dospara.co.jp/case-tower", "https://www.dospara.co.jp/case-compact"],
}

MARKET_BRAND_URLS = {
    "dospara_tc30_market": "https://www.dospara.co.jp/TC30?pmax=2%2C500%2C000.00&srule=04&includeNotInventory=false",
}

DOSPARA_GPU_PERFORMANCE_URL = "https://www.pc-koubou.jp/pc/benchmark.php"
GPU_PERF_SCORE_NOTE = "PC工房公開の3DMark Fire Strike Graphics Score参考値"
DOSPARA_AMD_CPU_PERFORMANCE_URL = "https://www.pc-koubou.jp/magazine/5813"
DOSPARA_INTEL_CPU_PERFORMANCE_URL = "https://www.pc-koubou.jp/magazine/5574"
PCKOUBOU_CPU_REFERENCE_URL = "https://www.pc-koubou.jp/magazine/references/ref-cpu"

PRICE_IN_HTML_PATTERN = re.compile(r"([1-9][0-9]{0,2}(?:,[0-9]{3})+)")
INTEL_13_14_GEN_PATTERN = re.compile(r"\bCORE\s+I[3579]\s*[- ]?\s*1[34]\d{3}[A-Z]*\b", re.IGNORECASE)

STOCK_IN_STOCK_HINTS = (
    '在庫あり',
    '在庫有り',
    '即納',
    '翌日出荷',
    '当日出荷',
    'available',
    'in stock',
)

STOCK_OUT_OF_STOCK_HINTS = (
    '在庫切れ',
    '在庫なし',
    '欠品',
    '販売終了',
    '取扱終了',
    '完売',
    '入荷待ち',
    'sold out',
    'out of stock',
    'unavailable',
    'backorder',
)


def _normalize_stock_status(raw_status: str) -> str:
    text = str(raw_status or '').strip().lower()
    if not text:
        return 'unknown'

    if any(hint in text for hint in STOCK_OUT_OF_STOCK_HINTS):
        return 'out_of_stock'
    if any(hint in text for hint in STOCK_IN_STOCK_HINTS):
        return 'in_stock'
    return 'unknown'


def _normalize_price(price_text: str) -> Optional[int]:
    digits = re.sub(r"[^0-9]", "", price_text or "")
    if not digits:
        return None
    return int(digits)


def _extract_market_prices(html: str) -> List[int]:
    prices: List[int] = []
    for match in PRICE_IN_HTML_PATTERN.finditer(html or ""):
        normalized = _normalize_price(match.group(1))
        if normalized is None:
            continue
        # TC30のpmax=2,500,000に合わせ、ハイエンド帯の上限価格も含める。
        if 70000 <= normalized <= 2500000:
            prices.append(normalized)
    return prices


def _extract_market_total_count(html: str) -> Optional[int]:
    # 例: "全 1,087 件"
    match = re.search(r"全\s*([0-9,]+)\s*件", html or "")
    if not match:
        return None
    return _normalize_price(match.group(1))


def _flatten_query_params(url: str) -> Dict[str, str]:
    parsed = urlparse(url)
    raw_params = parse_qs(parsed.query)
    return {key: values[-1] for key, values in raw_params.items() if values}


def _collect_market_prices_from_paginated_grid(
    first_html: str,
    category_url: str,
    headers: Dict[str, str],
    timeout: int,
    session: Optional[requests.Session],
    page_size: int = 60,
    max_pages: int = 120,
) -> List[int]:
    prices: List[int] = []
    prices.extend(_extract_market_prices(first_html))

    cgid = _extract_category_id(category_url)
    if not cgid:
        return prices

    total_count = _extract_market_total_count(first_html)
    query_params = _flatten_query_params(category_url)
    client = session or requests.Session()

    seen_page_signatures = set()

    for page_idx in range(1, max_pages + 1):
        start = page_idx * page_size
        if total_count is not None and start >= total_count:
            break

        params = {
            "cgid": cgid,
            "start": start,
            "sz": page_size,
        }
        # URLに指定された絞り込み条件（srule/pmax/includeNotInventory など）を維持する。
        for key, value in query_params.items():
            if key in {"pageno", "cgid", "start", "sz"}:
                continue
            params[key] = value

        response = client.get(
            DOSPARA_UPDATE_GRID_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()

        html = response.text or ""
        page_signature = hash(html[:2000])
        if page_signature in seen_page_signatures:
            break
        seen_page_signatures.add(page_signature)

        page_prices = _extract_market_prices(html)
        if not page_prices:
            break
        prices.extend(page_prices)

    return prices


def fetch_dospara_market_price_range(timeout: int = 15, session: Optional[requests.Session] = None) -> Dict:
    client = session or requests.Session()
    headers = DEFAULT_HEADERS

    per_brand: Dict[str, Dict] = {}
    all_prices: List[int] = []

    for brand, url in MARKET_BRAND_URLS.items():
        try:
            response = client.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            prices = _collect_market_prices_from_paginated_grid(
                first_html=response.text,
                category_url=url,
                headers=headers,
                timeout=timeout,
                session=client,
            )
            if not prices:
                per_brand[brand] = {"url": url, "min": None, "max": None, "count": 0}
                continue

            per_brand[brand] = {
                "url": url,
                "min": min(prices),
                "max": max(prices),
                "count": len(prices),
            }
            all_prices.extend(prices)
        except Exception:
            per_brand[brand] = {"url": url, "min": None, "max": None, "count": 0}

    if all_prices:
        market_min = min(all_prices)
        market_max = max(all_prices)
        # 中央値 - 15,000円をデフォルトにする
        median_price = (market_min + market_max) / 2
        suggested_default = max(market_min, int(median_price) - 15000)
    else:
        # 取得失敗時の安全なフォールバック
        market_min = 100000
        market_max = 400000
        suggested_default = 250000

    return {
        "min": market_min,
        "max": market_max,
        "default": suggested_default,
        "currency": "JPY",
        "sources": per_brand,
    }


def _parse_gpu_vram_gb(value: str) -> Optional[int]:
    text = (value or "").strip().lower()
    if not text or text in {"-", "--", "計測中"}:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*gb", text)
    if not match:
        return None
    return int(float(match.group(1)))


def _extract_gpu_model_key(name: str) -> Optional[str]:
    text = re.sub(r"\s+", " ", (name or "").upper()).strip()
    text = re.sub(r"^NEW\s+", "", text)

    patterns = [
        r"RTX\s*\d{4}\s*TI\s*SUPER",
        r"RTX\s*\d{4}\s*SUPER",
        r"RTX\s*\d{4}\s*TI",
        r"RTX\s*\d{4}",
        r"GTX\s*\d{3,4}\s*TI",
        r"GTX\s*\d{3,4}",
        r"GT\s*\d{3,4}",
        r"RX\s*\d{4}\s*XTX",
        r"RX\s*\d{4}\s*XT",
        r"RX\s*\d{4}\s*GRE",
        r"RX\s*\d{4}",
        r"INTEL\s+ARC\s+[AB]\d{3,4}",
        r"ARC\s+[AB]\d{3,4}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _infer_gpu_vendor(name: str) -> str:
    text = (name or "").lower()
    if "rtx" in text or "gtx" in text or re.search(r"\bgt\s*\d{3,4}\b", text):
        return "nvidia"
    if "radeon" in text or "rx " in text or re.search(r"\brx\d{4}", text):
        return "amd"
    if "arc" in text:
        return "intel"
    if "uhd" in text or "iris" in text or "vega" in text or "780m" in text:
        return "igpu"
    return "unknown"


def fetch_dospara_gpu_performance_table(timeout: int = 20, session: Optional[requests.Session] = None) -> Dict:
    """Fetch GPU performance rows from Dospara comparison page for specs-level enrichment."""
    client = session or requests.Session()
    response = client.get(DOSPARA_GPU_PERFORMANCE_URL, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title_text = soup.get_text(" ", strip=True)
    updated_match = re.search(r"更新日[：:]?\s*(\d{4}年\d{1,2}月\d{1,2}日)", title_text)
    updated_at_source = updated_match.group(1) if updated_match else None

    entries: List[Dict] = []
    seen = set()

    for table in soup.find_all("table"):
        header = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if "名称" not in header or "性能目安" not in header:
            continue
        if "詳細" not in header:
            # 過去製品一覧（中古列中心）を避ける。
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            name = cells[0].get_text(" ", strip=True)
            vram_raw = cells[1].get_text(" ", strip=True)
            score_raw = cells[2].get_text(" ", strip=True)
            detail_anchor = cells[3].find("a", href=True)
            detail_url = urljoin(DOSPARA_GPU_PERFORMANCE_URL, detail_anchor["href"]) if detail_anchor else ""

            if not name or "シリーズ" in name:
                continue

            score_digits = re.sub(r"[^0-9]", "", score_raw)
            if not score_digits:
                continue

            perf_score = int(score_digits)
            model_key = _extract_gpu_model_key(name)
            is_laptop = "laptop" in name.lower()
            vram_gb = _parse_gpu_vram_gb(vram_raw)

            row_key = (model_key or name, vram_gb, perf_score, is_laptop)
            if row_key in seen:
                continue
            seen.add(row_key)

            entries.append(
                {
                    "name": name,
                    "model_key": model_key,
                    "vendor": _infer_gpu_vendor(name),
                    "vram_raw": vram_raw,
                    "vram_gb": vram_gb,
                    "perf_score": perf_score,
                    "detail_url": detail_url,
                    "is_laptop": is_laptop,
                }
            )

    # PC工房ベンチマーク形式: GPU(CPU) / VRAM / Graphics Score
    for table in soup.find_all("table"):
        header = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if "GPU(CPU)" not in header or "Graphics Score" not in header:
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            cell_texts = [c.get_text(" ", strip=True) for c in cells]
            merged_text = " ".join(cell_texts)

            score_raw = cell_texts[-1]
            score_digits = re.sub(r"[^0-9]", "", score_raw)
            if not score_digits:
                continue
            perf_score = int(score_digits)

            # 代表的なGPU名称を抽出（CPU括弧書きは除去）
            gpu_name = ""
            for text in cell_texts:
                candidate = re.sub(r"\([^)]*\)", "", text).strip()
                upper = candidate.upper()
                if any(token in upper for token in ("RTX", "GTX", "RADEON", "RX ", "ARC", "UHD", "IRIS", "VEGA")):
                    gpu_name = candidate
                    break
            if not gpu_name:
                model_key_guess = _extract_gpu_model_key(merged_text)
                if model_key_guess:
                    gpu_name = model_key_guess
            if not gpu_name:
                continue

            vram_raw = next((t for t in cell_texts if "GB" in t.upper()), "")
            vram_gb = _parse_gpu_vram_gb(vram_raw)
            model_key = _extract_gpu_model_key(gpu_name)
            is_laptop = "laptop" in merged_text.lower() or "ノート" in merged_text

            row_key = (model_key or gpu_name, vram_gb, perf_score, is_laptop)
            if row_key in seen:
                continue
            seen.add(row_key)

            entries.append(
                {
                    "name": gpu_name,
                    "model_key": model_key,
                    "vendor": _infer_gpu_vendor(gpu_name),
                    "vram_raw": vram_raw,
                    "vram_gb": vram_gb,
                    "perf_score": perf_score,
                    "detail_url": DOSPARA_GPU_PERFORMANCE_URL,
                    "is_laptop": is_laptop,
                }
            )

    return {
        "source_name": "pckoubou_gpu_benchmark_page",
        "source_url": DOSPARA_GPU_PERFORMANCE_URL,
        "updated_at_source": updated_at_source,
        "score_note": GPU_PERF_SCORE_NOTE,
        "entries": entries,
    }


def _normalize_cpu_model_name(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("NEW", "").strip())


def _parse_cpu_perf_score(text: str) -> Optional[int]:
    digits = re.sub(r"[^0-9]", "", text or "")
    if not digits:
        return None
    return int(digits)


def _parse_first_int(text: str) -> Optional[int]:
    match = re.search(r"(\d+)", str(text or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _parse_cores_threads(text: str) -> Tuple[Optional[int], Optional[int]]:
    token = str(text or "")
    match = re.search(r"(\d+)\s*/\s*(\d+)", token)
    if match:
        return _parse_first_int(match.group(1)), _parse_first_int(match.group(2))
    cores = _parse_first_int(token)
    return cores, None


def _parse_clock_ghz(text: str) -> Optional[float]:
    token = str(text or "").lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*ghz", token)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _parse_cache_mb(text: str) -> Optional[int]:
    token = str(text or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*MB", token, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(float(match.group(1)))
    except (TypeError, ValueError):
        return None


def _derive_cpu_perf_score_from_specs(cores, threads, base_ghz, boost_ghz, cache_l2_mb, cache_l3_mb) -> int:
    cores_i = int(cores or 0)
    threads_i = int(threads or 0)
    base = float(base_ghz or 0.0)
    boost = float(boost_ghz or base_ghz or 0.0)
    cache_mb = int((cache_l2_mb or 0) + (cache_l3_mb or 0))

    # 既存ロジックの閾値(3000, 7000, 11000)に合わせた互換スケール
    score = (
        threads_i * 120
        + cores_i * 60
        + int(base * 350)
        + int(boost * 450)
        + cache_mb * 4
    )
    return max(score, 0)


def _is_excluded_intel_13_14_generation(model_name: str) -> bool:
    normalized = _normalize_cpu_model_name(model_name).upper()
    return INTEL_13_14_GEN_PATTERN.search(normalized) is not None


def _extract_cpu_performance_entries(html: str, vendor: str, source_url: str, exclude_intel_13_14: bool) -> Dict[str, List[Dict]]:
    soup = BeautifulSoup(html, "html.parser")
    entries: List[Dict] = []
    excluded: List[Dict] = []
    seen = set()

    for table in soup.find_all("table"):
        header = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if "型番" not in header or "性能目安" not in header:
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            model_name = _normalize_cpu_model_name(cells[0].get_text(" ", strip=True))
            perf_score = _parse_cpu_perf_score(cells[-1].get_text(" ", strip=True))
            if not model_name or perf_score is None:
                continue

            key = (vendor, model_name.upper(), perf_score)
            if key in seen:
                continue
            seen.add(key)

            row_data = {
                "vendor": vendor,
                "model_name": model_name,
                "perf_score": perf_score,
                "source_url": source_url,
            }

            if vendor == "intel" and exclude_intel_13_14 and _is_excluded_intel_13_14_generation(model_name):
                excluded.append({**row_data, "excluded_reason": "intel_13th_14th_generation"})
                continue

            entries.append(row_data)

    # PC工房のCPU資料形式（性能目安列なし）から合成スコアを作る
    for table in soup.find_all("table"):
        header = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
        if "コア/スレッド" not in header:
            continue
        if "動作クロック" not in header and "最大ブースト" not in header:
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            cell_texts = [c.get_text(" ", strip=True) for c in cells]
            model_name = _normalize_cpu_model_name(cell_texts[0])
            if not model_name:
                continue

            cores = None
            threads = None
            base_ghz = None
            boost_ghz = None
            cache_l2_mb = None
            cache_l3_mb = None

            for text in cell_texts:
                if cores is None and threads is None and re.search(r"\d+\s*/\s*\d+", text):
                    cores, threads = _parse_cores_threads(text)
                if base_ghz is None and "ghz" in text.lower():
                    base_ghz = _parse_clock_ghz(text)
                    continue
                if boost_ghz is None and "ghz" in text.lower():
                    boost_ghz = _parse_clock_ghz(text)

            mb_cells = [text for text in cell_texts if re.search(r"\d+(?:\.\d+)?\s*MB", text, re.IGNORECASE)]
            if mb_cells:
                cache_l2_mb = _parse_cache_mb(mb_cells[0])
            if len(mb_cells) >= 2:
                cache_l3_mb = _parse_cache_mb(mb_cells[1])

            if cores is None:
                continue
            if threads is None:
                threads = cores
            if base_ghz is None:
                base_ghz = 0.0
            if boost_ghz is None:
                boost_ghz = base_ghz

            perf_score = _derive_cpu_perf_score_from_specs(
                cores=cores,
                threads=threads,
                base_ghz=base_ghz,
                boost_ghz=boost_ghz,
                cache_l2_mb=cache_l2_mb,
                cache_l3_mb=cache_l3_mb,
            )
            if perf_score <= 0:
                continue

            key = (vendor, model_name.upper(), perf_score)
            if key in seen:
                continue
            seen.add(key)

            row_data = {
                "vendor": vendor,
                "model_name": model_name,
                "perf_score": perf_score,
                "source_url": source_url,
            }

            if vendor == "intel" and exclude_intel_13_14 and _is_excluded_intel_13_14_generation(model_name):
                excluded.append({**row_data, "excluded_reason": "intel_13th_14th_generation"})
                continue

            entries.append(row_data)

    return {"entries": entries, "excluded": excluded}


def fetch_dospara_cpu_selection_material(timeout: int = 20, session: Optional[requests.Session] = None, exclude_intel_13_14: bool = True) -> Dict:
    """Fetch CPU comparison materials from pc-koubou AMD/Intel pages with optional Intel 13/14 gen exclusion."""
    client = session or requests.Session()
    sources = [
        ("amd", DOSPARA_AMD_CPU_PERFORMANCE_URL),
        ("intel", DOSPARA_INTEL_CPU_PERFORMANCE_URL),
    ]

    all_entries: List[Dict] = []
    excluded_entries: List[Dict] = []

    for vendor, url in sources:
        response = client.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        response.raise_for_status()
        extracted = _extract_cpu_performance_entries(
            response.text,
            vendor=vendor,
            source_url=url,
            exclude_intel_13_14=exclude_intel_13_14,
        )
        all_entries.extend(extracted["entries"])
        excluded_entries.extend(extracted["excluded"])

    all_entries.sort(key=lambda row: (row.get("perf_score", 0), row.get("model_name", "")), reverse=True)

    return {
        "source_name": "pckoubou_cpu_spec_pages",
        "source_urls": [PCKOUBOU_CPU_REFERENCE_URL] + [url for _, url in sources],
        "exclude_intel_13_14": bool(exclude_intel_13_14),
        "entry_count": len(all_entries),
        "excluded_count": len(excluded_entries),
        "parser_version": "v2-pckoubou-spec-score",
        "entries": all_entries,
        "excluded_entries": excluded_entries,
    }


def _infer_part_type(name: str, url: str) -> Optional[str]:
    blob = f"{name} {url}".lower()

    # サーマルグリス/パッド等のアクセサリは構成パーツ対象外にする。
    accessory_excludes = (
        "thermal paste",
        "phase change material",
        "thermal pad",
        "perihelion",
        "mnm-ptmp",
        "サーマルグリス",
        "サーマルパッド",
        "熱伝導",
        "グリス",
    )
    if any(token in blob for token in accessory_excludes):
        return None

    # GT 710/1030 などの GeForce GT シリーズは対象外にする。
    # GTX/RTX は対象に残すため、"gt" + 数字のみを判定する。
    is_gt_series_gpu = re.search(r"\bgt[\s\-_/]*\d{3,4}\b", blob) is not None

    for hinted_type, hinted_paths in URL_CATEGORY_HINTS.items():
        if any(path in url.lower() for path in hinted_paths):
            if hinted_type == "gpu" and is_gt_series_gpu:
                return None
            return hinted_type

    scores: Dict[str, int] = {}
    for part_type, rule in CATEGORY_RULES.items():
        score = 0
        for kw in rule.get("include", []):
            if kw in blob:
                score += 2
        for kw in rule.get("exclude", []):
            if kw in blob:
                score -= 3
        scores[part_type] = score

    best_type = max(scores, key=scores.get)
    if best_type == "gpu" and is_gt_series_gpu:
        return None
    return best_type if scores.get(best_type, 0) > 0 else None


def _merge_selector_config(base: Dict[str, List[str]], override: Optional[Dict[str, List[str]]]) -> Dict[str, List[str]]:
    merged = {key: list(value) for key, value in base.items()}
    if not override:
        return merged

    for key, value in override.items():
        if isinstance(value, list) and value:
            merged[key] = value
    return merged


def _merge_scraper_config(base: Dict, override: Optional[Dict]) -> Dict:
    if not override:
        return dict(base)

    merged = dict(base)
    for key, value in override.items():
        if key == "selectors":
            merged["selectors"] = _merge_selector_config(base.get("selectors", {}), value)
        elif key == "headers":
            merged_headers = dict(base.get("headers", {}))
            if isinstance(value, dict):
                merged_headers.update(value)
            merged["headers"] = merged_headers
        else:
            merged[key] = value
    return merged


def get_dospara_scraper_config() -> Dict:
    configured = getattr(settings, "DOSPARA_SCRAPER", {}) or {}
    env_name = getattr(settings, "DOSPARA_SCRAPER_ENV", "development")
    env_map = getattr(settings, "DOSPARA_SCRAPER_BY_ENV", {}) or {}
    env_override = env_map.get(env_name, {}) if isinstance(env_map, dict) else {}

    base_config = _merge_scraper_config(SCRAPER_DEFAULT_CONFIG, configured)
    merged = _merge_scraper_config(base_config, env_override)

    return {
        "url": merged.get("url", SCRAPER_DEFAULT_CONFIG["url"]),
        "products_api_url": merged.get("products_api_url", SCRAPER_DEFAULT_CONFIG["products_api_url"]),
        "timeout": merged.get("timeout", SCRAPER_DEFAULT_CONFIG["timeout"]),
        "max_items": merged.get("max_items", SCRAPER_DEFAULT_CONFIG["max_items"]),
        "batch_size": merged.get("batch_size", SCRAPER_DEFAULT_CONFIG["batch_size"]),
        "headers": merged.get("headers", SCRAPER_DEFAULT_CONFIG["headers"]),
        "selectors": merged.get("selectors", SCRAPER_SELECTORS),
        "env": env_name,
    }


def _extract_ic_codes(html: str, max_codes: int) -> List[str]:
    codes = []
    seen = set()
    for code in IC_CODE_PATTERN.findall(html or ""):
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        if len(codes) >= max_codes:
            break
    return codes


def _extract_product_link_ic_codes(html: str, max_codes: int) -> List[str]:
    codes: List[str] = []
    seen = set()
    soup = BeautifulSoup(html or "", "html.parser")

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        match = PRODUCT_PAGE_HREF_PATTERN.search(href)
        if not match:
            continue

        code = match.group(1).upper()
        if code in seen:
            continue

        text = " ".join(anchor.stripped_strings)
        if not text or len(text) < 3:
            continue

        lowered = text.lower()
        if any(token in lowered for token in ["レビュー", "比較", "カート", "詳細を見る"]):
            continue

        seen.add(code)
        codes.append(code)
        if len(codes) >= max_codes:
            break

    return codes


def _extract_category_id(category_url: str) -> Optional[str]:
    match = re.search(r"dospara\.co\.jp/([A-Za-z]+\d+)", category_url or "", re.IGNORECASE)
    return match.group(1) if match else None


def _collect_ic_codes_from_category_pages(
    html: str,
    category_url: str,
    headers: Dict[str, str],
    timeout: int,
    session: Optional[requests.Session],
    max_codes: int,
    page_size: int = 20,
    max_pages: int = 30,
) -> List[str]:
    # 初回HTML + UpdateGridのページングからICコードを収集する。
    codes = _extract_product_link_ic_codes(html, max_codes=max_codes)
    if len(codes) < max_codes:
        for code in _extract_ic_codes(html, max_codes=max_codes):
            if code in codes:
                continue
            codes.append(code)
            if len(codes) >= max_codes:
                break
    if len(codes) >= max_codes:
        return codes

    cgid = _extract_category_id(category_url)
    if not cgid:
        return codes

    client = session or requests.Session()
    seen = set(codes)

    for page_idx in range(1, max_pages + 1):
        start = page_idx * page_size
        response = client.get(
            DOSPARA_UPDATE_GRID_URL,
            params={"cgid": cgid, "start": start, "sz": page_size},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()

        found_any = False
        for code in _extract_ic_codes(response.text, max_codes=page_size * 2):
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
            found_any = True
            if len(codes) >= max_codes:
                return codes

        # 新規コードがなければ末尾到達とみなして終了。
        if not found_any:
            break

    return codes


def _build_product_info_key(code: str, pname: str = "", kflg: str = "") -> str:
    return quote(f"pid:{code},q:{pname},kflg:{kflg}")


def _fetch_products_by_codes(
    codes: List[str],
    api_url: str,
    headers: Dict[str, str],
    timeout: int,
    batch_size: int,
    session: Optional[requests.Session],
) -> Dict[str, Dict]:
    client = session or requests.Session()
    product_info: Dict[str, Dict] = {}
    if not codes:
        return product_info

    step = max(1, int(batch_size or 1))
    for idx in range(0, len(codes), step):
        chunk = codes[idx: idx + step]
        payload = {
            "paramList": [{"pid": code, "q": "", "kflg": ""} for code in chunk],
        }
        response = client.post(api_url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        api_map = data.get("productInfoList", {}) if isinstance(data, dict) else {}

        for code in chunk:
            value = api_map.get(_build_product_info_key(code))
            if isinstance(value, dict) and value:
                product_info[code] = value

    return product_info


def _extract_specs_from_simplespec(part_type: str, simplespec: str) -> Dict:
    """simplespec テキストからパーツ種別ごとのスペック情報を抽出する。"""
    specs: Dict = {}
    if not simplespec:
        return specs
    text = simplespec

    # ソケット: CPU と マザーボード
    if part_type in ("cpu", "motherboard"):
        m = re.search(r"ソケット形状[：:\s]\s*([^●<\n]+?)(?:●|<|$)", text)
        if m:
            socket = re.sub(r"\s+", "", m.group(1).strip())
            socket = re.sub(r"^Socket", "", socket, flags=re.IGNORECASE)
            specs["socket"] = socket

    # CPU: コア数・スレッド数・ブーストクロック・TDP
    if part_type == "cpu":
        m = re.search(r"TDP[：:]\s*(\d+)W", text)
        if m:
            specs["tdp_w"] = int(m.group(1))
        m = re.search(r"コア数[：:]\s*(\d+)", text)
        if m:
            specs["core_count"] = int(m.group(1))
        m = re.search(r"スレッド数[：:]\s*(\d+)", text)
        if m:
            specs["thread_count"] = int(m.group(1))
        m = re.search(r"(?:最大クロック|ブーストクロック|Turbo\s*Boost)[：:]\s*([\d.]+)\s*GHz", text, re.IGNORECASE)
        if m:
            specs["boost_clock_ghz"] = float(m.group(1))

    # GPU: VRAM容量・VRAM規格
    if part_type == "gpu":
        m = re.search(r"(?:グラフィックス)?メモリ容量[：:]\s*(\d+)\s*GB", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(\d+)\s*GB\s+(?:GDDR|HBM)", text, re.IGNORECASE)
        if m:
            specs["vram_gb"] = int(m.group(1))
        m = re.search(r"(GDDR\d+X?|HBM\d*)", text, re.IGNORECASE)
        if m:
            specs["vram_type"] = m.group(1).upper()

    # メモリ: 規格・容量・動作周波数
    if part_type == "memory":
        m = re.search(r"規格[：:]\s*(DDR\d)", text, re.IGNORECASE)
        if m:
            specs["memory_type"] = m.group(1).upper()
        m = re.search(r"メモリ容量[：:]\s*(\d+)\s*GB", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(\d+)\s*GB\b", text)
        if m:
            specs["capacity_gb"] = int(m.group(1))
        m = re.search(r"DDR\d-(\d{4,5})", text, re.IGNORECASE)
        if m:
            specs["speed_mhz"] = int(m.group(1))
        else:
            m = re.search(r"(?:クロック|動作周波数)[：:]\s*(\d{4,5})\s*MHz", text, re.IGNORECASE)
            if m:
                specs["speed_mhz"] = int(m.group(1))

    # 対応メモリ規格・チップセット: マザーボード
    if part_type == "motherboard":
        m = re.search(r"対応メモリ[：:]\s*(DDR\d)", text, re.IGNORECASE)
        if m:
            specs["memory_type"] = m.group(1).upper()
        # チップセット (例: "チップセット：B650" / "Intel B760" など)
        m = re.search(
            r"チップセット[：:\s]\s*([A-Z]\d{2,4}[A-Z0-9]*)",
            text,
            re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r"\b(H610|H670|B550|B650|B650E|B760|X570|X670|X670E|Z690|Z790|Z890|W790|TRX50|WRX90)\b",
                text,
                re.IGNORECASE,
            )
        if m:
            specs["chipset"] = m.group(1).upper()

        # M.2 スロット数
        m2_values = []
        for pattern in (
            r"M\.2[^\n]{0,24}?(?:x|×)\s*(\d+)",
            r"M\.2[^\n]{0,24}?(\d+)\s*(?:基|本|スロット)",
            r"M\.2\s*Socket\s*\d+[^\n]{0,16}?(?:x|×)\s*(\d+)",
        ):
            m2_values.extend(re.findall(pattern, text, re.IGNORECASE))
        if m2_values:
            specs["m2_slots"] = max(int(v) for v in m2_values)

        # PCIe スロット数
        pcie_x16_values = re.findall(r"PCI(?:e|\s*Express)[^\n]{0,20}?x16[^\n]{0,10}?(?:x|×|：|:)\s*(\d+)", text, re.IGNORECASE)
        if pcie_x16_values:
            specs["pcie_x16_slots"] = max(int(v) for v in pcie_x16_values)

        pcie_total = 0
        for lane in ("16", "8", "4", "1"):
            lane_values = re.findall(
                rf"PCI(?:e|\s*Express)[^\n]{{0,24}}?x{lane}[^\n]{{0,10}}?(?:x|×|：|:)\s*(\d+)",
                text,
                re.IGNORECASE,
            )
            for value in lane_values:
                try:
                    pcie_total += int(value)
                except (TypeError, ValueError):
                    continue
        if pcie_total > 0:
            specs["pcie_slots"] = pcie_total

        # USB ポート数（Type-C を別カウント）
        usb_total = 0
        for value in re.findall(r"USB[^\n]{0,32}?(?:x|×|：|:)\s*(\d+)", text, re.IGNORECASE):
            try:
                usb_total += int(value)
            except (TypeError, ValueError):
                continue
        if usb_total > 0:
            specs["usb_total"] = min(usb_total, 40)

        type_c_total = 0
        for value in re.findall(r"(?:Type\s*[- ]?C|USB\s*[- ]?C)[^\n]{0,18}?(?:x|×|：|:)\s*(\d+)", text, re.IGNORECASE):
            try:
                type_c_total += int(value)
            except (TypeError, ValueError):
                continue
        if type_c_total > 0:
            specs["type_c_ports"] = min(type_c_total, 10)

        _apply_motherboard_expandability_fallback(specs, text)

    # ストレージ: 容量・インターフェース・フォームファクタ
    if part_type == "storage":
        m = re.search(r"容量[：:]\s*(\d+(?:\.\d+)?)\s*(TB|GB)", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(\d+(?:\.\d+)?)\s*(TB|GB)\b", text)
        if m:
            val, unit = float(m.group(1)), m.group(2).upper()
            specs["capacity_gb"] = int(val * 1024) if unit == "TB" else int(val)
        if re.search(r"NVMe", text, re.IGNORECASE):
            specs["interface"] = "NVMe"
        elif re.search(r"SATA", text, re.IGNORECASE):
            specs["interface"] = "SATA"
        if re.search(r"M\.2", text, re.IGNORECASE):
            specs["form_factor"] = "M.2"
        elif re.search(r"2\.5\s*(?:インチ|inch)", text, re.IGNORECASE):
            specs["form_factor"] = "2.5inch"
        elif re.search(r"3\.5\s*(?:インチ|inch)", text, re.IGNORECASE):
            specs["form_factor"] = "3.5inch"

    # PSU: 出力ワット数・80PLUS認証ランク
    if part_type == "psu":
        m = re.search(r"統合出力[：:]\s*(\d+)W", text)
        if m:
            specs["wattage"] = int(m.group(1))
        m = re.search(r"80\s*PLUS\s*(Bronze|Silver|Gold|Platinum|Titanium)", text, re.IGNORECASE)
        if m:
            specs["efficiency_grade"] = m.group(1).capitalize()

    # フォームファクタ: マザーボード・ケース
    if part_type in ("motherboard", "case"):
        m = re.search(r"フォームファクタ[：:]\s*([^●<\n]+?)(?:●|<|$)", text)
        if m:
            specs["form_factor"] = m.group(1).strip()

    # ケース: ラジエーター対応サイズ
    if part_type == "case":
        size_tokens = {
            int(token)
            for token in re.findall(r"(?:^|[^\d])(120|140|240|280|360|420)\s*mm", text, re.IGNORECASE)
        }

        # 「最大ラジエーター 360mm」「ラジエーターサイズ: 120/240/360mm」などを拾う
        max_hits = re.findall(
            r"(?:最大[^\n]{0,12}ラジエーター|ラジエーター最大|ラジエーター[^\n]{0,10}最大)[^\d]{0,8}(120|140|240|280|360|420)\s*mm",
            text,
            re.IGNORECASE,
        )
        if max_hits:
            specs["max_radiator_mm"] = max(int(v) for v in max_hits)

        if size_tokens:
            sorted_sizes = sorted(size_tokens)
            specs["radiator_sizes"] = sorted_sizes
            specs["supported_radiators"] = sorted_sizes
            if "max_radiator_mm" not in specs:
                specs["max_radiator_mm"] = max(sorted_sizes)

        # ケース: 付属ファン数
        if re.search(r"(?:付属|標準(?:搭載)?)[^\n]{0,12}ファン[^\n]{0,8}(?:なし|非搭載|無し)", text, re.IGNORECASE):
            specs["included_fan_count"] = 0
        else:
            included_matches = re.findall(
                r"(?:付属|標準(?:搭載)?)[^\n]{0,16}?(?:ファン|cooling fan)[^\d\n]{0,8}(\d+)\s*(?:基|個|pcs?|x)?",
                text,
                re.IGNORECASE,
            )
            if included_matches:
                specs["included_fan_count"] = max(int(v) for v in included_matches)

        # ケース: 搭載可能ファン総数
        supported_count = 0
        for size, count in re.findall(r"(120|140|200)\s*mm\s*[x×]\s*(\d+)", text, re.IGNORECASE):
            try:
                supported_count += int(count)
            except (TypeError, ValueError):
                continue
        if supported_count > 0:
            specs["supported_fan_count"] = supported_count
        else:
            m = re.search(r"(?:最大|搭載可能)[^\n]{0,16}(\d+)\s*(?:基|個)", text, re.IGNORECASE)
            if m:
                specs["supported_fan_count"] = int(m.group(1))

        # ケース: 前面/上面/背面の搭載可能ファン数
        position_slots = _extract_case_position_fan_slots(text)
        if position_slots:
            specs.update(position_slots)
            if "supported_fan_count" not in specs:
                specs["supported_fan_count"] = sum(position_slots.values())

    return specs


def _apply_motherboard_expandability_fallback(specs: Dict, text: str) -> Dict:
    """取得できる情報が乏しいマザーボード向けに、拡張性キーの下限値を補完する。"""
    raw = (text or "").lower()
    form_factor = str(specs.get("form_factor", "") or "").lower()
    chipset = str(specs.get("chipset", "") or "").lower()

    if not form_factor:
        if any(k in raw for k in ("e-atx", "eatx", "extended atx")):
            form_factor = "eatx"
        elif any(k in raw for k in ("mini-itx", "mini itx", "mitx")):
            form_factor = "mini-itx"
        elif any(k in raw for k in ("micro-atx", "micro atx", "microatx", "m-atx", "matx")):
            form_factor = "micro-atx"
        elif "atx" in raw:
            form_factor = "atx"

    base = {
        "usb_total": 6,
        "pcie_slots": 2,
        "pcie_x16_slots": 1,
        "m2_slots": 1,
        "type_c_ports": 0,
    }
    if form_factor in ("eatx", "e-atx"):
        base.update({"usb_total": 10, "pcie_slots": 5, "pcie_x16_slots": 2, "m2_slots": 3, "type_c_ports": 1})
    elif form_factor == "atx":
        base.update({"usb_total": 8, "pcie_slots": 4, "pcie_x16_slots": 1, "m2_slots": 2, "type_c_ports": 1})
    elif form_factor in ("micro-atx", "microatx"):
        base.update({"usb_total": 7, "pcie_slots": 3, "pcie_x16_slots": 1, "m2_slots": 2, "type_c_ports": 1})
    elif form_factor in ("mini-itx", "mini itx"):
        base.update({"usb_total": 6, "pcie_slots": 1, "pcie_x16_slots": 1, "m2_slots": 1, "type_c_ports": 1})

    high_end_chipsets = ("x870e", "x870", "x670e", "x670", "z890", "z790", "z690", "w790")
    if any(token in chipset for token in high_end_chipsets) or any(token in raw for token in high_end_chipsets):
        base["m2_slots"] = max(base["m2_slots"], 3)
        base["usb_total"] = max(base["usb_total"], 8)
        base["type_c_ports"] = max(base["type_c_ports"], 1)

    for key, value in base.items():
        current = specs.get(key)
        if current in (None, "", 0):
            specs[key] = value
    return specs


def _extract_case_position_fan_slots(text: str) -> Dict:
    """ケース説明文から前面/上面/背面の搭載可能ファン数を抽出する。"""
    extracted: Dict = {}
    if not text:
        return extracted

    source = text.lower()
    position_aliases = {
        "front": ["前面", "フロント", "front"],
        "top": ["上面", "トップ", "top"],
        "rear": ["背面", "リア", "rear"],
    }

    for position, aliases in position_aliases.items():
        values = []
        for alias in aliases:
            escaped = re.escape(alias)
            values.extend(
                re.findall(
                    rf"{escaped}[^\n]{{0,24}}?(?:120|140|200)\s*mm\s*[x×]\s*(\d+)",
                    source,
                    re.IGNORECASE,
                )
            )
            values.extend(
                re.findall(
                    rf"{escaped}[^\n]{{0,24}}?(?:最大|搭載可能)?[^\d\n]{{0,8}}(\d+)\s*(?:基|個)",
                    source,
                    re.IGNORECASE,
                )
            )
        if values:
            extracted[f"{position}_fan_slots"] = max(int(v) for v in values)

    return extracted


def _extract_case_fan_specs_from_product_page(
    product_url: str,
    headers: Dict[str, str],
    timeout: int,
    session: Optional[requests.Session],
) -> Dict:
    """ケース商品ページ本文から、付属ファン数と搭載可能ファン数を抽出する。"""
    extracted: Dict = {}
    if not product_url:
        return extracted

    client = session or requests.Session()
    response = client.get(product_url, headers=headers, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    text = " ".join(soup.stripped_strings)
    lower_text = text.lower()

    if re.search(r"(?:付属|標準(?:搭載)?|搭載)[^\n]{0,16}ファン[^\n]{0,8}(?:なし|非搭載|無し)", lower_text, re.IGNORECASE):
        extracted["included_fan_count"] = 0
    else:
        included_candidates = []
        included_candidates.extend(
            re.findall(
                r"(?:標準(?:搭載)?|付属|搭載)[^\n]{0,20}(\d+)\s*(?:基|個)",
                lower_text,
                re.IGNORECASE,
            )
        )
        included_candidates.extend(
            re.findall(
                r"(?:標準(?:搭載)?|付属|搭載)[^\n]{0,20}(?:120|140|200)\s*mm\s*[x×]\s*(\d+)",
                lower_text,
                re.IGNORECASE,
            )
        )
        if included_candidates:
            extracted["included_fan_count"] = max(int(v) for v in included_candidates)

    supported_count = 0
    for _, count in re.findall(r"(120|140|200)\s*mm\s*[x×]\s*(\d+)", lower_text, re.IGNORECASE):
        try:
            supported_count += int(count)
        except (TypeError, ValueError):
            continue
    if supported_count > 0:
        extracted["supported_fan_count"] = supported_count
    else:
        m = re.search(r"(?:搭載可能|最大)[^\n]{0,18}(\d+)\s*(?:基|個)", lower_text, re.IGNORECASE)
        if m:
            extracted["supported_fan_count"] = int(m.group(1))

    position_slots = _extract_case_position_fan_slots(lower_text)
    if position_slots:
        extracted.update(position_slots)
        if "supported_fan_count" not in extracted:
            extracted["supported_fan_count"] = sum(position_slots.values())

    return extracted


def _extract_motherboard_expandability_from_product_page(
    product_url: str,
    headers: Dict[str, str],
    timeout: int,
    session: Optional[requests.Session],
) -> Dict:
    """マザーボード商品ページ本文から USB/PCIe/M.2 の本数ヒントを抽出する。"""
    extracted: Dict = {}
    if not product_url:
        return extracted

    client = session or requests.Session()
    response = client.get(product_url, headers=headers, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    text = " ".join(soup.stripped_strings)

    m2_values = []
    for pattern in (
        r"M\.2[^\n]{0,24}?(?:x|×)\s*(\d+)",
        r"M\.2[^\n]{0,24}?(\d+)\s*(?:基|本|スロット)",
    ):
        m2_values.extend(re.findall(pattern, text, re.IGNORECASE))
    if m2_values:
        extracted["m2_slots"] = max(int(v) for v in m2_values)

    pcie_x16_values = re.findall(r"PCI(?:e|\s*Express)[^\n]{0,20}?x16[^\n]{0,10}?(?:x|×|：|:)\s*(\d+)", text, re.IGNORECASE)
    if pcie_x16_values:
        extracted["pcie_x16_slots"] = max(int(v) for v in pcie_x16_values)

    pcie_total = 0
    for lane in ("16", "8", "4", "1"):
        lane_values = re.findall(
            rf"PCI(?:e|\s*Express)[^\n]{{0,24}}?x{lane}[^\n]{{0,10}}?(?:x|×|：|:)\s*(\d+)",
            text,
            re.IGNORECASE,
        )
        for value in lane_values:
            try:
                pcie_total += int(value)
            except (TypeError, ValueError):
                continue
    if pcie_total > 0:
        extracted["pcie_slots"] = pcie_total

    usb_total = 0
    for value in re.findall(r"USB[^\n]{0,32}?(?:x|×|：|:)\s*(\d+)", text, re.IGNORECASE):
        try:
            usb_total += int(value)
        except (TypeError, ValueError):
            continue
    if usb_total > 0:
        extracted["usb_total"] = min(usb_total, 40)

    type_c_total = 0
    for value in re.findall(r"(?:Type\s*[- ]?C|USB\s*[- ]?C)[^\n]{0,18}?(?:x|×|：|:)\s*(\d+)", text, re.IGNORECASE):
        try:
            type_c_total += int(value)
        except (TypeError, ValueError):
            continue
    if type_c_total > 0:
        extracted["type_c_ports"] = min(type_c_total, 10)

    _apply_motherboard_expandability_fallback(extracted, text)

    return extracted


def _build_parts_from_products_map(
    products_map: Dict[str, Dict],
    base_url: str,
    max_items: int,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> List[Dict]:
    collected: List[Dict] = []
    page_fan_specs_cache: Dict[str, Dict] = {}
    motherboard_specs_cache: Dict[str, Dict] = {}
    for code, info in products_map.items():
        name = (info.get("pname") or "").strip()
        price = _normalize_price(str(info.get("amttax") or ""))
        relative_url = (info.get("url") or "").strip()
        full_url = urljoin(base_url, relative_url) if relative_url else base_url

        if not name or price is None:
            continue

        part_type = _infer_part_type(name, full_url)
        if not part_type:
            continue

        simplespec = (info.get("simplespec") or "").strip()
        extracted = _extract_specs_from_simplespec(part_type, simplespec)

        if part_type == "case" and (
            "included_fan_count" not in extracted and "supported_fan_count" not in extracted
        ):
            if full_url in page_fan_specs_cache:
                extracted.update(page_fan_specs_cache[full_url])
            elif headers:
                try:
                    page_specs = _extract_case_fan_specs_from_product_page(
                        product_url=full_url,
                        headers=headers,
                        timeout=timeout,
                        session=session,
                    )
                except Exception:
                    page_specs = {}
                page_fan_specs_cache[full_url] = page_specs
                extracted.update(page_specs)

        if part_type == "motherboard" and headers and (
            "usb_total" not in extracted
            or "pcie_slots" not in extracted
            or "m2_slots" not in extracted
        ):
            if full_url in motherboard_specs_cache:
                extracted.update(motherboard_specs_cache[full_url])
            else:
                try:
                    mb_specs = _extract_motherboard_expandability_from_product_page(
                        product_url=full_url,
                        headers=headers,
                        timeout=timeout,
                        session=session,
                    )
                except Exception:
                    mb_specs = {}
                motherboard_specs_cache[full_url] = mb_specs
                extracted.update(mb_specs)

        part_specs = {
            "source": "dospara",
            "parser": "products_api",
            "code": code,
        }
        raw_stock_text = (info.get("stkname") or "").strip()
        if raw_stock_text:
            part_specs["stock_text"] = raw_stock_text
        normalized_stock_status = _normalize_stock_status(raw_stock_text)
        part_specs["stock_status"] = normalized_stock_status
        part_specs.update(extracted)

        collected.append(
            {
                "part_type": part_type,
                "name": name,
                "price": price,
                "url": full_url,
                "specs": part_specs,
                "stock_status": normalized_stock_status,
                "is_active": normalized_stock_status != 'out_of_stock',
            }
        )

        if len(collected) >= max_items:
            break

    return collected


def _extract_first_text(root, selectors: List[str]) -> str:
    for selector in selectors:
        node = root.select_one(selector)
        if node:
            text = node.get("title") if node.has_attr("title") else node.get_text(" ", strip=True)
            if text:
                return text.strip()
    return ""


def _extract_first_url(root, selectors: List[str], base_url: str) -> str:
    for selector in selectors:
        node = root.select_one(selector)
        if node and node.get("href"):
            return urljoin(base_url, node.get("href").strip())
    return ""


def _extract_price(root, selectors: List[str]) -> Optional[int]:
    for selector in selectors:
        node = root.select_one(selector)
        if not node:
            continue

        data_price = node.get("data-price")
        if data_price:
            normalized = _normalize_price(data_price)
            if normalized is not None:
                return normalized

        text = node.get_text(" ", strip=True)
        for pattern in PRICE_PATTERNS:
            match = pattern.search(text)
            if match:
                normalized = _normalize_price(match.group(1))
                if normalized is not None:
                    return normalized

    blob_text = " ".join(root.stripped_strings)
    for pattern in PRICE_PATTERNS:
        match = pattern.search(blob_text)
        if match:
            normalized = _normalize_price(match.group(1))
            if normalized is not None:
                return normalized

    return None


def _iter_item_roots(soup: BeautifulSoup, selectors: Dict[str, List[str]]):
    seen_nodes = set()
    for selector in selectors.get("item_roots", []):
        for node in soup.select(selector):
            identity = id(node)
            if identity in seen_nodes:
                continue
            seen_nodes.add(identity)
            yield node

    # 設定セレクタで要素が取れないDOMにも対応するフォールバック。
    if not seen_nodes:
        for anchor in soup.select("a[href]"):
            identity = id(anchor)
            if identity in seen_nodes:
                continue
            seen_nodes.add(identity)
            yield anchor


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _extract_with_regex_fallback(html: str, base_url: str, max_items: int, seen: set) -> List[Dict]:
    collected: List[Dict] = []

    for match in PRODUCT_LINK_PATTERN.finditer(html):
        href = (match.group("href") or "").strip()
        name = _strip_tags(match.group("name") or "")
        full_url = urljoin(base_url, href)

        if "dospara.co.jp" not in full_url:
            continue
        if not name or len(name) < 3:
            continue

        window = html[match.end(): match.end() + 400]
        price = None
        for pattern in PRICE_PATTERNS:
            price_match = pattern.search(window)
            if price_match:
                price = _normalize_price(price_match.group(1))
                break
        if price is None:
            continue

        part_type = _infer_part_type(name, full_url)
        if not part_type:
            continue

        key = (part_type, name)
        if key in seen:
            continue
        seen.add(key)

        collected.append(
            {
                "part_type": part_type,
                "name": name,
                "price": price,
                "url": full_url,
                "specs": {"source": "dospara", "parser": "regex_fallback"},
            }
        )

        if len(collected) >= max_items:
            break

    return collected


def parse_dospara_parts_html(
    html: str,
    base_url: str = DOSPARA_PARTS_URL,
    max_items: int = 200,
    selectors: Optional[Dict[str, List[str]]] = None,
) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = selectors or SCRAPER_SELECTORS

    collected: List[Dict] = []
    seen = set()

    for root in _iter_item_roots(soup, selectors):
        full_url = _extract_first_url(root, selectors.get("link", ["a[href]"]), base_url)
        if "dospara.co.jp" not in full_url:
            continue

        name = _extract_first_text(root, selectors.get("name", ["a[href]"]))
        if not name or len(name) < 3:
            continue

        price = _extract_price(root, selectors.get("price", []))
        if price is None:
            continue

        part_type = _infer_part_type(name, full_url)
        if not part_type:
            continue

        key = (part_type, name)
        if key in seen:
            continue
        seen.add(key)

        collected.append(
            {
                "part_type": part_type,
                "name": name,
                "price": price,
                "url": full_url,
                "specs": {"source": "dospara"},
            }
        )

        if len(collected) >= max_items:
            break

    if not collected:
        collected.extend(_extract_with_regex_fallback(html, base_url, max_items, seen))

    return collected


def scrape_dospara_category_parts(
    timeout: int = 20,
    max_items_per_category: int = 80,
    session: Optional[requests.Session] = None,
) -> List[Dict]:
    """各パーツカテゴリページをスクレイピングしてパーツ一覧を返す。"""
    client = session or requests.Session()
    config = get_dospara_scraper_config()
    all_parts: List[Dict] = []
    seen: set = set()

    for part_type, urls in PART_CATEGORY_URLS.items():
        category_count = 0
        for url in urls:
            if category_count >= max_items_per_category:
                break
            try:
                resp = client.get(url, headers=config["headers"], timeout=timeout)
                resp.raise_for_status()
                codes = _collect_ic_codes_from_category_pages(
                    html=resp.text,
                    category_url=url,
                    headers=config["headers"],
                    timeout=timeout,
                    session=client,
                    max_codes=max_items_per_category * 10,
                )
                products_map = _fetch_products_by_codes(
                    codes=codes,
                    api_url=config["products_api_url"],
                    headers=config["headers"],
                    timeout=timeout,
                    batch_size=config["batch_size"],
                    session=client,
                )
                parts = _build_parts_from_products_map(
                    products_map,
                    url,
                    max_items_per_category,
                    headers=config["headers"],
                    timeout=timeout,
                    session=client,
                )
                for part in parts:
                    if part["part_type"] != part_type:
                        continue
                    key = (part["part_type"], part["name"])
                    if key in seen:
                        continue
                    seen.add(key)
                    all_parts.append(part)
                    category_count += 1
                    if category_count >= max_items_per_category:
                        break
            except Exception as e:
                logger.warning("category_scrape_failed part_type=%s url=%s error=%s", part_type, url, e)

    return all_parts


def scrape_dospara_parts(timeout: int = 20, max_items: int = 200, session: Optional[requests.Session] = None) -> List[Dict]:
    client = session or requests.Session()
    config = get_dospara_scraper_config()
    effective_timeout = timeout if timeout != SCRAPER_DEFAULT_CONFIG["timeout"] else config["timeout"]
    effective_max_items = max_items if max_items != SCRAPER_DEFAULT_CONFIG["max_items"] else config["max_items"]

    response = client.get(config["url"], headers=config["headers"], timeout=effective_timeout)
    response.raise_for_status()
    html = response.text

    codes = _extract_ic_codes(html, max_codes=effective_max_items * 10)
    products_map = _fetch_products_by_codes(
        codes=codes,
        api_url=config["products_api_url"],
        headers=config["headers"],
        timeout=effective_timeout,
        batch_size=config.get("batch_size", SCRAPER_DEFAULT_CONFIG["batch_size"]),
        session=client,
    )
    api_parts = _build_parts_from_products_map(
        products_map,
        config["url"],
        effective_max_items,
        headers=config["headers"],
        timeout=effective_timeout,
        session=client,
    )
    if api_parts:
        return api_parts

    return parse_dospara_parts_html(
        html,
        base_url=config["url"],
        max_items=effective_max_items,
        selectors=config["selectors"],
    )
