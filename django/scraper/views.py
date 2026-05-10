import re
import random
import time
from collections import defaultdict
from pathlib import Path

from rest_framework import viewsets, status
from django.conf import settings
from django.db import transaction
from django.db.models import Min, Max, Avg, Count as DbCount
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Configuration, CPUSelectionEntry, CPUSelectionSnapshot, GPUPerformanceEntry, GPUPerformanceSnapshot, MarketPriceRangeSnapshot, PCPart, ScraperStatus
from .serializers import PCPartSerializer, ConfigurationSerializer, ScraperStatusSerializer
from .dospara_scraper import fetch_dospara_cpu_selection_material


_GPU_PERF_CACHE = {
    'snapshot_key': None,
    'scores': {},
    'loaded_at': 0.0,
}

_CPU_SELECTION_CACHE = {
    'loaded_at': 0.0,
    'scores': {},
    'entries': [],
}


PART_ORDER = ['cpu', 'cpu_cooler', 'gpu', 'motherboard', 'memory', 'storage', 'os', 'psu', 'case']
CANONICAL_USAGE_CODES = frozenset({'gaming', 'general', 'creator', 'business', 'workstation'})
USAGE_COMPAT_ALIASES = {
    'video_editing': 'creator',
    'create': 'creator',
    'game': 'gaming',
    'ai': 'workstation',
    'standard': 'general',
}


def _normalize_usage_code(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    normalized = USAGE_COMPAT_ALIASES.get(normalized, normalized)
    if normalized in CANONICAL_USAGE_CODES:
        return normalized
    return None


USAGE_POWER_MAP = {
    'gaming': 550,       # ゲーム: GPU高負荷
    'creator': 500,      # クリエイト: CPU+GPU高負荷
    'workstation': 650,  # ワークステーション: GPU高負荷 + 連続実行
    'general': 300,      # 汎用: 省電力寄り
    'business': 350,     # ビジネス: 省電力
    'standard': 400,     # 旧互換
    'ai': 650,           # 旧互換
}

USAGE_BUDGET_WEIGHTS = {
    # ゲーミング: GPU最重視
    'gaming': {
        'cpu': 0.18,
        'cpu_cooler': 0.06,
        'gpu': 0.48,
        'motherboard': 0.09,
        'memory': 0.08,
        'storage': 0.05,
        'os': 0.04,
        'psu': 0.05,
        'case': 0.00,
    },
    # クリエイター: マザボ > メモリ > GPU をより明確化
    'creator': {
        'cpu': 0.17,
        'cpu_cooler': 0.08,
        'gpu': 0.08,
        'motherboard': 0.24,
        'memory': 0.20,
        'storage': 0.10,
        'os': 0.05,
        'psu': 0.06,
        'case': 0.02,
    },
    # ワークステーション: VRAM/メモリ/電源・冷却を重視
    'workstation': {
        'cpu': 0.16,
        'cpu_cooler': 0.09,
        'gpu': 0.32,
        'motherboard': 0.11,
        'memory': 0.14,
        'storage': 0.08,
        'os': 0.04,
        'psu': 0.05,
        'case': 0.01,
    },
    'ai': {
        'cpu': 0.16,
        'cpu_cooler': 0.09,
        'gpu': 0.32,
        'motherboard': 0.11,
        'memory': 0.14,
        'storage': 0.08,
        'os': 0.04,
        'psu': 0.05,
        'case': 0.01,
    },
    # 汎用: コスト効率重視
    'general': {
        'cpu': 0.20,
        'cpu_cooler': 0.04,
        'gpu': 0.08,
        'motherboard': 0.14,
        'memory': 0.13,
        'storage': 0.11,
        'os': 0.06,
        'psu': 0.10,
        'case': 0.14,
    },
    # ビジネス: CPU中程度、GPU控えめ、信頼性重視
    'business': {
        'cpu': 0.24,
        'cpu_cooler': 0.03,
        'gpu': 0.08,
        'motherboard': 0.15,
        'memory': 0.18,
        'storage': 0.14,
        'os': 0.06,
        'psu': 0.08,
        'case': 0.04,
    },
    # スタンダード: バランス型
    'standard': {
        'cpu': 0.20,
        'cpu_cooler': 0.04,
        'gpu': 0.16,
        'motherboard': 0.14,
        'memory': 0.13,
        'storage': 0.11,
        'os': 0.04,
        'psu': 0.10,
        'case': 0.08,
    },
}

# 高予算帯のクリエイター用途では、GPUを上位帯から選定して
# "フラッグシップ予算なのに中位GPU" になりにくくする。
CREATOR_FLAGSHIP_BUDGET_THRESHOLD = 900000
CREATOR_FLAGSHIP_GPU_BUDGET_CAP = 0.75
CREATOR_GPU_BUDGET_CAP_BY_PRIORITY = {
    'cost': 0.55,    # Radeon R9700 (259,800) を包含
    'spec': 0.75,    # NVIDIA RTX PRO 4500 (506,000) を包含
    'balanced': 0.14,
}
CREATOR_MOTHERBOARD_FLOOR_BY_PRIORITY = {
    'cost': 0.12,
    'spec': 0.15,
    'balanced': 0.13,
}

BUDGET_TIER_THRESHOLDS = {
    'low': 220000,
    'middle': 300000,
    'high': 500000,
}

# パソコン工房の用途別スペック検索ページ実測値をそのまま tier 境界へ反映。
# 各用途で low=25percentile / middle=median / high=75percentile とする。
BUDGET_TIER_THRESHOLDS_BY_USAGE = {
    'gaming': {'low': 129800, 'middle': 134800, 'high': 139800},
    # general/standard は frontend プリセット（ロー174,980 / ミドル224,980 / ハイ364,980）に合わせる。
    'desktop': {'low': 174980, 'middle': 224980, 'high': 364980},
    'creator': {'low': 134800, 'middle': 159800, 'high': 164800},
    # workstation: frontend プリセット送信値（cost: 379980/464980/624980）より少し上に境界を置く。
    # フロントの「ミドル」プリセット 479,980 - 15,000 = 464,980 が middle に分類されるよう 465,000 とする。
    'workstation': {'low': 380000, 'middle': 465000, 'high': 625000},
    'business': {'low': 109800, 'middle': 120700, 'high': 124800},
}

MARKET_TIER_FALLBACK_MIN = 179980
MARKET_TIER_FALLBACK_MAX = 1309980
GAMING_PREMIUM_BUDGET_MIN = 1027481

PART_PRICE_BANDS_BY_USAGE_TIER = {
    'motherboard': {
        'gaming': {
            'low': (0.03, 0.12),
            'middle': (0.04, 0.15),
            'high': (0.06, 0.18),
            'premium': (0.10, 0.28),
        },
        'creator': {
            'low': (0.08, 0.16),
            'middle': (0.10, 0.20),
            'high': (0.12, 0.24),
            'premium': (0.14, 0.28),
        },
        'ai': {
            'low': (0.08, 0.16),
            'middle': (0.10, 0.20),
            'high': (0.12, 0.24),
            'premium': (0.14, 0.28),
        },
        'general': {
            'low': (0.03, 0.12),
            'middle': (0.04, 0.14),
            'high': (0.05, 0.16),
            'premium': (0.08, 0.18),
        },
        'business': {
            'low': (0.03, 0.12),
            'middle': (0.04, 0.14),
            'high': (0.05, 0.16),
            'premium': (0.08, 0.18),
        },
        'standard': {
            'low': (0.03, 0.12),
            'middle': (0.04, 0.14),
            'high': (0.05, 0.16),
            'premium': (0.08, 0.18),
        },
    },
    'case': {
        'gaming': {
            'low': (0.02, 0.06),
            'middle': (0.03, 0.08),
            'high': (0.04, 0.10),
            'premium': (0.05, 0.12),
        },
        'creator': {
            'low': (0.03, 0.08),
            'middle': (0.04, 0.10),
            'high': (0.05, 0.12),
            'premium': (0.06, 0.14),
        },
        'ai': {
            'low': (0.03, 0.08),
            'middle': (0.04, 0.10),
            'high': (0.05, 0.12),
            'premium': (0.06, 0.14),
        },
        'general': {
            'low': (0.02, 0.06),
            'middle': (0.03, 0.07),
            'high': (0.03, 0.08),
            'premium': (0.04, 0.09),
        },
        'business': {
            'low': (0.02, 0.06),
            'middle': (0.03, 0.07),
            'high': (0.03, 0.08),
            'premium': (0.04, 0.09),
        },
        'standard': {
            'low': (0.03, 0.08),
            'middle': (0.04, 0.10),
            'high': (0.05, 0.12),
            'premium': (0.06, 0.14),
        },
    },
}

CATEGORY_DROP_PRIORITY = ['case', 'storage', 'memory', 'cpu_cooler', 'motherboard', 'psu', 'gpu', 'cpu']

UPGRADE_PRIORITY_BY_USAGE = {
    'gaming':      ['gpu', 'cpu', 'cpu_cooler', 'memory', 'storage', 'motherboard', 'psu', 'case'],
    'creator':     ['cpu', 'motherboard', 'memory', 'gpu', 'storage', 'cpu_cooler', 'psu', 'case'],
    'workstation': ['gpu', 'memory', 'cpu', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
    'general':     ['cpu', 'memory', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
    'business':    ['cpu', 'memory', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
    'standard':    ['cpu', 'memory', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
    'ai':          ['gpu', 'memory', 'cpu', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
}

# 内蔵GPU(iGPU)使用: ビジネス・スタンダードはdGPU不要
IGPU_USAGES = frozenset({'general', 'business', 'standard'})

# standard/business + spec重視 + 予算がこの値以上の場合はdGPUを許可する
SPEC_GPU_UNLOCK_BUDGET_THRESHOLD = 160000

# GPUウェイト分を他パーツへ再分配した予算配分
IGPU_BUDGET_WEIGHTS = {
    'general': {
        'cpu': 0.24,
        'cpu_cooler': 0.06,
        'motherboard': 0.18,
        'memory': 0.18,
        'storage': 0.12,
        'os': 0.06,
        'psu': 0.10,
        'case': 0.08,
    },
    'business': {
        'cpu': 0.25,
        'cpu_cooler': 0.05,
        'motherboard': 0.17,
        'memory': 0.20,
        'storage': 0.17,
        'os': 0.08,
        'psu': 0.08,
        'case': 0.04,
    },
    'standard': {
        'cpu': 0.24,
        'cpu_cooler': 0.06,
        'motherboard': 0.18,
        'memory': 0.18,
        'storage': 0.12,
        'os': 0.06,
        'psu': 0.10,
        'case': 0.08,
    },
}

IGPU_POWER_MAP = {
    'general': 300,
    'business': 250,
    'standard': 300,
}

# 汎用(general/standard)+spec 構成: コスト重視ベースにGPUと電源強化のみ付加
# ティアごとのGPU目標価格（在庫の最近傍候補が選ばれる）
GENERAL_SPEC_GPU_TARGET_BY_TIER = {
    'low':     36760,   # RTX 3050 相当
    'middle':  65000,   # RTX 5060 相当
    'high':    88800,   # RTX 5060 Ti / RX 9060 XT 相当
    'premium': 112500,  # RTX 5070 相当
}

UNSUITABLE_KEYWORDS = {
    'cpu': ['グリス', 'cooler', 'クーラー', 'fan', 'ファン'],
    'memory': ['sodimm', 'ノート'],
    'storage': ['microatx', 'mini-itx', 'am5', 'am4', 'lga1700', 'lga1851', 'motherboard', 'マザーボード'],
}

UNSUITABLE_URL_HINTS = {
    'cpu': ['/sbr131/', '/sbr95/'],
    'motherboard': ['/sbr1969/'],
}

COOLER_TYPE_KEYWORDS = {
    'liquid': ['水冷', 'aio', 'liquid', 'radiator', 'ラジエーター', '簡易水冷'],
    'air': ['空冷', 'air', 'tower', 'top flow', 'サイドフロー', 'トップフロー', 'nh-d', 'ak', 'assassin'],
}

COOLING_PROFILE_KEYWORDS = {
    'silent': ['静音', 'silent', 'low noise', 'noctua', 'be quiet'],
    'performance': ['high performance', 'extreme', 'oc', 'overclock', 'ハイパフォーマンス'],
}

CASE_FAN_POLICY_KEYWORDS = {
    'silent': ['静音', 'silent', 'low noise', 'be quiet', 'define', 'p12 pwm pst', 'f12 silent'],
    'airflow': ['airflow', 'mesh', 'high airflow', 'high static pressure', 'p14', '140mm', '200mm', 'front mesh'],
}

CASE_SIZE_KEYWORDS = {
    'mini': ['mini-itx', 'mini itx', 'itx', 'sff', '小型', 'コンパクト', 'mini tower'],
    'mid': ['mid tower', 'ミドルタワー', 'micro-atx', 'micro atx', 'matx', 'atx'],
    'full': ['full tower', 'フルタワー', 'e-atx', 'eatx', 'super tower'],
}

CPU_VENDOR_KEYWORDS = {
    'intel': ['intel', 'core i', 'core ultra', 'pentium', 'celeron'],
    'amd': ['amd', 'ryzen', 'athlon', 'epyc', 'threadripper'],
}

GAMING_SPEC_GPU_KEYWORDS = (
    'rtx',
    'radeon rx',
)

AMD_CPU_COST_RANKING_FILE = Path(__file__).resolve().parents[2] / 'AMDコスパ順.txt'
AMD_CPU_SPEC_RANKING_FILE = Path(__file__).resolve().parents[2] / 'AMDCPUスペック順.txt'
AMD_CPU_RANKING_CACHE = {
    'cost': None,
    'spec': None,
}

GAMING_GPU_TIER_RANKS = {
    'ローエンド': 1,
    'ミドル': 2,
    'ハイエンド': 3,
    'プレミアム': 4,
}

GAMING_CREATIVE_GPU_KEYWORDS = (
    'ai pro',
    'creator',
    'proart',
    'radeon pro',
    'rtx pro',
    'workstation',
    'professional',
    'nvbox',
)

GAMING_COST_CPU_PRICE_CAP = 50000
GAMING_SPEC_PREMIUM_SOFT_CAP_BUDGET = 700000

# gaming + cost: フラッグシップパーツを避けるため、高級パーツの上限を設定
GAMING_COST_FLAGSHIP_CPU_PREMIUM_PRICE_CAP = 75000  # 9850X3D や 9900X は除外
GAMING_COST_MOTHERBOARD_MAX_CHIPSET = 'x870'  # X870E は除外
GAMING_COST_MEMORY_MAX_SPEED_MHZ = 5600  # PC5-44800 以上は除外

GAMING_GPU_TIER_LABEL_RULES = (
    (
        'プレミアム',
        (
            r'rtx\s*4090\b',
            r'rtx\s*5090\b',
            r'rtx\s*5080\b',
            r'rx\s*9070\s*xt\b',
            r'rx9070xt\b',
        ),
    ),
    (
        'ハイエンド',
        (
            r'rtx\s*4070\s*ti\b',
            r'rtx\s*4080\s*super\b',
            r'rtx\s*4080\b',
            r'rtx\s*5070\s*ti\b',
            r'rtx\s*5070\b',
            r'rx\s*7900\b',
            r'rx\s*9070\b',
            r'rx9070\b',
        ),
    ),
    (
        'ミドル',
        (
            r'rtx\s*3060\s*ti\b',
            r'rtx\s*3070\s*ti\b',
            r'rtx\s*3070\b',
            r'rtx\s*3060\b',
            r'rtx\s*4060\s*ti\b',
            r'rtx\s*4060\b',
            r'rtx\s*4070\b',
            r'rtx\s*5060\s*ti\b',
            r'rtx\s*5060\b',
            r'rx\s*7700(?:\s*xt)?\b',
            r'rx7700(?:xt)?\b',
            r'rx\s*7800(?:\s*xt)?\b',
            r'rx7800(?:xt)?\b',
            r'rx\s*9060(?:\s*xt)?\b',
            r'rx9060(?:xt)?\b',
        ),
    ),
    (
        'ローエンド',
        (
            r'gtx\s*1650\b',
            r'gtx\s*1660\s*super\b',
            r'gtx1660super\b',
            r'gtx\s*1660\b',
            r'rtx\s*3050\b',
            r'rtx\s*5050\b',
            r'rx\s*6400\b',
            r'rx6400\b',
            r'rx\s*6600\b',
            r'rx6600\b',
            r'rx\s*7600\b',
            r'rx7600\b',
        ),
    ),
)

GAMING_CPU_X3D_PATTERN = re.compile(r'\b(?:ryzen\s*[3579]\s*)?\d{4,5}x3d\b', re.IGNORECASE)
GAMING_EXCLUDED_CREATOR_CPU_MODELS = frozenset({
    'RYZEN 5 7500F',
    'RYZEN 5 9500F',
    'RYZEN 7 8700G',
    'RYZEN 9 9900X',
    'RYZEN 9 9900X3D',
    'RYZEN 9 9950X',
    'RYZEN 9 9950X3D',
})
GAMING_SPEC_PRIORITY_CPU_IDS = frozenset({2604, 2603, 2554, 2555, 2547})
UNSTABLE_INTEL_CORE_I_PATTERN = re.compile(r'\bcore\s*i[3579]?[-\s]?(?:13|14)\d{3,4}[a-z]{0,3}\b', re.IGNORECASE)
PLACEHOLDER_URL_HINTS = ('example.com',)

RADIATOR_SIZE_VALUES = (120, 140, 240, 280, 360, 420)

# 一部ケースはAPIスペックにラジエーター情報がないため、
# 確認済みモデルのみ保守的に補助判定する。
CASE_RADIATOR_HINTS = {
    'the tower 250': {120, 140, 240, 280, 360},
    'tr100': {120, 140, 240, 280, 360},
    # BR72で流通が多いmini系モデル（段階追加）
    'the tower 100': {120, 140},
    'meshroom d': {120, 140, 240, 280},
    'h2 flow': {120, 140, 240},
    'ridge': {120, 140, 240},
    'mood': {120, 140, 240},
    'terra': {120},
    'core v1': {120, 140},
    'node 202': {120},
}

OUT_OF_STOCK_STATUS_VALUES = frozenset({
    'out_of_stock',
    'sold_out',
    'unavailable',
    'no_stock',
    'discontinued',
    'backorder',
    'preorder',
    '欠品',
    '在庫切れ',
    '在庫なし',
    '取扱終了',
    '販売終了',
    '入荷待ち',
})

OUT_OF_STOCK_TEXT_HINTS = (
    'out of stock',
    'sold out',
    'unavailable',
    'backorder',
    'pre-order',
    '在庫切れ',
    '在庫なし',
    '欠品',
    '取扱終了',
    '販売終了',
    '入荷待ち',
    '予約受付',
)


def _is_part_in_stock(part):
    if not getattr(part, 'is_active', True):
        return False

    specs = getattr(part, 'specs', {}) or {}
    status_candidates = [
        getattr(part, 'stock_status', ''),
        specs.get('stock_status', ''),
        specs.get('availability', ''),
        specs.get('inventory_status', ''),
    ]

    for candidate in status_candidates:
        normalized = str(candidate or '').strip().lower()
        if not normalized:
            continue
        if normalized in OUT_OF_STOCK_STATUS_VALUES:
            return False

    text = ' '.join([
        str(getattr(part, 'name', '') or ''),
        str(getattr(part, 'url', '') or ''),
        str(specs.get('stock_text', '') or ''),
        str(specs.get('availability_text', '') or ''),
    ]).lower()
    return not any(hint in text for hint in OUT_OF_STOCK_TEXT_HINTS)


def _is_part_suitable(part_type, part):
    if not _is_part_in_stock(part):
        return False

    text = f"{part.name} {part.url}".lower()
    for keyword in UNSUITABLE_KEYWORDS.get(part_type, []):
        if keyword in text:
            return False

    url = (part.url or '').lower()
    if any(hint in url for hint in PLACEHOLDER_URL_HINTS):
        # テストデータでは example.com を使うため、実URL候補が存在する場合のみ除外する。
        has_real_url_candidate = PCPart.objects.filter(part_type=part_type).exclude(url__icontains='example.com').exists()
        if has_real_url_candidate:
            return False
    for hint in UNSUITABLE_URL_HINTS.get(part_type, []):
        if hint in url:
            return False

    return True


def _normalize_cooler_type(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        alias = {
            '空冷': 'air',
            '水冷': 'liquid',
            '指定なし': 'any',
            'なし': 'any',
        }
        normalized = alias.get(normalized, normalized)
        if normalized in {'air', 'liquid'}:
            return normalized
    return 'any'


def _normalize_radiator_size(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        normalized = normalized.replace('mm', '').replace('ｍｍ', '').strip()
        if normalized in {'120', '240', '360'}:
            return normalized
    return 'any'


def _normalize_cooling_profile(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        alias = {
            '冷却重視': 'performance',
            '静音重視': 'silent',
            'バランス': 'balanced',
            '標準': 'balanced',
        }
        normalized = alias.get(normalized, normalized)
        if normalized in {'silent', 'performance'}:
            return normalized
    return 'balanced'


def _normalize_case_size(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        alias = {
            'mini': 'mini',
            'mid': 'mid',
            'full': 'full',
            '小型': 'mini',
            'ミニ': 'mini',
            '中型': 'mid',
            'ミドル': 'mid',
            '大型': 'full',
            'フル': 'full',
            '指定なし': 'any',
        }
        normalized = alias.get(normalized, normalized)
        if normalized in {'mini', 'mid', 'full'}:
            return normalized
    return 'any'


def _normalize_case_fan_policy(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        alias = {
            '自動': 'auto',
            '冷却重視': 'airflow',
            '静音重視': 'silent',
            'バランス': 'auto',
        }
        normalized = alias.get(normalized, normalized)
        if normalized in {'silent', 'airflow'}:
            return normalized
    return 'auto'


def _normalize_cpu_vendor(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        alias = {
            '指定なし': 'any',
            'なし': 'any',
            'インテル': 'intel',
            'intel': 'intel',
            'amd': 'amd',
        }
        normalized = alias.get(normalized, normalized)
        if normalized in {'intel', 'amd'}:
            return normalized
    return 'any'


def _normalize_build_priority(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        alias = {
            'コスト重視': 'cost',
            '費用重視': 'cost',
            '性能重視': 'spec',
            'スペック重視': 'spec',
            'バランス': 'balanced',
            '標準': 'balanced',
        }
        normalized = alias.get(normalized, normalized)
        if normalized in {'cost', 'spec'}:
            return normalized
    return 'balanced'


def _normalize_storage_preference(value):
    # メインストレージはSSD固定。'hdd' は受け付けない。
    return 'ssd'


def _normalize_os_edition(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'auto', 'home', 'pro'}:
            return normalized
    return 'auto'


def _resolve_os_edition_by_usage(usage, os_edition):
    if os_edition != 'auto':
        return os_edition

    auto_map = {
        'gaming': 'home',
        'workstation': 'pro',
        'ai': 'pro',
        'standard': 'home',
        'general': 'home',
        'creator': 'pro',
        'business': 'pro',
        'video_editing': 'pro',
    }
    return auto_map.get(usage, 'home')


def _normalize_custom_budget_weights(value):
    if not isinstance(value, dict):
        return None

    normalized = {}
    total = 0.0
    for part_type in PART_ORDER:
        raw = value.get(part_type)
        if raw in (None, ''):
            normalized[part_type] = 0.0
            continue
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            return None
        if numeric < 0:
            return None
        normalized[part_type] = numeric
        total += numeric

    if total <= 0:
        return None

    return {part_type: weight / total for part_type, weight in normalized.items()}


def _normalize_optional_storage_part_id(value):
    if value in (None, ''):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _normalize_min_storage_capacity_gb(value):
    if value in (None, ''):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if normalized not in {512, 1024, 2048, 4096}:
        return None
    return normalized


def _normalize_max_motherboard_chipset(value):
    """マザーボードチップセット上限を正規化: 'x870e' / 'x870' / 'x670e' / 'x670' / 'any'"""
    if value in (None, ''):
        return 'any'
    normalized = str(value).lower().strip()
    if normalized in ('x870e', 'x870', 'x670e', 'x670'):
        return normalized
    return 'any'


def _resolve_storage_part_by_id(part_id):
    normalized = _normalize_optional_storage_part_id(part_id)
    if normalized is None:
        return None
    try:
        return PCPart.objects.get(id=normalized, part_type='storage')
    except PCPart.DoesNotExist:
        return None


def _normalize_selection_options(cooler_type, radiator_size, cooling_profile, case_size, case_fan_policy, cpu_vendor, build_priority, os_edition, storage_preference, min_storage_capacity_gb=None, max_motherboard_chipset='any'):
    return {
        'cooler_type': _normalize_cooler_type(cooler_type),
        'radiator_size': _normalize_radiator_size(radiator_size),
        'cooling_profile': _normalize_cooling_profile(cooling_profile),
        'case_size': _normalize_case_size(case_size),
        'case_fan_policy': _normalize_case_fan_policy(case_fan_policy),
        'cpu_vendor': _normalize_cpu_vendor(cpu_vendor),
        'build_priority': _normalize_build_priority(build_priority),
        'os_edition': _normalize_os_edition(os_edition),
        'storage_preference': _normalize_storage_preference(storage_preference),
        'enforce_main_storage_ssd': True,
        'min_storage_capacity_gb': _normalize_min_storage_capacity_gb(min_storage_capacity_gb),
        'max_motherboard_chipset': _normalize_max_motherboard_chipset(max_motherboard_chipset),
    }


def _is_os_edition_match(part, os_edition):
    if os_edition == 'auto':
        return True

    text = f"{part.name} {part.url}".lower()
    if os_edition == 'home':
        return 'home' in text
    if os_edition == 'pro':
        return ' pro ' in f' {text} ' or 'windows 11 pro' in text
    return True


def _is_cpu_cooler_type_match(part, cooler_type):
    if cooler_type == 'any':
        return True

    text = f"{part.name} {part.url}".lower()

    def _has_keyword(keyword):
        if keyword in {'air', 'aio', 'liquid'}:
            return re.search(rf'\b{re.escape(keyword)}\b', text) is not None
        return keyword in text

    for keyword in COOLER_TYPE_KEYWORDS.get(cooler_type, []):
        if _has_keyword(keyword):
            return True
    return False


def _is_cpu_cooler_product(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    specs = getattr(part, 'specs', {}) or {}

    # ケースファン単品/セットをCPUクーラー候補から除外
    if (
        'case fan' in text
        or 'ケースファン' in text
        or 'single fan' in text
        or 'fan kit' in text
        or '2pack' in text
        or '3pack' in text
        or '4pack' in text
        or '2個パック' in text
        or re.search(r'\bcl-f\d', text)
    ):
        return False

    # CPUクーラーらしい明示キーワード
    cooler_hints = (
        'cpu cooler',
        'cpuクーラー',
        'air cooler',
        'aio',
        'liquid cooler',
        '水冷',
        '空冷',
        'heatsink',
        'ヒートシンク',
        'nh-',
        'ak',
        'assassin',
    )
    if any(hint in text for hint in cooler_hints):
        return True

    # 仕様にCPUソケット互換情報があればCPUクーラーとみなす
    socket_keys = ('socket', 'supported_socket', 'supported_sockets', 'socket_support')
    if any(specs.get(key) for key in socket_keys):
        return True

    return False


AMD_SOCKET_INCOMPATIBLE_COOLER_PATTERNS = (
    r'\bUX150-L\b',
)


def _is_known_amd_socket_incompatible_cooler(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()
    return any(re.search(pattern, text) for pattern in AMD_SOCKET_INCOMPATIBLE_COOLER_PATTERNS)


def _is_cpu_cooler_socket_compatible(part, cpu_socket):
    if not part or not cpu_socket:
        return True

    socket_key = str(cpu_socket or '').strip().upper()
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()
    specs = getattr(part, 'specs', {}) or {}

    spec_socket_text = " ".join(
        str(specs.get(key, '') or '')
        for key in ('socket', 'supported_socket', 'supported_sockets', 'socket_support')
    ).upper()
    combined = f"{text} {spec_socket_text}"

    if socket_key in {'AM4', 'AM5'}:
        if _is_known_amd_socket_incompatible_cooler(part):
            return False
        has_amd = ('AM4' in combined) or ('AM5' in combined) or ('AMD' in combined)
        has_intel_only = ('LGA1700' in combined or 'LGA1851' in combined) and not has_amd
        if has_intel_only:
            return False
        return True

    if socket_key in {'LGA1700', 'LGA1851'}:
        has_intel = ('LGA1700' in combined) or ('LGA1851' in combined) or ('INTEL' in combined)
        has_amd_only = ('AM4' in combined or 'AM5' in combined) and not has_intel
        if has_amd_only:
            return False
        return True

    return True


def _extract_radiator_size_token(text):
    for token in ('120', '240', '360'):
        if f'{token}mm' in text or f'{token} mm' in text or token in text:
            return token
    return None


def _is_radiator_size_match(part, radiator_size):
    if radiator_size == 'any':
        return True
    text = f"{part.name} {part.url}".lower()
    token = _extract_radiator_size_token(text)
    return token == radiator_size


def _cpu_cooler_profile_score(part, cooling_profile, cooler_type):
    text = f"{part.name} {part.url}".lower()
    score = 0

    if cooling_profile == 'silent':
        for keyword in COOLING_PROFILE_KEYWORDS['silent']:
            if keyword in text:
                score += 2
        if cooler_type == 'air':
            score += 1
    elif cooling_profile == 'performance':
        for keyword in COOLING_PROFILE_KEYWORDS['performance']:
            if keyword in text:
                score += 2
        if cooler_type == 'liquid':
            score += 1
        for token in ('240', '280', '360'):
            if token in text:
                score += 1

    return score


def _is_allowed_cpu_cooler_brand(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return 'noctua' not in text


def _case_fan_policy_score(part, case_fan_policy):
    if case_fan_policy == 'auto':
        return 0

    text = f"{part.name} {part.url}".lower()
    score = 0
    for keyword in CASE_FAN_POLICY_KEYWORDS.get(case_fan_policy, []):
        if keyword in text:
            score += 2

    # 仕様情報がある場合は追加加点
    specs = getattr(part, 'specs', {}) or {}
    max_radiator_mm = _extract_numeric_radiator_size(specs.get('max_radiator_mm'))
    included_fan_count = _extract_numeric_fan_count(specs.get('included_fan_count'))
    supported_fan_count = _extract_numeric_fan_count(specs.get('supported_fan_count'))
    front_fan_slots = _extract_numeric_fan_count(specs.get('front_fan_slots'))
    top_fan_slots = _extract_numeric_fan_count(specs.get('top_fan_slots'))
    rear_fan_slots = _extract_numeric_fan_count(specs.get('rear_fan_slots'))

    # 付属ファンなしケースは方針不一致として強く減点
    if included_fan_count == 0:
        score -= 6
    elif included_fan_count is None:
        if any(keyword in text for keyword in ('ファン非搭載', 'ファンなし', 'ファン別売', 'fanless', 'without fan')):
            score -= 6

    # 同梱ファン数を優先評価
    if included_fan_count is not None:
        if included_fan_count >= 4:
            score += 5
        elif included_fan_count == 3:
            score += 4
        elif included_fan_count == 2:
            score += 2
        elif included_fan_count == 1:
            score += 1

    # 搭載可能ファン数は冷却重視でより強く評価
    if supported_fan_count is not None:
        if case_fan_policy == 'airflow':
            if supported_fan_count >= 10:
                score += 5
            elif supported_fan_count >= 8:
                score += 4
            elif supported_fan_count >= 6:
                score += 3
            elif supported_fan_count >= 4:
                score += 1
        elif case_fan_policy == 'silent':
            if supported_fan_count >= 7:
                score += 2
            elif supported_fan_count >= 5:
                score += 1

    # 前面/上面/背面スロットがある場合は airflow を段階的に強化
    if case_fan_policy == 'airflow':
        weighted_slots = (
            (front_fan_slots or 0) * 1.8
            + (top_fan_slots or 0) * 1.3
            + (rear_fan_slots or 0) * 1.0
        )
        if weighted_slots >= 10:
            score += 6
        elif weighted_slots >= 7:
            score += 4
        elif weighted_slots >= 5:
            score += 2

        if (front_fan_slots or 0) >= 3:
            score += 3
        elif (front_fan_slots or 0) >= 2:
            score += 1

        if (top_fan_slots or 0) >= 3:
            score += 2
        elif (top_fan_slots or 0) >= 2:
            score += 1

        if (rear_fan_slots or 0) >= 1:
            score += 1
    elif case_fan_policy == 'silent':
        # 静音重視は最低限の吸排気を評価し、過多なトップ排気は軽く抑制
        if (front_fan_slots or 0) >= 2:
            score += 1
        if (rear_fan_slots or 0) >= 1:
            score += 1
        if (top_fan_slots or 0) >= 4:
            score -= 1

    if case_fan_policy == 'airflow' and max_radiator_mm:
        if max_radiator_mm >= 360:
            score += 2
        elif max_radiator_mm >= 240:
            score += 1

    return score


def _case_quality_score(part):
    text = f"{part.name} {part.url}".lower()
    specs = getattr(part, 'specs', {}) or {}

    score = 0
    included_fan_count = _extract_numeric_fan_count(specs.get('included_fan_count'))
    supported_fan_count = _extract_numeric_fan_count(specs.get('supported_fan_count'))
    max_radiator_mm = _extract_numeric_radiator_size(specs.get('max_radiator_mm'))

    if included_fan_count is not None:
        score += min(included_fan_count, 4) * 2
    elif any(keyword in text for keyword in ('fanless', 'without fan', 'ファンなし', 'ファン非搭載')):
        score -= 6

    if supported_fan_count is not None:
        if supported_fan_count >= 8:
            score += 4
        elif supported_fan_count >= 6:
            score += 3
        elif supported_fan_count >= 4:
            score += 2

    if max_radiator_mm:
        if max_radiator_mm >= 360:
            score += 3
        elif max_radiator_mm >= 240:
            score += 1

    if any(keyword in text for keyword in ('airflow', 'mesh', 'メッシュ')):
        score += 2
    if any(keyword in text for keyword in ('silent', '静音')):
        score += 1

    return score


def _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=None):
    if not candidates:
        return None

    if case_fan_policy != 'auto':
        if build_priority == 'cost':
            return sorted(
                candidates,
                key=lambda p: (-_case_fan_policy_score(p, case_fan_policy), p.price),
            )[0]
        return sorted(
            candidates,
            key=lambda p: (_case_fan_policy_score(p, case_fan_policy), p.price),
            reverse=True,
        )[0]

    if build_priority == 'cost':
        return sorted(
            candidates,
            key=lambda p: (-_case_quality_score(p), p.price),
        )[0]

    if target_price is None:
        return sorted(candidates, key=lambda p: (_case_quality_score(p), p.price), reverse=True)[0]

    return sorted(
        candidates,
        key=lambda p: (_case_quality_score(p), -abs(p.price - target_price), p.price),
        reverse=True,
    )[0]


def _is_case_size_match(part, case_size):
    if case_size == 'any':
        return True

    text = f"{part.name} {part.url}".lower()
    is_mini = any(keyword in text for keyword in CASE_SIZE_KEYWORDS['mini'])
    is_full = any(keyword in text for keyword in CASE_SIZE_KEYWORDS['full'])

    if case_size == 'mini':
        return is_mini
    if case_size == 'full':
        return is_full

    # midはATX系を含めるが、mini/fullと明示されるものは除外する。
    if case_size == 'mid':
        is_mid_keyword = any(keyword in text for keyword in CASE_SIZE_KEYWORDS['mid'])
        return is_mid_keyword and not is_mini and not is_full

    return False


def _is_cpu_vendor_match(part, cpu_vendor):
    if cpu_vendor == 'any':
        return True
    text = f"{part.name} {part.url}".lower()
    return any(keyword in text for keyword in CPU_VENDOR_KEYWORDS.get(cpu_vendor, []))


def _cpu_socket_code(part):
    return str(_get_spec(part, 'socket', '') or '').strip().upper()


def _is_am5_cpu(part):
    return _cpu_socket_code(part) == 'AM5'


def _is_general_cost_low_tier(usage, build_priority, budget):
    return (
        usage in {'general', 'business', 'standard'}
        and build_priority == 'cost'
        and _classify_budget_tier(int(budget or 0), usage=usage) == 'low'
    )


def _is_general_low_tier(usage, budget):
    return usage in {'general', 'business', 'standard'} and _classify_budget_tier(int(budget or 0), usage=usage) == 'low'


def _is_general_cost_legacy_cpu(part):
    socket = _cpu_socket_code(part)
    return socket == 'AM4' or _is_cpu_vendor_match(part, 'intel')


def _pick_general_low_tier_cpu_candidate(candidates):
    if not candidates:
        return None

    preferred = [
        p
        for p in candidates
        if 'core ultra 5 225 box' in f'{getattr(p, "name", "")} {getattr(p, "url", "")}'.lower()
    ]
    if preferred:
        return sorted(preferred, key=lambda p: (p.price, 0 if _is_cpu_vendor_match(p, 'intel') else 1))[0]

    legacy_pool = [p for p in candidates if _is_general_cost_legacy_cpu(p)]
    if legacy_pool:
        return sorted(
            legacy_pool,
            key=lambda p: (
                0 if not _is_am5_cpu(p) else 1,
                0 if _is_cpu_vendor_match(p, 'intel') else 1,
                p.price,
            ),
        )[0]

    non_am5_pool = [p for p in candidates if not _is_am5_cpu(p)]
    if non_am5_pool:
        return sorted(non_am5_pool, key=lambda p: (0 if _is_cpu_vendor_match(p, 'intel') else 1, p.price))[0]

    return sorted(candidates, key=lambda p: p.price)[0]


def _pick_general_cost_cpu_candidate(candidates):
    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda p: (
            0 if _is_general_cost_legacy_cpu(p) else 1,
            0 if not _is_am5_cpu(p) else 1,
            p.price,
        ),
    )[0]


def _prefer_general_cost_cpu_budget_band(candidates, target_price, usage, build_priority, budget):
    if not candidates:
        return candidates
    if _is_general_cost_low_tier(usage, build_priority, budget):
        return candidates

    floor_price = int(target_price * 0.8)
    banded_candidates = [p for p in candidates if int(getattr(p, 'price', 0) or 0) >= floor_price]
    return banded_candidates or candidates


def _is_general_spec_entry_cpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    entry_pattern = r'athlon|sempron|processor\s*300|celeron|pentium|\bn\d{3}\b|core\s*i3\b|ryzen\s*3\b'
    return re.search(entry_pattern, text) is not None


def _prefer_general_spec_cpu_quality_pool(candidates, usage, budget):
    if not candidates:
        return candidates

    # spec重視ではエントリーCPUを優先対象から外す。
    non_entry = [p for p in candidates if not _is_general_spec_entry_cpu(p)]
    filtered = non_entry or candidates

    tier = _classify_budget_tier(int(budget or 0), usage=usage)
    min_cores = 6 if tier in {'middle', 'high', 'premium'} else 4
    core_filtered = [p for p in filtered if _extract_cpu_core_count(p) >= min_cores]
    if core_filtered:
        return core_filtered

    if tier in {'middle', 'high', 'premium'}:
        performance_named = [
            p for p in filtered
            if re.search(r'ryzen\s*[5-9]\b|core\s*i[5-9]\b|core\s*ultra\s*[579]\b', f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower())
        ]
        if performance_named:
            return performance_named

    return filtered


def _is_ai_latest_generation_cpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    intel_latest = re.search(r'core\s*ultra\s*[3579]\s*2\d{2}[a-z]*', text) is not None
    # AMD は AI用途で Ryzen 9000 系のみを「最新世代」として扱う。
    amd_latest = re.search(r'ryzen\s*[3579]\s*9\d{3}(?:x3d|x|g|gt|ge|f)?', text) is not None
    # AI/Creator のワークステーション用途では Threadripper / EPYC も許可する。
    workstation_cpu = _is_workstation_cpu(part)
    return intel_latest or amd_latest or workstation_cpu


def _is_workstation_cpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return any(keyword in text for keyword in ('threadripper', 'epyc'))


def _is_workstation_threadripper_9000(part):
    """Threadripper 9000系（PRO含む）を識別する。
    workstation + spec + premium で最優先選定対象。"""
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return bool(re.search(r'threadripper(?:\s*pro)?\s*9\d{3}', text))


def _is_workstation_ryzen_9950x3d2(part):
    """Ryzen 9 9950X3D2 Dual Edition を識別する。
    workstation + cost + premium で最優先選定対象。"""
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return '9950x3d2' in text or ('9950x3d' in text and 'dual' in text)


def _is_workstation_ryzen_9950x3d(part):
    """Ryzen 9 9950X3D（通常版、Dual Edition を除く）を識別する。
    workstation + spec + high の選定対象。"""
    if _is_workstation_ryzen_9950x3d2(part):
        return False
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return bool(re.search(r'ryzen\s*9\s*9950x3d\b', text))


def _is_workstation_ryzen_9700_class(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return bool(re.search(r'ryzen\s*[79]\s*9700(?:x3d2|x3d|x|g|gt|ge|f)?\b', text))


def _is_workstation_ryzen_9800_class(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return bool(re.search(r'ryzen\s*[79]\s*9800(?:x3d2|x3d|x|g|gt|ge|f)?\b', text))


def _is_workstation_mainstream_high_end_cpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    if _is_workstation_ryzen_9950x3d2(part):
        return False
    return bool(
        re.search(r'ryzen\s*9\s*9900(?:x3d|x)?\b', text)
        or re.search(r'ryzen\s*9\s*9950(?:x3d|x)?\b', text)
    )


def _matches_workstation_cpu_tier(part, budget_tier, build_priority='spec'):
    budget_tier = _normalize_budget_tier_code(budget_tier)
    if not budget_tier:
        return True
    if budget_tier == 'low':
        return _is_workstation_ryzen_9700_class(part)
    if budget_tier == 'middle':
        return _is_workstation_ryzen_9800_class(part)
    if budget_tier == 'high':
        if build_priority == 'spec':
            return _is_workstation_ryzen_9950x3d(part)
        text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
        return bool(re.search(r'ryzen\s*9\s*9950(?:x3d2|x3d|x)?\b', text))
    if budget_tier == 'premium':
        if build_priority == 'spec':
            return _is_workstation_threadripper_9000(part)
        return _is_workstation_ryzen_9950x3d2(part)
    return True


def _is_supported_intel_client_cpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    normalized_text = ' '.join(text.replace('™', ' ').replace('®', ' ').split())
    intel_tokens = ('intel', 'core', 'pentium', 'celeron', 'ultra')
    if not any(token in normalized_text for token in intel_tokens):
        return True
    return any(token in normalized_text for token in ('core i', 'core ultra', 'pentium', 'celeron'))


def _pick_workstation_cpu(candidates, build_priority='spec'):
    if not candidates:
        return None
    if build_priority == 'cost':
        return sorted(
            candidates,
            key=lambda p: (
                int(getattr(p, 'price', 0) or 0),
                -(_get_cpu_perf_score(p) or 0),
                -_extract_cpu_core_count(p),
                -_extract_cpu_core_threads(p),
            ),
        )[0]
    return sorted(
        candidates,
        key=lambda p: (
            (_get_cpu_perf_score(p) or 0),
            _extract_cpu_core_count(p),
            _extract_cpu_core_threads(p),
            -int(getattr(p, 'price', 0) or 0),
        ),
        reverse=True,
    )[0]


def _is_ai_latest_generation_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    nvidia_latest = re.search(r'rtx\s*50\d{2}(?:\s*ti|\s*super)?', text) is not None
    amd_latest = re.search(r'(?:radeon\s*rx|rx)\s*9\d{3}', text) is not None
    # workstation/ai向けに業務系NVIDIAを許可する。
    # 要件: RTX PRO 5000 / 4000 / 2000 と RTX 4500 Ada を明示許可。
    nvidia_pro_patterns = (
        r'rtx\s*pro\s*5000\b',
        r'rtx\s*pro\s*4000\b',
        r'rtx\s*pro\s*2000\b',
        r'rtx\s*4500\s*ada\b',
        r'rtx\s*pro\s*4500\b',
    )
    nvidia_pro_latest = any(re.search(pattern, text) is not None for pattern in nvidia_pro_patterns)
    pro_ai_latest = 'radeon ai pro r9700' in text
    return nvidia_latest or amd_latest or nvidia_pro_latest or pro_ai_latest


def _is_gt_series_gpu(part):
    text = f"{part.name} {part.url}".lower()
    return re.search(r'\bgt[\s\-_/]*\d{3,4}\b', text) is not None


def _is_nvidia_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return any(keyword in text for keyword in ('nvidia', 'geforce', 'rtx', 'quadro'))


def _is_creator_r9700_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return 'radeon ai pro r9700' in text or ('r9700' in text and 'creator' in text)


def _is_creator_rtxpro4500_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return bool(re.search(r'rtx\s*(?:pro\s*)?4500(?:\s*ada)?\b', text))


def _is_creator_rtx5090_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return 'rtx 5090' in text


def _creator_gpu_priority_key(part, build_priority='cost'):
    if build_priority == 'spec':
        spec_rank = 0
        if _is_creator_rtxpro4500_gpu(part):
            spec_rank = 3
        elif _is_creator_rtx5090_gpu(part):
            spec_rank = 2
        elif _is_creator_r9700_gpu(part):
            spec_rank = 1
        return (
            spec_rank,
            _infer_gaming_gpu_perf_score(part),
            _infer_gpu_memory_gb(part),
            1 if _is_nvidia_gpu(part) else 0,
            -int(getattr(part, 'price', 0) or 0),
        )

    return (
        _infer_gpu_memory_gb(part),
        _infer_gaming_gpu_perf_score(part),
        1 if _is_nvidia_gpu(part) else 0,
        -int(getattr(part, 'price', 0) or 0),
    )


def _prefer_creator_gpu_with_vram_flex(candidates, build_priority='cost'):
    """creator用途: cost は VRAM優先、spec は性能優先で比較する。"""
    if not candidates:
        return candidates

    return sorted(
        candidates,
        key=lambda part: _creator_gpu_priority_key(part, build_priority=build_priority),
        reverse=True,
    )


def _prefer_creator_premium_gpu(candidates, build_priority='cost'):
    if not candidates:
        return candidates

    if build_priority == 'spec':
        exact_pro4500 = [p for p in candidates if _is_creator_rtxpro4500_gpu(p)]
        if exact_pro4500:
            return sorted(exact_pro4500, key=lambda part: _creator_gpu_priority_key(part, build_priority=build_priority), reverse=True)

        exact_5090 = [p for p in candidates if _is_creator_rtx5090_gpu(p)]
        if exact_5090:
            return sorted(exact_5090, key=lambda part: _creator_gpu_priority_key(part, build_priority=build_priority), reverse=True)

        exact_r9700 = [p for p in candidates if _is_creator_r9700_gpu(p)]
        if exact_r9700:
            return sorted(exact_r9700, key=lambda part: _creator_gpu_priority_key(part, build_priority=build_priority), reverse=True)

    if build_priority == 'cost':
        exact = [p for p in candidates if _is_creator_r9700_gpu(p)]
        if exact:
            return sorted(exact, key=lambda part: _creator_gpu_priority_key(part, build_priority=build_priority), reverse=True)

    return sorted(candidates, key=lambda part: _creator_gpu_priority_key(part, build_priority=build_priority), reverse=True)


def _creator_gpu_within_limits(candidate, selected_parts, budget, usage, options=None):
    options = options or {}
    current_gpu = selected_parts.get('gpu')
    current_total = _sum_selected_price(selected_parts)
    if current_gpu and current_total - int(current_gpu.price or 0) + int(candidate.price or 0) > int(budget):
        return False

    trial_parts = dict(selected_parts)
    trial_parts['gpu'] = candidate

    required_psu_wattage = _required_psu_wattage(trial_parts, usage)
    psu = trial_parts.get('psu')
    if psu and required_psu_wattage is not None:
        psu_wattage = _infer_psu_wattage_w(psu)
        if psu_wattage > 0 and psu_wattage < required_psu_wattage:
            return False

    estimated_power = _estimate_system_power_w(trial_parts, usage)
    if psu and _infer_psu_wattage_w(psu) > 0:
        psu_wattage = _infer_psu_wattage_w(psu)
        if estimated_power > int(psu_wattage * 0.95):
            return False

    return True


def _creator_motherboard_expandability_score(part):
    specs = getattr(part, 'specs', {}) or {}
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()

    score = 0

    form_factor = _infer_motherboard_form_factor(part)
    form_factor_score = {
        'eatx': 45,
        'atx': 35,
        'micro-atx': 20,
        'mini-itx': 8,
    }
    score += form_factor_score.get(form_factor, 10)

    chipset = _infer_motherboard_chipset(part)
    chipset_score = {
        'x870e': 20,
        'x870': 16,
        'x670e': 14,
        'x670': 10,
    }
    score += chipset_score.get(chipset, 0)

    # specs が疎なデータセットでも動くよう、URL/名称ヒントを併用する。
    if any(kw in text for kw in ('creator', 'proart', 'aorus master', 'taichi', 'steel legend', 'tomahawk', 'rog strix')):
        score += 8
    if any(kw in text for kw in ('gaming x', 'aorus', 'tuf')):
        score += 4

    usb_like_keys = ('usb_total', 'usb_ports', 'rear_usb_ports', 'usb3_ports', 'usb2_ports', 'type_c_ports')
    pcie_like_keys = ('pcie_slots', 'pcie_x16_slots', 'm2_slots', 'm_2_slots')
    for key in usb_like_keys + pcie_like_keys:
        value = specs.get(key)
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            numeric = 0
        score += min(max(numeric, 0), 12)

    return score


def _pick_creator_preferred_motherboard(candidates):
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda p: (
            _creator_motherboard_expandability_score(p),
            -p.price,
        ),
        reverse=True,
    )[0]


def _pick_motherboard_candidate(candidates, build_priority, usage, target_price=None):
    if not candidates:
        return None

    if build_priority == 'cost':
        # 価格重視でも拡張性スコアを併用し、最安固定を避ける。
        return sorted(
            candidates,
            key=lambda p: (-_creator_motherboard_expandability_score(p), p.price),
        )[0]

    usage_bias = 1.15 if usage == 'creator' else 1.0
    if usage == 'gaming' and build_priority == 'spec' and target_price is not None:
        return sorted(
            candidates,
            key=lambda p: (
                _infer_motherboard_memory_type(p) == 'DDR5',
                -abs(p.price - target_price),
                _creator_motherboard_expandability_score(p),
                p.price,
            ),
            reverse=True,
        )[0]

    if build_priority == 'spec' and usage in {'general', 'business', 'standard'}:
        # 汎用specは、価格の近さよりも拡張性・上位チップセットを優先する。
        return sorted(
            candidates,
            key=lambda p: (
                _creator_motherboard_expandability_score(p),
                _infer_motherboard_chipset(p) != 'b550',
                p.price,
            ),
            reverse=True,
        )[0]

    if target_price is None:
        return sorted(
            candidates,
            key=lambda p: (_creator_motherboard_expandability_score(p) * usage_bias, p.price),
            reverse=True,
        )[0]

    return sorted(
        candidates,
        key=lambda p: (
            _creator_motherboard_expandability_score(p) * usage_bias,
            -abs(p.price - target_price),
            p.price,
        ),
        reverse=True,
    )[0]


def _infer_gpu_memory_gb(part):
    try:
        memory_gb = int(_get_spec(part, 'memory_gb', 0) or 0)
    except (TypeError, ValueError):
        memory_gb = 0
    if memory_gb > 0:
        return memory_gb

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    match = re.search(r'(\d+)\s*gb', text)
    if match:
        return int(match.group(1))
    return 0


def _gaming_spec_gpu_tier(part):
    text = f"{part.name} {part.url}".lower()
    memory_gb = _infer_gpu_memory_gb(part)

    tier_label = _infer_gaming_gpu_tier_label(part)
    if tier_label:
        tier_rank = GAMING_GPU_TIER_RANKS.get(tier_label, 0)
        if tier_rank == 1 and memory_gb < 6:
            return 0
        if tier_rank >= 2 and memory_gb < 8:
            return tier_rank - 1
        return tier_rank

    if any(keyword in text for keyword in GAMING_SPEC_GPU_KEYWORDS) or re.search(r'\brx\s*\d{3,4}\b', text):
        if memory_gb >= 8:
            return 2
        if memory_gb >= 6:
            return 1
        return 0

    return 0


def _is_gaming_gpu_within_priority_cap(part, build_priority, budget=None):
    if not part or build_priority not in {'cost', 'spec'}:
        return True

    if _is_gaming_creative_gpu(part):
        return False

    tier_label = _infer_gaming_gpu_tier_label(part)
    tier_rank = GAMING_GPU_TIER_RANKS.get(tier_label, 0)

    # 予算帯なしの既存呼び出しは、従来ルールを維持する。
    if budget is None:
        if build_priority == 'cost':
            return tier_rank in {0, 1, 2}
        return tier_rank in {0, 1, 2, 3, 4}

    try:
        budget_value = int(budget)
    except (TypeError, ValueError):
        budget_value = 0

    budget_tier = _classify_budget_tier(budget_value, usage='gaming')
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    is_rtx_5050 = bool(re.search(r'\brtx\s*5050\b', text))
    is_rtx_5060 = bool(re.search(r'\brtx\s*5060\b', text)) and not bool(re.search(r'\brtx\s*5060\s*ti\b', text))
    is_rtx_5070 = bool(re.search(r'\brtx\s*5070\b', text)) and not bool(re.search(r'\brtx\s*5070\s*ti\b', text))
    is_rtx_5080 = bool(re.search(r'\brtx\s*5080\b', text))
    is_rtx_5090_or_4090 = bool(re.search(r'\brtx\s*(?:5090|4090)\b', text))
    is_rx_9070_xt = bool(re.search(r'\brx\s*9070\s*xt\b|\brx9070xt\b', text))

    if build_priority == 'cost':
        if budget_tier in {'low', 'middle'}:
            return tier_rank in {0, 1, 2}
        if budget_tier == 'high':
            # high + cost: 5070(無印) / 9070XT まで許容。
            # 5070 Ti や 4080/5080 などは除外する。
            return tier_rank in {0, 1, 2} or is_rx_9070_xt or is_rtx_5070
        if budget_tier == 'premium':
            # premium + cost: 十分な予算がある場合は 5080 級まで許容する。
            if budget_value >= 1_000_000:
                return tier_rank in {0, 1, 2, 3, 4} or is_rtx_5080 or is_rx_9070_xt or is_rtx_5070
            return tier_rank in {0, 1, 2} or is_rx_9070_xt or is_rtx_5070
        return tier_rank in {0, 1, 2}

    if budget_tier == 'low':
        # low + spec: 5050 まで。
        return tier_rank <= 1 or is_rtx_5050
    if budget_tier == 'middle':
        # middle + spec: 5060 まで。
        return tier_rank <= 2 or is_rtx_5060
    if budget_tier == 'high':
        # high + spec: 5080 まで（5090/4090 は除外）。
        if is_rtx_5090_or_4090:
            return False
        return tier_rank in {0, 1, 2, 3, 4} or is_rtx_5080

    if budget_tier == 'premium' and budget_value <= GAMING_SPEC_PREMIUM_SOFT_CAP_BUDGET:
        # premium下限のspecは、GPUを1段抑えてストレージ/全体バランスを優先する。
        return tier_rank in {0, 1, 2, 3} or is_rtx_5070 or is_rx_9070_xt

    return tier_rank in {0, 1, 2, 3, 4}


def _is_gaming_creative_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return any(keyword in text for keyword in GAMING_CREATIVE_GPU_KEYWORDS)


def _infer_gaming_gpu_tier_label(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    for label, patterns in GAMING_GPU_TIER_LABEL_RULES:
        if any(re.search(pattern, text) for pattern in patterns):
            return label
    return ''


def _is_gaming_low_end_tier_gpu(part):
    return _infer_gaming_gpu_tier_label(part) == 'ローエンド'


def _load_amd_cpu_rankings():
    if AMD_CPU_RANKING_CACHE['cost'] is not None and AMD_CPU_RANKING_CACHE['spec'] is not None:
        return AMD_CPU_RANKING_CACHE

    def _parse_ranking_file(file_path):
        rankings = {}
        if not file_path.exists():
            return rankings

        try:
            raw_text = file_path.read_text(encoding='utf-8-sig')
        except OSError:
            return rankings

        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue

            match = re.match(r'^\s*\d+\.?\s*(.+?)\s*\|', line)
            if not match:
                continue

            model_name = _normalize_cpu_model_query(match.group(1))
            if model_name and model_name not in rankings:
                rankings[model_name] = line_number

        return rankings

    AMD_CPU_RANKING_CACHE['cost'] = _parse_ranking_file(AMD_CPU_COST_RANKING_FILE)
    AMD_CPU_RANKING_CACHE['spec'] = _parse_ranking_file(AMD_CPU_SPEC_RANKING_FILE)
    return AMD_CPU_RANKING_CACHE


def _extract_amd_cpu_ranking_key(part):
    if not part:
        return ''

    extracted = _extract_cpu_model_key_for_perf(getattr(part, 'name', ''))
    candidates = []
    if extracted:
        candidates.append(_normalize_cpu_model_query(extracted))
    candidates.append(_normalize_cpu_model_query(getattr(part, 'name', '')))

    for candidate in candidates:
        if candidate:
            return candidate
    return ''


def _get_amd_cpu_rank(part, build_priority):
    rankings = _load_amd_cpu_rankings().get(build_priority)
    if not rankings:
        return None

    candidate_key = _extract_amd_cpu_ranking_key(part)
    if not candidate_key:
        return None

    direct_rank = rankings.get(candidate_key)
    if direct_rank is not None:
        return direct_rank

    for model_name, rank in rankings.items():
        if candidate_key in model_name or model_name in candidate_key:
            return rank

    return None


def _get_amd_cpu_rank_by_name(model_name, build_priority):
    rankings = _load_amd_cpu_rankings().get(build_priority)
    if not rankings:
        return None

    candidate_key = _normalize_cpu_model_query(model_name)
    if not candidate_key:
        return None

    direct_rank = rankings.get(candidate_key)
    if direct_rank is not None:
        return direct_rank

    for ranking_model_name, rank in rankings.items():
        if candidate_key in ranking_model_name or ranking_model_name in candidate_key:
            return rank

    return None


def _pick_amd_gaming_cpu(candidates, build_priority, require_x3d=False):
    if not candidates:
        return None

    candidates = [p for p in candidates if not _is_gaming_excluded_creator_cpu(p)]
    if not candidates:
        return None

    spec_priority_candidates = [
        p for p in candidates 
        if build_priority == 'spec' and int(getattr(p, 'id', 0) or 0) in GAMING_SPEC_PRIORITY_CPU_IDS
    ]
    if spec_priority_candidates:
        candidates = spec_priority_candidates

    if require_x3d:
        x3d_candidates = [p for p in candidates if _is_gaming_cpu_x3d_preferred(p)]
        if x3d_candidates:
            candidates = x3d_candidates

    ranked_candidates = []
    fallback_candidates = []
    for part in candidates:
        rank = _get_amd_cpu_rank(part, build_priority)
        if rank is None:
            fallback_candidates.append(part)
            continue
        ranked_candidates.append((rank, part))

    if ranked_candidates:
        return sorted(
            ranked_candidates,
            key=lambda item: (
                item[0],
                item[1].price,
                -(_get_cpu_perf_score(item[1]) or 0),
            ),
        )[0][1]

    if build_priority == 'cost':
        return sorted(
            fallback_candidates or candidates,
            key=lambda p: (
                (_get_cpu_perf_score(p) or 0) / max(int(getattr(p, 'price', 0) or 0), 1),
                _get_cpu_perf_score(p) or 0,
                -(int(getattr(p, 'price', 0) or 0)),
            ),
            reverse=True,
        )[0]

    return sorted(
        fallback_candidates or candidates,
        key=lambda p: (
            _get_cpu_perf_score(p) or 0,
            -(int(getattr(p, 'price', 0) or 0)),
        ),
        reverse=True,
    )[0]


def _minimum_gaming_spec_gpu_tier(budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return 0
    if budget >= 500000:
        return 4
    if budget >= 350000:
        return 3
    if budget >= 180000:
        return 1
    return 1


def _is_low_end_gaming_budget(budget, usage):
    if usage != 'gaming':
        return False
    try:
        value = int(budget)
    except (TypeError, ValueError):
        return False
        # 低予算 gaming では 5000+ を強制すると 3050-class が候補外になるため、
        # 低予算帯は perf floor を外す。
        if _is_low_end_gaming_budget(budget, usage):
            return 0
        # それ以外の gaming では Dospara Time Spy benchmark guidance を使う。
        return 5000
    return 0


def _gaming_spec_gpu_price_floor(budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return 0

    if budget >= 180000:
        # 18万円帯のspecは RTX 5060 クラス（約56,000円）を目安にする。
        return max(57980, int(budget * 0.28))
    if budget >= 150000:
        return max(47800, int(budget * 0.26))
    return 0


def _standard_business_spec_gpu_price_floor(budget):
    """standard/business + spec 構成で dGPU が許可された場合の GPU 最低価格。
    
    予算帯ごとに「これ以上の GPU を必ず選ぶ」ための下限を設定する。
    - 16万〜22万: エントリーGPU (RTX 4060 / RX 7600 クラス ≈ 3.5万)
    - 22万〜35万: ミドルGPU (RTX 4060 Ti / RX 7700 XT クラス ≈ 5万)
    - 35万以上:   ハイGPU   (RTX 4070 / RX 7800 XT クラス ≈ 7万)
    """
    if budget >= 350000:
        return max(69980, int(budget * 0.18))
    if budget >= 220000:
        return max(49980, int(budget * 0.21))
    if budget >= SPEC_GPU_UNLOCK_BUDGET_THRESHOLD:
        return max(34980, int(budget * 0.22))
    return 0


def _gaming_spec_gpu_tier_cap(budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return None
    tier = _classify_budget_tier(int(budget or 0), usage=usage)
    if tier == 'low':
        return 1
    if tier == 'middle':
        return 2
    if tier == 'high':
        return 4
    return 4


def _is_gaming_spec_exact_5060_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return bool(re.search(r'\brtx\s*5060\b', text)) and not bool(re.search(r'\brtx\s*5060\s*ti\b', text))


def _gaming_low_end_gpu_policy(budget, usage, build_priority):
    if usage != 'gaming' or budget >= _budget_tier_threshold(usage, 'low'):
        return None

    if build_priority == 'cost':
        return {
            'target_price': max(31980, int(budget * 0.195)),
            'price_cap': max(34980, int(budget * 0.21)),
        }
    if build_priority == 'spec':
        return {
            'target_price': max(44980, int(budget * 0.27)),
            'price_cap': max(49980, int(budget * 0.30)),
        }
    return None


def _gaming_cost_gpu_cap_price(reference_budget):
    try:
        budget = int(reference_budget)
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        return 0

    # premium帯(高予算)のcostは、上位GPUへ予算を回せるよう上限を緩和する。
    if budget >= 1_000_000:
        return min(249800, int(budget * 0.20))

    # コスパ重視の25万円帯は、GPUを5050級に寄せてメモリ/他パーツへ予算を回す。
    if budget >= 250000:
        return min(52980, int(budget * 0.24))
    if budget >= 200000:
        return min(64980, int(budget * 0.28))
    return int(budget * 0.31)


def _gaming_cost_gpu_floor_price(reference_budget):
    try:
        budget = int(reference_budget)
    except (TypeError, ValueError):
        budget = 0
    if budget >= 250000:
        return 44000
    return 0


def _creator_gpu_tier(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    memory_gb = _infer_gpu_memory_gb(part)

    # Tier 3: Flagship professional/gaming cards
    if any(keyword in text for keyword in ('rtx 5090', 'rtx 5080', 'rtx 5070 ti', 'rtx 4090', 'rtx 4080', 'rtx pro 6000', 'rtx pro 5880')):
        return 3
    # Tier 2: Mid-range professional/gaming cards (including Radeon R9700, RTX PRO 4500)
    if any(keyword in text for keyword in ('rtx 5070', 'rtx 5060 ti', 'rtx 4070', 'rtx 4060 ti', 'radeon ai pro r9700', 'rtx pro 4500')):
        return 2
    # Tier 1: Entry professional/gaming cards
    if any(keyword in text for keyword in ('rtx 5060', 'rtx 4060', 'rtx 3060')):
        return 1
    if 'rtx 3050' in text and memory_gb >= 6:
        return 1
    return 0


def _minimum_creator_gpu_tier(budget, options=None):
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')

    if build_priority == 'spec':
        if budget >= 350000:
            return 2
        return 1

    # cost でも、クリエイター用途は最低限の CUDA クラスを維持する。
    if budget >= 180000:
        return 1
    return 0


def _creator_gpu_cap_price(budget, options=None):
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')
    cap_ratio = CREATOR_GPU_BUDGET_CAP_BY_PRIORITY.get(build_priority, CREATOR_GPU_BUDGET_CAP_BY_PRIORITY['balanced'])
    return int(budget * cap_ratio)


def _creator_motherboard_floor_price(budget, options=None):
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')
    floor_ratio = CREATOR_MOTHERBOARD_FLOOR_BY_PRIORITY.get(build_priority, CREATOR_MOTHERBOARD_FLOOR_BY_PRIORITY['balanced'])
    return int(budget * floor_ratio)


def _normalize_budget_tier_usage(usage):
    usage_key = str(usage or 'standard').strip().lower()
    if usage_key in {'general', 'standard'}:
        return 'desktop'
    if usage_key == 'ai':
        return 'workstation'
    if usage_key in {'gaming', 'desktop', 'creator', 'workstation', 'business'}:
        return usage_key
    return 'desktop'


def _budget_tier_thresholds_for_usage(usage):
    normalized = _normalize_budget_tier_usage(usage)
    return BUDGET_TIER_THRESHOLDS_BY_USAGE.get(normalized, BUDGET_TIER_THRESHOLDS)


def _budget_tier_threshold(usage, tier_key):
    thresholds = _budget_tier_thresholds_for_usage(usage)
    return int(thresholds.get(tier_key, BUDGET_TIER_THRESHOLDS[tier_key]))


def _classify_budget_tier(budget, usage=None):
    thresholds = _budget_tier_thresholds_for_usage(usage)
    if budget <= thresholds['low']:
        return 'low'
    if budget <= thresholds['middle']:
        return 'middle'
    if budget <= thresholds['high']:
        return 'high'
    return 'premium'


def _budget_tier_label_jp(budget_tier):
    return {
        'low': 'ローエンド',
        'middle': 'ミドル',
        'high': 'ハイエンド',
        'premium': 'プレミアム',
    }.get(budget_tier, '不明')


def _normalize_budget_tier_code(budget_tier):
    budget_tier_value = str(budget_tier or '').strip().lower()
    if budget_tier_value in {'low', 'middle', 'high', 'premium'}:
        return budget_tier_value
    return None


def _spec_budget_multiplier_for_usage(usage):
    usage_key = str(usage or '').strip().lower()
    if usage_key == 'gaming':
        return 1.20
    if usage_key == 'creator':
        return 1.18
    if usage_key in {'standard', 'business'}:
        return 1.15
    if usage_key == 'workstation':
        return 1.20
    return 1.10


def _budget_for_tier_display(input_budget, usage, requested_build_priority):
    """
    UI が spec 切替で予算を上乗せして送信する用途では、
    予算帯ラベルをユーザーが選んだティア感覚に合わせるため、
    上乗せ前相当の基準予算で分類する。
    """
    budget_value = int(input_budget or 0)
    if budget_value <= 0:
        return budget_value

    if requested_build_priority == 'spec' and usage in {'general', 'standard', 'business', 'workstation'}:
        multiplier = _spec_budget_multiplier_for_usage(usage)
        if multiplier > 0:
            return int(round(budget_value / multiplier))

    return budget_value


def _is_creator_premium_budget(budget):
    try:
        budget_value = int(budget)
    except (TypeError, ValueError):
        return False
    return _classify_budget_tier(budget_value, usage='creator') == 'premium'


def _get_latest_market_price_range_from_db():
    latest = MarketPriceRangeSnapshot.objects.order_by('-fetched_at', '-id').first()
    if latest:
        return {
            'min': int(latest.market_min),
            'max': int(latest.market_max),
            'default': int(latest.suggested_default),
            'currency': latest.currency or 'JPY',
            'sources': latest.sources or {},
        }

    fallback_default = int((MARKET_TIER_FALLBACK_MIN + MARKET_TIER_FALLBACK_MAX) / 2)
    return {
        'min': int(MARKET_TIER_FALLBACK_MIN),
        'max': int(MARKET_TIER_FALLBACK_MAX),
        'default': fallback_default,
        'currency': 'JPY',
        'sources': {},
    }


def _apply_scraped_market_budget_correction(budget, usage, build_priority='balanced', market_range=None):
    # スクレイピング相場（min〜max）を基準に予算を補正する。
    # ただしユーザーが明示的に低予算を選んだ場合は尊重し、上方補正は行わない。
    if market_range is None:
        market_range = _get_latest_market_price_range_from_db()

    source_stats = (market_range.get('sources') or {}).get('dospara_tc30_market') or {}
    source_count = int(source_stats.get('count') or 0)
    market_min = int(market_range.get('min') or 0)
    market_max = int(market_range.get('max') or 0)

    # スクレイピング失敗時のフォールバック値（100,000〜400,000）で過補正しない。
    if source_count <= 0:
        return budget, False, None

    if market_min <= 0 or market_max <= 0 or market_max <= market_min:
        return budget, False, None

    # min〜maxを4等分して low/middle/high/premium を動的分類する。
    span = market_max - market_min
    q1 = market_min + int(span * 0.25)
    q2 = market_min + int(span * 0.50)
    q3 = market_min + int(span * 0.75)

    requested = int(budget)
    corrected_budget = max(market_min, min(requested, market_max))

    # 低予算選択を尊重し、相場下限への強制引き上げは行わない。
    if corrected_budget > requested:
        return requested, False, None

    if corrected_budget == requested:
        return budget, False, None

    if corrected_budget <= q1:
        tier_label = 'low'
    elif corrected_budget <= q2:
        tier_label = 'middle'
    elif corrected_budget <= q3:
        tier_label = 'high'
    else:
        tier_label = 'premium'

    action_label = '引き上げ' if corrected_budget > requested else '引き下げ'
    note = (
        f"予算を補正しました。相場データ（{tier_label}帯）に基づき、"
        f"予算を¥{corrected_budget:,}へ{action_label}ました。"
    )
    return corrected_budget, True, note


def _classify_budget_tier_by_min_max(budget, market_min, market_max):
    if market_min <= 0 or market_max <= market_min:
        return _classify_budget_tier(budget, usage='gaming')

    span = market_max - market_min
    q1 = market_min + int(span * 0.25)
    q2 = market_min + int(span * 0.50)
    q3 = market_min + int(span * 0.75)
    value = int(budget)

    if value <= q1:
        return 'low'
    if value <= q2:
        return 'middle'
    if value <= q3:
        return 'high'
    return 'premium'


def _classify_budget_tier_from_market_range(budget, market_range=None):
    if market_range is None:
        market_range = _get_latest_market_price_range_from_db()

    source_stats = (market_range.get('sources') or {}).get('dospara_tc30_market') or {}
    source_count = int(source_stats.get('count') or 0)
    market_min = int(market_range.get('min') or 0)
    market_max = int(market_range.get('max') or 0)

    if source_count <= 0:
        return _classify_budget_tier_by_min_max(
            budget,
            MARKET_TIER_FALLBACK_MIN,
            MARKET_TIER_FALLBACK_MAX,
        )

    # スクレイピングレンジが狭すぎる/低すぎる場合は、検証済みの下限レンジで正規化する。
    normalized_min = max(int(market_min), MARKET_TIER_FALLBACK_MIN)
    normalized_max = max(int(market_max), MARKET_TIER_FALLBACK_MAX)

    return _classify_budget_tier_by_min_max(budget, normalized_min, normalized_max)


def _select_gaming_x3d_cpu_by_budget_tier(budget, usage='gaming', build_priority='balanced', market_range=None):
    if usage == 'gaming' and build_priority == 'cost':
        budget_tier = _classify_budget_tier_from_market_range(budget, market_range=market_range)
    else:
        budget_tier = _classify_budget_tier(budget, usage='gaming')
    candidates = [
        part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
        if _is_part_suitable('cpu', part)
        and _is_gaming_cpu_x3d_preferred(part)
    ]
    if not candidates:
        return None

    # gaming + spec + premium は 9850X3D を選ぶ
    if budget_tier == 'premium' and build_priority == 'spec':
        if int(budget or 0) <= GAMING_SPEC_PREMIUM_SOFT_CAP_BUDGET:
            preferred_9800 = [
                part for part in candidates
                if '9800x3d' in str(getattr(part, 'name', '') or '').lower()
            ]
            if preferred_9800:
                return sorted(preferred_9800, key=lambda p: p.price)[0]
        premium_candidates = [
            part for part in candidates
            if '9850x3d' in str(getattr(part, 'name', '') or '').lower()
        ]
        if premium_candidates:
            return sorted(premium_candidates, key=lambda p: p.price)[0]
        # 9850X3D がない場合は他のX3Dから選ぶ
        return sorted(candidates, key=lambda p: p.price)[0]

    # gaming + cost は non-premium 帯で 9850X3D を除外
    non_premium_candidates = [
        part for part in candidates
        if '9850x3d' not in str(getattr(part, 'name', '') or '').lower()
    ]
    
    if budget_tier == 'premium':
        premium_candidates = [
            part for part in candidates
            if '9850x3d' in str(getattr(part, 'name', '') or '').lower()
        ]
        if premium_candidates:
            return sorted(premium_candidates, key=lambda p: p.price)[0]
        if not non_premium_candidates:
            return None
        preferred_9800 = [
            part for part in non_premium_candidates
            if '9800x3d' in str(getattr(part, 'name', '') or '').lower()
        ]
        if preferred_9800:
            return sorted(preferred_9800, key=lambda p: p.price)[0]
        return sorted(non_premium_candidates, key=lambda p: p.price)[0]
    
    if not non_premium_candidates:
        return None

    if budget_tier in ('middle', 'high'):
        preferred_9800 = [
            part for part in non_premium_candidates
            if '9800x3d' in str(getattr(part, 'name', '') or '').lower()
        ]
        if preferred_9800:
            return sorted(preferred_9800, key=lambda p: p.price)[0]

    return sorted(non_premium_candidates, key=lambda p: p.price)[0]


def _enforce_gaming_x3d_cpu_by_budget_tier(selected_parts, selected, budget, usage, build_priority='balanced', options=None):
    options = options or {}
    market_range = options.get('market_price_range')
    if usage != 'gaming':
        return selected_parts, selected, False

    target_cpu = _select_gaming_x3d_cpu_by_budget_tier(
        budget,
        usage=usage,
        build_priority=build_priority,
        market_range=market_range,
    )

    if not target_cpu and usage == 'gaming' and build_priority == 'cost':
        budget_tier = _classify_budget_tier_from_market_range(budget, market_range=market_range)
        current_cpu = selected_parts.get('cpu')
        current_name = str(getattr(current_cpu, 'name', '') or '').lower()
        if budget_tier != 'premium' and '9850x3d' in current_name:
            downgrade_candidates = [
                part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
                if _is_part_suitable('cpu', part)
                and '9850x3d' not in str(getattr(part, 'name', '') or '').lower()
                and ('ryzen' in str(getattr(part, 'name', '') or '').lower() or 'amd' in str(getattr(part, 'name', '') or '').lower())
            ]
            target_cpu = _pick_amd_gaming_cpu(downgrade_candidates, 'cost', require_x3d=False)

    if not target_cpu:
        return selected_parts, selected, False

    current_cpu = selected_parts.get('cpu')
    if current_cpu and int(getattr(current_cpu, 'id', 0) or 0) == int(getattr(target_cpu, 'id', 0) or 0):
        return selected_parts, selected, False

    updated_parts = dict(selected_parts)
    updated_parts['cpu'] = target_cpu

    updated_selected = list(selected)
    replaced = False
    for item in updated_selected:
        if item.get('category') == 'cpu':
            item['name'] = target_cpu.name
            item['price'] = target_cpu.price
            item['url'] = target_cpu.url
            item['specs'] = target_cpu.specs
            replaced = True
            break

    if not replaced:
        updated_selected.append({
            'category': 'cpu',
            'name': target_cpu.name,
            'price': target_cpu.price,
            'url': target_cpu.url,
            'specs': target_cpu.specs,
        })

    return updated_parts, updated_selected, True


def _enforce_non_premium_gaming_cost_cpu_guard(selected_parts, budget, usage, build_priority='balanced', options=None):
    options = options or {}
    if usage != 'gaming' or build_priority != 'cost':
        return selected_parts, False

    budget_tier = _classify_budget_tier_from_market_range(
        budget,
        market_range=options.get('market_price_range'),
    )
    if budget_tier == 'premium':
        return selected_parts, False

    # gaming+cost の non-premium 帯では 9850X3D を除外
    current_cpu = selected_parts.get('cpu')
    current_name = str(getattr(current_cpu, 'name', '') or '').lower()
    if '9850x3d' not in current_name:
        return selected_parts, False

    cpu_pool = [
        part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
        if _is_part_suitable('cpu', part)
        and _matches_selection_options('cpu', part, options=options)
        and '9850x3d' not in str(getattr(part, 'name', '') or '').lower()
        and ('ryzen' in str(getattr(part, 'name', '') or '').lower() or 'amd' in str(getattr(part, 'name', '') or '').lower())
    ]
    if not cpu_pool:
        cpu_pool = [
            part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
            if _is_part_suitable('cpu', part)
            and '9850x3d' not in str(getattr(part, 'name', '') or '').lower()
        ]
    if not cpu_pool:
        return selected_parts, False

    replacement = None
    if int(budget) < GAMING_PREMIUM_BUDGET_MIN:
        preferred_9800 = [
            part for part in cpu_pool
            if '9800x3d' in str(getattr(part, 'name', '') or '').lower()
        ]
        if preferred_9800:
            replacement = sorted(preferred_9800, key=lambda p: p.price)[0]

    if not replacement:
        replacement = _pick_amd_gaming_cpu(cpu_pool, 'cost', require_x3d=False) or cpu_pool[0]

    if not replacement:
        return selected_parts, False

    updated = dict(selected_parts)
    updated['cpu'] = replacement
    updated = _resolve_compatibility(updated, usage, options=options)
    return updated, True


def _part_price_band(part_type, budget, usage):
    usage_bands = PART_PRICE_BANDS_BY_USAGE_TIER.get(part_type, {}).get(usage)
    if not usage_bands:
        return None

    budget_tier = _classify_budget_tier(budget, usage=usage)
    ratio_range = usage_bands.get(budget_tier)
    if not ratio_range:
        return None

    min_ratio, max_ratio = ratio_range
    return int(budget * min_ratio), int(budget * max_ratio)


def _filter_candidates_by_part_price_band(candidates, part_type, budget, usage):
    if not candidates:
        return candidates

    budget_tier = _classify_budget_tier(budget, usage=usage)
    if budget_tier in ('low', 'middle'):
        return candidates

    price_band = _part_price_band(part_type, budget, usage)
    if not price_band:
        return candidates

    min_price, max_price = price_band
    in_band = [p for p in candidates if min_price <= p.price <= max_price]
    if in_band:
        return in_band

    at_or_above_floor = [p for p in candidates if p.price >= min_price]
    if at_or_above_floor:
        return at_or_above_floor

    return candidates


def _infer_rx_model_and_variant(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    model_match = re.search(r'\brx\s*(\d{4})', text)
    if not model_match:
        return None, ''

    model = model_match.group(1)
    variant = 'xt' if re.search(r'\brx\s*\d{4}\s*xt\b|\brx\d{4}xt\b', text) else 'base'
    return model, variant


def _prefer_rx_xt_value_candidates(candidates):
    if not candidates:
        return candidates

    cheapest_xt_by_model = {}
    for part in candidates:
        model, variant = _infer_rx_model_and_variant(part)
        if not model or variant != 'xt':
            continue
        cheapest = cheapest_xt_by_model.get(model)
        if cheapest is None or part.price < cheapest.price:
            cheapest_xt_by_model[model] = part

    filtered = []
    for part in candidates:
        model, variant = _infer_rx_model_and_variant(part)
        if not model or variant != 'base':
            filtered.append(part)
            continue

        xt = cheapest_xt_by_model.get(model)
        if xt and xt.price <= part.price:
            # 同型番でXTが同価格以下なら、価値の低い非XTは候補から外す。
            continue
        filtered.append(part)

    return filtered or candidates


def _is_gaming_spec_gpu_preferred(part, minimum_tier=1):
    if _gaming_spec_gpu_tier(part) >= minimum_tier:
        return True

    return False


def _infer_gaming_gpu_perf_score(part):
    cached = getattr(part, '_cached_gaming_gpu_perf_score', None)
    if cached is not None:
        return cached

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    bonus = _infer_optional_gpu_perf_bonus(part)
    score_rules = (
        (r'rtx\s*5090', 1200),
        (r'rtx\s*5080', 1050),
        (r'rtx\s*5070\s*ti', 900),
        (r'rx\s*9070\s*xt|rx9070xt', 860),
        (r'rtx\s*5070', 820),
        (r'rx\s*9070(?!\s*xt)|rx9070(?!xt)', 780),
        (r'rtx\s*5060\s*ti', 730),
        (r'rx\s*9060\s*xt|rx9060xt', 720),
        (r'rtx\s*5060', 660),
        (r'rtx\s*5050', 620),
        (r'rx\s*7600|rx7600', 610),
        (r'rtx\s*3050', 420),
        (r'rx\s*6400|rx6400', 360),
    )
    for pattern, score in score_rules:
        if re.search(pattern, text):
            result = score + bonus
            setattr(part, '_cached_gaming_gpu_perf_score', result)
            return result
    result = 500 + bonus
    setattr(part, '_cached_gaming_gpu_perf_score', result)
    return result


def _infer_optional_gpu_perf_bonus(part):
    """Optional bonus from imported specs['gpu_perf_score'] without schema change."""
    raw_score = _get_gpu_perf_score_from_snapshot(part)
    legacy_specs_fallback_enabled = bool(getattr(settings, 'GPU_PERF_ENABLE_LEGACY_SPECS_FALLBACK', True))
    if raw_score is None and legacy_specs_fallback_enabled:
        specs = getattr(part, 'specs', {}) or {}
        raw_score = specs.get('gpu_perf_score')
    if raw_score is None:
        return 0
    try:
        score_value = float(raw_score)
    except (TypeError, ValueError):
        return 0
    if score_value <= 0:
        return 0

    # Dospara score scale (~100-5300) -> bounded additive bonus (0-180).
    return min(180, int(score_value / 30))


def _infer_gpu_perf_score_for_requirement(part):
    cached = getattr(part, '_cached_gpu_perf_score_for_requirement', None)
    if cached is not None:
        return cached

    score = _get_gpu_perf_score_from_snapshot(part)
    legacy_specs_fallback_enabled = bool(getattr(settings, 'GPU_PERF_ENABLE_LEGACY_SPECS_FALLBACK', True))
    if score is None and legacy_specs_fallback_enabled:
        specs = getattr(part, 'specs', {}) or {}
        score = specs.get('gpu_perf_score')
    try:
        result = int(float(score)) if score is not None else 0
    except (TypeError, ValueError):
        result = 0
    setattr(part, '_cached_gpu_perf_score_for_requirement', result)
    return result


def _extract_gpu_model_key_for_perf(text):
    normalized = re.sub(r'\s+', ' ', (text or '').upper()).strip()
    patterns = [
        r'RTX\s*\d{4}\s*TI\s*SUPER',
        r'RTX\s*\d{4}\s*SUPER',
        r'RTX\s*\d{4}\s*TI',
        r'RTX\s*\d{4}',
        r'GTX\s*\d{3,4}\s*TI',
        r'GTX\s*\d{3,4}',
        r'GT\s*\d{3,4}',
        r'RX\s*\d{4}\s*XTX',
        r'RX\s*\d{4}\s*XT',
        r'RX\s*\d{4}\s*GRE',
        r'RX\s*\d{4}',
        r'INTEL\s+ARC\s+[AB]\d{3,4}',
        r'ARC\s+[AB]\d{3,4}',
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return re.sub(r'\s+', ' ', match.group(0)).strip()
    return None


def _normalize_gpu_model_key(value):
    return re.sub(r'[^A-Z0-9]+', '', (value or '').upper())


def _load_latest_gpu_perf_scores(cache_ttl_seconds=600):
    now = time.time()
    if (
        _GPU_PERF_CACHE.get('snapshot_key') is not None
        and (now - float(_GPU_PERF_CACHE.get('loaded_at', 0.0))) < cache_ttl_seconds
    ):
        return _GPU_PERF_CACHE['scores']

    try:
        latest = GPUPerformanceSnapshot.objects.order_by('-fetched_at', '-id').first()
    except Exception:
        return _GPU_PERF_CACHE.get('scores', {})
    if latest is None:
        _GPU_PERF_CACHE['snapshot_key'] = None
        _GPU_PERF_CACHE['scores'] = {}
        _GPU_PERF_CACHE['loaded_at'] = now
        return {}

    snapshot_key = (latest.id, latest.fetched_at)
    if _GPU_PERF_CACHE['snapshot_key'] == snapshot_key:
        _GPU_PERF_CACHE['loaded_at'] = now
        return _GPU_PERF_CACHE['scores']

    rows = GPUPerformanceEntry.objects.filter(snapshot=latest, is_laptop=False).values(
        'model_key', 'vram_gb', 'perf_score'
    )
    scores = {}
    for row in rows:
        model_key = row.get('model_key')
        if not model_key:
            continue
        key = (_normalize_gpu_model_key(model_key), row.get('vram_gb'))
        scores[key] = max(scores.get(key, 0), int(row.get('perf_score') or 0))

    _GPU_PERF_CACHE['snapshot_key'] = snapshot_key
    _GPU_PERF_CACHE['scores'] = scores
    _GPU_PERF_CACHE['loaded_at'] = now
    return scores


def _get_gpu_perf_score_from_snapshot(part):
    model_key = _extract_gpu_model_key_for_perf(getattr(part, 'name', ''))
    if not model_key:
        return None
    normalized_model_key = _normalize_gpu_model_key(model_key)

    scores = _load_latest_gpu_perf_scores()
    if not scores:
        return None

    vram_gb = _infer_gpu_memory_gb(part)
    exact = scores.get((normalized_model_key, vram_gb))
    if exact is not None:
        return exact

    # VRAM不一致時は同モデルの最大スコアを採用。
    candidates = [score for (key_model, _), score in scores.items() if key_model == normalized_model_key]
    if candidates:
        return max(candidates)
    return None


def _pick_gaming_spec_gpu(candidates):
    if not candidates:
        return None

    candidates = [p for p in candidates if not _is_gaming_creative_gpu(p)]
    if not candidates:
        return None

    # ゲーミング・スペック重視: 性能優先。
    # 同程度の性能帯では上位OCモデルを取りこぼさないよう高価格側を優先する。
    ranked = _prefer_rx_xt_value_candidates(candidates)
    return sorted(
        ranked,
        key=lambda p: (
            _infer_gaming_gpu_perf_score(p),
            p.price,
        ),
        reverse=True,
    )[0]


def _pick_gaming_low_end_gpu(candidates, budget, usage, build_priority):
    if not candidates:
        return None

    policy = _gaming_low_end_gpu_policy(budget, usage, build_priority)
    if not policy:
        return None

    target_price = int(policy['target_price'])
    price_cap = int(policy['price_cap'])
    
    bounded_candidates = [
        p
        for p in candidates
        if p.price <= price_cap and _is_gaming_gpu_within_priority_cap(p, build_priority, budget=budget)
    ]
    
    if not bounded_candidates:
        return None

    # ローエンド cost では RTX 3050 を積極的に優先（distance-to-target）
    if build_priority == 'cost' and budget < _budget_tier_threshold('gaming', 'low'):
        rtx_3050_candidates = [
            p for p in bounded_candidates 
            if 'rtx 3050' in f"{p.name} {p.url}".lower() and p.price <= price_cap
        ]
        if rtx_3050_candidates:
            # RTX 3050 が複数あれば target_price に最も近いものを選ぶ
            return sorted(
                rtx_3050_candidates,
                key=lambda p: (
                    abs(int(p.price) - target_price),
                    -_infer_gaming_gpu_perf_score(p),
                    p.price,
                ),
            )[0]
    
    return sorted(
        bounded_candidates,
        key=lambda p: (
            abs(int(p.price) - target_price),
            -_infer_gaming_gpu_perf_score(p),
            p.price,
        ),
    )[0]


def _pick_gaming_cost_gpu_for_auto_adjust(candidates, reference_budget):
    if not candidates:
        return None

    try:
        budget = int(reference_budget)
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        budget = 169980

    target_price = int(budget * 0.30)
    price_cap = _gaming_cost_gpu_cap_price(budget)
    floor_price = _gaming_cost_gpu_floor_price(budget)
    bounded_candidates = [
        p for p in candidates
        if p.price <= price_cap
        and p.price >= floor_price
        and not _is_gaming_cost_excluded_gpu(p)
        and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
    ]
    if not bounded_candidates:
        bounded_candidates = [
            p for p in candidates
            if p.price >= floor_price
            and not _is_gaming_cost_excluded_gpu(p)
            and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
        ]
    if not bounded_candidates:
        bounded_candidates = [
            p for p in candidates
            if not _is_gaming_cost_excluded_gpu(p)
            and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
        ] or candidates

    return sorted(
        bounded_candidates,
        key=lambda p: (
            p.price,
            abs(p.price - target_price),
            -_infer_gaming_gpu_perf_score(p),
            p.price,
        ),
    )[0]


def _is_gaming_cost_excluded_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    if 'intel arc' in text:
        return True
    if re.search(r'\barc\s*[ab]\d{3,4}\b', text):
        return True
    return False


def _gaming_cost_cpu_price_cap(_budget):
    return GAMING_COST_CPU_PRICE_CAP


def _minimum_gaming_low_end_gpu_perf_score(_budget, usage):
    # 現行の gaming/cost 選定では、RTX 3050 などの低価格候補を落とさないため
    # 明示的な最低スコア閾値は置かず、従来どおり 0 を返す。
    if usage != 'gaming':
        return 0
    return 0


def _is_gaming_cpu_x3d_preferred(part):
    if not part:
        return False
    text = f"{part.name} {part.url}".lower()
    if 'ryzen' not in text and 'amd' not in text:
        return False
    return GAMING_CPU_X3D_PATTERN.search(text) is not None


def _is_cpu_x3d(part):
    """CPU が X3D モデルかどうかを判定する"""
    if not part:
        return False
    text = f"{part.name} {part.url}".lower()
    return GAMING_CPU_X3D_PATTERN.search(text) is not None


def _is_gaming_excluded_creator_cpu(part):
    if not part:
        return False
    normalized_name = _normalize_cpu_model_query(getattr(part, 'name', ''))
    if not normalized_name:
        return False
    normalized_name = normalized_name.replace('AMD ', '').replace(' BOX', '')
    return any(
        model_name == normalized_name or model_name in normalized_name
        for model_name in GAMING_EXCLUDED_CREATOR_CPU_MODELS
    )


def _extract_cpu_model_key_for_perf(text):
    normalized = re.sub(r'\s+', ' ', (text or '').strip())
    patterns = [
        r'Ryzen\s+[3579]\s+\d{4}[A-Z0-9]*',
        r'Core\s+Ultra\s+[3579]\s+\d{3}[A-Z]*',
        r'Core\s+i[3579]\s*-?\s*\d{4,5}[A-Z]*',
        r'Pentium\s+G\d{3,4}[A-Z]*',
        r'Celeron\s+G\d{3,4}[A-Z]*',
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return re.sub(r'\s+', ' ', match.group(0)).strip()
    return None


def _load_latest_cpu_selection_scores(cache_ttl_seconds=600, force_refresh=False):
    now = time.time()
    if not force_refresh and (now - _CPU_SELECTION_CACHE['loaded_at']) < cache_ttl_seconds:
        return _CPU_SELECTION_CACHE['scores'], _CPU_SELECTION_CACHE['entries']

    if not force_refresh:
        snapshot, db_scores, db_entries = _load_latest_cpu_selection_scores_from_db()
        if snapshot and db_entries:
            snapshot_age_seconds = (timezone.now() - snapshot.fetched_at).total_seconds()
            if snapshot_age_seconds < cache_ttl_seconds:
                _CPU_SELECTION_CACHE['loaded_at'] = now
                _CPU_SELECTION_CACHE['scores'] = db_scores
                _CPU_SELECTION_CACHE['entries'] = db_entries
                return db_scores, db_entries

    # 構成生成のレスポンス遅延を避けるため、ここでは外部スクレイプを行わず
    # DBスナップショットが無い場合は空結果を短期キャッシュする。
    snapshot, db_scores, db_entries = _load_latest_cpu_selection_scores_from_db()
    if snapshot and db_entries:
        _CPU_SELECTION_CACHE['loaded_at'] = now
        _CPU_SELECTION_CACHE['scores'] = db_scores
        _CPU_SELECTION_CACHE['entries'] = db_entries
        return db_scores, db_entries

    _CPU_SELECTION_CACHE['loaded_at'] = now
    _CPU_SELECTION_CACHE['scores'] = {}
    _CPU_SELECTION_CACHE['entries'] = []
    return {}, []


def _get_cpu_perf_score(part):
    """CPU の Dospara 性能目安表スコア（39/52など）を取得する。"""
    if not part:
        return None

    cpu_score_cached = getattr(part, '_cached_cpu_perf_score', None)
    if cpu_score_cached is not None:
        return cpu_score_cached

    # 互換性のため、specs 側に score がある場合は優先。
    try:
        score = _get_spec(part, 'cpu_perf_score', None)
        if score is None:
            raise ValueError
        parsed = int(float(score))
        if parsed > 0:
            setattr(part, '_cached_cpu_perf_score', parsed)
            return parsed
    except (TypeError, ValueError):
        pass

    scores, entries = _load_latest_cpu_selection_scores()
    if not scores:
        return None

    candidate_keys = []
    raw_name = getattr(part, 'name', '')
    extracted = _extract_cpu_model_key_for_perf(raw_name)
    if extracted:
        candidate_keys.append(_normalize_cpu_model_query(extracted))
    candidate_keys.append(_normalize_cpu_model_query(raw_name))

    for key in candidate_keys:
        matched_score = scores.get(key)
        if matched_score is not None:
            setattr(part, '_cached_cpu_perf_score', matched_score)
            return matched_score

    for key in candidate_keys:
        matched = _match_cpu_model_entry(entries, key)
        if matched:
            matched_name = _normalize_cpu_model_query(matched.get('model_name'))
            matched_score = scores.get(matched_name)
            if matched_score is not None:
                setattr(part, '_cached_cpu_perf_score', matched_score)
                return matched_score
    setattr(part, '_cached_cpu_perf_score', 0)
    return None


def _ai_cpu_selection_key(part, build_priority='spec'):
    perf_score = _get_cpu_perf_score(part) or 0
    price = max(int(getattr(part, 'price', 0) or 0), 1)

    if build_priority == 'cost':
        return (
            perf_score / price,
            perf_score,
            -price,
        )

    return (
        perf_score,
        -price,
    )


def _minimum_ai_cpu_perf_score(budget, build_priority='cost', usage='ai'):
    try:
        budget_value = int(budget or 0)
    except (TypeError, ValueError):
        budget_value = 0

    tier = _classify_budget_tier(budget_value, usage=usage)
    if build_priority == 'spec':
        if tier == 'premium':
            return 11000
        if tier == 'high':
            return 9500
        if tier == 'middle':
            return 8000
        return 0

    if tier == 'premium':
        return 9900
    if tier == 'high':
        return 8600
    if tier == 'middle':
        return 7000
    return 0


def _pick_ai_cpu_candidate(candidates, build_priority='spec', budget=0, usage='ai', selected_budget_tier=None):
    if not candidates:
        return None

    tier = _normalize_budget_tier_code(selected_budget_tier) or _classify_budget_tier(int(budget or 0), usage=usage)

    # ─── workstation 専用ロジック ───────────────────────────────────────────
    if usage == 'workstation':
        # workstation では Ryzen 5 / Ryzen 3 は除外（Ryzen 7/9 以上を要求）
        # Threadripper / EPYC は _is_workstation_cpu で別途扱うのでここでは除外しない
        ryzen_higher = [
            p for p in candidates
            if not re.search(r'ryzen\s*[35]\s*\d', f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower())
        ]
        if ryzen_higher:
            candidates = ryzen_higher

        if tier == 'low':
            ryzen_9700_pool = [p for p in candidates if _is_workstation_ryzen_9700_class(p)]
            if ryzen_9700_pool:
                candidates = ryzen_9700_pool
        elif tier == 'middle':
            ryzen_9800_pool = [p for p in candidates if _is_workstation_ryzen_9800_class(p)]
            if ryzen_9800_pool:
                candidates = ryzen_9800_pool

        # premium: cost → 9950X3D2 優先 / spec → Threadripper 9000系 優先
        if tier == 'premium':
            if build_priority == 'cost':
                x3d2_pool = [p for p in candidates if _is_workstation_ryzen_9950x3d2(p)]
                if x3d2_pool:
                    return sorted(x3d2_pool, key=lambda p: int(getattr(p, 'price', 0) or 0))[0]
            else:  # spec
                tr9k_pool = [p for p in candidates if _is_workstation_threadripper_9000(p)]
                if tr9k_pool:
                    return _pick_workstation_cpu(tr9k_pool, build_priority='spec')

        if tier == 'high':
            if build_priority == 'spec':
                x3d_pool = [p for p in candidates if _is_workstation_ryzen_9950x3d(p)]
                if x3d_pool:
                    return max(x3d_pool, key=lambda part: ((_get_cpu_perf_score(part) or 0), -int(getattr(part, 'price', 0) or 0)))
                candidates = [p for p in candidates if not _is_workstation_cpu(p)] or candidates
                mainstream_high_end = [p for p in candidates if _is_workstation_mainstream_high_end_cpu(p)]
                if mainstream_high_end:
                    candidates = mainstream_high_end
            else:
                candidates = [p for p in candidates if not _is_workstation_cpu(p)] or candidates
                mainstream_9950 = [
                    p for p in candidates
                    if re.search(r'ryzen\s*9\s*9950(?:x3d2|x3d|x)?\b', f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower())
                ]
                if mainstream_9950:
                    candidates = mainstream_9950
                mainstream_high_end = [p for p in candidates if _is_workstation_mainstream_high_end_cpu(p)]
                if mainstream_high_end:
                    candidates = mainstream_high_end

        # premium cost と middle/low cost では Threadripper を除外する。
        if tier in {'low', 'middle', 'premium'} and build_priority == 'cost':
            candidates = [p for p in candidates if not _is_workstation_cpu(p)] or candidates

        # middle 以下はキャップで上振れを抑える
        if build_priority == 'cost':
            price_cap = None
            if tier == 'low':
                price_cap = max(109800, int(int(budget or 0) * 0.30))
            elif tier == 'middle':
                price_cap = max(149800, int(int(budget or 0) * 0.33))
            if price_cap is not None:
                capped = [p for p in candidates if int(getattr(p, 'price', 0) or 0) <= price_cap]
                if capped:
                    candidates = capped

    # ─── ai 用途（従来ロジック）────────────────────────────────────────────
    else:
        workstation_candidates = [p for p in candidates if _is_workstation_cpu(p)]
        if workstation_candidates and tier in {'high', 'premium'}:
            picked = _pick_workstation_cpu(workstation_candidates, build_priority=build_priority)
            if picked:
                return picked

    minimum_perf = _minimum_ai_cpu_perf_score(budget, build_priority=build_priority, usage=usage)
    if minimum_perf > 0:
        floored_candidates = [p for p in candidates if (_get_cpu_perf_score(p) or 0) >= minimum_perf]
        if floored_candidates:
            candidates = floored_candidates

    # premium 帯は性能最優先
    if tier == 'premium':
        return max(candidates, key=lambda part: ((_get_cpu_perf_score(part) or 0), -int(getattr(part, 'price', 0) or 0)))

    return max(candidates, key=lambda part: _ai_cpu_selection_key(part, build_priority=build_priority))


def _pick_ai_premium_gpu_candidate(candidates, build_priority='cost'):
    if not candidates:
        return None

    if build_priority == 'spec':
        # 優先チェーン: RTX PRO 4500 / RTX 4500 Ada → RTX PRO 5000 → RTX PRO 4000 → RTX PRO 2000 → best available
        def _is_rtx_pro5000(part):
            text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
            return bool(re.search(r'rtx\s*pro\s*5000\b', text))

        def _is_rtx_pro4000(part):
            text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
            return bool(re.search(r'rtx\s*pro\s*4000\b', text))

        def _is_rtx_pro2000(part):
            text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
            return bool(re.search(r'rtx\s*pro\s*2000\b', text))

        sort_key = lambda p: (_infer_gpu_memory_gb(p), _infer_gaming_gpu_perf_score(p), -p.price)
        for check in [_is_creator_rtxpro4500_gpu, _is_rtx_pro5000, _is_rtx_pro4000, _is_rtx_pro2000]:
            pool = [p for p in candidates if check(p)]
            if pool:
                return sorted(pool, key=sort_key, reverse=True)[0]
        # フォールバック: VRAM容量・性能スコアが最大のものを返す
        return sorted(candidates, key=sort_key, reverse=True)[0]

    if build_priority == 'cost':
        exact_r9700 = [p for p in candidates if _is_creator_r9700_gpu(p)]
        if exact_r9700:
            return sorted(exact_r9700, key=lambda p: p.price)[0]

    return None


def _minimum_gaming_cpu_perf_score(usage):
    """gaming 用途での CPU 最小性能目安スコア（性能目安表基準）を返す"""
    if usage == 'gaming':
        return 3000
    return 0


def _response_has_gaming_x3d_cpu(response_data):
    if not isinstance(response_data, dict):
        return False
    parts = response_data.get('parts') or []
    for part in parts:
        if part.get('category') != 'cpu':
            continue
        text = f"{part.get('name', '')} {part.get('url', '')}".lower()
        return GAMING_CPU_X3D_PATTERN.search(text) is not None
    return False


def _has_same_configuration_signature(latest_config, usage, budget, selected_parts, extra_storage_parts, use_igpu, case_fan_part=None):
    if not latest_config:
        return False
    if latest_config.usage != usage or int(latest_config.budget) != int(budget):
        return False

    expected_gpu = None if use_igpu else selected_parts.get('gpu')
    return (
        latest_config.cpu_id == (selected_parts.get('cpu').id if selected_parts.get('cpu') else None)
        and latest_config.cpu_cooler_id == (selected_parts.get('cpu_cooler').id if selected_parts.get('cpu_cooler') else None)
        and latest_config.gpu_id == (expected_gpu.id if expected_gpu else None)
        and latest_config.motherboard_id == (selected_parts.get('motherboard').id if selected_parts.get('motherboard') else None)
        and latest_config.memory_id == (selected_parts.get('memory').id if selected_parts.get('memory') else None)
        and latest_config.storage_id == (selected_parts.get('storage').id if selected_parts.get('storage') else None)
        and latest_config.storage2_id == (extra_storage_parts.get('storage2').id if extra_storage_parts.get('storage2') else None)
        and latest_config.storage3_id == (extra_storage_parts.get('storage3').id if extra_storage_parts.get('storage3') else None)
        and latest_config.os_id == (selected_parts.get('os').id if selected_parts.get('os') else None)
        and latest_config.psu_id == (selected_parts.get('psu').id if selected_parts.get('psu') else None)
        and latest_config.case_id == (selected_parts.get('case').id if selected_parts.get('case') else None)
        and latest_config.case_fan_id == (case_fan_part.id if case_fan_part else None)
    )


def _recommend_min_budget_for_gaming_x3d(
    current_budget,
    usage,
    cooler_type='any',
    radiator_size='any',
    cooling_profile='balanced',
    case_size='any',
    case_fan_policy='auto',
    cpu_vendor='any',
    build_priority='balanced',
    storage_preference='ssd',
    storage2_part_id=None,
    storage3_part_id=None,
    os_edition='auto',
    custom_budget_weights=None,
    min_storage_capacity_gb=None,
    max_motherboard_chipset='any',
):
    if usage != 'gaming':
        return None

    start_budget = max(50000, int(current_budget or 0))
    step = 5000
    rounded_start = ((start_budget + step - 1) // step) * step

    for probe_budget in range(rounded_start, 1500000 + 1, step):
        simulated, sim_error = build_configuration_response(
            probe_budget,
            usage,
            cooler_type,
            radiator_size,
            cooling_profile,
            case_size,
            case_fan_policy,
            cpu_vendor,
            build_priority,
            storage_preference,
            storage2_part_id,
            storage3_part_id,
            os_edition,
            custom_budget_weights,
            min_storage_capacity_gb,
            max_motherboard_chipset,
            enforce_gaming_x3d=False,
            persist=False,
        )
        if sim_error:
            continue
        if _response_has_gaming_x3d_cpu(simulated):
            return probe_budget

    return None


def _recommend_min_budget_for_gaming_x3d_from_low_end_config(
    selected_parts,
    current_budget,
    usage,
):
    if usage != 'gaming' or not selected_parts:
        return None

    current_cpu = selected_parts.get('cpu')
    if not current_cpu:
        return None

    current_total = _sum_selected_price(selected_parts)
    baseline_budget = max(int(current_budget or 0), current_total)

    current_cpu_price = int(getattr(current_cpu, 'price', 0) or 0)
    current_socket = _get_spec(current_cpu, 'socket')
    current_motherboard = selected_parts.get('motherboard')
    current_motherboard_socket = _infer_motherboard_socket(current_motherboard) if current_motherboard else ''
    current_memory = selected_parts.get('memory')
    current_memory_type = _infer_memory_type(current_memory) or _infer_motherboard_memory_type(current_motherboard)

    x3d_candidates = [p for p in PCPart.objects.filter(part_type='cpu').order_by('price') if _is_gaming_cpu_x3d_preferred(p)]
    if not x3d_candidates:
        return None

    if current_socket:
        socket_matched = [p for p in x3d_candidates if str(_get_spec(p, 'socket', '') or '').upper() == str(current_socket).upper()]
        if socket_matched:
            x3d_candidates = socket_matched

    for cpu in x3d_candidates:
        cpu_socket = str(_get_spec(cpu, 'socket', '') or current_socket or '').upper()
        if current_motherboard_socket and cpu_socket and current_motherboard_socket == cpu_socket:
            estimated_budget = baseline_budget - current_cpu_price + int(cpu.price)
            return int(((max(estimated_budget, baseline_budget) + 4999) // 5000) * 5000)

        compatible_motherboards = [
            part for part in PCPart.objects.filter(part_type='motherboard').order_by('price')
            if _is_part_suitable('motherboard', part) and _infer_motherboard_socket(part) == cpu_socket
        ]
        if not compatible_motherboards:
            continue

        motherboard = compatible_motherboards[0]
        estimated_budget = baseline_budget - current_cpu_price + int(cpu.price)
        if current_motherboard:
            estimated_budget -= int(current_motherboard.price)
        estimated_budget += int(motherboard.price)

        motherboard_memory_type = _infer_motherboard_memory_type(motherboard)
        if current_memory and current_memory_type and motherboard_memory_type and current_memory_type != motherboard_memory_type:
            memory_candidates = [
                part for part in PCPart.objects.filter(part_type='memory').order_by('price')
                if _is_part_suitable('memory', part) and _infer_memory_type(part) == motherboard_memory_type
            ]
            if not memory_candidates:
                continue

            replacement_memory = memory_candidates[0]
            estimated_budget -= int(current_memory.price)
            estimated_budget += int(replacement_memory.price)

        return int(((max(estimated_budget, baseline_budget) + 4999) // 5000) * 5000)

    return None


def _extract_cpu_core_threads(part):
    """CPU の総スレッド数(コア数 × 2 相当)を抽出する。未入力の場合は 0"""
    if not part:
        return 0
    try:
        core_count = int(_get_spec(part, 'core_count', 0) or 0)
        thread_count = int(_get_spec(part, 'thread_count', 0) or 0)
    except (TypeError, ValueError):
        core_count = 0
        thread_count = 0
    # スレッド数が優先、なければコア数×2の推定を使う
    if thread_count > 0:
        return thread_count
    if core_count > 0:
        return core_count * 2

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    # 例: 8コア16スレッド / 8C16T / 8c/16t
    jp_match = re.search(r'(\d+)\s*コア\s*(\d+)\s*スレッド', text)
    if jp_match:
        return int(jp_match.group(2))

    compact_match = re.search(r'(\d+)\s*c\s*[/x]?\s*(\d+)\s*t', text)
    if compact_match:
        return int(compact_match.group(2))

    thread_only = re.search(r'(\d+)\s*threads?', text)
    if thread_only:
        return int(thread_only.group(1))

    inferred_threads = _infer_cpu_core_threads_from_name(text)
    if inferred_threads > 0:
        return inferred_threads

    return 0


def _extract_cpu_core_count(part):
    """CPU のコア数を抽出する。未入力・変換不可は 0"""
    if not part:
        return 0
    try:
        core_count = int(_get_spec(part, 'core_count', 0) or 0)
    except (TypeError, ValueError):
        core_count = 0

    if core_count > 0:
        return core_count

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    jp_match = re.search(r'(\d+)\s*コア', text)
    if jp_match:
        return int(jp_match.group(1))

    en_match = re.search(r'(\d+)\s*[- ]?cores?', text)
    if en_match:
        return int(en_match.group(1))

    compact_match = re.search(r'(\d+)\s*c\s*[/x]?\s*(\d+)\s*t', text)
    if compact_match:
        return int(compact_match.group(1))

    inferred_cores = _infer_cpu_core_count_from_name(text)
    if inferred_cores > 0:
        return inferred_cores

    return 0


def _infer_cpu_core_count_from_name(text):
    normalized = str(text or '').lower()

    amd_exact_core_map = {
        '2200g': 4,
        '3200g': 4,
        '3400g': 4,
        '4300g': 4,
        '5300g': 4,
        '5500': 6,
        '5600': 6,
        '5600g': 6,
        '5600x': 6,
        '5700g': 8,
        '5700x': 8,
        '5800x': 8,
        '5800x3d': 8,
        '7600': 6,
        '7600x': 6,
        '7500f': 6,
        '7700': 8,
        '7800x3d': 8,
        '7900': 12,
        '7900x': 12,
        '7950': 16,
        '7950x': 16,
        '9700x': 8,
        '9800x3d': 8,
        '9900x': 12,
        '9950x': 16,
    }
    for key, cores in amd_exact_core_map.items():
        if key in normalized:
            return cores

    intel_core_map = {
        'i3': 4,
        'i5': 6,
        'i7': 8,
        'i9': 12,
        'ultra 5': 6,
        'ultra 7': 8,
        'ultra 9': 12,
    }
    for key, cores in intel_core_map.items():
        if key in normalized:
            return cores

    return 0


def _infer_cpu_core_threads_from_name(text):
    normalized = str(text or '').lower()
    cores = _infer_cpu_core_count_from_name(normalized)
    if cores <= 0:
        return 0
    if cores <= 4:
        return cores * 2
    if cores <= 6:
        return cores * 2
    if cores <= 8:
        return cores * 2
    if cores <= 12:
        return cores * 2
    return cores * 2


def _cpu_meets_creator_minimum(part, min_cores=8, min_threads=16):
    if not part:
        return False
    if _is_workstation_cpu(part):
        return True
    cores = _extract_cpu_core_count(part)
    threads = _extract_cpu_core_threads(part)
    return cores >= min_cores and threads >= min_threads


def _is_high_heat_cpu(part):
    """高発熱CPU を判定する（TDP >= 140W またはスペック情報から推定）"""
    if not part:
        return False
    try:
        tdp_w = int(_get_spec(part, 'tdp_w', 0) or 0)
    except (TypeError, ValueError):
        tdp_w = 0
    # TDP >= 140W の場合、または X3D CPU（通常発熱が高い）
    return tdp_w >= 140 or _is_cpu_x3d(part)


def _is_liquid_cooler(part):
    """液冷クーラーかどうかを判定する"""
    if not part:
        return False
    text = f"{part.name} {part.url}".lower()
    return any(kw in text for kw in ['liquid', 'aio', '水冷', 'cooler master ml', 'asus rog strix'])


def _is_dual_tower_cooler(part):
    """ツインタワー空冷クーラーかどうかを判定する"""
    if not part:
        return False
    text = f"{part.name} {part.url}".lower()
    dual_tower_keywords = (
        'dual tower',
        'twin tower',
        '2tower',
        '2-tower',
        '2 タワー',
        '2タワー',
        'ツインタワー',
        'dual heatsink',
        'nh-d15',
        'ak620',
        'peerless assassin',
        'frost commander',
    )
    return any(kw in text for kw in dual_tower_keywords)


def _prefer_creator_cpu_by_core_threads(candidates):
    """クリエイター用途: 最小要件コア数を満たす最安値CPUを選ぶ（X3D は完全に除外）"""
    if not candidates:
        return None
    # X3D を除外
    non_x3d_candidates = [p for p in candidates if not _is_cpu_x3d(p)]
    if not non_x3d_candidates:
        # X3D のみの場合は警告の上、非X3D を優先（ログには出力しない）
        non_x3d_candidates = candidates
    
    # 最小要件: 8コア以上（Ryzen 7相当）
    min_cores = 8
    qualified_cpus = [p for p in non_x3d_candidates 
                      if (_get_spec(p, 'core_count', 0) or 0) >= min_cores]
    
    # 条件を満たすCPUがあれば、その中から最安値を選ぶ
    if qualified_cpus:
        return sorted(qualified_cpus, key=lambda p: p.price)[0]
    
    # 条件を満たすCPUがない場合は、コアスレッド数優先で選定
    return sorted(
        non_x3d_candidates,
        key=lambda p: (
            -_extract_cpu_core_threads(p),    # スレッド数が多い方かな優先
            -(_get_spec(p, 'core_count', 0) or 0),  # コア数が多い方が優先
            p.price,  # 同じスレッド数ならより安い方を選ぶ
        ),
    )[0]


def _prefer_creator_spec_cpu_by_core_threads(candidates):
    """クリエイター用途 + スペック重視: 8コア以上の中から高コア・高スレッド・高価格を優先する（X3D は除外）"""
    if not candidates:
        return None

    non_x3d_candidates = [p for p in candidates if not _is_cpu_x3d(p)]
    if not non_x3d_candidates:
        non_x3d_candidates = candidates

    qualified_cpus = [p for p in non_x3d_candidates if _extract_cpu_core_count(p) >= 8 and _extract_cpu_core_threads(p) >= 16]
    pool = qualified_cpus or non_x3d_candidates

    return sorted(
        pool,
        key=lambda p: (
            _extract_cpu_core_count(p),
            _extract_cpu_core_threads(p),
            p.price,
        ),
        reverse=True,
    )[0]


def _is_creator_premium_cpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    premium_keywords = (
        'ryzen 9 9950x',
        'ryzen 9 9950x3d',
        'core ultra 9 285k',
        'ryzen 9 9900x3d',
        'core ultra 9 285',
    )
    return any(keyword in text for keyword in premium_keywords)


def _is_creator_excluded_intel_core_i(part):
    if not part:
        return False
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}"
    return bool(UNSTABLE_INTEL_CORE_I_PATTERN.search(text))


def _creator_premium_cpu_priority_rank(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    priority_patterns = (
        (0, ('ryzen 9 9950x',)),
        (1, ('core ultra 9 285k',)),
        (2, ('ryzen 9 9950x3d',)),
        (3, ('ryzen 9 9900x3d',)),
        (4, ('core ultra 9 285',)),
        (5, ('ryzen 9 9900x',)),
    )
    for rank, patterns in priority_patterns:
        if any(pattern in text for pattern in patterns):
            return rank
    return 99


def _prefer_creator_premium_cpu(candidates, build_priority='cost'):
    """creator premium 用: 9950X を最優先し、次点で 285K を優先する。"""
    if not candidates:
        return None

    premium_candidates = [p for p in candidates if _is_creator_premium_cpu(p)]
    if not premium_candidates:
        return None

    pool = premium_candidates

    if build_priority == 'spec':
        return sorted(
            pool,
            key=lambda p: (
                _creator_premium_cpu_priority_rank(p),
                -(_get_cpu_perf_score(p) or 0),
                -_extract_cpu_core_count(p),
                -_extract_cpu_core_threads(p),
                p.price,
            ),
        )[0]

    return sorted(
        pool,
        key=lambda p: (
            _creator_premium_cpu_priority_rank(p),
            p.price,
            -(_get_cpu_perf_score(p) or 0),
            -_extract_cpu_core_count(p),
            -_extract_cpu_core_threads(p),
        ),
    )[0]


def _pick_creator_cpu_with_budget(candidates, budget, build_priority='balanced'):
    """creator 用 CPU 選定: premium 予算では 9950X3D/285K 級を最優先する。"""
    if not candidates:
        return None

    filtered_candidates = [p for p in candidates if not _is_creator_excluded_intel_core_i(p)]
    if filtered_candidates:
        candidates = filtered_candidates

    budget_tier = _classify_budget_tier(int(budget or 0), usage='creator')
    workstation_candidates = [p for p in candidates if _is_workstation_cpu(p)]
    if workstation_candidates and budget_tier in {'high', 'premium'}:
        picked = _pick_workstation_cpu(workstation_candidates, build_priority=build_priority)
        if picked:
            return picked

    if _is_creator_premium_budget(budget):
        premium_picked = _prefer_creator_premium_cpu(candidates, build_priority=build_priority)
        if premium_picked:
            return premium_picked

    if build_priority == 'cost':
        return _prefer_creator_cost_cpu_8_to_24_cores(candidates, budget=budget)
    if build_priority == 'spec':
        return _prefer_creator_spec_cpu_by_core_threads(candidates)
    return _prefer_creator_cpu_by_core_threads(candidates)


def _prefer_creator_cost_cpu_8_to_24_cores(candidates, budget=None):
    """creator + cost 用: 8～24コアかつ16スレッド以上を優先し、最安値を選ぶ（X3D除外）"""
    if not candidates:
        return None

    if _is_creator_premium_budget(budget):
        premium_picked = _prefer_creator_premium_cpu(candidates, build_priority='cost')
        if premium_picked:
            return premium_picked

    non_x3d_candidates = [p for p in candidates if not _is_cpu_x3d(p)]
    if not non_x3d_candidates:
        non_x3d_candidates = candidates

    min_threads = 16
    in_band = [
        p for p in non_x3d_candidates
        if 8 <= _extract_cpu_core_count(p) <= 24
        and _extract_cpu_core_threads(p) >= min_threads
    ]

    # スレッド条件を満たすCPUがない場合のみ、コア帯のみで選ぶ
    in_band_core_only = [
        p for p in non_x3d_candidates
        if 8 <= _extract_cpu_core_count(p) <= 24
    ]
    if in_band_core_only:
        ranked = in_band_core_only
    else:
        # 8～24コアが無い場合のみ既存のcreatorロジックへフォールバック
        ranked = non_x3d_candidates

    try:
        budget_value = int(budget) if budget is not None else 0
    except (TypeError, ValueError):
        budget_value = 0

    ranked = in_band or ranked

    # ハイエンド帯の creator + cost は、単純最安よりも実効性能を優先し、
    # Radeon AI PRO 系との組み合わせを考慮して AMD 候補を優先する。
    if budget_value >= _budget_tier_threshold('creator', 'high'):
        amd_ranked = [p for p in ranked if _is_cpu_vendor_match(p, 'amd')]
        pool = amd_ranked or ranked
        return sorted(
            pool,
            key=lambda p: (
                -(_get_cpu_perf_score(p) or 0),
                -_extract_cpu_core_threads(p),
                -_extract_cpu_core_count(p),
                p.price,
            ),
        )[0]

    # 中予算以上では、同じ creator 条件を満たす中でも上位CPUへ寄せる。
    if budget_value >= _budget_tier_threshold('creator', 'middle'):
        return sorted(
            ranked,
            key=lambda p: (
                _extract_cpu_core_count(p),
                _extract_cpu_core_threads(p),
                p.price,
            ),
            reverse=True,
        )[0]

    if in_band:
        return sorted(in_band, key=lambda p: p.price)[0]

    if in_band_core_only:
        return sorted(
            in_band_core_only,
            key=lambda p: (-_extract_cpu_core_threads(p), p.price),
        )[0]

    return _prefer_creator_cpu_by_core_threads(non_x3d_candidates)


def _extract_numeric_mm(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    match = re.search(r'(\d{2,4})', str(value))
    if not match:
        return None

    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _extract_numeric_radiator_size(value):
    return _extract_numeric_mm(value)


def _extract_numeric_fan_count(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _extract_case_supported_radiators(part):
    specs = getattr(part, 'specs', {}) or {}
    supported = set()

    for key in ('max_radiator_mm', 'radiator_mm'):
        numeric = _extract_numeric_radiator_size(specs.get(key))
        if numeric:
            supported.add(numeric)

    list_values = specs.get('radiator_sizes') or specs.get('supported_radiators') or []
    if isinstance(list_values, (list, tuple, set)):
        for item in list_values:
            numeric = _extract_numeric_radiator_size(item)
            if numeric:
                supported.add(numeric)

    text = f"{part.name} {part.url}".lower()
    for size in RADIATOR_SIZE_VALUES:
        if f'{size}mm' in text or f'{size} mm' in text:
            supported.add(size)

    for keyword, hint_sizes in CASE_RADIATOR_HINTS.items():
        if keyword in text:
            supported.update(hint_sizes)

    if supported:
        return supported

    # スペック抽出がないケース名向けの保守的フォールバック
    if any(keyword in text for keyword in CASE_SIZE_KEYWORDS['mini']):
        return {120, 140, 240}
    if any(keyword in text for keyword in CASE_SIZE_KEYWORDS['mid']):
        return {120, 140, 240, 280, 360}
    if any(keyword in text for keyword in CASE_SIZE_KEYWORDS['full']):
        return {120, 140, 240, 280, 360, 420}

    return set()


def _is_case_radiator_compatible(part, radiator_size):
    requested = _extract_numeric_radiator_size(radiator_size)
    if not requested:
        return True

    supported = _extract_case_supported_radiators(part)
    if not supported:
        return False
    return any(size >= requested for size in supported)


def _extract_case_supported_form_factors(part):
    specs = getattr(part, 'specs', {}) or {}
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()

    supported = set()

    raw_forms = specs.get('supported_form_factors') or specs.get('supported_form_factor') or specs.get('form_factor') or []
    if isinstance(raw_forms, str):
        raw_forms = re.split(r'[,/|]', raw_forms)

    if isinstance(raw_forms, (list, tuple, set)):
        for item in raw_forms:
            value = str(item or '').lower()
            if any(token in value for token in ('mini-itx', 'mini itx', 'mitx')):
                supported.add('mini-itx')
            if any(token in value for token in ('micro-atx', 'micro atx', 'microatx', 'matx', 'm-atx')):
                supported.update({'micro-atx', 'mini-itx'})
            if re.search(r'(^|\b)e-?atx(\b|$)', value) or 'extended atx' in value:
                supported.update({'eatx', 'atx', 'micro-atx', 'mini-itx'})
            elif re.search(r'(^|\b)atx(\b|$)', value):
                supported.update({'atx', 'micro-atx', 'mini-itx'})

    if any(keyword in text for keyword in CASE_SIZE_KEYWORDS['mini']):
        supported.add('mini-itx')

    if any(keyword in text for keyword in ('micro-atx', 'micro atx', 'microatx', 'matx', 'm-atx', 'micro tower')):
        supported.update({'micro-atx', 'mini-itx'})

    if any(keyword in text for keyword in ('e-atx', 'eatx', 'extended atx')):
        supported.update({'eatx', 'atx', 'micro-atx', 'mini-itx'})
    elif re.search(r'(^|\b)atx(\b|$)', text):
        supported.update({'atx', 'micro-atx', 'mini-itx'})

    return supported


def _is_case_compatible_with_motherboard(part, motherboard_form_factor):
    normalized = str(motherboard_form_factor or '').strip().lower()
    if normalized in {'', 'unknown'}:
        return True

    supported = _extract_case_supported_form_factors(part)
    if not supported:
        return True

    return normalized in supported


def _is_case_preferred_for_motherboard(part, motherboard_form_factor):
    normalized = str(motherboard_form_factor or '').strip().lower()
    if normalized in {'', 'unknown'}:
        return True

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()

    if normalized == 'micro-atx':
        return any(token in text for token in ('micro-atx', 'micro atx', 'microatx', 'matx', 'm-atx', 'micro tower'))

    if normalized == 'mini-itx':
        return any(token in text for token in ('mini-itx', 'mini itx', 'itx', 'sff'))

    if normalized == 'eatx':
        return any(token in text for token in ('e-atx', 'eatx', 'full tower', 'extended atx'))

    if normalized == 'atx':
        if any(token in text for token in ('micro-atx', 'micro atx', 'microatx', 'matx', 'm-atx', 'mini-itx', 'mini itx')):
            return False
        return re.search(r'(^|\b)atx(\b|$)', text) is not None or 'mid tower' in text or 'full tower' in text

    return _is_case_compatible_with_motherboard(part, normalized)


def _extract_case_max_gpu_length_mm(part):
    specs = getattr(part, 'specs', {}) or {}
    for key in ('max_gpu_length_mm', 'gpu_max_length_mm', 'max_vga_length_mm', 'vga_max_length_mm'):
        numeric = _extract_numeric_mm(specs.get(key))
        if numeric:
            return numeric
    return None


def _is_case_gpu_length_compatible(part, gpu_length_mm):
    required = _extract_numeric_mm(gpu_length_mm)
    if not required:
        return True

    max_length = _extract_case_max_gpu_length_mm(part)
    if not max_length:
        return True

    return max_length >= required


def _infer_motherboard_form_factor(part):
    """マザーボードのフォームファクターを推定する: 'eatx' / 'atx' / 'micro-atx' / 'mini-itx' / 'unknown'"""
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    form_factor = str(_get_spec(part, 'form_factor', '') or '').lower()
    combined = f"{text} {form_factor}"
    if any(kw in combined for kw in ('e-atx', 'eatx', 'extended atx')):
        return 'eatx'
    if any(kw in combined for kw in ('mini-itx', 'mini itx', 'mitx')):
        return 'mini-itx'
    if any(kw in combined for kw in ('micro-atx', 'micro atx', 'microatx', 'matx', 'm-atx')):
        return 'micro-atx'
    if 'atx' in combined:
        return 'atx'
    return 'unknown'


def _preferred_motherboard_form_factors(case_size):
    if case_size == 'full':
        return ('eatx', 'atx')
    if case_size == 'mid':
        return ('atx', 'micro-atx', 'eatx')
    if case_size == 'mini':
        return ('mini-itx',)
    return tuple()


def _infer_motherboard_chipset(part):
    """マザーボードのチップセットを推定する: 'x870e' / 'x870' / 'x670e' / 'x670' / 'unknown'"""
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    chipset = str(_get_spec(part, 'chipset', '') or '').lower()
    combined = f"{text} {chipset}"
    
    if any(kw in combined for kw in ('x870e', 'x870-e')):
        return 'x870e'
    if 'x870' in combined:
        return 'x870'
    if any(kw in combined for kw in ('x670e', 'x670-e')):
        return 'x670e'
    if 'x670' in combined:
        return 'x670'
    return 'unknown'


def _infer_motherboard_socket(part):
    """マザーボードのCPUソケットを推定する: AM4 / AM5 / LGA1700 / LGA1851 / ''"""
    socket_raw = str(_get_spec(part, 'socket', '') or '').upper().replace(' ', '')
    if socket_raw in {'AM4', 'AM5', 'LGA1700', 'LGA1851'}:
        return socket_raw

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper().replace(' ', '')
    if 'AM5' in text:
        return 'AM5'
    if 'AM4' in text:
        return 'AM4'
    if 'LGA1851' in text or '1851' in text:
        return 'LGA1851'
    if 'LGA1700' in text or '1700' in text:
        return 'LGA1700'

    chipset = _infer_motherboard_chipset(part)
    if chipset in {'x870e', 'x870', 'x670e', 'x670'}:
        return 'AM5'

    if re.search(r'\b(?:B850|B650|B550|A620|A520|X670|X870|X570|B450|A320)\b', text):
        # B650/B850/X670/X870 はAM5、B550/A520/X570/B450/A320 はAM4系
        if re.search(r'\b(?:B650|B850|A620|X670|X870)\b', text):
            return 'AM5'
        return 'AM4'

    if re.search(r'\b(?:H610|H670|B660|B760|Z690|Z790|Q670|W680)\b', text):
        return 'LGA1700'
    if re.search(r'\b(?:H810|B860|Z890|W880)\b', text):
        return 'LGA1851'

    return ''


def _prefer_motherboard_candidates(candidates, case_size):
    preferred_form_factors = _preferred_motherboard_form_factors(case_size)
    if not preferred_form_factors:
        return candidates

    preferred_candidates = [
        part for part in candidates
        if _infer_motherboard_form_factor(part) in preferred_form_factors
    ]
    return preferred_candidates or candidates


def _infer_cpu_power_w(part):
    if not part:
        return 0

    try:
        tdp_w = int(_get_spec(part, 'tdp_w', 0) or 0)
    except (TypeError, ValueError):
        tdp_w = 0
    if tdp_w > 0:
        return tdp_w

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    for watts in (170, 125, 105, 95, 65, 35):
        if f'{watts}w' in text:
            return watts
    return 95


GPU_POWER_RULES = (
    (r'rtx\s*5090', 575),
    (r'rtx\s*5080', 360),
    (r'rtx\s*5070\s*ti', 300),
    (r'rtx\s*5070', 250),
    (r'rtx\s*5060\s*ti', 180),
    (r'rtx\s*5060', 150),
    (r'rtx\s*5050', 130),
    (r'rtx\s*3050', 70),
    (r'rx\s*9070\s*xt', 320),
    (r'rx\s*9070', 260),
    (r'rx\s*9060\s*xt', 190),
    (r'rx\s*6400', 55),
    (r'arc\s*b580', 190),
    (r'arc\s*b570', 150),
    (r'arc\s*a310', 50),
)


def _infer_gpu_power_w(part):
    if not part:
        return 0

    try:
        tdp_w = int(_get_spec(part, 'tdp_w', 0) or 0)
    except (TypeError, ValueError):
        tdp_w = 0
    if tdp_w > 0:
        return tdp_w

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    for pattern, watts in GPU_POWER_RULES:
        if re.search(pattern, text):
            return watts

    return 180


def _estimate_system_power_w(selected_parts, usage):
    cpu = selected_parts.get('cpu')
    gpu = selected_parts.get('gpu')
    cpu_cooler = selected_parts.get('cpu_cooler')
    motherboard = selected_parts.get('motherboard')
    memory = selected_parts.get('memory')
    storage_parts = [selected_parts.get('storage')]
    for key in ('storage2', 'storage3'):
        if selected_parts.get(key):
            storage_parts.append(selected_parts.get(key))

    cpu_power = _infer_cpu_power_w(cpu)
    gpu_power = _infer_gpu_power_w(gpu)
    motherboard_power = 45 if motherboard else 0
    memory_power = 10 if memory else 0

    storage_power = 0
    for storage_part in storage_parts:
        if not storage_part:
            continue
        media_type = _infer_storage_media_type(storage_part)
        storage_power += 12 if media_type == 'hdd' else 6

    cooler_text = f"{getattr(cpu_cooler, 'name', '')} {getattr(cpu_cooler, 'url', '')}".lower()
    if cpu_cooler:
        cooler_power = 20 if any(token in cooler_text for token in ('水冷', 'aio', '360', '280', '240')) else 8
    else:
        cooler_power = 0

    case_power = 10 if selected_parts.get('case') else 0

    estimated = cpu_power + gpu_power + motherboard_power + memory_power + storage_power + cooler_power + case_power
    if estimated <= 0:
        return IGPU_POWER_MAP.get(usage, USAGE_POWER_MAP.get(usage, 300)) if usage in IGPU_USAGES else USAGE_POWER_MAP.get(usage, 400)
    return estimated


def _recommended_psu_floor_w(selected_parts, usage):
    gpu_power = _infer_gpu_power_w(selected_parts.get('gpu'))
    cpu_power = _infer_cpu_power_w(selected_parts.get('cpu'))

    if gpu_power == 0 and usage in IGPU_USAGES:
        if cpu_power >= 125:
            return 500
        return 400 if cpu_power > 0 else 0

    if gpu_power >= 550:
        return 1200
    if gpu_power >= 350:
        return 1000
    if gpu_power >= 300:
        return 850
    if gpu_power >= 250:
        return 850
    if gpu_power >= 180:
        return 750
    if cpu_power >= 170:
        return 750
    if gpu_power > 0 or cpu_power > 0:
        return 650
    return 0


def _required_psu_wattage(selected_parts, usage):
    estimated = _estimate_system_power_w(selected_parts, usage)
    cpu_gpu_total = _infer_cpu_power_w(selected_parts.get('cpu')) + _infer_gpu_power_w(selected_parts.get('gpu'))
    required = max(
        int(estimated * 1.25),
        estimated + 100,
        cpu_gpu_total + 100,
        _recommended_psu_floor_w(selected_parts, usage),
    )
    return int(((required + 49) // 50) * 50)


def _infer_psu_wattage_w(part):
    if not part:
        return 0
    try:
        return int(_get_spec(part, 'wattage', 0) or 0)
    except (TypeError, ValueError):
        return 0


def _psu_selection_sort_key(part, required_wattage):
    wattage = _infer_psu_wattage_w(part)
    if required_wattage is None:
        return (part.price, 0 if wattage > 0 else 1)

    headroom = max(0, wattage - int(required_wattage)) if wattage > 0 else 10_000
    # まず価格最小を優先し、同価格帯では必要Wに近いものを選ぶ。
    return (part.price, headroom)


def _psu_headroom_cap_w(required_wattage, usage=None, build_priority=None):
    if required_wattage is None:
        return None

    try:
        required = int(required_wattage)
    except (TypeError, ValueError):
        return None

    # ゲーミング・スペック重視は過剰容量を抑え、その他は少し広めに許容する。
    margin = 200 if usage == 'gaming' and build_priority == 'spec' else 300
    return required + margin


def _filter_psu_candidates_by_headroom(candidates, required_wattage, usage=None, build_priority=None):
    if required_wattage is None:
        return candidates

    max_allowed = _psu_headroom_cap_w(required_wattage, usage=usage, build_priority=build_priority)
    if max_allowed is None:
        return candidates

    bounded = [p for p in candidates if _infer_psu_wattage_w(p) <= max_allowed]
    return bounded or candidates


def _rightsize_psu_after_selection(selected_parts, usage, options=None):
    options = options or {}
    current_psu = selected_parts.get('psu')
    if not current_psu:
        return selected_parts

    required_w = _required_psu_wattage(selected_parts, usage)
    psu_options = dict(options)
    psu_options['required_psu_wattage'] = required_w
    preferred_psu_wattage = psu_options.get('preferred_psu_wattage')

    candidates = [
        p
        for p in PCPart.objects.filter(part_type='psu').order_by('price')
        if _is_part_suitable('psu', p) and _matches_selection_options('psu', p, options=psu_options)
    ]
    if preferred_psu_wattage is not None:
        preferred_candidates = [p for p in candidates if _infer_psu_wattage_w(p) >= int(preferred_psu_wattage)]
        if preferred_candidates:
            candidates = preferred_candidates
    candidates = _filter_psu_candidates_by_headroom(
        candidates,
        required_w,
        usage=options.get('usage', usage),
        build_priority=options.get('build_priority'),
    )
    if not candidates:
        return selected_parts

    best_fit = sorted(candidates, key=lambda p: _psu_selection_sort_key(p, required_w))[0]
    if best_fit.id == current_psu.id:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['psu'] = best_fit
    return adjusted


def _get_or_create_part_cache(options):
    if options is None:
        return {}
    cache = options.get('_part_type_cache')
    if cache is None:
        cache = {}
        options['_part_type_cache'] = cache
    return cache


def _get_cached_parts_by_type(part_type, options=None):
    cache = _get_or_create_part_cache(options)
    if part_type not in cache:
        cache[part_type] = list(PCPart.objects.filter(part_type=part_type).order_by('price'))
    return cache[part_type]


def _pick_part_by_target(part_type, budget, usage, weights_override=None, options=None):
    options = options or {}
    cooler_type = options.get('cooler_type', 'any')
    radiator_size = options.get('radiator_size', 'any')
    cooling_profile = options.get('cooling_profile', 'balanced')
    case_size = options.get('case_size', 'any')
    case_fan_policy = options.get('case_fan_policy', 'auto')
    cpu_vendor = options.get('cpu_vendor', 'any')
    build_priority = options.get('build_priority', 'balanced')
    storage_preference = options.get('storage_preference', 'ssd')
    required_psu_wattage = options.get('required_psu_wattage')
    preferred_psu_wattage = options.get('preferred_psu_wattage')
    minimum_gaming_gpu_tier = options.get('minimum_gaming_gpu_tier', 1)
    motherboard_memory_type = str(options.get('motherboard_memory_type', '') or '').upper()
    min_storage_capacity_gb = options.get('min_storage_capacity_gb')
    max_memory_capacity_gb = options.get('max_memory_capacity_gb')
    max_storage_capacity_gb = options.get('max_storage_capacity_gb')
    auto_adjust_reference_budget = options.get('auto_adjust_reference_budget')
    require_gaming_x3d_cpu = options.get('require_gaming_x3d_cpu', False)
    minimum_gaming_gpu_perf_score = int(options.get('minimum_gaming_gpu_perf_score') or 0)
    cpu_socket = options.get('cpu_socket')

    base_parts = _get_cached_parts_by_type(part_type, options=options)
    candidates = [p for p in base_parts if _is_part_suitable(part_type, p)]
    if part_type == 'gpu':
        candidates = [p for p in candidates if not _is_gt_series_gpu(p)]
        # general/business: クリエイター・AI専用GPU（RTX PRO/Radeon Pro等）を除外する
        if usage in {'general', 'business', 'standard'}:
            non_creative_candidates = [p for p in candidates if not _is_gaming_creative_gpu(p)]
            if non_creative_candidates:
                candidates = non_creative_candidates
    if part_type == 'cpu_cooler':
        candidates = [
            p for p in candidates
            if _is_cpu_cooler_product(p)
            and _is_cpu_cooler_type_match(p, cooler_type)
            and _is_allowed_cpu_cooler_brand(p)
        ]
        if cpu_socket:
            socket_filtered = [p for p in candidates if _is_cpu_cooler_socket_compatible(p, cpu_socket)]
            if socket_filtered:
                candidates = socket_filtered
        if usage == 'creator':
            creator_cooler_candidates = [p for p in candidates if _is_liquid_cooler(p) or _is_dual_tower_cooler(p)]
            if creator_cooler_candidates:
                candidates = creator_cooler_candidates
        if cooler_type == 'liquid' and radiator_size != 'any':
            radiator_filtered = [p for p in candidates if _is_radiator_size_match(p, radiator_size)]
            if radiator_filtered:
                candidates = radiator_filtered
    elif part_type == 'case':
        size_filtered = [p for p in candidates if _is_case_size_match(p, case_size)]
        if size_filtered:
            candidates = size_filtered

        motherboard_form_factor = str(options.get('motherboard_form_factor', '') or '').lower()
        if motherboard_form_factor:
            preferred_form_factor_cases = [
                p for p in candidates if _is_case_preferred_for_motherboard(p, motherboard_form_factor)
            ]
            if preferred_form_factor_cases:
                candidates = preferred_form_factor_cases
            else:
                compatible_form_factor_cases = [
                    p for p in candidates if _is_case_compatible_with_motherboard(p, motherboard_form_factor)
                ]
                if compatible_form_factor_cases:
                    candidates = compatible_form_factor_cases

        gpu_length_mm = options.get('gpu_length_mm')
        gpu_length_filtered = [p for p in candidates if _is_case_gpu_length_compatible(p, gpu_length_mm)]
        if gpu_length_filtered:
            candidates = gpu_length_filtered

        if cooler_type == 'liquid' and radiator_size != 'any':
            radiator_filtered = [p for p in candidates if _is_case_radiator_compatible(p, radiator_size)]
            if radiator_filtered:
                candidates = radiator_filtered
        candidates = _filter_candidates_by_part_price_band(candidates, 'case', budget, usage)
    elif part_type == 'cpu':
        candidates = [
            p for p in candidates
            if not _is_cpu_vendor_match(p, 'intel') or _is_supported_intel_client_cpu(p)
        ]
        vendor_filtered = [p for p in candidates if _is_cpu_vendor_match(p, cpu_vendor)]
        if vendor_filtered:
            candidates = vendor_filtered
        if usage in {'workstation', 'ai'}:
            ai_latest_filtered = [p for p in candidates if _is_ai_latest_generation_cpu(p)]
            if ai_latest_filtered:
                candidates = ai_latest_filtered
            else:
                candidates = []
        if usage == 'gaming' and cpu_vendor == 'any':
            candidates = [p for p in candidates if _is_cpu_vendor_match(p, 'amd')]
        if usage == 'gaming' and require_gaming_x3d_cpu and cpu_vendor == 'any':
            x3d_filtered = [p for p in candidates if _is_gaming_cpu_x3d_preferred(p)]
            if x3d_filtered:
                candidates = x3d_filtered
            else:
                candidates = []
        # gaming + spec: スペック重視 CPU に限定する
        if usage == 'gaming' and build_priority == 'spec':
            spec_priority_filtered = [
                p for p in candidates 
                if int(getattr(p, 'id', 0) or 0) in GAMING_SPEC_PRIORITY_CPU_IDS
            ]
            if spec_priority_filtered:
                candidates = spec_priority_filtered
        # gaming 用途: 性能目安表スコアが 3000 未満の CPU は除外する。
        # X3D必須時は、スコア未登録で候補が消えないようこのフィルタを適用しない。
        if usage == 'gaming' and not require_gaming_x3d_cpu:
            minimum_perf_score = _minimum_gaming_cpu_perf_score(usage)
            if minimum_perf_score > 0:
                perf_filtered = [
                    p for p in candidates
                    if (
                        _is_gaming_cpu_x3d_preferred(p)
                        or (_get_cpu_perf_score(p) is not None and _get_cpu_perf_score(p) >= minimum_perf_score)
                    )
                ]
                if perf_filtered:
                    candidates = perf_filtered
                else:
                    candidates = []
        if usage == 'creator':
            creator_cpu_filtered = [
                p for p in candidates
                if _cpu_meets_creator_minimum(p, min_cores=8, min_threads=16) or _is_workstation_cpu(p)
            ]
            if creator_cpu_filtered:
                candidates = creator_cpu_filtered
        if _is_general_cost_low_tier(usage, build_priority, budget):
            legacy_pool = [p for p in candidates if _is_general_cost_legacy_cpu(p)]
            if legacy_pool:
                candidates = legacy_pool
            else:
                non_am5_pool = [p for p in candidates if not _is_am5_cpu(p)]
                if non_am5_pool:
                    candidates = non_am5_pool
    elif part_type == 'motherboard':
        cpu_socket = options.get('cpu_socket')
        if cpu_socket:
            socket_filtered = [p for p in candidates if _infer_motherboard_socket(p) == cpu_socket]
            if socket_filtered:
                candidates = socket_filtered
        
        # ローエンド gaming/cost では B860/X670 など低価格チップセットを優先
        if options.get('usage') == 'gaming' and options.get('build_priority') == 'cost' and options.get('budget', 0) < _budget_tier_threshold('gaming', 'low'):
            low_end_chipsets = [p for p in candidates if _infer_motherboard_chipset(p) in ('b860', 'b760', 'x670')]
            if low_end_chipsets:
                candidates = low_end_chipsets
        
        # gaming + cost: X870E を除外（コストダウン）
        if usage == 'gaming' and build_priority == 'cost':
            exclude_flagship_mb = [
                p for p in candidates
                if not _is_gaming_cost_flagship_motherboard(p)
            ]
            if exclude_flagship_mb:
                candidates = exclude_flagship_mb
        
        max_chipset = options.get('max_motherboard_chipset', 'any')
        if max_chipset != 'any':
            if max_chipset == 'x870':
                chipset_filtered = [p for p in candidates if _infer_motherboard_chipset(p) != 'x870e']
            elif max_chipset == 'x670':
                chipset_filtered = [p for p in candidates if _infer_motherboard_chipset(p) not in ('x870e', 'x870', 'x670e')]
            else:
                chipset_filtered = candidates
            if chipset_filtered:
                candidates = chipset_filtered

        if usage == 'creator':
            motherboard_floor = _creator_motherboard_floor_price(budget, options=options)
            floor_filtered = [p for p in candidates if p.price >= motherboard_floor]
            if floor_filtered:
                candidates = floor_filtered

        candidates = _filter_candidates_by_part_price_band(candidates, 'motherboard', budget, usage)

        # ローエンド gaming/cost ではマザーボード価格を制限（GPU予算を圧迫しないため）
        if usage == 'gaming' and build_priority == 'cost' and budget < _budget_tier_threshold(usage, 'low'):
            mb_price_cap = max(25000, int(budget * 0.18))
            capped_candidates = [p for p in candidates if p.price <= mb_price_cap]
            if capped_candidates:
                candidates = capped_candidates

        candidates = _prefer_motherboard_candidates(candidates, case_size)
    elif part_type == 'memory':
        if max_memory_capacity_gb:
            max_capacity_filtered = [
                p for p in candidates
                if _infer_memory_capacity_gb(p) <= int(max_memory_capacity_gb)
            ]
            if max_capacity_filtered:
                candidates = max_capacity_filtered
        if motherboard_memory_type:
            mem_type_filtered = [
                p for p in candidates
                if _infer_memory_type(p) == motherboard_memory_type
            ]
            if mem_type_filtered:
                candidates = mem_type_filtered
        # gaming + cost: 高速メモリ（PC5-44800以上）を除外（コストダウン）
        if usage == 'gaming' and build_priority == 'cost':
            exclude_high_speed_mem = [
                p for p in candidates
                if not _is_gaming_cost_high_speed_memory(p)
            ]
            if exclude_high_speed_mem:
                candidates = exclude_high_speed_mem
        if usage == 'creator':
            creator_memory_filtered = [p for p in candidates if _infer_memory_capacity_gb(p) >= 16]
            if creator_memory_filtered:
                candidates = creator_memory_filtered
    elif part_type == 'storage':
        if min_storage_capacity_gb:
            capacity_filtered = [
                p for p in candidates
                if _infer_storage_capacity_gb(p) >= int(min_storage_capacity_gb)
            ]
            if capacity_filtered:
                candidates = capacity_filtered
        if max_storage_capacity_gb:
            max_capacity_filtered = [
                p for p in candidates
                if _infer_storage_capacity_gb(p) <= int(max_storage_capacity_gb)
            ]
            if max_capacity_filtered:
                candidates = max_capacity_filtered
        if _is_general_cost_low_tier(usage, build_priority, budget):
            storage_price_cap = max(18000, int(budget * 0.12))
            capped_candidates = [p for p in candidates if p.price <= storage_price_cap]
            if capped_candidates:
                candidates = capped_candidates
        # 既定はSSD優先。gaming+specのみ高容量HDDのフォールバックを許容する。
        if not (usage == 'gaming' and build_priority == 'spec'):
            ssd_filtered = [p for p in candidates if _infer_storage_media_type(p) == 'ssd']
            if ssd_filtered:
                candidates = ssd_filtered
        if usage == 'gaming' and build_priority == 'cost':
            # gaming + cost では、ストレージ単体の過剰比率を抑えて
            # CPU/GPU予算を圧迫しないようにする。
            storage_price_cap = max(18000, int(budget * 0.22))
            capped_candidates = [p for p in candidates if p.price <= storage_price_cap]
            if capped_candidates:
                candidates = capped_candidates
    elif part_type == 'psu':
        if required_psu_wattage is not None:
            psu_filtered = [
                p for p in candidates
                if _infer_psu_wattage_w(p) >= int(required_psu_wattage)
            ]
            if psu_filtered:
                candidates = psu_filtered
            if preferred_psu_wattage is not None:
                preferred_candidates = [
                    p for p in candidates
                    if _infer_psu_wattage_w(p) >= int(preferred_psu_wattage)
                ]
                if preferred_candidates:
                    candidates = preferred_candidates
            candidates = _filter_psu_candidates_by_headroom(
                candidates,
                required_psu_wattage,
                usage=usage,
                build_priority=build_priority,
            )

    if part_type == 'gpu' and usage == 'gaming' and build_priority == 'spec':
        candidates = [p for p in candidates if _is_gaming_gpu_within_priority_cap(p, 'spec', budget=budget)]
        preferred_gpu = [p for p in candidates if _is_gaming_spec_gpu_preferred(p, minimum_gaming_gpu_tier)]
        if preferred_gpu:
            candidates = preferred_gpu
        gpu_floor_price = _gaming_spec_gpu_price_floor(budget, usage, options=options)
        if gpu_floor_price > 0:
            floor_filtered = [p for p in candidates if p.price >= gpu_floor_price]
            if floor_filtered:
                candidates = floor_filtered
        candidates = _prefer_rx_xt_value_candidates(candidates)

    if part_type == 'gpu' and usage == 'gaming' and minimum_gaming_gpu_perf_score > 0:
        perf_filtered = [p for p in candidates if _infer_gpu_perf_score_for_requirement(p) >= minimum_gaming_gpu_perf_score]
        if perf_filtered:
            candidates = perf_filtered

    if part_type == 'gpu' and usage == 'creator':
        # creator は VRAM を最優先し、同条件で性能/メーカーを比較する。
        candidates = _prefer_creator_gpu_with_vram_flex(candidates, build_priority=build_priority)

        creator_gpu_cap = _creator_gpu_cap_price(budget, options=options)
        capped_candidates = [p for p in candidates if p.price <= creator_gpu_cap]
        if capped_candidates:
            candidates = capped_candidates

        minimum_creator_tier = _minimum_creator_gpu_tier(budget, options=options)
        if minimum_creator_tier > 0:
            tier_filtered = [p for p in candidates if _creator_gpu_tier(p) >= minimum_creator_tier]
            if tier_filtered:
                candidates = tier_filtered

    if part_type == 'gpu' and usage in {'workstation', 'ai'}:
        ai_latest_filtered = [p for p in candidates if _is_ai_latest_generation_gpu(p)]
        if ai_latest_filtered:
            candidates = ai_latest_filtered
        else:
            candidates = []
        if _classify_budget_tier(int(budget or 0), usage=usage) == 'premium':
            ai_premium_pick = _pick_ai_premium_gpu_candidate(candidates, build_priority=build_priority)
            if ai_premium_pick:
                return ai_premium_pick

    # general/business + spec + dGPU 解禁済みの場合は最低 GPU 価格フロアを適用する。
    # ただし gaming 向けでないため、floor でコンシューマー GPU を全落ちさせないよう
    # floor フィルタは gaming 用途のみ適用し、general/business は within_target 最近傍選択に委ねる。
    if False and part_type == 'gpu' and usage in {'general', 'standard', 'business'} and build_priority == 'spec':
        gpu_floor = _standard_business_spec_gpu_price_floor(budget)
        gpu_weights = weights_override if weights_override is not None else _apply_build_priority_weights(usage, build_priority, use_igpu=False, budget=budget)
        if gpu_weights:
            gpu_floor = min(gpu_floor, int(budget * gpu_weights.get('gpu', 0.1)))
        if gpu_floor > 0:
            floor_filtered = [p for p in candidates if p.price >= gpu_floor]
            if floor_filtered:
                candidates = floor_filtered

    if part_type == 'gpu' and usage == 'gaming' and build_priority == 'cost':
        if auto_adjust_reference_budget is not None:
            pick = _pick_gaming_cost_gpu_for_auto_adjust(candidates, auto_adjust_reference_budget)
            if pick:
                return pick
        
        # ローエンド gaming/cost では RTX 3050 を積極的に優先
        if budget < _budget_tier_threshold(usage, 'low'):
            price_cap = max(34980, int(budget * 0.21))
            rtx_3050_pool = [
                p for p in candidates
                if 'rtx 3050' in f"{p.name} {p.url}".lower() 
                and p.price <= price_cap
                and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
            ]
            if rtx_3050_pool:
                target_price = max(31980, int(budget * 0.195))
                return sorted(
                    rtx_3050_pool,
                    key=lambda p: (abs(int(p.price) - target_price), -_infer_gaming_gpu_perf_score(p), p.price),
                )[0]
        
        cap_budget = int(auto_adjust_reference_budget) if auto_adjust_reference_budget else budget
        gaming_cost_gpu_cap = _gaming_cost_gpu_cap_price(cap_budget)
        gaming_cost_gpu_floor = _gaming_cost_gpu_floor_price(cap_budget)
        capped_candidates = [
            p for p in candidates
            if p.price <= gaming_cost_gpu_cap
            and p.price >= gaming_cost_gpu_floor
            and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
        ]
        if capped_candidates:
            if int(cap_budget or 0) >= 1_000_000:
                return sorted(
                    capped_candidates,
                    key=lambda p: (_infer_gaming_gpu_perf_score(p), p.price),
                    reverse=True,
                )[0]
            candidates = capped_candidates
        elif candidates:
            non_excluded = [p for p in candidates if not _is_gaming_cost_excluded_gpu(p)]
            floor_pool = [p for p in non_excluded if p.price >= gaming_cost_gpu_floor]
            fallback_pool = floor_pool or non_excluded or candidates
            # cost重視で上限に収まる候補がない場合は、除外/下限を考慮した最安GPUを選ぶ。
            return sorted(fallback_pool, key=lambda p: p.price)[0]

    if not candidates:
        return None

    if (
        usage == 'creator'
        and part_type == 'gpu'
        and _is_creator_premium_budget(budget)
    ):
        upper_cap = int(budget * CREATOR_FLAGSHIP_GPU_BUDGET_CAP)
        premium_candidates = [p for p in candidates if p.price <= upper_cap]
        if premium_candidates:
            premium_ranked = _prefer_creator_premium_gpu(premium_candidates, build_priority=build_priority)
            if premium_ranked:
                return premium_ranked[0]

    weights = weights_override if weights_override is not None else USAGE_BUDGET_WEIGHTS[usage]
    target_price = int(budget * weights.get(part_type, 0.1))
    if part_type == 'cpu_cooler':
        if cooling_profile == 'performance':
            target_price = int(target_price * 1.3)
        elif cooling_profile == 'silent':
            target_price = int(target_price * 0.85)

    within_target = [p for p in candidates if p.price <= target_price]
    if within_target:
        if part_type == 'gpu' and usage == 'gaming' and build_priority == 'spec':
            picked_gpu = _pick_gaming_spec_gpu(within_target)
            if picked_gpu:
                return picked_gpu
        # general/business/standard + spec: 目標価格に最も近い GPU を選ぶ
        if part_type == 'gpu' and usage in {'general', 'business', 'standard'} and build_priority == 'spec':
            return sorted(within_target, key=lambda p: abs(p.price - target_price))[0]
        if part_type == 'cpu' and usage in {'general', 'business', 'standard'} and build_priority == 'spec' and _is_general_low_tier(usage, budget):
            picked_general_low_tier_cpu = _pick_general_low_tier_cpu_candidate(within_target)
            if picked_general_low_tier_cpu:
                return picked_general_low_tier_cpu
        if part_type == 'cpu' and usage in {'general', 'business', 'standard'} and build_priority == 'spec':
            budget_tier = _classify_budget_tier(int(budget or 0), usage=usage)
            within_target = _prefer_general_spec_cpu_quality_pool(within_target, usage, budget)
            if budget_tier in {'middle', 'high', 'premium'} and all(_is_general_spec_entry_cpu(p) for p in within_target):
                # 目標価格帯が狭くてエントリーCPUしか残らない場合、候補全体から再評価する。
                within_target = _prefer_general_spec_cpu_quality_pool(candidates, usage, budget)
            # 性能スコア同点（または未取得）時は、上位価格固定を避けて安価側を優先する。
            return max(within_target, key=lambda p: ((_get_cpu_perf_score(p) or 0), -int(getattr(p, 'price', 0) or 0)))
        if part_type == 'cpu' and usage in {'general', 'business', 'standard'} and build_priority == 'cost':
            general_cost_pool = _prefer_general_cost_cpu_budget_band(
                within_target,
                target_price,
                usage,
                build_priority,
                budget,
            )
            picked_general_cost_cpu = _pick_general_cost_cpu_candidate(general_cost_pool)
            if picked_general_cost_cpu:
                return picked_general_cost_cpu
        if part_type == 'psu':
            # PSU は過剰容量・過剰価格より、必要W数に近い候補を優先する。
            return sorted(
                within_target,
                key=lambda p: _psu_selection_sort_key(p, required_psu_wattage),
            )[0]
        if part_type == 'cpu' and usage == 'gaming':
            amd_within_target = [p for p in within_target if _is_cpu_vendor_match(p, 'amd')]
            cpu_pool = amd_within_target or within_target
            picked_cpu = _pick_amd_gaming_cpu(cpu_pool, build_priority, require_x3d=require_gaming_x3d_cpu)
            if picked_cpu:
                return picked_cpu
        if part_type == 'cpu' and usage == 'creator':
            # クリエイター用途: コアスレッド数が多いCPUを優先選定
            # within_target が空の場合は candidates 全体から選定
            budget_tier = _classify_budget_tier(int(budget or 0), usage=usage)
            if budget_tier in {'high', 'premium'}:
                target_cpus = candidates
            else:
                target_cpus = within_target if within_target else candidates
            picked_creator_cpu = _pick_creator_cpu_with_budget(target_cpus, budget=budget, build_priority=build_priority)
            if picked_creator_cpu:
                return picked_creator_cpu
        if part_type == 'memory':
            # gaming + spec はGPU優先のため、メモリは目標価格内から選ぶ。
            # それ以外の spec では、候補全体から上位メモリを選んでもよい。
            if build_priority == 'spec' and usage != 'gaming':
                memory_pool = candidates
            else:
                memory_pool = within_target
            profiled = _memory_profile_pick(memory_pool, build_priority, budget=budget, usage=usage, options=options)
            if usage == 'creator':
                min_capacity_candidates = [p for p in candidates if _infer_memory_capacity_gb(p) >= 16]
                if min_capacity_candidates:
                    candidates = min_capacity_candidates
            if profiled:
                return profiled
        if part_type == 'motherboard':
            motherboard_pool = candidates if usage == 'gaming' and build_priority == 'spec' else within_target
            picked_mb = _pick_motherboard_candidate(motherboard_pool, build_priority, usage, target_price=target_price)
            if picked_mb:
                return picked_mb
        if part_type == 'storage':
            # スペック重視では目標価格内の安価HDDに固定されやすいため、
            # 候補全体からSSD/NVMe優先で選ぶ。
            storage_pool = candidates if build_priority == 'spec' else within_target
            profiled = _storage_profile_pick(storage_pool, build_priority, storage_preference, options=options)
            if profiled:
                return profiled
        if part_type == 'case':
            if within_target:
                return _pick_case_candidate(within_target, case_fan_policy, build_priority, target_price=target_price)
            return _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=target_price)
        if part_type == 'cpu_cooler' and build_priority == 'cost':
            return sorted(
                within_target,
                key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), -p.price),
                reverse=True,
            )[0]
        if part_type == 'gpu' and usage == 'gaming':
            low_end_gpu = _pick_gaming_low_end_gpu(within_target, budget, usage, build_priority)
            if low_end_gpu:
                return low_end_gpu
        if part_type == 'cpu' and usage in {'workstation', 'ai'}:
            # selected_budget_tier（フロントが送ったティア）を優先し、
            # なければ budget から算出したティアを使う。
            ui_tier = _normalize_budget_tier_code(options.get('selected_budget_tier'))
            budget_tier = ui_tier or _classify_budget_tier(int(budget or 0), usage=usage)
            if usage == 'workstation':
                # workstation は _pick_ai_cpu_candidate 側の価格キャップロジックに委ねるため
                # 常に全候補プールを渡す（within_target で絞ると安すぎるCPUのみ残るケースがある）
                ai_cpu_pool = candidates
            elif budget_tier in {'high', 'premium'}:
                ai_cpu_pool = candidates
            else:
                ai_cpu_pool = within_target if within_target else candidates
            picked_ai_cpu = _pick_ai_cpu_candidate(
                ai_cpu_pool,
                build_priority=build_priority,
                budget=budget,
                usage=usage,
                selected_budget_tier=ui_tier,
            )
            if picked_ai_cpu:
                return picked_ai_cpu
        if build_priority == 'cost':
            return random.choice(within_target) if within_target else None
        if part_type == 'cpu_cooler':
            # creator 用途: 水冷またはツインタワー空冷を優先
            if usage == 'creator':
                # 水冷クーラーを最優先
                liquid_coolers = [p for p in within_target if _is_liquid_cooler(p)]
                if liquid_coolers:
                    return sorted(
                        liquid_coolers,
                        key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                        reverse=True,
                    )[0]
                # 水冷がなければツインタワー空冷を優先
                dual_tower_coolers = [p for p in within_target if _is_dual_tower_cooler(p)]
                if dual_tower_coolers:
                    return sorted(
                        dual_tower_coolers,
                        key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                        reverse=True,
                    )[0]
            return sorted(
                within_target,
                key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                reverse=True,
            )[0]
        sorted_candidates = sorted(within_target, key=lambda p: p.price, reverse=True)
        return random.choice(sorted_candidates) if sorted_candidates else None

    if build_priority == 'cost':
        if part_type == 'cpu' and usage == 'gaming':
            cpu_price_cap = _gaming_cost_cpu_price_cap(budget)
            amd_pool = [p for p in candidates if _is_cpu_vendor_match(p, 'amd')]
            capped_candidates = [p for p in amd_pool if p.price <= cpu_price_cap]
            cpu_pool = capped_candidates or amd_pool or candidates
            # gaming + cost: 9850X3D を exclude
            cpu_pool = _remove_9850x3d_from_cpu_pool(cpu_pool, 'cost')
            picked_cpu = _pick_amd_gaming_cpu(cpu_pool, 'cost', require_x3d=require_gaming_x3d_cpu)
            if picked_cpu:
                return picked_cpu
        if part_type == 'cpu' and _is_general_low_tier(usage, budget):
            picked_general_low_tier_cpu = _pick_general_low_tier_cpu_candidate(candidates)
            if picked_general_low_tier_cpu:
                return picked_general_low_tier_cpu
        if part_type == 'cpu' and usage in {'general', 'business', 'standard'} and build_priority == 'cost':
            picked_general_cost_cpu = _pick_general_cost_cpu_candidate(candidates)
            if picked_general_cost_cpu:
                return picked_general_cost_cpu
        if part_type == 'cpu' and usage == 'creator':
            # クリエイター用途 + コスト重視: 8～24コア帯を優先
            picked_creator_cpu = _pick_creator_cpu_with_budget(candidates, budget=budget, build_priority='cost')
            if picked_creator_cpu:
                return picked_creator_cpu
        if part_type == 'memory':
            profiled = _memory_profile_pick(candidates, build_priority, budget=budget, usage=usage, options=options)
            if profiled:
                return profiled
        if part_type == 'motherboard':
            picked_mb = _pick_motherboard_candidate(candidates, build_priority, usage, target_price=target_price)
            if picked_mb:
                return picked_mb
        if part_type == 'storage':
            profiled = _storage_profile_pick(candidates, build_priority, storage_preference, options=options)
            if profiled:
                return profiled
        if part_type == 'case':
            return _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=target_price)
        if part_type == 'gpu' and usage == 'gaming':
            return None
        # general/business/standard + spec: within_target が空でも目標価格最近傍を選ぶ
        if part_type == 'gpu' and usage in {'general', 'business', 'standard'} and build_priority == 'spec':
            return sorted(candidates, key=lambda p: abs(p.price - target_price))[0] if candidates else None
        return random.choice(candidates) if candidates else None

    if part_type == 'cpu_cooler':
        if build_priority == 'spec':
            return sorted(
                candidates,
                key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                reverse=True,
            )[0]
        return sorted(
            candidates,
            key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), -p.price),
            reverse=True,
        )[0]

    if part_type == 'case':
        return _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=target_price)

    if part_type == 'motherboard':
        picked_mb = _pick_motherboard_candidate(candidates, build_priority, usage, target_price=target_price)
        if picked_mb:
            return picked_mb

    if part_type == 'psu':
        return sorted(
            candidates,
            key=lambda p: _psu_selection_sort_key(p, required_psu_wattage),
        )[0]

    if part_type == 'memory' and build_priority == 'spec':
        profiled = _memory_profile_pick(candidates, build_priority, budget=budget, usage=usage, options=options)
        if profiled:
            return profiled

    if part_type == 'storage':
        profiled = _storage_profile_pick(candidates, build_priority, storage_preference, options=options)
        if profiled:
            return profiled

        if part_type == 'cpu' and usage == 'gaming':
            amd_candidates = [p for p in candidates if _is_cpu_vendor_match(p, 'amd')]
            cpu_pool = candidates if require_gaming_x3d_cpu else (amd_candidates or candidates)
            picked_cpu = _pick_amd_gaming_cpu(cpu_pool, build_priority, require_x3d=require_gaming_x3d_cpu)
            if picked_cpu:
                return picked_cpu

    if part_type == 'cpu' and usage == 'creator':
        # クリエイター用途: 目標価格を超えた候補からもコアスレッド数で優先
        # 注: クーラー条件によって候補が制限されている場合でも、creator CPU ロジックを適用
        picked_creator_cpu = _pick_creator_cpu_with_budget(candidates, budget=budget, build_priority=build_priority)
        if picked_creator_cpu:
            return picked_creator_cpu
        # それでも candidates が空の場合は、制限を緩和して再試行
        # (例: 空冷・水冷のどちらでも互換性のある CPU から選定)
        if not candidates and part_type == 'cpu':
            # cooler_type と radiator_size を無視して全 CPU 候補から選定
            all_creator_cpus = PCPart.objects.filter(part_type='cpu').order_by('price')
            if all_creator_cpus:
                return _pick_creator_cpu_with_budget(list(all_creator_cpus), budget=budget, build_priority=build_priority)

    if part_type == 'gpu' and usage == 'gaming' and build_priority == 'spec':
        picked_gpu = _pick_gaming_spec_gpu(candidates)
        if picked_gpu:
            return picked_gpu

    # general/business/standard + spec: within_target 外でもティア目標価格最近傍の GPU を選ぶ
    if part_type == 'gpu' and usage in {'general', 'business', 'standard'} and build_priority == 'spec':
        # weights から target_price を再計算（priority_weights が渡された場合はそれを使う）
        _w = weights_override if weights_override is not None else USAGE_BUDGET_WEIGHTS.get(usage, {})
        _gpu_target = int(budget * _w.get('gpu', 0.1)) if _w else int(budget * 0.1)
        return sorted(candidates, key=lambda p: abs(p.price - _gpu_target))[0] if candidates else None

    if build_priority == 'spec':
        if part_type == 'cpu':
            return max(candidates, key=lambda p: (_get_cpu_perf_score(p) or 0, p.price))
        if part_type == 'motherboard':
            return _pick_motherboard_candidate(candidates, build_priority, usage, target_price=target_price)
        return candidates[-1]

    if part_type == 'gpu' and usage == 'gaming' and build_priority == 'cost':
        if auto_adjust_reference_budget is not None:
            pick = _pick_gaming_cost_gpu_for_auto_adjust(candidates, auto_adjust_reference_budget)
            if pick:
                return pick
        low_end_gpu = _pick_gaming_low_end_gpu(candidates, budget, usage, build_priority)
        if low_end_gpu:
            return low_end_gpu
        cap_budget = int(auto_adjust_reference_budget) if auto_adjust_reference_budget else budget
        gaming_cost_gpu_cap = _gaming_cost_gpu_cap_price(cap_budget)
        gaming_cost_gpu_floor = _gaming_cost_gpu_floor_price(cap_budget)
        non_excluded = [
            p for p in candidates
            if not _is_gaming_cost_excluded_gpu(p)
            and not _is_gaming_creative_gpu(p)
        ]
        capped_candidates = [
            p for p in non_excluded
            if p.price <= gaming_cost_gpu_cap
            and p.price >= gaming_cost_gpu_floor
            and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
        ]
        if capped_candidates:
            candidates = capped_candidates
        elif non_excluded:
            floor_filtered = [p for p in non_excluded if p.price >= gaming_cost_gpu_floor]
            candidates = floor_filtered or non_excluded

    if not candidates:
        return None

    if (
        usage == 'creator'
        and part_type == 'gpu'
        and _is_creator_premium_budget(budget)
    ):
        upper_cap = int(budget * CREATOR_FLAGSHIP_GPU_BUDGET_CAP)
        premium_candidates = [p for p in candidates if p.price <= upper_cap]
        if premium_candidates:
            premium_ranked = _prefer_creator_premium_gpu(premium_candidates, build_priority=build_priority)
            if premium_ranked:
                return premium_ranked[0]

    weights = weights_override if weights_override is not None else USAGE_BUDGET_WEIGHTS[usage]
    target_price = int(budget * weights.get(part_type, 0.1))
    if part_type == 'cpu_cooler':
        if cooling_profile == 'performance':
            target_price = int(target_price * 1.3)
        elif cooling_profile == 'silent':
            target_price = int(target_price * 0.85)

    within_target = [p for p in candidates if p.price <= target_price]
    if within_target:
        if part_type == 'gpu' and usage == 'gaming' and build_priority == 'spec':
            picked_gpu = _pick_gaming_spec_gpu(within_target)
            if picked_gpu:
                return picked_gpu
        if part_type == 'psu':
            # PSU は過剰容量・過剰価格より、必要W数に近い候補を優先する。
            return sorted(
                within_target,
                key=lambda p: _psu_selection_sort_key(p, required_psu_wattage),
            )[0]
        if part_type == 'cpu' and usage == 'gaming':
            amd_candidates = [p for p in within_target if _is_cpu_vendor_match(p, 'amd')]
            cpu_pool = amd_candidates or within_target
            picked_cpu = _pick_amd_gaming_cpu(cpu_pool, build_priority, require_x3d=require_gaming_x3d_cpu)
            if picked_cpu:
                return picked_cpu
        if part_type == 'cpu' and usage == 'creator':
            # クリエイター用途: コアスレッド数が多いCPUを優先選定
            # within_target が空の場合は candidates 全体から選定
            target_cpus = within_target if within_target else candidates
            picked_creator_cpu = _pick_creator_cpu_with_budget(target_cpus, budget=budget, build_priority=build_priority)
            if picked_creator_cpu:
                return picked_creator_cpu
        if part_type == 'memory':
            # gaming + spec はGPU優先のため、メモリは目標価格内から選ぶ。
            # それ以外の spec では、候補全体から上位メモリを選んでもよい。
            if build_priority == 'spec' and usage != 'gaming':
                memory_pool = candidates
            else:
                memory_pool = within_target
            profiled = _memory_profile_pick(memory_pool, build_priority, budget=budget, usage=usage, options=options)
            if usage == 'creator':
                min_capacity_candidates = [p for p in candidates if _infer_memory_capacity_gb(p) >= 16]
                if min_capacity_candidates:
                    candidates = min_capacity_candidates
            if profiled:
                return profiled
        if part_type == 'motherboard':
            motherboard_pool = candidates if usage == 'gaming' and build_priority == 'spec' else within_target
            picked_mb = _pick_motherboard_candidate(motherboard_pool, build_priority, usage, target_price=target_price)
            if picked_mb:
                return picked_mb
        if part_type == 'storage':
            # スペック重視では目標価格内の安価HDDに固定されやすいため、
            # 候補全体からSSD/NVMe優先で選ぶ。
            storage_pool = candidates if build_priority == 'spec' else within_target
            profiled = _storage_profile_pick(storage_pool, build_priority, storage_preference, options=options)
            if profiled:
                return profiled
        if part_type == 'case':
            return _pick_case_candidate(within_target, case_fan_policy, build_priority, target_price=target_price)
        if part_type == 'gpu' and usage == 'gaming' and build_priority == 'cost':
            non_excluded = [
                p for p in within_target
                if not _is_gaming_cost_excluded_gpu(p)
                and not _is_gaming_creative_gpu(p)
            ]
            if non_excluded:
                return sorted(non_excluded, key=lambda p: (p.price, -_infer_gaming_gpu_perf_score(p)))[0]
        if build_priority == 'cost':
            if not (part_type == 'cpu' and usage == 'gaming' and require_gaming_x3d_cpu):
                return within_target[0]
        if part_type == 'cpu_cooler':
            # creator 用途: 水冷またはツインタワー空冷を優先
            if usage == 'creator':
                # 水冷クーラーを最優先
                liquid_coolers = [p for p in within_target if _is_liquid_cooler(p)]
                if liquid_coolers:
                    return sorted(
                        liquid_coolers,
                        key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                        reverse=True,
                    )[0]
                # 水冷がなければツインタワー空冷を優先
                dual_tower_coolers = [p for p in within_target if _is_dual_tower_cooler(p)]
                if dual_tower_coolers:
                    return sorted(
                        dual_tower_coolers,
                        key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                        reverse=True,
                    )[0]
            return sorted(
                within_target,
                key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                reverse=True,
            )[0]
        sorted_candidates = sorted(within_target, key=lambda p: p.price, reverse=True)
        return random.choice(sorted_candidates) if sorted_candidates else None

    if build_priority == 'cost':
        if part_type == 'cpu' and usage == 'gaming':
            if cpu_vendor == 'any':
                amd_candidates = [p for p in candidates if _is_cpu_vendor_match(p, 'amd')]
                cpu_pool = candidates if require_gaming_x3d_cpu else (amd_candidates or candidates)
            else:
                cpu_pool = candidates
            picked_cpu = _pick_amd_gaming_cpu(cpu_pool, 'cost', require_x3d=(require_gaming_x3d_cpu and cpu_vendor == 'any'))
            if picked_cpu:
                return picked_cpu
        if part_type == 'cpu' and usage == 'creator':
            # クリエイター用途 + コスト重視: 8～24コア帯を優先
            picked_creator_cpu = _prefer_creator_cost_cpu_8_to_24_cores(candidates, budget=budget)
            if picked_creator_cpu:
                return picked_creator_cpu
        if part_type == 'memory':
            profiled = _memory_profile_pick(candidates, build_priority, budget=budget, usage=usage, options=options)
            if profiled:
                return profiled
        if part_type == 'motherboard':
            picked_mb = _pick_motherboard_candidate(candidates, build_priority, usage, target_price=target_price)
            if picked_mb:
                return picked_mb
        if part_type == 'storage':
            profiled = _storage_profile_pick(candidates, build_priority, storage_preference, options=options)
            if profiled:
                return profiled
        if part_type == 'case':
            return _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=target_price)
        if part_type == 'gpu' and usage == 'gaming':
            low_end_gpu = _pick_gaming_low_end_gpu(candidates, budget, usage, build_priority)
            if low_end_gpu:
                return low_end_gpu
        return None

    if part_type == 'cpu_cooler':
        if build_priority == 'spec':
            return sorted(
                candidates,
                key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), p.price),
                reverse=True,
            )[0]
        return sorted(
            candidates,
            key=lambda p: (_cpu_cooler_profile_score(p, cooling_profile, cooler_type), -p.price),
            reverse=True,
        )[0]

    if part_type == 'case':
        return _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=target_price)

    if part_type == 'motherboard':
        picked_mb = _pick_motherboard_candidate(candidates, build_priority, usage, target_price=target_price)
        if picked_mb:
            return picked_mb

    if part_type == 'psu':
        return sorted(
            candidates,
            key=lambda p: _psu_selection_sort_key(p, required_psu_wattage),
        )[0]

    if part_type == 'memory' and build_priority == 'spec':
        profiled = _memory_profile_pick(candidates, build_priority, budget=budget, usage=usage, options=options)
        if profiled:
            return profiled

    if part_type == 'storage':
        profiled = _storage_profile_pick(candidates, build_priority, storage_preference, options=options)
        if profiled:
            return profiled

    if part_type == 'cpu' and usage == 'gaming':
        amd_candidates = [p for p in candidates if _is_cpu_vendor_match(p, 'amd')]
        cpu_pool = amd_candidates or candidates
        picked_cpu = _pick_amd_gaming_cpu(cpu_pool, build_priority, require_x3d=require_gaming_x3d_cpu)
        if picked_cpu:
            return picked_cpu

    if part_type == 'cpu' and usage == 'creator':
        # クリエイター用途: 目標価格を超えた候補からもコアスレッド数で優先
        # 注: クーラー条件によって候補が制限されている場合でも、creator CPU ロジックを適用
        picked_creator_cpu = _pick_creator_cpu_with_budget(candidates, budget=budget, build_priority=build_priority)
        if picked_creator_cpu:
            return picked_creator_cpu
        # それでも candidates が空の場合は、制限を緩和して再試行
        # (例: 空冷・水冷のどちらでも互換性のある CPU から選定)
        if not candidates and part_type == 'cpu':
            # cooler_type と radiator_size を無視して全 CPU 候補から選定
            all_creator_cpus = PCPart.objects.filter(part_type='cpu').order_by('price')
            if all_creator_cpus:
                return _pick_creator_cpu_with_budget(list(all_creator_cpus), budget=budget, build_priority=build_priority)

    return None

def _get_spec(part, key, default=None):
    if not part:
        return default
    specs = getattr(part, 'specs', {}) or {}
    return specs.get(key, default)


def _infer_memory_type(part):
    memory_type = str(_get_spec(part, 'memory_type', '') or '').upper()
    if memory_type in {'DDR4', 'DDR5'}:
        return memory_type

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()
    if 'DDR5' in text:
        return 'DDR5'
    if 'DDR4' in text:
        return 'DDR4'
    return ''


def _infer_memory_capacity_gb(part):
    try:
        capacity = int(_get_spec(part, 'capacity_gb', 0) or 0)
    except (TypeError, ValueError):
        capacity = 0
    if capacity > 0:
        return capacity

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()

    kit_match = re.search(r'(\d+)\s*GB\s*[X×*]\s*(\d+)', text)
    if kit_match:
        return int(kit_match.group(1)) * int(kit_match.group(2))

    pair_match = re.search(r'(\d+)\s*GB[^\d]{0,12}(\d+)\s*枚組', text)
    if pair_match:
        return int(pair_match.group(1)) * int(pair_match.group(2))

    single_match = re.search(r'(\d+)\s*GB', text)
    if single_match:
        return int(single_match.group(1))

    return 0


def _infer_memory_speed_mhz(part):
    try:
        speed = int(_get_spec(part, 'speed_mhz', 0) or 0)
    except (TypeError, ValueError):
        speed = 0
    if speed > 0:
        return speed

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()

    pc5_match = re.search(r'PC5-(\d{5})', text)
    if pc5_match:
        return int(int(pc5_match.group(1)) / 8)

    pc4_match = re.search(r'PC4-(\d{5})', text)
    if pc4_match:
        return int(int(pc4_match.group(1)) / 8)

    mhz_match = re.search(r'(\d{4,5})\s*MHZ', text)
    if mhz_match:
        return int(mhz_match.group(1))

    return 0


def _infer_memory_module_count(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()

    kit_match = re.search(r'(\d+)\s*GB\s*[X×*]\s*(\d+)', text)
    if kit_match:
        return int(kit_match.group(2))

    pair_match = re.search(r'(\d+)\s*GB[^\d]{0,12}(\d+)\s*枚組', text)
    if pair_match:
        return int(pair_match.group(2))

    return 1


def _infer_motherboard_memory_type(part):
    memory_type = str(_get_spec(part, 'memory_type', '') or '').upper()
    if memory_type in {'DDR4', 'DDR5'}:
        return memory_type

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()
    if 'DDR5' in text:
        return 'DDR5'
    if 'DDR4' in text:
        return 'DDR4'

    # 規格が欠損している場合の保守的推定
    socket = str(_get_spec(part, 'socket', '') or '').upper()
    if socket == 'AM5':
        return 'DDR5'
    if socket == 'AM4':
        return 'DDR4'

    chipset = str(_get_spec(part, 'chipset', '') or '').upper()
    ddr4_chipsets = {'A320', 'A520', 'B450', 'B550', 'X470', 'X570'}
    ddr5_chipsets = {'A620', 'B650', 'B650E', 'X670', 'X670E', 'B850', 'X870', 'X870E'}
    if chipset in ddr4_chipsets:
        return 'DDR4'
    if chipset in ddr5_chipsets:
        return 'DDR5'

    # 名前/URLからのフォールバック推定
    if 'AM5' in text:
        return 'DDR5'
    if 'AM4' in text:
        return 'DDR4'

    if any(token in text for token in ddr5_chipsets):
        return 'DDR5'
    if any(token in text for token in ddr4_chipsets):
        return 'DDR4'

    return ''


def _minimum_memory_speed_for_selected_cpu(cpu_part, usage, options=None):
    options = options or {}
    if not cpu_part or usage != 'gaming':
        return None

    text = f"{getattr(cpu_part, 'name', '')} {getattr(cpu_part, 'url', '')}".lower()
    if '9850x3d' in text:
        return 5600

    return None


def _target_memory_profile(budget, usage, options=None):
    options = options or {}
    build_priority = options.get('build_priority')

    if usage in {'creator', 'workstation', 'ai'}:
        if usage in {'workstation', 'ai'}:
            if budget >= 500000:
                return {'capacity_gb': 64, 'preferred_modules': 2}
            return {'capacity_gb': 32, 'preferred_modules': 2}
        if budget >= 500000:
            return {'capacity_gb': 64, 'preferred_modules': 2}
        if budget >= 250000:
            return {'capacity_gb': 32, 'preferred_modules': 2}
        return {'capacity_gb': 32, 'preferred_modules': 2}

    if usage == 'gaming':
        if build_priority == 'spec':
            if budget >= 500000:
                return {'capacity_gb': 64, 'preferred_modules': 2}
            if budget >= 280000:
                return {'capacity_gb': 32, 'preferred_modules': 2}
            return {'capacity_gb': 16, 'preferred_modules': 1}
        if build_priority == 'cost':
            if budget >= 250000:
                return {'capacity_gb': 32, 'preferred_modules': 2}
            if budget >= 160000:
                return {'capacity_gb': 16, 'preferred_modules': 1}
            return {'capacity_gb': 8, 'preferred_modules': 1}
        if budget >= 400000:
            return {'capacity_gb': 32, 'preferred_modules': 2}
        if budget >= 220000:
            return {'capacity_gb': 16, 'preferred_modules': 2}
        return {'capacity_gb': 8, 'preferred_modules': 1}

    if usage in {'general', 'business', 'standard'}:
        if build_priority == 'cost':
            # コスト重視: 段階的容量（gaming に合わせた戦略）
            if budget >= 300000:
                return {'capacity_gb': 32, 'preferred_modules': 2}
            if budget >= 200000:
                return {'capacity_gb': 16, 'preferred_modules': 1}
            return {'capacity_gb': 8, 'preferred_modules': 1}
        else:
            # spec または build_priority 未指定
            if budget >= 300000:
                return {'capacity_gb': 32, 'preferred_modules': 2}
            return {'capacity_gb': 16, 'preferred_modules': 1}

    return {'capacity_gb': 16, 'preferred_modules': 1}


def _memory_profile_pick(candidates, build_priority, budget=None, usage=None, options=None):
    if not candidates:
        return None

    options = options or {}
    target_profile = _target_memory_profile(budget or 0, usage or options.get('usage', 'general'), options=options)
    target_capacity = target_profile['capacity_gb']
    preferred_modules = target_profile['preferred_modules']

    def _normalized_memory_type(part):
        return _infer_memory_type(part)

    def _capacity_gb(part):
        return _infer_memory_capacity_gb(part)

    def _module_count(part):
        return _infer_memory_module_count(part)

    min_memory_speed_mhz = options.get('min_memory_speed_mhz')
    if min_memory_speed_mhz:
        speed_filtered = [p for p in candidates if _infer_memory_speed_mhz(p) >= int(min_memory_speed_mhz)]
        if speed_filtered:
            candidates = speed_filtered

    if build_priority == 'cost':
        current_usage = usage or options.get('usage', 'general')
        creator_min_capacity_gb = 32 if current_usage in {'creator', 'workstation', 'ai'} else 0
        
        # usage別の preferred_capacity_gb 設定（fallback ロジック用）
        budget_val = budget or 0
        
        if current_usage == 'gaming':
            preferred_capacity_gb = 16 if budget_val >= 220000 else 8
        elif current_usage in {'general', 'business', 'standard'}:
            if budget_val >= 300000:
                preferred_capacity_gb = 32
            elif budget_val >= 200000:
                preferred_capacity_gb = 16
            else:
                preferred_capacity_gb = 8
        else:
            preferred_capacity_gb = 8
        
        # コスト重視: DDR4優先 + 小容量優先 + 同条件なら安価なもの
        return sorted(
            candidates,
            key=lambda p: (
                _capacity_gb(p) < creator_min_capacity_gb,
                _normalized_memory_type(p) != 'DDR4',
                _capacity_gb(p) < preferred_capacity_gb,
                _capacity_gb(p) > 16,
                _infer_memory_speed_mhz(p) < int(min_memory_speed_mhz or 0),
                _capacity_gb(p),
                p.price,
            ),
        )[0]

    if build_priority == 'spec':
        current_usage = usage or options.get('usage', 'general')
        if current_usage == 'gaming':
            return sorted(
                candidates,
                key=lambda p: (
                    _capacity_gb(p) < target_capacity,
                    _infer_memory_speed_mhz(p) < int(min_memory_speed_mhz or 0),
                    abs(_capacity_gb(p) - target_capacity),
                    _module_count(p) != preferred_modules,
                    p.price,
                    _normalized_memory_type(p) != 'DDR5',
                ),
            )[0]
        # スペック重視: DDR5優先 + 予算帯ごとの容量/枚数ルール優先
        return sorted(
            candidates,
            key=lambda p: (
                _normalized_memory_type(p) == 'DDR5',
                _capacity_gb(p) >= target_capacity,
                _infer_memory_speed_mhz(p) >= int(min_memory_speed_mhz or 0),
                -abs(_capacity_gb(p) - target_capacity),
                _module_count(p) == preferred_modules,
                _capacity_gb(p),
                -p.price,
            ),
            reverse=True,
        )[0]

    return None


def _target_memory_capacity_gb(budget, usage, options=None):
    return _target_memory_profile(budget, usage, options=options)['capacity_gb']


def _upgrade_memory_to_capacity_target(selected_parts, total_price, budget, usage, options=None):
    options = options or {}
    memory = selected_parts.get('memory')
    if not memory:
        return selected_parts, total_price

    current_capacity = _infer_memory_capacity_gb(memory)
    target_capacity = _target_memory_capacity_gb(budget, usage, options=options)
    if current_capacity >= target_capacity:
        return selected_parts, total_price

    affordable_max_price = memory.price + max(0, budget - total_price)
    if affordable_max_price <= memory.price:
        return selected_parts, total_price

    candidates = [
        p
        for p in PCPart.objects.filter(part_type='memory').order_by('price')
        if _is_part_suitable('memory', p)
        and _matches_selection_options('memory', p, options=options)
        and memory.price < p.price <= affordable_max_price
        and _infer_memory_capacity_gb(p) >= target_capacity
    ]
    if not candidates:
        return selected_parts, total_price

    upgraded_memory = sorted(
        candidates,
        key=lambda p: (
            _infer_memory_capacity_gb(p) == target_capacity,
            _infer_memory_module_count(p) == _target_memory_profile(budget, usage, options=options)['preferred_modules'],
            _infer_memory_type(p) == 'DDR5',
            -_infer_memory_capacity_gb(p),
            -p.price,
        ),
        reverse=True,
    )[0]

    adjusted = dict(selected_parts)
    adjusted['memory'] = upgraded_memory
    return adjusted, _sum_selected_price(adjusted)


def _infer_storage_capacity_gb(part):
    capacity = int(_get_spec(part, 'capacity_gb', 0) or 0)
    if capacity > 0:
        return capacity

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}"
    # TB単位を優先して検索し、モデル番号埋め込み (例: "F20GB") を除外するため負の後読みを使用
    tb_match = re.search(r'(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*TB', text, re.IGNORECASE)
    if tb_match:
        return int(float(tb_match.group(1)) * 1024)
    gb_match = re.search(r'(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*GB', text, re.IGNORECASE)
    if gb_match:
        return int(float(gb_match.group(1)))
    return 0


def _infer_storage_media_type(part):
    media_type = str(_get_spec(part, 'media_type', '') or '').strip().lower()
    if media_type in {'ssd', 'hdd'}:
        return media_type

    storage_type = str(_get_spec(part, 'storage_type', '') or '').strip().lower()
    if storage_type in {'ssd', 'hdd'}:
        return storage_type

    name_text = str(getattr(part, 'name', '') or '').lower()
    form_factor = str(_get_spec(part, 'form_factor', '') or '').strip().lower()
    interface = _infer_storage_interface(part)

    if interface == 'nvme':
        return 'ssd'
    # SSD キーワード・フォームファクター・名前中の M.2
    if 'ssd' in name_text or form_factor in {'m.2', '2.5inch'}:
        return 'ssd'
    if 'm.2' in name_text:
        return 'ssd'
    # WD SSD モデル番号 (SA500=SATA, SN500/580/700/750/850=NVMe)
    if re.search(r'\b(sa500|sn500|sn580|sn700|sn750|sn850)\b', name_text):
        return 'ssd'
    if re.search(r'(5400|7200|10000|15000)\s*rpm', name_text, re.IGNORECASE):
        return 'hdd'

    # HDD キーワードは "wd red" 単体を除外し "wd red wd" 等の HDDのみに絞る
    hdd_keywords = (
        'barracuda',
        'ironwolf',
        'wd blue wd',
        'wd green wd',
        'wd red wd',
        'wd purple wd',
        'mq04',
        'dt02',
        'n300',
        'mg10',
        'mg11',
        'hat3300',
        'hdd',
    )
    if any(keyword in name_text for keyword in hdd_keywords):
        return 'hdd'
    if interface == 'sata' and form_factor == '3.5inch':
        return 'hdd'
    if interface == 'sata' and form_factor in {'2.5inch', 'm.2'}:
        return 'ssd'
    return 'other'


def _storage_profile_pick(candidates, build_priority, storage_preference='ssd', options=None):
    if not candidates:
        return None

    # 既定はSSD優先。ただし gaming+spec では高容量HDDのフォールバックを許容する。
    ssd_candidates = [p for p in candidates if _infer_storage_media_type(p) == 'ssd']
    if ssd_candidates:
        candidates = ssd_candidates
    else:
        # SSD候補がない場合でも、HDD以外の"other"タイプを優先
        other_candidates = [p for p in candidates if _infer_storage_media_type(p) == 'other']
        if other_candidates:
            candidates = other_candidates
        # それでもなければ、HDD候補しかないため最低価格HDDを返さざるを得ない
        # ただしこれは本来あるべき状態ではない

    # === NVMe 優先フィルタ ===
    # メインストレージ選定では NVMe > SATA > other の優先度を明示的に適用
    # SSD が確定した後、その中から NVMe SSD に限定、次点として SATA SSD を許容
    nvme_candidates = [p for p in candidates if _infer_storage_interface(p) == 'nvme']
    if nvme_candidates:
        candidates = nvme_candidates
    else:
        # NVMe が無い場合は SATA に限定
        sata_candidates = [p for p in candidates if _infer_storage_interface(p) == 'sata']
        if sata_candidates:
            candidates = sata_candidates
        # それでもなければ、other タイプを使用（テキスト推論失敗の場合）

    prefer_hdd = False  # storage_preference == 'hdd' は廃止

    def _media_rank(part):
        media_type = _infer_storage_media_type(part)
        if media_type == ('hdd' if prefer_hdd else 'ssd'):
            return 0
        if media_type == ('ssd' if prefer_hdd else 'hdd'):
            return 1
        if media_type == 'other':
            return 2
        return 3

    def _interface_rank(part):
        interface = _infer_storage_interface(part)
        if interface == 'nvme':
            return 0
        if interface == 'sata':
            return 1
        return 2

    def _capacity(part):
        return _infer_storage_capacity_gb(part)

    if build_priority == 'cost':
        return sorted(
            candidates,
            key=lambda p: (
                _media_rank(p),
                p.price,
                _interface_rank(p),
                -_capacity(p),
            ),
        )[0]

    if build_priority == 'spec':
        # スペック重視: SSD > HDD、NVMe > SATA を最優先。
        # ただし SSD が 1TB未満しかない場合で、1TB以上のHDDがあるなら容量優先でHDDを許容する。
        all_ssd = [p for p in candidates if _infer_storage_media_type(p) == 'ssd']
        all_hdd = [p for p in (ssd_candidates or []) if _infer_storage_media_type(p) == 'hdd']
        if not all_hdd:
            storage_pool = _get_cached_parts_by_type('storage', options=options)
            all_hdd = [
                p for p in storage_pool
                if _is_part_suitable('storage', p) and _infer_storage_media_type(p) == 'hdd'
            ]

        if all_ssd and all_hdd:
            max_ssd_capacity = max(_capacity(p) for p in all_ssd)
            high_capacity_hdd = [p for p in all_hdd if _capacity(p) >= 1000]
            if max_ssd_capacity < 1000 and high_capacity_hdd:
                return sorted(
                    high_capacity_hdd,
                    key=lambda p: (
                        -_capacity(p),
                        p.price,
                    ),
                )[0]

        # 同一メディア・インターフェース階層内では 1TB+ を優先し、最安値を選ぶ。
        # 最高価格を選ぶと予算を大幅超過してダウングレードループが起きHDDが残るため。
        return sorted(
            candidates,
            key=lambda p: (
                _media_rank(p),
                _interface_rank(p),
                0 if _capacity(p) >= 1000 else 1,
                p.price,
            ),
        )[0]

    return sorted(
        candidates,
        key=lambda p: (
            _media_rank(p),
            _interface_rank(p),
            0 if _capacity(p) >= 1000 else 1,
            -_capacity(p),
            p.price,
        ),
    )[0]


def _required_power_w(usage):
    if usage in IGPU_USAGES:
        return int(IGPU_POWER_MAP.get(usage, 300) * 1.2)
    return int(USAGE_POWER_MAP.get(usage, 400) * 1.2)


def _compatibility_issues(selected_parts, usage, options=None):
    options = options or {}
    issues = []

    cpu = selected_parts.get('cpu')
    motherboard = selected_parts.get('motherboard')
    memory = selected_parts.get('memory')
    psu = selected_parts.get('psu')
    case = selected_parts.get('case')
    gpu = selected_parts.get('gpu')
    cpu_cooler = selected_parts.get('cpu_cooler')

    cooler_type = options.get('cooler_type', 'any')
    radiator_size = options.get('radiator_size', 'any')

    cpu_socket = _get_spec(cpu, 'socket')
    mb_socket = _infer_motherboard_socket(motherboard)
    if cpu and motherboard and cpu_socket and mb_socket and cpu_socket != mb_socket:
        issues.append('socket_mismatch')

    mb_mem_type = _infer_motherboard_memory_type(motherboard)
    mem_type = _infer_memory_type(memory)
    if motherboard and memory and mb_mem_type and mem_type and mb_mem_type != mem_type:
        issues.append('memory_type_mismatch')

    psu_watt = _get_spec(psu, 'wattage')
    required_psu_wattage = options.get('required_psu_wattage') or _required_psu_wattage(selected_parts, usage)
    if psu and psu_watt:
        if int(psu_watt) < int(required_psu_wattage):
            issues.append('psu_too_weak')

    mb_form = _infer_motherboard_form_factor(motherboard)
    if motherboard and case and mb_form not in {'', 'unknown'} and not _is_case_compatible_with_motherboard(case, mb_form):
        issues.append('form_factor_mismatch')

    gpu_len = _extract_numeric_mm(_get_spec(gpu, 'gpu_length_mm'))
    max_gpu_len = _extract_case_max_gpu_length_mm(case)
    if gpu and case and gpu_len and max_gpu_len and int(gpu_len) > int(max_gpu_len):
        issues.append('gpu_too_long')

    if cpu_cooler and case and cooler_type == 'liquid' and radiator_size != 'any':
        if not _is_case_radiator_compatible(case, radiator_size):
            issues.append('radiator_not_supported')

    return issues


def _pick_candidate(part_type, predicate, usage=None, options=None):
    """部品候補を条件に基づいて選定する。CPUの場合、gaming用途では2000以下を除外"""
    options = options or {}
    candidates = _get_cached_parts_by_type(part_type, options=options)
    require_gaming_x3d_cpu = options.get('require_gaming_x3d_cpu', False)
    cpu_vendor_opt = options.get('cpu_vendor', 'any')
    
    # gaming 用途: 性能目安表スコアが 2000 以下の CPU を除外
    if part_type == 'cpu' and usage == 'gaming':
        if require_gaming_x3d_cpu and cpu_vendor_opt == 'any':
            candidates = [p for p in candidates if _is_gaming_cpu_x3d_preferred(p)]
        minimum_perf_score = _minimum_gaming_cpu_perf_score(usage)
        if minimum_perf_score > 0 and not require_gaming_x3d_cpu:
            perf_filtered = [
                p for p in candidates
                if (
                    _is_gaming_cpu_x3d_preferred(p)
                    or (_get_cpu_perf_score(p) is not None and _get_cpu_perf_score(p) > minimum_perf_score)
                )
            ]
            if perf_filtered:
                candidates = perf_filtered
            else:
                candidates = []
    
    for candidate in candidates:
        if _is_part_suitable(part_type, candidate) and predicate(candidate):
            return candidate
    return None


def _matches_selection_options(part_type, part, options=None):
    options = options or {}
    cooler_type = options.get('cooler_type', 'any')
    radiator_size = options.get('radiator_size', 'any')
    case_size = options.get('case_size', 'any')
    cpu_vendor = options.get('cpu_vendor', 'any')
    os_edition = options.get('os_edition', 'auto')
    motherboard_memory_type = str(options.get('motherboard_memory_type', '') or '').upper()
    min_memory_speed_mhz = options.get('min_memory_speed_mhz')
    min_storage_capacity_gb = options.get('min_storage_capacity_gb')
    max_memory_capacity_gb = options.get('max_memory_capacity_gb')
    max_storage_capacity_gb = options.get('max_storage_capacity_gb')
    require_preferred_gaming_gpu = options.get('require_preferred_gaming_gpu', False)
    minimum_gaming_gpu_tier = options.get('minimum_gaming_gpu_tier', 1)
    required_psu_wattage = options.get('required_psu_wattage')
    usage = options.get('usage', 'general')
    enforce_main_storage_ssd = options.get('enforce_main_storage_ssd', True)
    require_gaming_x3d_cpu = options.get('require_gaming_x3d_cpu', False)

    if part_type == 'cpu_cooler':
        if not _is_cpu_cooler_product(part):
            return False
        if not _is_cpu_cooler_type_match(part, cooler_type):
            return False
        cpu_socket = options.get('cpu_socket')
        if cpu_socket and not _is_cpu_cooler_socket_compatible(part, cpu_socket):
            return False
        if not _is_allowed_cpu_cooler_brand(part):
            return False
        if usage in {'creator', 'workstation', 'ai'} and not (_is_liquid_cooler(part) or _is_dual_tower_cooler(part)):
            return False
        if cooler_type == 'liquid' and radiator_size != 'any' and not _is_radiator_size_match(part, radiator_size):
            return False
        return True

    if part_type == 'case':
        if not _is_case_size_match(part, case_size):
            return False

        motherboard_form_factor = str(options.get('motherboard_form_factor', '') or '').lower()
        if motherboard_form_factor and not _is_case_compatible_with_motherboard(part, motherboard_form_factor):
            return False

        if not _is_case_gpu_length_compatible(part, options.get('gpu_length_mm')):
            return False

        if cooler_type == 'liquid' and radiator_size != 'any' and not _is_case_radiator_compatible(part, radiator_size):
            return False
        return True

    if part_type == 'cpu':
        # ゲーミング用途は AMD の順位表を基準に選定する
        if usage == 'gaming' and _is_gaming_excluded_creator_cpu(part):
            return False
        if usage == 'creator' and UNSTABLE_INTEL_CORE_I_PATTERN.search(f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}"):
            return False
        if usage == 'gaming' and cpu_vendor == 'any' and not _is_cpu_vendor_match(part, 'amd'):
            return False
        if usage == 'gaming' and require_gaming_x3d_cpu and cpu_vendor == 'any' and not _is_gaming_cpu_x3d_preferred(part):
            return False
        if usage == 'gaming' and not require_gaming_x3d_cpu:
            if _is_gaming_cpu_x3d_preferred(part):
                return True
            rank = _get_amd_cpu_rank(part, options.get('build_priority', 'balanced'))
            if rank is not None:
                return True
            minimum_perf_score = _minimum_gaming_cpu_perf_score(usage)
            if minimum_perf_score > 0:
                score = _get_cpu_perf_score(part)
                if score is not None and score <= minimum_perf_score:
                    return False
        if usage == 'creator' and not _cpu_meets_creator_minimum(part, min_cores=8, min_threads=16):
            return False
        if usage in {'workstation', 'ai'} and not _is_ai_latest_generation_cpu(part):
            return False
        return _is_cpu_vendor_match(part, cpu_vendor)

    if part_type == 'gpu':
        if usage in {'workstation', 'ai'} and _infer_gpu_memory_gb(part) < 8:
            return False
        if usage in {'workstation', 'ai'} and not _is_ai_latest_generation_gpu(part):
            return False
        if usage == 'gaming' and _is_gaming_creative_gpu(part):
            return False
        if usage == 'gaming' and not _is_gaming_gpu_within_priority_cap(
            part,
            options.get('build_priority', 'balanced'),
            budget=options.get('budget'),
        ):
            return False
        gaming_gpu_policy = _gaming_low_end_gpu_policy(options.get('budget', 0), usage, options.get('build_priority', 'balanced'))
        if gaming_gpu_policy and int(getattr(part, 'price', 0) or 0) > int(gaming_gpu_policy['price_cap']):
            return False
        if require_preferred_gaming_gpu and not _is_gaming_spec_gpu_preferred(part, minimum_gaming_gpu_tier):
            return False
        if usage == 'creator' and _is_creator_premium_budget(options.get('budget')):
            if _is_creator_r9700_gpu(part) or _is_creator_rtx5090_gpu(part):
                return True
        return not _is_gt_series_gpu(part)

    if part_type == 'motherboard':
        cpu_socket = options.get('cpu_socket')
        if cpu_socket:
            mb_socket = _infer_motherboard_socket(part)
            if mb_socket and mb_socket != cpu_socket:
                return False
        max_chipset = options.get('max_motherboard_chipset', 'any')
        if max_chipset != 'any':
            chipset = _infer_motherboard_chipset(part)
            if max_chipset == 'x870' and chipset == 'x870e':
                return False
            if max_chipset == 'x670' and chipset in ('x870e', 'x870', 'x670e'):
                return False
        current_case_size = options.get('case_size', 'any')
        preferred_form_factors = _preferred_motherboard_form_factors(current_case_size)
        if preferred_form_factors:
            form_factor_cache = options.get('_mb_form_factor_preference_cache')
            if form_factor_cache is None:
                form_factor_cache = {}
                options['_mb_form_factor_preference_cache'] = form_factor_cache
            cache_key = (str(cpu_socket or ''), str(current_case_size or 'any'))
            has_preferred_candidates = form_factor_cache.get(cache_key)
            if has_preferred_candidates is None:
                motherboard_pool = _get_cached_parts_by_type('motherboard', options=options)
                has_preferred_candidates = any(
                    _is_part_suitable('motherboard', candidate)
                    and (not cpu_socket or _infer_motherboard_socket(candidate) == cpu_socket)
                    and _infer_motherboard_form_factor(candidate) in preferred_form_factors
                    for candidate in motherboard_pool
                )
                form_factor_cache[cache_key] = has_preferred_candidates
            if has_preferred_candidates and _infer_motherboard_form_factor(part) not in preferred_form_factors:
                return False
        return True

    if part_type == 'memory':
        if motherboard_memory_type:
            mem_type = _infer_memory_type(part)
            if mem_type and mem_type != motherboard_memory_type:
                return False
        if min_memory_speed_mhz and _infer_memory_speed_mhz(part) < int(min_memory_speed_mhz):
            return False
        if usage in {'creator', 'workstation', 'ai'} and _infer_memory_capacity_gb(part) < 32:
            return False
        if usage == 'general' and _infer_memory_capacity_gb(part) < 16:
            return False
        if max_memory_capacity_gb and _infer_memory_capacity_gb(part) > int(max_memory_capacity_gb):
            return False
        return True

    if part_type == 'storage':
        if enforce_main_storage_ssd and _infer_storage_media_type(part) != 'ssd':
            return False
        if min_storage_capacity_gb:
            capacity_gb = _infer_storage_capacity_gb(part)
            if capacity_gb < int(min_storage_capacity_gb):
                return False
        if max_storage_capacity_gb:
            capacity_gb = _infer_storage_capacity_gb(part)
            if capacity_gb > int(max_storage_capacity_gb):
                return False
        return True

    if part_type == 'psu':
        if required_psu_wattage is None:
            return True
        try:
            wattage = int(_get_spec(part, 'wattage', 0) or 0)
        except (TypeError, ValueError):
            wattage = 0
        return wattage >= int(required_psu_wattage)

    if part_type == 'os':
        return _is_os_edition_match(part, os_edition)

    return True


def _resolve_compatibility(selected_parts, usage, options=None):
    options = options or {}
    case_size = options.get('case_size', 'any')
    for _ in range(10):
        issues = _compatibility_issues(selected_parts, usage, options=options)
        if not issues:
            return selected_parts

        issue = issues[0]
        cpu = selected_parts.get('cpu')
        motherboard = selected_parts.get('motherboard')
        memory = selected_parts.get('memory')

        if issue == 'socket_mismatch':
            cpu_socket = _get_spec(cpu, 'socket')
            mb_socket = _infer_motherboard_socket(motherboard)
            replaced = False
            if cpu_socket:
                motherboard_candidates = [
                    candidate for candidate in _get_cached_parts_by_type('motherboard', options=options)
                    if _is_part_suitable('motherboard', candidate) and _infer_motherboard_socket(candidate) == cpu_socket
                ]
                motherboard_candidates = _prefer_motherboard_candidates(motherboard_candidates, case_size)
                new_mb = motherboard_candidates[0] if motherboard_candidates else None
                if new_mb:
                    selected_parts['motherboard'] = new_mb
                    replaced = True
            if not replaced and mb_socket:
                new_cpu = _pick_candidate('cpu', lambda p: _get_spec(p, 'socket') == mb_socket, usage=usage, options=options)
                if new_cpu:
                    selected_parts['cpu'] = new_cpu
                    replaced = True
            if not replaced:
                break

        elif issue == 'memory_type_mismatch':
            mb_mem_type = _infer_motherboard_memory_type(motherboard)
            mem_type = _infer_memory_type(memory)
            # まずメモリをマザーボードの規格に合わせて変更
            if mb_mem_type:
                new_mem = _pick_candidate('memory', lambda p: _infer_memory_type(p) == mb_mem_type)
                if new_mem:
                    selected_parts['memory'] = new_mem
                    continue
            # マザーボードに対応するメモリが存在しなければ、マザーボードをメモリ規格に合わせて変更
            if mem_type:
                cpu_socket = _get_spec(cpu, 'socket') if cpu else None
                def _mb_fits_mem(p, _mem_type=mem_type, _cpu_socket=cpu_socket):
                    if _infer_motherboard_memory_type(p) != _mem_type:
                        return False
                    p_socket = _infer_motherboard_socket(p)
                    if _cpu_socket and p_socket and p_socket != _cpu_socket:
                        return False
                    return True
                motherboard_candidates = [
                    candidate for candidate in _get_cached_parts_by_type('motherboard', options=options)
                    if _is_part_suitable('motherboard', candidate) and _mb_fits_mem(candidate)
                ]
                motherboard_candidates = _prefer_motherboard_candidates(motherboard_candidates, case_size)
                new_mb = motherboard_candidates[0] if motherboard_candidates else None
                if new_mb:
                    selected_parts['motherboard'] = new_mb
                else:
                    break
            else:
                break

        elif issue == 'psu_too_weak':
            required_w = options.get('required_psu_wattage') or _required_psu_wattage(selected_parts, usage)
            psu_candidates = [
                p
                for p in _get_cached_parts_by_type('psu', options=options)
                if _is_part_suitable('psu', p)
                and int(_get_spec(p, 'wattage', 0) or 0) >= int(required_w)
            ]
            psu_candidates = _filter_psu_candidates_by_headroom(
                psu_candidates,
                required_w,
                usage=options.get('usage', usage),
                build_priority=options.get('build_priority'),
            )
            new_psu = psu_candidates[0] if psu_candidates else None
            if new_psu:
                selected_parts['psu'] = new_psu
                options = dict(options)
                options['required_psu_wattage'] = _required_psu_wattage(selected_parts, usage)
            else:
                break

        elif issue == 'form_factor_mismatch':
            mb_form = _infer_motherboard_form_factor(motherboard)
            if mb_form in {'', 'unknown'}:
                break
            new_case = _pick_candidate(
                'case',
                lambda p: _is_case_size_match(p, case_size) and _is_case_compatible_with_motherboard(p, mb_form),
            )
            if new_case:
                selected_parts['case'] = new_case
            else:
                break

        elif issue == 'gpu_too_long':
            gpu = selected_parts.get('gpu')
            gpu_len = _extract_numeric_mm(_get_spec(gpu, 'gpu_length_mm'))
            if not gpu_len:
                break
            new_case = _pick_candidate(
                'case',
                lambda p: _is_case_size_match(p, case_size) and _is_case_gpu_length_compatible(p, gpu_len),
            )
            if new_case:
                selected_parts['case'] = new_case
            else:
                break

        elif issue == 'radiator_not_supported':
            radiator_size = options.get('radiator_size', 'any')
            preferred_case = _pick_candidate(
                'case',
                lambda p: _is_case_size_match(p, case_size) and _is_case_radiator_compatible(p, radiator_size),
            )
            if preferred_case:
                selected_parts['case'] = preferred_case
                continue

            # 希望サイズに対応ケースがない場合は、ケースサイズ制約を緩和して互換性を優先
            fallback_case = _pick_candidate(
                'case',
                lambda p: _is_case_radiator_compatible(p, radiator_size),
            )
            if fallback_case:
                selected_parts['case'] = fallback_case
            else:
                break

        else:
            break

    return selected_parts


def _downgrade_selected_parts(selected_parts, total_price, budget, options=None):
    if total_price <= budget:
        return selected_parts, total_price

    options = options or {}
    protect_x3d_cpu = (
        options.get('usage') == 'gaming'
        and (
            options.get('build_priority') == 'spec'
            or options.get('require_gaming_x3d_cpu', False)
        )
    )
    
    # ローエンド gaming/cost では GPU を下限の low-end policy で保護
    protect_low_end_gpu = (
        options.get('usage') == 'gaming'
        and options.get('build_priority') == 'cost'
        and options.get('budget') < _budget_tier_threshold('gaming', 'low')
    )

    changed = True
    while changed and total_price > budget:
        changed = False
        for part_type, current in sorted(selected_parts.items(), key=lambda item: item[1].price if item[1] else 0, reverse=True):
            if current is None:
                continue
            if protect_x3d_cpu and part_type == 'cpu' and _is_gaming_cpu_x3d_preferred(current):
                continue
            # ローエンド gaming/cost では GPU を absolutely 保護（downgrade しない）
            if protect_low_end_gpu and part_type == 'gpu':
                continue

            build_priority = options.get('build_priority', 'balanced')
            part_pool = _get_cached_parts_by_type(part_type, options=options)
            cheaper_candidates = [
                c for c in reversed(part_pool)
                if c.price < current.price
                and _is_part_suitable(part_type, c)
                and _matches_selection_options(part_type, c, options=options)
            ]
            if part_type == 'storage' and options.get('build_priority') != 'spec':
                cheaper_candidates = [c for c in cheaper_candidates if _infer_storage_media_type(c) == 'ssd']
                nvme_candidates = [c for c in cheaper_candidates if _infer_storage_interface(c) == 'nvme']
                if nvme_candidates:
                    cheaper_candidates = nvme_candidates
            cheaper = None
            if cheaper_candidates:
                if part_type == 'storage' and build_priority == 'spec':
                    storage_preference = options.get('storage_preference', 'ssd')
                    cheaper = _storage_profile_pick(cheaper_candidates, build_priority, storage_preference, options=options)
                elif (
                    part_type == 'gpu'
                    and options.get('usage') == 'gaming'
                    and build_priority == 'spec'
                ):
                    minimum_tier = options.get('minimum_gaming_gpu_tier', 1)
                    preferred_gpu = [c for c in cheaper_candidates if _is_gaming_spec_gpu_preferred(c, minimum_tier)]
                    gpu_pool = preferred_gpu or cheaper_candidates
                    gpu_pool = _prefer_rx_xt_value_candidates(gpu_pool)
                    cheaper = gpu_pool[0]
                else:
                    cheaper = cheaper_candidates[0]
            if cheaper:
                total_price -= (current.price - cheaper.price)
                selected_parts[part_type] = cheaper
                changed = True
                if total_price <= budget:
                    break

    return selected_parts, total_price


def _drop_until_budget(selected_parts, total_price, budget, options=None):
    if total_price <= budget:
        return selected_parts, total_price

    options = options or {}
    protect_x3d_cpu = options.get('usage') == 'gaming' and options.get('require_gaming_x3d_cpu', False)
    protect_low_end_gpu = (
        options.get('usage') == 'gaming'
        and options.get('build_priority') == 'cost'
        and options.get('budget', 0) < _budget_tier_threshold('gaming', 'low')
    )

    for part_type in CATEGORY_DROP_PRIORITY:
        part = selected_parts.get(part_type)
        if part is None:
            continue
        if protect_x3d_cpu and part_type == 'cpu' and _is_gaming_cpu_x3d_preferred(part):
            continue
        if protect_low_end_gpu and part_type == 'gpu':
            continue
        selected_parts[part_type] = None
        total_price -= part.price
        if total_price <= budget:
            break

    return selected_parts, total_price


def _sum_selected_price(selected_parts):
    return sum(part.price for part in selected_parts.values() if part is not None)


def _serialize_selected_parts(selected_parts, extra_storage_parts=None, use_igpu=False):
    selected = []
    for part_type in PART_ORDER:
        part = selected_parts.get(part_type)
        if not part:
            continue
        selected.append({
            'category': part_type,
            'name': part.name,
            'price': part.price,
            'url': part.url,
            'specs': part.specs,
        })

    extra_storage_parts = extra_storage_parts or {}
    for part_type in ('storage2', 'storage3'):
        part = extra_storage_parts.get(part_type)
        if not part:
            continue
        selected.append({
            'category': part_type,
            'name': part.name,
            'price': part.price,
            'url': part.url,
            'specs': part.specs,
        })

    if use_igpu:
        cpu_part = selected_parts.get('cpu')
        igpu_entry = {
            'category': 'gpu',
            'name': '内蔵GPU（統合グラフィックス）',
            'price': 0,
            'url': cpu_part.url if cpu_part else '',
        }
        cpu_index = next((i for i, p in enumerate(selected) if p['category'] == 'cpu'), -1)
        selected.insert(cpu_index + 1, igpu_entry)

    return selected


def _enforce_required_os_with_budget_policy(selected_parts, budget, options=None):
    options = options or {}
    adjusted = dict(selected_parts)
    policy_notes = []
    usage = options.get('usage')
    build_priority = options.get('build_priority')

    os_part = adjusted.get('os')

    if usage in {'general', 'business', 'standard'} and build_priority == 'cost':
        home_candidates = [
            p
            for p in _get_cached_parts_by_type('os', options=options)
            if _is_part_suitable('os', p) and _is_os_edition_match(p, 'home')
        ]
        if home_candidates:
            preferred_home = min(home_candidates, key=lambda p: p.price)
            if os_part is None or not _is_os_edition_match(os_part, 'home'):
                adjusted['os'] = preferred_home
                os_part = preferred_home
                policy_notes.append('汎用コスト重視のため、OSをHome版に調整しました。')

    if os_part is None:
        os_candidates = [
            p
            for p in _get_cached_parts_by_type('os', options=options)
            if _is_part_suitable('os', p) and _matches_selection_options('os', p, options=options)
        ]
        if not os_candidates:
            return adjusted, budget, None, Response(
                {'detail': 'OS必須予算不足: 利用可能なOS候補が見つかりません。条件を緩めるか、予算を増やしてください。'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        adjusted['os'] = min(os_candidates, key=lambda p: p.price)
        policy_notes.append('OSを必須として追加しました。')

    trial_total = _sum_selected_price(adjusted)
    if trial_total <= budget:
        return adjusted, budget, ' '.join(policy_notes) if policy_notes else None, None

    dropped_labels = []
    drop_label_map = {
        'cpu_cooler': 'CPUクーラー',
        'case': 'ケース',
    }
    for part_type in ('cpu_cooler', 'case'):
        part = adjusted.get(part_type)
        if part is None:
            continue
        adjusted[part_type] = None
        trial_total -= part.price
        dropped_labels.append(drop_label_map[part_type])
        if trial_total <= budget:
            drop_note = f"OS必須のため、予算内に収めるため{'/'.join(dropped_labels)}を調整しました。"
            policy_notes.append(drop_note)
            return adjusted, budget, ' '.join(policy_notes), None

    required_budget = int(trial_total)
    if required_budget <= 1500000:
        drop_note = ''
        if dropped_labels:
            drop_note = f"{'/'.join(dropped_labels)}を調整しても不足したため、"
        policy_notes.append(f"OS必須のため、{drop_note}予算を¥{required_budget:,}へ自動補正しました。")
        return adjusted, required_budget, ' '.join(policy_notes), None

    return adjusted, budget, None, Response(
        {
            'detail': (
                f"OS必須予算不足: CPUクーラー/ケースを調整しても不足しています。"
                f"最低でも¥{required_budget:,}が必要です。"
            )
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _upgrade_fallback_config_for_budget_utilization(config_response, budget, usage, options=None):
    """
    フォールバック後のレスポンスに対して、残りの予算内で各部品をアップグレードする。
    コスト重視にフォールバックした後、高性能部品で予算を有効活用する。
    """
    options = options or {}
    if not config_response or not isinstance(config_response, dict):
        return config_response
    
    try:
        total_price = config_response.get('total_price', 0)
        if total_price >= budget:
            return config_response  # 既に予算を使い切っている
        
        remaining_budget = budget - total_price
        if remaining_budget < 5000:
            return config_response  # 残りが少なすぎる
        
        # パーツデータから'selected_parts'ディクショナリを再構成（レスポンス形式）
        selected_parts_by_category = {}
        for part in config_response.get('parts', []):
            category = part.get('category')
            if category:
                selected_parts_by_category[category] = part
        
        requested_priority = options.get('build_priority')
        general_spec_mode = usage in {'general', 'business', 'standard'} and requested_priority == 'spec'
        general_low_tier_spec_mode = general_spec_mode and _classify_budget_tier(int(budget or 0), usage=usage) == 'low'
        target_memory_capacity = _target_memory_capacity_gb(budget, usage, options=options)
        if general_low_tier_spec_mode:
            upgrade_order = ['memory', 'storage']
        else:
            upgrade_order = ['cpu', 'motherboard', 'cpu_cooler', 'memory'] if general_spec_mode else ['cpu', 'memory', 'gpu']

        upgraded = False
        for part_type in upgrade_order:
            current_part = selected_parts_by_category.get(part_type)
            if not current_part:
                continue
            
            current_price = current_part.get('price', 0)
            current_capacity = _infer_memory_capacity_gb(current_part) if part_type == 'memory' else None
            effective_options = dict(options)
            effective_options.setdefault('usage', usage)
            effective_options.setdefault('budget', budget)
            current_cpu_part = selected_parts_by_category.get('cpu')
            current_mb_part = selected_parts_by_category.get('motherboard')
            if current_cpu_part:
                cpu_socket = str((current_cpu_part.get('specs') or {}).get('socket', '') or '').upper()
                if cpu_socket:
                    effective_options['cpu_socket'] = cpu_socket
            if current_mb_part:
                mb_mem_type = _infer_motherboard_memory_type(current_mb_part)
                if mb_mem_type:
                    effective_options['motherboard_memory_type'] = mb_mem_type
            
            # 現在の部品より高い性能の候補をDB から探す
            candidates = [
                p for p in _get_cached_parts_by_type(part_type, options=options)
                if _is_part_suitable(part_type, p)
                and _matches_selection_options(part_type, p, options=effective_options)
                and current_price < p.price <= current_price + remaining_budget
            ]
            if part_type == 'memory' and general_spec_mode:
                candidates = [
                    p for p in candidates
                    if _infer_memory_capacity_gb(p) <= target_memory_capacity
                    and _infer_memory_capacity_gb(p) > (current_capacity or 0)
                ]
            if part_type == 'storage' and general_low_tier_spec_mode:
                candidates = [p for p in candidates if _infer_storage_media_type(p) == 'ssd']
                storage_price_cap = max(18000, int(budget * 0.12))
                capped_by_price = [p for p in candidates if p.price <= storage_price_cap]
                if not capped_by_price:
                    candidates = []
                else:
                    candidates = capped_by_price
                capped_by_capacity = [p for p in candidates if _infer_storage_capacity_gb(p) <= 1024]
                if not capped_by_capacity:
                    candidates = []
                else:
                    candidates = capped_by_capacity
            
            if candidates:
                if part_type == 'cpu':
                    best = max(candidates, key=lambda p: _get_cpu_perf_score(p) or 0)
                elif part_type == 'motherboard':
                    best = max(candidates, key=lambda p: _creator_motherboard_expandability_score(p))
                elif part_type == 'cpu_cooler':
                    best = max(
                        candidates,
                        key=lambda p: (
                            _cpu_cooler_profile_score(p, options.get('cooling_profile', 'balanced'), options.get('cooler_type', 'any')),
                            p.price,
                        ),
                    )
                elif part_type == 'memory':
                    best = max(
                        candidates,
                        key=lambda p: (
                            _infer_memory_capacity_gb(p),
                            _infer_memory_module_count(p),
                            -p.price,
                        ),
                    )
                elif part_type == 'storage':
                    storage_priority = 'cost' if general_low_tier_spec_mode else options.get('build_priority', 'balanced')
                    best = _storage_profile_pick(
                        candidates,
                        storage_priority,
                        options.get('storage_preference', 'ssd'),
                        options=effective_options,
                    ) or candidates[0]
                elif part_type == 'gpu':
                    best = max(candidates, key=lambda p: _infer_gaming_gpu_perf_score(p) or 0)
                else:
                    best = candidates[-1]
                
                price_diff = best.price - current_price
                if 0 < price_diff <= remaining_budget:
                    # レスポンス形式に合わせてアップグレード
                    selected_parts_by_category[part_type] = {
                        'category': part_type,
                        'name': best.name,
                        'price': best.price,
                        'url': best.url or '',
                        'specs': best.specs,
                    }
                    remaining_budget -= price_diff
                    total_price += price_diff
                    upgraded = True
        
        # パーツリストを更新
        if upgraded:
            original_order = [part.get('category') for part in config_response.get('parts', [])]
            config_response['parts'] = [
                selected_parts_by_category[category]
                for category in original_order
                if category in selected_parts_by_category
            ]
            config_response['total_price'] = total_price
    except Exception:
        # エラーが発生した場合はオリジナルを返す
        pass
    
    return config_response


def _upgrade_memory_with_surplus(selected_parts, total_price, budget, usage, options=None):
    options = options or {}
    if total_price >= budget:
        return selected_parts, total_price

    if options.get('build_priority') == 'cost':
        return selected_parts, total_price

    memory = selected_parts.get('memory')
    if not memory:
        return selected_parts, total_price

    target_capacity = _target_memory_capacity_gb(budget, usage, options=options)
    if _infer_memory_capacity_gb(memory) >= target_capacity:
        return selected_parts, total_price

    affordable_max_price = memory.price + (budget - total_price)

    gpu = selected_parts.get('gpu')
    if usage == 'gaming' and options.get('build_priority') == 'spec' and gpu:
        # gaming + spec はGPU優先を維持し、メモリがGPU価格を超えない範囲で増強する。
        affordable_max_price = min(affordable_max_price, gpu.price)

    if affordable_max_price <= memory.price:
        return selected_parts, total_price

    candidates = [
        p
        for p in _get_cached_parts_by_type('memory', options=options)
        if _is_part_suitable('memory', p)
        and _matches_selection_options('memory', p, options=options)
        and memory.price < p.price <= affordable_max_price
        and _infer_memory_capacity_gb(p) <= target_capacity
    ]
    if not candidates:
        return selected_parts, total_price

    preferred = _memory_profile_pick(candidates, 'spec', budget=budget, usage=usage, options=options)
    upgraded_memory = preferred or candidates[-1]

    adjusted = dict(selected_parts)
    adjusted['memory'] = upgraded_memory
    return adjusted, _sum_selected_price(adjusted)


def _rebalance_gaming_cost_gpu_for_memory(selected_parts, total_price, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'cost':
        return selected_parts, total_price
    if budget < 240000:
        return selected_parts, total_price

    current_gpu = selected_parts.get('gpu')
    current_memory = selected_parts.get('memory')
    if not current_gpu or not current_memory:
        return selected_parts, total_price
    if _infer_memory_capacity_gb(current_memory) >= 32:
        return selected_parts, total_price

    base_without_gpu_mem = total_price - current_gpu.price - current_memory.price
    if base_without_gpu_mem <= 0:
        return selected_parts, total_price

    gpu_candidates = [
        p
        for p in _get_cached_parts_by_type('gpu', options=options)
        if _is_part_suitable('gpu', p)
        and not _is_gt_series_gpu(p)
        and not _is_gaming_creative_gpu(p)
        and _matches_selection_options('gpu', p, options=options)
        and p.price <= current_gpu.price
        and _is_gaming_gpu_within_priority_cap(p, 'cost', budget=budget)
    ]
    if not gpu_candidates:
        return selected_parts, total_price

    memory_candidates = [
        p
        for p in _get_cached_parts_by_type('memory', options=options)
        if _is_part_suitable('memory', p)
        and _matches_selection_options('memory', p, options=options)
        and _infer_memory_capacity_gb(p) >= 32
    ]
    if not memory_candidates:
        return selected_parts, total_price

    best_combo = None
    for gpu in gpu_candidates:
        for mem in memory_candidates:
            trial_total = base_without_gpu_mem + gpu.price + mem.price
            if trial_total > budget:
                continue
            perf = _infer_gaming_gpu_perf_score(gpu)
            if perf < 2600:
                continue
            rank_key = (
                -gpu.price,
                perf,
                -_infer_memory_capacity_gb(mem),
                -mem.price,
                -trial_total,
            )
            if best_combo is None or rank_key > best_combo[0]:
                best_combo = (rank_key, gpu, mem, trial_total)

    if not best_combo:
        return selected_parts, total_price

    _, chosen_gpu, chosen_mem, _ = best_combo
    adjusted = dict(selected_parts)
    adjusted['gpu'] = chosen_gpu
    adjusted['memory'] = chosen_mem
    adjusted = _resolve_compatibility(adjusted, usage, options=options)
    return adjusted, _sum_selected_price(adjusted)


def _upgrade_parts_with_surplus(selected_parts, total_price, budget, usage, options=None):
    """余剰予算が大きい場合に優先度順でパーツをアップグレードし、予算を有効活用する。"""
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')

    # cost は「最安」寄りを維持しつつ、予算からの極端な下振れだけ抑える。
    target_budget = budget
    if build_priority == 'cost':
        cost_budget_anchor = int(options.get('auto_adjust_reference_budget') or budget)
        utilization_floor_by_usage = {
            'gaming': 0.82,
            'creator': 0.92,
            'business': 0.65,
            'standard': 0.65,
        }
        floor_ratio = utilization_floor_by_usage.get(usage, 0.65)
        target_budget = int(cost_budget_anchor * floor_ratio)
        if total_price >= target_budget:
            return selected_parts, total_price

    use_igpu = usage in IGPU_USAGES
    # general/business: spec重視かつ予算しきい値以上なら dGPU アップグレードも許可
    # (standard は USAGE_COMPAT_ALIASES により general に正規化されるため general を対象とする)
    if use_igpu and usage in {'general', 'business'} \
            and build_priority == 'spec' \
            and budget >= SPEC_GPU_UNLOCK_BUDGET_THRESHOLD:
        use_igpu = False
    upgrade_order = UPGRADE_PRIORITY_BY_USAGE.get(usage, list(PART_ORDER))
    if usage == 'creator':
        # 予算余りの再配分はGPUを最優先にして、体感性能を引き上げる。
        upgrade_order = ['gpu'] + [p for p in upgrade_order if p != 'gpu']
    elif not use_igpu and usage in {'general', 'business'}:
        # spec+dGPU解禁時: CPUは固定してGPUと電源のみをアップグレード対象にする
        upgrade_order = ['gpu', 'psu']

    for _ in range(len(upgrade_order) * 4):
        surplus = target_budget - total_price
        if surplus < 5000:
            break

        upgraded = False
        for part_type in upgrade_order:
            if use_igpu and part_type == 'gpu':
                continue
            if part_type == 'psu' and options.get('build_priority') == 'spec':
                # 余剰予算でPSUを肥大化させない。必要Wの見直しは互換/右サイズ処理に任せる。
                continue
            if part_type == 'cpu' and _is_general_low_tier(usage, budget):
                # 汎用 low 帯の spec/cost は、初期選定の低価格CPUを固定する。
                continue
            if part_type == 'motherboard' and _is_general_low_tier(usage, budget):
                # 汎用 low 帯では CPU に合わせた互換MB を維持し、上位化しない。
                continue
            if part_type == 'motherboard' and usage == 'gaming' and build_priority == 'spec':
                # gaming + spec はGPU優先のため、余剰予算でMBを再肥大化させない。
                continue
            if part_type == 'motherboard' and usage == 'gaming' and build_priority == 'cost':
                # gaming + cost はDDR4/廉価MBの選定方針を維持する。
                continue
            if part_type == 'memory' and usage == 'gaming' and build_priority == 'cost':
                # gaming + cost は基本的に低コスト構成を維持するが、
                # 予算消化率が不足している場合のみメモリ増設を許可する。
                if total_price >= int(budget * 0.82):
                    continue
            if part_type == 'storage' and _is_general_cost_low_tier(usage, build_priority, budget):
                # 汎用 low + cost はストレージの過剰上振れを防ぎ、低価格帯を維持する。
                continue
            if part_type == 'cpu_cooler' and _is_general_cost_low_tier(usage, build_priority, budget):
                # 汎用 low + cost はクーラーの過剰上振れを防ぐ。
                continue
            if part_type == 'cpu' and usage in {'general', 'business', 'standard'} and build_priority == 'cost':
                # 汎用 low + cost は AM4/Intel 優先の安価CPU方針を維持する。
                continue
            current = selected_parts.get(part_type)
            if not current:
                continue

            affordable_max = current.price + surplus
            part_pool = _get_cached_parts_by_type(part_type, options=options)
            better_candidates = [
                c for c in reversed(part_pool)
                if c.price > current.price
                and c.price <= affordable_max
                and _is_part_suitable(part_type, c)
                and _matches_selection_options(part_type, c, options=options)
            ]
            if part_type == 'storage':
                better_candidates = [c for c in better_candidates if _infer_storage_media_type(c) == 'ssd']
                nvme_candidates = [c for c in better_candidates if _infer_storage_interface(c) == 'nvme']
                if nvme_candidates:
                    better_candidates = nvme_candidates
            if part_type == 'memory' and build_priority == 'cost':
                current_capacity = _infer_memory_capacity_gb(current)
                current_speed = _infer_memory_speed_mhz(current)
                better_candidates = [
                    c for c in better_candidates
                    if (
                        _infer_memory_capacity_gb(c) > current_capacity
                        or _infer_memory_speed_mhz(c) > current_speed
                    )
                ]
            if part_type == 'memory' and usage in {'general', 'business', 'standard'}:
                memory_target_capacity = _target_memory_capacity_gb(budget, usage, options=options)
                current_capacity = _infer_memory_capacity_gb(current)
                if current_capacity >= memory_target_capacity:
                    continue
                better_candidates = [
                    c for c in better_candidates
                    if _infer_memory_capacity_gb(c) <= memory_target_capacity
                ]
            if part_type == 'gpu' and usage == 'creator':
                if _is_creator_premium_budget(budget):
                    if build_priority == 'cost':
                        if _is_creator_r9700_gpu(current):
                            continue
                        premium_exact = [c for c in better_candidates if _is_creator_r9700_gpu(c)]
                    else:
                        if _is_creator_rtx5090_gpu(current):
                            continue
                        premium_exact = [c for c in better_candidates if _is_creator_rtx5090_gpu(c)]
                    if premium_exact:
                        better_candidates = premium_exact
                    else:
                        continue
                else:
                    better_candidates = _prefer_creator_gpu_with_vram_flex(better_candidates, build_priority=build_priority)
                creator_gpu_cap = _creator_gpu_cap_price(budget, options=options)
                capped_candidates = [c for c in better_candidates if c.price <= creator_gpu_cap]
                if capped_candidates:
                    better_candidates = capped_candidates
            if part_type == 'gpu' and usage in {'general', 'business', 'standard'} and build_priority == 'spec':
                gpu_weights = _apply_build_priority_weights(usage, build_priority, use_igpu=False, budget=budget)
                gpu_target_price = None
                if gpu_weights:
                    gpu_target_price = int(budget * gpu_weights.get('gpu', 0.1))
                if gpu_target_price is not None:
                    # ティア目標価格の 1.1 倍を上限にしてティア逸脱を防ぐ
                    tier_cap = int(gpu_target_price * 1.1)
                    capped_better = [c for c in better_candidates if c.price <= tier_cap]
                    if capped_better:
                        better_candidates = capped_better
                    ranked_gpu_candidates = sorted(
                        better_candidates,
                        key=lambda c: (
                            abs(int(c.price) - gpu_target_price),
                            -_infer_gaming_gpu_perf_score(c),
                            c.price,
                        ),
                    )
                    if not ranked_gpu_candidates:
                        continue
                    current_gpu_distance = abs(int(current.price) - gpu_target_price)
                    best_gpu_distance = abs(int(ranked_gpu_candidates[0].price) - gpu_target_price)
                    # 目標価格から遠ざかるだけの上振れは避ける。
                    if best_gpu_distance >= current_gpu_distance:
                        continue
                    better_candidates = ranked_gpu_candidates
            if part_type == 'gpu' and usage == 'gaming' and build_priority == 'cost':
                # gaming + cost は low-end では 3050 クラス前後に留める。
                cap_budget = int(options.get('auto_adjust_reference_budget') or budget)
                gaming_cost_gpu_cap = int(cap_budget * 0.31)
                low_end_policy = _gaming_low_end_gpu_policy(budget, usage, build_priority)
                if low_end_policy:
                    target_price = int(low_end_policy['target_price'])
                    low_end_candidates = [
                        c for c in better_candidates
                        if not _is_gaming_cost_excluded_gpu(c)
                        and not _is_gaming_creative_gpu(c)
                        and c.price <= target_price
                    ]
                    if low_end_candidates:
                        better_candidates = low_end_candidates
                    else:
                        continue
                else:
                    non_excluded_candidates = [
                        c for c in better_candidates
                        if not _is_gaming_cost_excluded_gpu(c)
                        and not _is_gaming_creative_gpu(c)
                    ]
                    capped_candidates = [c for c in non_excluded_candidates if c.price <= gaming_cost_gpu_cap]
                    if capped_candidates:
                        better_candidates = capped_candidates
                    elif non_excluded_candidates:
                        better_candidates = non_excluded_candidates
            if part_type == 'cpu' and usage == 'creator':
                better_candidates = [c for c in better_candidates if not _is_cpu_x3d(c)]
            if part_type == 'cpu' and usage == 'workstation':
                requested_tier = _normalize_budget_tier_code(options.get('selected_budget_tier')) or _classify_budget_tier(int(budget or 0), usage=usage)
                better_candidates = [
                    c for c in better_candidates
                    if _matches_workstation_cpu_tier(c, requested_tier, build_priority=build_priority)
                ]
            if part_type == 'cpu' and usage == 'gaming' and build_priority == 'cost' and int(budget) < GAMING_PREMIUM_BUDGET_MIN:
                better_candidates = [
                    c for c in better_candidates
                    if '9850x3d' not in str(getattr(c, 'name', '') or '').lower()
                ]
            better = None
            if better_candidates:
                if part_type == 'storage' and build_priority == 'spec':
                    storage_preference = options.get('storage_preference', 'ssd')
                    better = _storage_profile_pick(better_candidates, build_priority, storage_preference, options=options)
                elif (
                    part_type == 'gpu'
                    and options.get('usage') == 'gaming'
                    and build_priority == 'spec'
                ):
                    minimum_tier = options.get('minimum_gaming_gpu_tier', 1)
                    preferred_gpu = [c for c in better_candidates if _is_gaming_spec_gpu_preferred(c, minimum_tier)]
                    gpu_pool = preferred_gpu or better_candidates
                    low_end_policy = _gaming_low_end_gpu_policy(budget, usage, build_priority)
                    if low_end_policy:
                        target_price = int(low_end_policy['target_price'])
                        gpu_pool = [c for c in gpu_pool if c.price <= int(low_end_policy['price_cap'])]
                        if not gpu_pool:
                            continue
                        gpu_pool = sorted(
                            gpu_pool,
                            key=lambda c: (
                                abs(int(c.price) - target_price),
                                -_infer_gaming_gpu_perf_score(c),
                                c.price,
                            ),
                        )
                    else:
                        gpu_pool = _prefer_rx_xt_value_candidates(gpu_pool)
                    better = gpu_pool[0]
                elif part_type == 'cpu' and build_priority == 'cost' and usage in {'general', 'business', 'standard'}:
                    # コスト重視の汎用系では最安・コスパ優先（Intel優先→価格昇順）で選ぶ
                    better = _pick_general_cost_cpu_candidate(better_candidates)
                else:
                    better = better_candidates[0]

            if better:
                total_price += better.price - current.price
                selected_parts[part_type] = better
                upgraded = True
                break

        if not upgraded:
            break

    return selected_parts, total_price


def _rebalance_gaming_spec_gpu_memory(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    gpu = selected_parts.get('gpu')
    memory = selected_parts.get('memory')
    if not gpu:
        return selected_parts

    target_memory_capacity = _target_memory_capacity_gb(budget, usage, options=options)

    if not memory:
        # 過程でメモリが欠落した場合、現行マザーボード条件で復元を試みる。
        mb = selected_parts.get('motherboard')
        recover_options = dict(options)
        if mb:
            mb_mem_type = _infer_motherboard_memory_type(mb)
            if mb_mem_type:
                recover_options['motherboard_memory_type'] = mb_mem_type
        recovered_memories = [
            p for p in _get_cached_parts_by_type('memory', options=recover_options)
            if _is_part_suitable('memory', p) and _matches_selection_options('memory', p, options=recover_options)
        ]
        if recovered_memories:
            repaired = dict(selected_parts)
            repaired['memory'] = recovered_memories[0]
            selected_parts = repaired
            memory = repaired.get('memory')
        else:
            return selected_parts

    current_memory_capacity = _infer_memory_capacity_gb(memory) if memory else 0
    require_target_capacity = current_memory_capacity < target_memory_capacity
    if gpu.price >= memory.price and current_memory_capacity >= target_memory_capacity:
        return selected_parts

    def _gpu_candidates(base_options):
        candidates = [
            p
            for p in _get_cached_parts_by_type('gpu', options=base_options)
            if _is_part_suitable('gpu', p) and _matches_selection_options('gpu', p, options=base_options)
        ]
        preferred = [p for p in candidates if _is_gaming_spec_gpu_preferred(p, base_options.get('minimum_gaming_gpu_tier', 1))]
        picked = preferred or candidates
        return _prefer_rx_xt_value_candidates(picked)

    def _memory_candidates(base_options):
        return [
            p
            for p in _get_cached_parts_by_type('memory', options=base_options)
            if _is_part_suitable('memory', p) and _matches_selection_options('memory', p, options=base_options)
        ]

    # 1) まずは現行マザーボード前提でGPU/メモリのみ再配分する。
    motherboard = selected_parts.get('motherboard')
    same_mb_options = dict(options)
    if motherboard:
        mb_mem_type = _infer_motherboard_memory_type(motherboard)
        if mb_mem_type:
            same_mb_options['motherboard_memory_type'] = mb_mem_type

    gpu_candidates = _gpu_candidates(same_mb_options)
    memory_candidates = _memory_candidates(same_mb_options)

    if gpu_candidates and memory_candidates:
        total_other = _sum_selected_price(selected_parts) - gpu.price - memory.price
        if total_other < 0:
            total_other = 0

        for gpu_candidate in reversed(gpu_candidates):
            max_memory_price = min(gpu_candidate.price, budget - total_other - gpu_candidate.price)
            if max_memory_price < 0:
                continue

            affordable_memories = [m for m in memory_candidates if m.price <= max_memory_price]
            if not affordable_memories:
                continue

            target_capacity_memories = [m for m in affordable_memories if _infer_memory_capacity_gb(m) >= target_memory_capacity]
            if target_capacity_memories:
                affordable_memories = target_capacity_memories
            elif require_target_capacity:
                continue

            memory_candidate = affordable_memories[-1]
            rebalanced = dict(selected_parts)
            rebalanced['gpu'] = gpu_candidate
            rebalanced['memory'] = memory_candidate
            return rebalanced

    # 2) それでも成立しない場合、マザーボード+メモリ+GPUを同時に再選定する。
    cpu = selected_parts.get('cpu')
    total_fixed = _sum_selected_price(selected_parts) - gpu.price - memory.price
    if motherboard:
        total_fixed -= motherboard.price
    if total_fixed < 0:
        total_fixed = 0

    motherboard_candidates = [
        p
        for p in _get_cached_parts_by_type('motherboard', options=options)
        if _is_part_suitable('motherboard', p) and _matches_selection_options('motherboard', p, options=options)
    ]

    if cpu:
        cpu_socket = _get_spec(cpu, 'socket')
        if cpu_socket:
            socket_filtered = [p for p in motherboard_candidates if _infer_motherboard_socket(p) == cpu_socket]
            if socket_filtered:
                motherboard_candidates = socket_filtered

    motherboard_candidates = _prefer_motherboard_candidates(motherboard_candidates, options.get('case_size', 'any'))

    for gpu_candidate in reversed(_gpu_candidates(options)):
        for motherboard_candidate in motherboard_candidates:
            mb_mem_type = _infer_motherboard_memory_type(motherboard_candidate)
            mb_options = dict(options)
            if mb_mem_type:
                mb_options['motherboard_memory_type'] = mb_mem_type

            memory_candidates_for_mb = _memory_candidates(mb_options)
            if not memory_candidates_for_mb:
                continue

            max_memory_price = min(
                gpu_candidate.price,
                budget - total_fixed - gpu_candidate.price - motherboard_candidate.price,
            )
            if max_memory_price < 0:
                continue

            affordable_memories = [m for m in memory_candidates_for_mb if m.price <= max_memory_price]
            if not affordable_memories:
                continue

            target_capacity_memories = [m for m in affordable_memories if _infer_memory_capacity_gb(m) >= target_memory_capacity]
            if target_capacity_memories:
                affordable_memories = target_capacity_memories
            elif require_target_capacity:
                continue

            memory_candidate = affordable_memories[-1]
            rebalanced = dict(selected_parts)
            rebalanced['gpu'] = gpu_candidate
            rebalanced['motherboard'] = motherboard_candidate
            rebalanced['memory'] = memory_candidate
            return rebalanced

    return selected_parts


def _rebalance_gaming_spec_gpu_for_storage(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    target_storage_capacity = int(options.get('min_storage_capacity_gb') or 0)
    if target_storage_capacity <= 0:
        return selected_parts

    gpu = selected_parts.get('gpu')
    storage = selected_parts.get('storage')
    if not gpu or not storage:
        return selected_parts

    current_storage_capacity = _infer_storage_capacity_gb(storage)
    if current_storage_capacity >= target_storage_capacity:
        return selected_parts

    total_fixed = _sum_selected_price(selected_parts) - gpu.price - storage.price
    if total_fixed < 0:
        total_fixed = 0

    gpu_candidates = [
        p
        for p in _get_cached_parts_by_type('gpu', options=options)
        if _is_part_suitable('gpu', p)
        and _matches_selection_options('gpu', p, options=options)
        and p.price <= gpu.price
    ]
    if not gpu_candidates:
        return selected_parts

    storage_candidates = [
        p
        for p in _get_cached_parts_by_type('storage', options=options)
        if _is_part_suitable('storage', p)
        and _matches_selection_options('storage', p, options=options)
        and _infer_storage_capacity_gb(p) >= target_storage_capacity
    ]
    if not storage_candidates:
        return selected_parts

    # 目標容量を満たすストレージを優先し、必要な範囲でGPUをダウングレードする。
    for storage_candidate in storage_candidates:
        for gpu_candidate in reversed(gpu_candidates):
            projected_total = total_fixed + gpu_candidate.price + storage_candidate.price
            if projected_total > budget:
                continue
            if int(getattr(gpu_candidate, 'id', 0) or 0) == int(getattr(gpu, 'id', 0) or 0) and int(getattr(storage_candidate, 'id', 0) or 0) == int(getattr(storage, 'id', 0) or 0):
                continue

            adjusted = dict(selected_parts)
            adjusted['gpu'] = gpu_candidate
            adjusted['storage'] = storage_candidate
            adjusted = _resolve_compatibility(adjusted, usage, options=options)

            adjusted_storage = adjusted.get('storage')
            if adjusted_storage and _infer_storage_capacity_gb(adjusted_storage) >= target_storage_capacity:
                return adjusted

    return selected_parts


def _enforce_gaming_spec_gpu_not_lower_than_memory(selected_parts, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    gpu = selected_parts.get('gpu')
    memory = selected_parts.get('memory')
    if not gpu or not memory:
        return selected_parts
    if gpu.price >= memory.price:
        return selected_parts

    memory_candidates = [
        p
        for p in _get_cached_parts_by_type('memory', options=options)
        if _is_part_suitable('memory', p) and _matches_selection_options('memory', p, options=options)
    ]
    memory_candidates = [p for p in memory_candidates if p.price <= gpu.price]
    if not memory_candidates:
        # 現行マザーボード制約で解決できない場合、マザーボード+メモリを同時に再選定する。
        current_mb = selected_parts.get('motherboard')
        if not current_mb:
            return selected_parts

        current_total = _sum_selected_price(selected_parts)
        mb_candidates = [
            p
            for p in _get_cached_parts_by_type('motherboard', options=options)
            if _is_part_suitable('motherboard', p) and _matches_selection_options('motherboard', p, options=options)
        ]
        mb_candidates = _prefer_motherboard_candidates(mb_candidates, options.get('case_size', 'any'))

        for mb_candidate in mb_candidates:
            mb_options = dict(options)
            mb_mem_type = _infer_motherboard_memory_type(mb_candidate)
            if mb_mem_type:
                mb_options['motherboard_memory_type'] = mb_mem_type

            mb_memory_candidates = [
                p
                for p in _get_cached_parts_by_type('memory', options=mb_options)
                if _is_part_suitable('memory', p)
                and _matches_selection_options('memory', p, options=mb_options)
                and p.price <= gpu.price
            ]
            if not mb_memory_candidates:
                continue

            memory_candidate = mb_memory_candidates[-1]
            trial_total = current_total - current_mb.price - memory.price + mb_candidate.price + memory_candidate.price
            if trial_total <= options.get('budget', 10**9):
                adjusted = dict(selected_parts)
                adjusted['motherboard'] = mb_candidate
                adjusted['memory'] = memory_candidate
                return adjusted

        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['memory'] = memory_candidates[-1]
    return adjusted


def _enforce_gaming_spec_prefers_rx_xt(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    gpu = selected_parts.get('gpu')
    if not gpu:
        return selected_parts

    text = f"{getattr(gpu, 'name', '')} {getattr(gpu, 'url', '')}".lower()
    model_match = re.search(r'\brx\s*(\d{4})\b', text)
    if not model_match:
        return selected_parts
    if re.search(r'\brx\s*\d{4}\s*xt\b', text):
        return selected_parts

    model = model_match.group(1)
    xt_pattern = re.compile(rf'\brx\s*{model}\s*xt\b', re.IGNORECASE)

    xt_candidates = [
        p
        for p in _get_cached_parts_by_type('gpu', options=options)
        if _is_part_suitable('gpu', p)
        and _matches_selection_options('gpu', p, options=options)
        and xt_pattern.search(f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}")
    ]
    if not xt_candidates:
        return selected_parts

    total_current = _sum_selected_price(selected_parts)

    # 同価格以下のXTがあれば最優先で置換
    for candidate in xt_candidates:
        if candidate.price <= gpu.price:
            adjusted = dict(selected_parts)
            adjusted['gpu'] = candidate
            return adjusted

    # 少し高くても予算内ならXTへ置換
    for candidate in xt_candidates:
        projected_total = total_current - gpu.price + candidate.price
        if projected_total <= budget:
            adjusted = dict(selected_parts)
            adjusted['gpu'] = candidate
            return adjusted

    return selected_parts


def _enforce_gaming_spec_best_value_gpu(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    current_gpu = selected_parts.get('gpu')
    if not current_gpu:
        return selected_parts

    minimum_tier = options.get('minimum_gaming_gpu_tier', 1)
    tier_cap = _gaming_spec_gpu_tier_cap(budget, usage, options=options)
    total_without_gpu = _sum_selected_price(selected_parts) - current_gpu.price

    affordable_candidates = [
        p
        for p in _get_cached_parts_by_type('gpu', options=options)
        if _is_part_suitable('gpu', p)
        and _matches_selection_options('gpu', p, options=options)
        and _is_gaming_spec_gpu_preferred(p, minimum_tier)
        and total_without_gpu + p.price <= budget
    ]
    if tier_cap is not None:
        tier_capped_candidates = [p for p in affordable_candidates if _gaming_spec_gpu_tier(p) <= tier_cap]
        if tier_capped_candidates:
            affordable_candidates = tier_capped_candidates
    if int(budget or 0) <= _budget_tier_threshold(usage, 'middle'):
        exact_5060_candidates = [p for p in affordable_candidates if _is_gaming_spec_exact_5060_gpu(p)]
        if exact_5060_candidates:
            affordable_candidates = exact_5060_candidates
    if not affordable_candidates:
        return selected_parts

    best = _pick_gaming_spec_gpu(affordable_candidates)
    if not best or best.id == current_gpu.id:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['gpu'] = best
    return adjusted


def _enforce_gaming_spec_prefers_x3d_cpu(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming':
        return selected_parts

    build_priority = options.get('build_priority')
    if build_priority not in {'spec', 'cost'}:
        return selected_parts
    if build_priority == 'spec' and budget < _budget_tier_threshold(usage, 'low'):
        return selected_parts
    if build_priority == 'cost':
        # 自動調整中は昇格ロジックを重ねない。
        if options.get('auto_adjust_reference_budget') is not None:
            return selected_parts
        # ある程度の余剰があるケースのみ X3D へ寄せる。
        if _sum_selected_price(selected_parts) >= int(budget * 0.88):
            return selected_parts

    cpu = selected_parts.get('cpu')
    if not cpu:
        return selected_parts
    if _is_gaming_cpu_x3d_preferred(cpu):
        return selected_parts

    cpu_price_cap = _gaming_cost_cpu_price_cap(budget)

    x3d_candidates = [
        p
        for p in _get_cached_parts_by_type('cpu', options=options)
        if _is_part_suitable('cpu', p)
        and _is_gaming_cpu_x3d_preferred(p)
        and _matches_selection_options('cpu', p, options=options)
        and p.price <= cpu_price_cap
    ]
    if not x3d_candidates:
        return selected_parts

    total_without_cpu = _sum_selected_price(selected_parts) - cpu.price
    affordable = [candidate for candidate in x3d_candidates if total_without_cpu + candidate.price <= budget]
    if affordable:
        adjusted = dict(selected_parts)
        adjusted['cpu'] = affordable[-1] if build_priority == 'spec' else affordable[0]
        return _resolve_compatibility(adjusted, usage, options=options)

    trial = dict(selected_parts)
    trial['cpu'] = x3d_candidates[0]
    trial = _resolve_compatibility(trial, usage, options=options)
    trial_total = _sum_selected_price(trial)
    trial, trial_total = _downgrade_selected_parts(trial, trial_total, budget, options=options)
    if trial_total <= budget and _is_gaming_cpu_x3d_preferred(trial.get('cpu')):
        return trial

    return selected_parts


def _enforce_gaming_cost_gpu_policy(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'cost':
        return selected_parts

    current_gpu = selected_parts.get('gpu')
    if not current_gpu or (not _is_gaming_cost_excluded_gpu(current_gpu) and not _is_gaming_creative_gpu(current_gpu)):
        return selected_parts

    candidates = [
        p
        for p in _get_cached_parts_by_type('gpu', options=options)
        if _is_part_suitable('gpu', p)
        and not _is_gt_series_gpu(p)
        and not _is_gaming_cost_excluded_gpu(p)
        and not _is_gaming_creative_gpu(p)
        and _matches_selection_options('gpu', p, options=options)
    ]
    if not candidates:
        return selected_parts

    current_total = _sum_selected_price(selected_parts)
    sorted_candidates = sorted(
        candidates,
        key=lambda p: (
            p.price,
            -_infer_gaming_gpu_perf_score(p),
        ),
    )
    for candidate in sorted_candidates:
        trial = dict(selected_parts)
        trial['gpu'] = candidate
        trial = _resolve_compatibility(trial, usage, options=options)
        trial_total = _sum_selected_price(trial)
        if trial_total <= budget:
            return trial

    # 候補はあるが予算内で置換できない場合は現状維持。
    return selected_parts


def _rightsize_case_after_selection(selected_parts, usage, options=None):
    options = options or {}

    current_case = selected_parts.get('case')
    if not current_case:
        return selected_parts

    case_options = dict(options)

    motherboard = selected_parts.get('motherboard')
    motherboard_form_factor = _infer_motherboard_form_factor(motherboard)
    if motherboard_form_factor not in {'', 'unknown'}:
        case_options['motherboard_form_factor'] = motherboard_form_factor

    gpu = selected_parts.get('gpu')
    gpu_length_mm = _extract_numeric_mm(_get_spec(gpu, 'gpu_length_mm'))
    if gpu_length_mm:
        case_options['gpu_length_mm'] = gpu_length_mm

    candidates = [
        p
        for p in _get_cached_parts_by_type('case', options=case_options)
        if _is_part_suitable('case', p) and _matches_selection_options('case', p, options=case_options)
    ]
    if not candidates:
        return selected_parts

    if motherboard_form_factor not in {'', 'unknown'}:
        preferred_form_factor_cases = [
            p for p in candidates if _is_case_preferred_for_motherboard(p, motherboard_form_factor)
        ]
        if preferred_form_factor_cases:
            candidates = preferred_form_factor_cases

    selected_case = _pick_case_candidate(
        candidates,
        case_options.get('case_fan_policy', 'auto'),
        case_options.get('build_priority', 'balanced'),
        target_price=current_case.price,
    )
    if not selected_case or selected_case.id == current_case.id:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['case'] = selected_case
    return adjusted


def _rescue_case_for_igpu_usage(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage not in IGPU_USAGES:
        return selected_parts

    current_case = selected_parts.get('case')
    if current_case:
        return selected_parts

    # Skip rescue for spec-priority builds to avoid timeout on expensive compatibility checks
    # Spec priority focuses on performance over case selection
    build_priority = options.get('build_priority', 'cost')
    if build_priority == 'spec':
        return selected_parts

    # For cost-priority builds, attempt rescue if budget is tight (>70% consumed without case)
    current_total = _sum_selected_price(selected_parts)
    if current_total < budget * 0.70:
        return selected_parts

    case_options = dict(options)
    motherboard = selected_parts.get('motherboard')
    motherboard_form_factor = _infer_motherboard_form_factor(motherboard)
    if motherboard_form_factor not in {'', 'unknown'}:
        case_options['motherboard_form_factor'] = motherboard_form_factor

    gpu = selected_parts.get('gpu')
    gpu_length_mm = _extract_numeric_mm(_get_spec(gpu, 'gpu_length_mm'))
    if gpu_length_mm:
        case_options['gpu_length_mm'] = gpu_length_mm

    candidates = [
        p
        for p in _get_cached_parts_by_type('case', options=case_options)
        if _is_part_suitable('case', p) and _matches_selection_options('case', p, options=case_options)
    ]
    if motherboard_form_factor not in {'', 'unknown'}:
        preferred_form_factor_cases = [
            p for p in candidates if _is_case_preferred_for_motherboard(p, motherboard_form_factor)
        ]
        if preferred_form_factor_cases:
            candidates = preferred_form_factor_cases

    if not candidates:
        return selected_parts

    picked_case = _pick_case_candidate(
        candidates,
        case_options.get('case_fan_policy', 'auto'),
        case_options.get('build_priority', 'cost'),
        target_price=None,
    )
    if not picked_case:
        return selected_parts

    trial = dict(selected_parts)
    trial['case'] = picked_case
    trial = _resolve_compatibility(trial, usage, options=case_options)
    trial_total = _sum_selected_price(trial)
    if trial_total <= budget:
        return trial

    if trial.get('os') is not None:
        trial_without_os = dict(trial)
        trial_without_os['os'] = None
        if _sum_selected_price(trial_without_os) <= budget:
            return trial_without_os

    return selected_parts


def _rightsize_motherboard_for_gaming_spec(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    current_mb = selected_parts.get('motherboard')
    current_gpu = selected_parts.get('gpu')
    if not current_mb or not current_gpu:
        return selected_parts

    # ゲーミングではGPUを主役にするため、MBの過剰高額化を抑える。
    # 目安: GPU価格の55% or 総予算12% を超えるMBは右サイズ化対象。
    price_cap = min(int(current_gpu.price * 0.55), int(budget * 0.12))
    price_cap = max(price_cap, 18000)
    if current_mb.price <= price_cap:
        return selected_parts

    current_mem_type = _infer_motherboard_memory_type(current_mb)
    cpu_part = selected_parts.get('cpu')
    cpu_socket = _get_spec(cpu_part, 'socket') if cpu_part else ''

    candidates = [
        p
        for p in _get_cached_parts_by_type('motherboard', options=options)
        if _is_part_suitable('motherboard', p)
        and _matches_selection_options('motherboard', p, options=options)
        and p.price < current_mb.price
        and p.price <= price_cap
    ]

    if cpu_socket:
        socket_filtered = [p for p in candidates if _infer_motherboard_socket(p) == cpu_socket]
        if socket_filtered:
            candidates = socket_filtered

    if current_mem_type:
        mem_type_filtered = [p for p in candidates if _infer_motherboard_memory_type(p) == current_mem_type]
        if mem_type_filtered:
            candidates = mem_type_filtered

    candidates = _prefer_motherboard_candidates(candidates, options.get('case_size', 'any'))
    if not candidates:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['motherboard'] = candidates[-1]
    return adjusted


def _upgrade_gpu_after_motherboard_rightsize(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    current_gpu = selected_parts.get('gpu')
    if not current_gpu:
        return selected_parts

    total_price = _sum_selected_price(selected_parts)
    surplus = budget - total_price
    if surplus < 5000:
        return selected_parts

    affordable_max = current_gpu.price + surplus
    candidates = [
        c for c in reversed(_get_cached_parts_by_type('gpu', options=options))
        if c.price > current_gpu.price
        and c.price <= affordable_max
        and _is_part_suitable('gpu', c)
        and _matches_selection_options('gpu', c, options=options)
    ]
    if not candidates:
        return selected_parts

    minimum_tier = options.get('minimum_gaming_gpu_tier', 1)
    tier_cap = _gaming_spec_gpu_tier_cap(budget, usage, options=options)
    preferred_gpu = [c for c in candidates if _is_gaming_spec_gpu_preferred(c, minimum_tier)]
    gpu_pool = preferred_gpu or candidates
    if tier_cap is not None:
        tier_capped_pool = [c for c in gpu_pool if _gaming_spec_gpu_tier(c) <= tier_cap]
        if tier_capped_pool:
            gpu_pool = tier_capped_pool
    if int(budget or 0) <= _budget_tier_threshold(usage, 'middle'):
        exact_5060_pool = [c for c in gpu_pool if _is_gaming_spec_exact_5060_gpu(c)]
        if exact_5060_pool:
            gpu_pool = exact_5060_pool
    gpu_pool = _prefer_rx_xt_value_candidates(gpu_pool)
    if not gpu_pool:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['gpu'] = gpu_pool[0]
    return adjusted


def _upgrade_to_liquid_cooler_with_surplus(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts
    if options.get('cooling_profile') != 'performance':
        return selected_parts

    current_cooler = selected_parts.get('cpu_cooler')
    if not current_cooler:
        return selected_parts

    current_text = f"{getattr(current_cooler, 'name', '')} {getattr(current_cooler, 'url', '')}".lower()
    if _is_cpu_cooler_type_match(current_cooler, 'liquid') or '水冷' in current_text:
        return selected_parts

    total_price = _sum_selected_price(selected_parts)
    surplus = budget - total_price
    # 十分な余剰がある場合のみ、水冷への自動アップグレードを許可する。
    if surplus < 15000:
        return selected_parts

    liquid_options = dict(options)
    liquid_options['cooler_type'] = 'liquid'
    # 余剰アップグレード時は、ユーザー指定の初期ラジエーターサイズ制約より
    # 「水冷化そのもの」を優先する。
    liquid_options['radiator_size'] = 'any'

    current_case = selected_parts.get('case')

    def _infer_cooler_radiator_mm(part):
        text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
        for size in (420, 360, 280, 240, 140, 120):
            if f"{size}mm" in text or f"{size} mm" in text:
                return size
        return _extract_numeric_radiator_size(_get_spec(part, 'radiator_mm', None))

    liquid_candidates = []
    for candidate in PCPart.objects.filter(part_type='cpu_cooler').order_by('price'):
        if candidate.price <= current_cooler.price:
            continue
        if total_price - current_cooler.price + candidate.price > budget:
            continue
        if not _is_part_suitable('cpu_cooler', candidate):
            continue
        if not _matches_selection_options('cpu_cooler', candidate, options=liquid_options):
            continue
        if not _is_allowed_cpu_cooler_brand(candidate):
            continue

        radiator_mm = _infer_cooler_radiator_mm(candidate)
        if current_case and radiator_mm and not _is_case_radiator_compatible(current_case, str(radiator_mm)):
            continue

        liquid_candidates.append(candidate)

    if not liquid_candidates:
        return selected_parts

    picked = sorted(
        liquid_candidates,
        key=lambda p: (_cpu_cooler_profile_score(p, 'performance', 'liquid'), p.price),
        reverse=True,
    )[0]

    adjusted = dict(selected_parts)
    adjusted['cpu_cooler'] = picked
    return adjusted


def _upgrade_case_for_cooling_with_surplus(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    current_case = selected_parts.get('case')
    if not current_case:
        return selected_parts

    total_price = _sum_selected_price(selected_parts)
    surplus = budget - total_price
    if surplus < 8000:
        return selected_parts

    # auto は airflow と同等の冷却優先として評価する。
    requested_policy = options.get('case_fan_policy', 'auto')
    target_policy = 'airflow' if requested_policy == 'auto' else requested_policy

    current_cooler = selected_parts.get('cpu_cooler')
    cooler_text = f"{getattr(current_cooler, 'name', '')} {getattr(current_cooler, 'url', '')}".lower()

    def _infer_cooler_radiator_mm(part):
        text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
        for size in (420, 360, 280, 240, 140, 120):
            if f"{size}mm" in text or f"{size} mm" in text:
                return size
        return _extract_numeric_radiator_size(_get_spec(part, 'radiator_mm', None))

    required_radiator_mm = None
    if current_cooler and (_is_cpu_cooler_type_match(current_cooler, 'liquid') or '水冷' in cooler_text):
        required_radiator_mm = _infer_cooler_radiator_mm(current_cooler)

    current_score = _case_fan_policy_score(current_case, target_policy)
    candidates = []
    for candidate in PCPart.objects.filter(part_type='case').order_by('price'):
        if candidate.price <= current_case.price:
            continue
        if total_price - current_case.price + candidate.price > budget:
            continue
        if not _is_part_suitable('case', candidate):
            continue
        if not _matches_selection_options('case', candidate, options=options):
            continue
        if required_radiator_mm and not _is_case_radiator_compatible(candidate, str(required_radiator_mm)):
            continue
        candidates.append(candidate)

    if not candidates:
        return selected_parts

    best = sorted(
        candidates,
        key=lambda p: (_case_fan_policy_score(p, target_policy), p.price),
        reverse=True,
    )[0]
    if _case_fan_policy_score(best, target_policy) <= current_score:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['case'] = best
    return adjusted


def _enforce_gaming_spec_prefers_nvme_storage(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    storage = selected_parts.get('storage')
    if not storage:
        return selected_parts

    current_media = _infer_storage_media_type(storage)
    current_interface = _infer_storage_interface(storage)

    # 現行が高容量HDDで、SSD候補が1TB未満しかない場合はHDDを維持する。
    if current_media == 'hdd' and _infer_storage_capacity_gb(storage) >= 1000:
        ssd_candidates = [
            p for p in PCPart.objects.filter(part_type='storage').order_by('price')
            if _is_part_suitable('storage', p) and _infer_storage_media_type(p) == 'ssd'
        ]
        if ssd_candidates and max(_infer_storage_capacity_gb(p) for p in ssd_candidates) < 1000:
            return selected_parts

    if current_media == 'ssd' and current_interface == 'nvme':
        return selected_parts

    def _preferred_storage_candidates(base_options):
        pool = [
            p
            for p in PCPart.objects.filter(part_type='storage').order_by('price')
            if _is_part_suitable('storage', p) and _matches_selection_options('storage', p, options=base_options)
        ]
        if not pool:
            return []

        nvme_ssd = [p for p in pool if _infer_storage_media_type(p) == 'ssd' and _infer_storage_interface(p) == 'nvme']
        if nvme_ssd:
            return nvme_ssd
        sata_ssd = [p for p in pool if _infer_storage_media_type(p) == 'ssd']
        return sata_ssd

    strict_preferred = _preferred_storage_candidates(options)

    # 容量条件が厳しい場合でも、最低512GBまで緩めたNVMe候補を必ず試す。
    relaxed_options = dict(options)
    relaxed_options['min_storage_capacity_gb'] = 512
    relaxed_preferred = _preferred_storage_candidates(relaxed_options)

    preferred = list(strict_preferred)
    strict_ids = {p.id for p in strict_preferred}
    preferred.extend([p for p in relaxed_preferred if p.id not in strict_ids])

    if not preferred:
        return selected_parts

    total_current = _sum_selected_price(selected_parts)
    for candidate in preferred:
        projected_total = total_current - storage.price + candidate.price
        if projected_total <= budget:
            adjusted = dict(selected_parts)
            adjusted['storage'] = candidate
            return adjusted

    # 直接置換で予算超過する場合は、他パーツを調整してでもSSD/NVMe維持を試みる。
    for candidate in preferred:
        trial = dict(selected_parts)
        trial['storage'] = candidate
        trial_total = _sum_selected_price(trial)
        trial, trial_total = _downgrade_selected_parts(trial, trial_total, budget, options=relaxed_options)

        final_storage = trial.get('storage')
        if not final_storage:
            continue
        if trial_total > budget:
            continue

        final_media = _infer_storage_media_type(final_storage)
        final_interface = _infer_storage_interface(final_storage)
        if final_media == 'ssd' and final_interface == 'nvme':
            return trial

    return selected_parts


def _enforce_main_storage_ssd(selected_parts, budget, usage, options=None):
    options = options or {}
    storage = selected_parts.get('storage')
    if not storage:
        return selected_parts

    current_media = _infer_storage_media_type(storage)
    current_interface = _infer_storage_interface(storage)
    if current_media == 'ssd' and current_interface == 'nvme':
        return selected_parts

    strict_options = dict(options)
    strict_options['enforce_main_storage_ssd'] = True
    if not strict_options.get('min_storage_capacity_gb'):
        strict_options['min_storage_capacity_gb'] = 512

    current_capacity = _infer_storage_capacity_gb(storage)
    candidates = [
        p
        for p in _get_cached_parts_by_type('storage', options=strict_options)
        if _is_part_suitable('storage', p)
        and _matches_selection_options('storage', p, options=strict_options)
    ]
    if not candidates:
        return selected_parts

    ssd_candidates = [p for p in candidates if _infer_storage_media_type(p) == 'ssd']
    if ssd_candidates:
        candidates = ssd_candidates

    nvme_candidates = [p for p in candidates if _infer_storage_interface(p) == 'nvme']
    if nvme_candidates:
        candidates = nvme_candidates

    candidates = sorted(
        candidates,
        key=lambda p: (
            0 if _infer_storage_interface(p) == 'nvme' else 1,
            0 if _infer_storage_capacity_gb(p) >= max(512, current_capacity) else 1,
            abs(_infer_storage_capacity_gb(p) - max(512, current_capacity)),
            p.price,
        ),
    )

    current_total = _sum_selected_price(selected_parts)
    for candidate in candidates:
        projected = current_total - storage.price + candidate.price
        if projected <= budget:
            adjusted = dict(selected_parts)
            adjusted['storage'] = candidate
            return adjusted

    for candidate in candidates:
        trial = dict(selected_parts)
        trial['storage'] = candidate
        trial_total = _sum_selected_price(trial)
        trial, trial_total = _downgrade_selected_parts(trial, trial_total, budget, options=strict_options)
        final_storage = trial.get('storage')
        if (
            final_storage
            and _infer_storage_media_type(final_storage) == 'ssd'
            and _infer_storage_interface(final_storage) == 'nvme'
            and trial_total <= budget
        ):
            return trial

    return selected_parts


def _enforce_creator_staged_requirements(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'creator':
        return selected_parts

    adjusted = dict(selected_parts)

    # Stage 1: CPU/Memory/Storage の最低要件を満たす
    current_cpu = adjusted.get('cpu')
    if current_cpu and not _cpu_meets_creator_minimum(current_cpu, min_cores=8, min_threads=16):
        cpu_candidates = [
            p
            for p in _get_cached_parts_by_type('cpu', options=options)
            if _is_part_suitable('cpu', p)
            and _matches_selection_options('cpu', p, options=options)
            and _cpu_meets_creator_minimum(p, min_cores=8, min_threads=16)
        ]
        if cpu_candidates:
            picked_cpu = _pick_creator_cpu_with_budget(
                cpu_candidates,
                budget=budget,
                build_priority=options.get('build_priority', 'balanced'),
            )
            if picked_cpu:
                adjusted['cpu'] = picked_cpu

    current_memory = adjusted.get('memory')
    if current_memory and _infer_memory_capacity_gb(current_memory) < 16:
        memory_candidates = [
            p
            for p in _get_cached_parts_by_type('memory', options=options)
            if _is_part_suitable('memory', p)
            and _matches_selection_options('memory', p, options=options)
            and _infer_memory_capacity_gb(p) >= 16
        ]
        current_total = _sum_selected_price(adjusted)
        affordable = [p for p in memory_candidates if current_total - current_memory.price + p.price <= budget]
        if affordable:
            preferred_memory = _memory_profile_pick(
                affordable,
                options.get('build_priority', 'balanced'),
                budget=budget,
                usage=usage,
                options=options,
            )
            adjusted['memory'] = preferred_memory or affordable[0]

    adjusted = _enforce_main_storage_ssd(adjusted, budget, usage, options=options)

    # Stage 2: creator GPU は VRAM/性能/メーカー順で再評価し、マザーボードは拡張性重視
    current_gpu = adjusted.get('gpu')
    if current_gpu:
        gpu_candidates = [
            p
            for p in _get_cached_parts_by_type('gpu', options=options)
            if _is_part_suitable('gpu', p)
            and _matches_selection_options('gpu', p, options=options)
        ]
        if gpu_candidates:
            minimum_tier = _minimum_creator_gpu_tier(budget, options=options)
            tier_candidates = [p for p in gpu_candidates if _creator_gpu_tier(p) >= minimum_tier]
            if tier_candidates:
                gpu_candidates = tier_candidates

            ranked_gpu = _prefer_creator_gpu_with_vram_flex(gpu_candidates, build_priority=options.get('build_priority', 'balanced'))
            if _is_creator_premium_budget(budget):
                premium_ranked = _prefer_creator_premium_gpu(gpu_candidates, build_priority=options.get('build_priority', 'balanced'))
                if premium_ranked:
                    if options.get('build_priority') == 'cost' and _is_creator_r9700_gpu(current_gpu):
                        ranked_gpu = [current_gpu]
                    elif options.get('build_priority') == 'spec' and _is_creator_rtx5090_gpu(current_gpu):
                        ranked_gpu = [current_gpu]
                    else:
                        ranked_gpu = premium_ranked
            if ranked_gpu:
                best_gpu = ranked_gpu[0]
                if _creator_gpu_priority_key(best_gpu, build_priority=options.get('build_priority', 'balanced')) > _creator_gpu_priority_key(current_gpu, build_priority=options.get('build_priority', 'balanced')):
                    if _creator_gpu_within_limits(best_gpu, adjusted, budget, usage, options=options):
                        adjusted['gpu'] = best_gpu

    current_mb = adjusted.get('motherboard')
    if current_mb:
        current_mb_score = _creator_motherboard_expandability_score(current_mb)
        mb_candidates = [
            p
            for p in _get_cached_parts_by_type('motherboard', options=options)
            if _is_part_suitable('motherboard', p)
            and _matches_selection_options('motherboard', p, options=options)
            and _creator_motherboard_expandability_score(p) > current_mb_score
        ]
        current_total = _sum_selected_price(adjusted)
        ranked_mb = sorted(
            mb_candidates,
            key=lambda p: (
                _creator_motherboard_expandability_score(p),
                -p.price,
            ),
            reverse=True,
        )
        for candidate in ranked_mb:
            if current_total - current_mb.price + candidate.price <= budget:
                adjusted['motherboard'] = candidate
                break

    # Stage 3: CPU消費電力に応じて、液冷またはツインタワー空冷を選ぶ
    current_cpu = adjusted.get('cpu')
    current_cooler = adjusted.get('cpu_cooler')
    if current_cpu and current_cooler:
        cpu_power_w = _infer_cpu_power_w(current_cpu)
        current_case = adjusted.get('case')

        def _infer_cooler_radiator_mm(part):
            text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
            for size in (420, 360, 280, 240, 140, 120):
                if f"{size}mm" in text or f"{size} mm" in text:
                    return size
            return _extract_numeric_radiator_size(_get_spec(part, 'radiator_mm', None))

        cooler_candidates = [
            p
            for p in _get_cached_parts_by_type('cpu_cooler', options=options)
            if _is_part_suitable('cpu_cooler', p)
            and _is_cpu_cooler_product(p)
            and _is_allowed_cpu_cooler_brand(p)
        ]

        # 液冷候補はケース互換性を事前に確認
        liquid_compatible_candidates = []
        for candidate in cooler_candidates:
            if not _is_liquid_cooler(candidate):
                continue
            radiator_mm = _infer_cooler_radiator_mm(candidate)
            if current_case and radiator_mm and not _is_case_radiator_compatible(current_case, str(radiator_mm)):
                continue
            liquid_compatible_candidates.append(candidate)

        if cpu_power_w >= 125:
            preferred_coolers = liquid_compatible_candidates
            if not preferred_coolers:
                preferred_coolers = [p for p in cooler_candidates if _is_dual_tower_cooler(p)]
        else:
            preferred_coolers = [p for p in cooler_candidates if _is_dual_tower_cooler(p)]
            if not preferred_coolers:
                preferred_coolers = liquid_compatible_candidates

        current_total = _sum_selected_price(adjusted)
        ranked_coolers = sorted(
            preferred_coolers,
            key=lambda p: (
                _cpu_cooler_profile_score(p, options.get('cooling_profile', 'performance'), options.get('cooler_type', 'any')),
                p.price,
            ),
            reverse=True,
        )
        replaced = False
        for candidate in ranked_coolers:
            if current_total - current_cooler.price + candidate.price <= budget:
                adjusted['cpu_cooler'] = candidate
                replaced = True
                break

        if not replaced and ranked_coolers:
            for candidate in sorted(ranked_coolers, key=lambda p: p.price):
                trial = dict(adjusted)
                trial['cpu_cooler'] = candidate
                trial_total = _sum_selected_price(trial)
                trial, trial_total = _downgrade_selected_parts(trial, trial_total, budget, options=options)
                final_cooler = trial.get('cpu_cooler')
                if not final_cooler:
                    continue
                if trial_total > budget:
                    continue
                if _is_liquid_cooler(final_cooler) or _is_dual_tower_cooler(final_cooler):
                    adjusted = trial
                    break

    return adjusted


def _apply_build_priority_weights(usage, build_priority, use_igpu, custom_budget_weights=None, budget=None):
    if custom_budget_weights is not None:
        return dict(custom_budget_weights)

    base = IGPU_BUDGET_WEIGHTS.get(usage) if use_igpu else USAGE_BUDGET_WEIGHTS.get(usage)
    if not base:
        return None

    adjusted = dict(base)
    if build_priority != 'spec' or use_igpu:
        return adjusted

    # 汎用(general/standard)+spec: コスト構成をベースにGPUと電源のみ強化する
    # CPU/MB/メモリ/ストレージはコスト重視時と同じ配分を維持
    if usage in {'general', 'standard'} and budget and int(budget) > 0:
        igpu_base = IGPU_BUDGET_WEIGHTS.get(usage, {})
        if igpu_base:
            weights = dict(igpu_base)
            tier = _classify_budget_tier(int(budget), usage=usage)
            gpu_target = GENERAL_SPEC_GPU_TARGET_BY_TIER.get(tier, 65000)
            # 端数で target_price が gpu_target を下回らないよう、小バッファを加えて切り上げ
            gpu_weight_raw = gpu_target / int(budget)
            import math
            # 0.3% の安全マージンを加える（四捨五入の誤差をカバー）
            gpu_weight = min(0.55, math.ceil(gpu_weight_raw * 1.003 * 10000) / 10000)
            weights['gpu'] = gpu_weight
            # GPU搭載時は電源に余裕を持たせる
            weights['psu'] = max(weights.get('psu', 0.10), 0.12)
            return weights

    gpu_boost_map = {
        'gaming': 0.20,
        'creator': 0.08,
        'general': 0.12,       # base 0.08 → 0.20 total (dGPU解禁時のみ適用)
        'standard': 0.12,      # base 0.16 → 0.28 total
        'business': 0.14,      # base 0.08 → 0.22 total
        'workstation': 0.13,   # base 0.32 → 0.45 total (LLM/DeepLearning向け VRAM最優先)
    }
    boost = gpu_boost_map.get(usage, 0.06)
    adjusted['gpu'] = min(0.75, adjusted.get('gpu', 0) + boost)

    # GPUへ寄せた分は、優先度の低いカテゴリから順に減らす。
    remaining = boost
    reduce_order = ['memory', 'storage', 'motherboard', 'case', 'psu', 'cpu_cooler', 'cpu']
    floors = {
        'cpu': 0.17 if usage == 'gaming' else (0.14 if usage in {'creator', 'general', 'standard', 'business'} else 0.10),
        'motherboard': 0.08,
        # workstation は大容量 RAM(64GB+) が必須なのでメモリフロアを高めに設定
        'memory': 0.10 if usage == 'workstation' else 0.05,
        'storage': 0.05,
        'os': 0.04,
        'case': 0.00,
        'psu': 0.04,
        'cpu_cooler': 0.03,
    }

    for key in reduce_order:
        if remaining <= 0:
            break
        current = adjusted.get(key, 0)
        floor = floors.get(key, 0.03)
        reducible = max(0, current - floor)
        delta = min(remaining, reducible)
        adjusted[key] = current - delta
        remaining -= delta

    # 減額しきれない場合はGPU増加分を戻して配分合計の歪みを抑える。
    if remaining > 0:
        adjusted['gpu'] = max(0, adjusted.get('gpu', 0) - remaining)

    return adjusted


def _refresh_selection_options_with_selected_parts(selection_options, selected_parts):
    updated = dict(selection_options)

    cpu_part = selected_parts.get('cpu')
    if cpu_part:
        cpu_socket = _get_spec(cpu_part, 'socket')
        if cpu_socket:
            updated['cpu_socket'] = cpu_socket
        else:
            updated.pop('cpu_socket', None)

        min_memory_speed_mhz = _minimum_memory_speed_for_selected_cpu(
            cpu_part,
            updated.get('usage', 'gaming'),
            options=updated,
        )
        if min_memory_speed_mhz:
            updated['min_memory_speed_mhz'] = min_memory_speed_mhz
        else:
            updated.pop('min_memory_speed_mhz', None)

    motherboard_part = selected_parts.get('motherboard')
    if motherboard_part:
        mb_mem_type = _infer_motherboard_memory_type(motherboard_part)
        if mb_mem_type:
            updated['motherboard_memory_type'] = mb_mem_type
        else:
            updated.pop('motherboard_memory_type', None)

    updated['required_psu_wattage'] = _required_psu_wattage(selected_parts, updated.get('usage', 'gaming'))
    return updated


def _is_premium_gaming_cpu_for_cost_build(part, budget, options=None):
    options = options or {}
    market_range = options.get('market_price_range')
    if not part or not _is_gaming_cpu_x3d_preferred(part):
        return False

    text = f"{part.name} {part.url}".lower()

    if '9850x3d' in text:
        return _classify_budget_tier_from_market_range(budget, market_range=market_range) != 'premium'

    if 'ryzen 9' in text:
        return True

    # Ryzen 7 X3D は gaming+cost の主力帯として許容する。
    # 9850X3D などを過剰CPU扱いすると、メモリ調整による妥当な昇格まで抑止してしまう。
    if 'ryzen 7' in text:
        return False

    # gaming + cost は予算帯に応じて CPU 上限を段階制にする。
    # 低予算帯: X3D昇格余地を確保。
    # 中高予算帯: GPU優先を崩さないようCPU過剰投資を抑制。
    if int(budget) <= 200000:
        premium_floor = max(75000, int(budget * 0.30))
    else:
        premium_floor = max(60000, int(budget * 0.14))

    return part.price >= premium_floor


def _is_gaming_cost_flagship_motherboard(part):
    """
    gaming + cost モードで、フラッグシップマザーボード（X870E）を除外する判定。
    """
    if not part:
        return False
    text = f"{part.name}".lower()
    # X870E は除外
    return 'x870e' in text or 'x970' in text


def _is_gaming_cost_high_speed_memory(part):
    """
    gaming + cost モードで、高速メモリ（DDR5 PC5-44800以上）を除外する判定。
    """
    if not part:
        return False
    
    specs_text = (part.specs or {}).get('specs_text', '').lower()
    name_text = f"{part.name}".lower()
    spec_and_name = f"{specs_text} {name_text}"
    
    # PC5-44800, PC5-48000, PC5-52000 などの高速メモリを除外
    if any(speed in spec_and_name for speed in ['pc5-44800', 'pc5-48000', 'pc5-52000']):
        return True
    
    # JEDEC標準より一つ上のランク以上を除外する目安: MHz表記でチェック
    # DDR5-5600 (PC5-44800) 以上を避ける
    import re
    speed_patterns = re.findall(r'ddr5-(\d+)', spec_and_name)
    if speed_patterns:
        speeds = [int(s) for s in speed_patterns if s.isdigit()]
        if speeds and max(speeds) >= 5600:
            return True
    
    return False


def _should_exclude_cpu_for_gaming_cost(part):
    """
    gaming + cost モードで、超フラッグシップ CPU を除外する判定。    """
    if not part:
        return False
    
    text = f"{part.name}".lower()
    
    # 9850X3D, 9900X3D, 9950X, 9950X3D を除外
    flagship_models = ['9850x3d', '9900x3d', '9950x', '9950x3d']
    if any(model in text for model in flagship_models):
        return True
    
    # Intel 高級モデル（Core i9-13900KS など）も除外
    if 'core i9-13900ks' in text or 'core i9-14900ks' in text:
        return True
    
    return False


def _remove_9850x3d_from_cpu_pool(cpu_pool, build_priority):
    """
    gaming + cost: CPU pool から 9850X3D を確実に remove（全側面対応）
    """
    if build_priority != 'cost':
        return cpu_pool
    
    return [p for p in cpu_pool if '9850x3d' not in p.name.lower()]


def _rebalance_gaming_cost_cpu_to_storage(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'cost':
        return selected_parts

    cpu = selected_parts.get('cpu')
    storage = selected_parts.get('storage')
    if not cpu or not storage:
        return selected_parts

    if not _is_premium_gaming_cpu_for_cost_build(cpu, budget, options=options):
        return selected_parts

    desired_capacity = int(options.get('min_storage_capacity_gb') or 0)
    current_capacity = _infer_storage_capacity_gb(storage)

    def _storage_upgrade_score(part):
        return (
            1 if _infer_storage_media_type(part) == 'ssd' else 0,
            1 if _infer_storage_interface(part) == 'nvme' else 0,
            _infer_storage_capacity_gb(part),
        )

    current_storage_score = _storage_upgrade_score(storage)
    current_cpu_text = f"{cpu.name} {cpu.url}".lower()
    current_cpu_socket = _get_spec(cpu, 'socket')

    base_total = _sum_selected_price(selected_parts) - cpu.price - storage.price
    cpu_candidates = [
        part
        for part in PCPart.objects.filter(part_type='cpu', price__lt=cpu.price).order_by('-price')
        if _is_part_suitable('cpu', part)
        and _matches_selection_options('cpu', part, options=options)
        and _is_gaming_cpu_x3d_preferred(part)
        and ('amd' in current_cpu_text or 'ryzen' in current_cpu_text)
        and (not current_cpu_socket or _get_spec(part, 'socket') == current_cpu_socket)
    ]
    if not cpu_candidates:
        return selected_parts

    storage_candidates = [
        part
        for part in PCPart.objects.filter(part_type='storage').order_by('price')
        if _is_part_suitable('storage', part)
        and _matches_selection_options('storage', part, options=options)
        and _infer_storage_media_type(part) == 'ssd'
        and _storage_upgrade_score(part) > current_storage_score
    ]
    if not storage_candidates:
        return selected_parts

    storage_candidates = sorted(
        storage_candidates,
        key=lambda part: (
            1 if desired_capacity and _infer_storage_capacity_gb(part) >= desired_capacity else 0,
            1 if _infer_storage_media_type(part) == 'ssd' else 0,
            1 if _infer_storage_interface(part) == 'nvme' else 0,
            _infer_storage_capacity_gb(part),
            -part.price,
        ),
        reverse=True,
    )

    best_trial = None
    best_score = None
    current_total = _sum_selected_price(selected_parts)

    for storage_candidate in storage_candidates:
        for cpu_candidate in cpu_candidates:
            trial = dict(selected_parts)
            trial['cpu'] = cpu_candidate
            trial['storage'] = storage_candidate
            trial = _resolve_compatibility(trial, usage, options=options)

            trial_total = _sum_selected_price(trial)

            final_cpu = trial.get('cpu')
            final_storage = trial.get('storage')
            if not final_cpu or not final_storage:
                continue
            if trial_total > budget:
                continue
            if final_cpu.price >= cpu.price:
                continue
            if not _is_gaming_cpu_x3d_preferred(final_cpu):
                continue
            if current_cpu_socket and _get_spec(final_cpu, 'socket') != current_cpu_socket:
                continue
            if _storage_upgrade_score(final_storage) <= current_storage_score:
                continue

            final_capacity = _infer_storage_capacity_gb(final_storage)
            score = (
                1 if desired_capacity and final_capacity >= desired_capacity else 0,
                1 if _infer_storage_media_type(final_storage) == 'ssd' else 0,
                1 if _infer_storage_interface(final_storage) == 'nvme' else 0,
                final_capacity,
                final_cpu.price,
                current_total - trial_total,
            )
            if best_score is None or score > best_score:
                best_trial = trial
                best_score = score

        if best_trial and desired_capacity and best_score[0] == 1:
            break

    return best_trial or selected_parts


def _prefer_higher_gaming_cost_x3d_cpu(selected_parts, budget, usage, options=None):
    """
    gaming+cost で X3D CPU を優先するロジック。
    - 現在の CPU が X3D の場合: さらに高性能な X3D CPU へのアップグレードを検討
    - 現在の CPU が非 X3D の場合: 予算内で X3D CPU への upgrade を検討
    """
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'cost':
        return selected_parts

    # 予算自動調整中は、X3D へ昇格するだけで十分なので、
    # それ以上の高価格 X3D への再昇格は行わない。
    if options.get('auto_adjust_reference_budget') is not None:
        return selected_parts

    current_cpu = selected_parts.get('cpu')
    current_memory = selected_parts.get('memory')
    if not current_cpu or not current_memory:
        return selected_parts

    # X3D CPU 候補を取得（現在の CPU より高価、かつ premium ではない）
    if _is_gaming_cpu_x3d_preferred(current_cpu):
        # 既に X3D: さらに高い X3D CPU を探す
        cpu_price_cap = _gaming_cost_cpu_price_cap(budget)
        upgrade_candidates = [
            part
            for part in PCPart.objects.filter(part_type='cpu', price__gt=current_cpu.price).order_by('-price')
            if _is_part_suitable('cpu', part)
            and _matches_selection_options('cpu', part, options=options)
            and _is_gaming_cpu_x3d_preferred(part)
            and not _is_premium_gaming_cpu_for_cost_build(part, budget, options=options)
            and part.price <= cpu_price_cap
        ]
    else:
        # 非 X3D: 予算比率の固定上限ではなく、後段の合計金額判定で可否を判断する。
        # これにより、全体予算に余剰があるケースで X3D 候補を取りこぼさない。
        cpu_price_cap = _gaming_cost_cpu_price_cap(budget)
        upgrade_candidates = [
            part
            for part in PCPart.objects.filter(part_type='cpu', price__gt=current_cpu.price).order_by('-price')
            if _is_part_suitable('cpu', part)
            and _matches_selection_options('cpu', part, options=options)
            and _is_gaming_cpu_x3d_preferred(part)
            and not _is_premium_gaming_cpu_for_cost_build(part, budget, options=options)
            and part.price <= cpu_price_cap
        ]
    
    if not upgrade_candidates:
        return selected_parts

    current_total = _sum_selected_price(selected_parts)
    target_profile = _target_memory_profile(budget, usage, options=options)
    target_capacity = target_profile['capacity_gb']
    preferred_modules = target_profile['preferred_modules']

    def _memory_downgrade_rank(part):
        return (
            _infer_memory_type(part) == 'DDR5',
            _infer_memory_capacity_gb(part) == target_capacity,
            _infer_memory_module_count(part) == preferred_modules,
            _infer_memory_speed_mhz(part),
            _infer_memory_capacity_gb(part),
            -part.price,
        )

    for cpu_candidate in upgrade_candidates:
        trial_options = dict(options)
        min_memory_speed_mhz = _minimum_memory_speed_for_selected_cpu(
            cpu_candidate,
            usage,
            options=trial_options,
        )
        if min_memory_speed_mhz:
            trial_options['min_memory_speed_mhz'] = min_memory_speed_mhz
        else:
            trial_options.pop('min_memory_speed_mhz', None)

        cheaper_memory_candidates = [
            part
            for part in PCPart.objects.filter(part_type='memory', price__lt=current_memory.price).order_by('price')
            if _is_part_suitable('memory', part)
            and _matches_selection_options('memory', part, options=trial_options)
            and _infer_memory_capacity_gb(part) >= target_capacity
        ]
        cheaper_memory_candidates = sorted(
            cheaper_memory_candidates,
            key=_memory_downgrade_rank,
            reverse=True,
        )

        direct_total = current_total - current_cpu.price + cpu_candidate.price
        if direct_total <= budget:
            adjusted = dict(selected_parts)
            adjusted['cpu'] = cpu_candidate
            adjusted = _resolve_compatibility(adjusted, usage, options=trial_options)
            return adjusted

        required_savings = direct_total - budget
        for memory_candidate in cheaper_memory_candidates:
            memory_savings = current_memory.price - memory_candidate.price
            if memory_savings < required_savings:
                continue

            trial = dict(selected_parts)
            trial['cpu'] = cpu_candidate
            trial['memory'] = memory_candidate
            trial = _resolve_compatibility(trial, usage, options=trial_options)
            if _sum_selected_price(trial) <= budget:
                return trial

    return selected_parts


def _enforce_memory_speed_floor(selected_parts, budget, usage, options=None):
    options = options or {}
    memory = selected_parts.get('memory')
    if not memory:
        return selected_parts

    min_memory_speed_mhz = options.get('min_memory_speed_mhz')
    if not min_memory_speed_mhz:
        return selected_parts
    if _infer_memory_speed_mhz(memory) >= int(min_memory_speed_mhz):
        return selected_parts

    current_total = _sum_selected_price(selected_parts)
    current_memory = selected_parts.get('memory')
    candidates = [
        part
        for part in PCPart.objects.filter(part_type='memory').order_by('price')
        if part.id != current_memory.id
        and _is_part_suitable('memory', part)
        and _matches_selection_options('memory', part, options=options)
        and _infer_memory_capacity_gb(part) >= _infer_memory_capacity_gb(current_memory)
    ]

    for candidate in candidates:
        projected_total = current_total - current_memory.price + candidate.price
        if projected_total <= budget:
            adjusted = dict(selected_parts)
            adjusted['memory'] = candidate
            return adjusted

    return selected_parts


def _prefer_non_x3d_cpu_when_possible(selected_parts, budget, usage, options=None):
    """現在CPUがX3Dの場合、可能なら非X3Dへ置換する（置換不能なら現状維持）。"""
    options = options or {}
    if usage == 'gaming' and options.get('build_priority') == 'spec':
        return selected_parts, False
    if usage == 'gaming' and options.get('require_gaming_x3d_cpu'):
        return selected_parts, False
    # workstation は tier 設計で X3D CPU（9800X3D 等）を意図的に選んでいるため置換しない
    if usage == 'workstation':
        return selected_parts, False
    current_cpu = selected_parts.get('cpu')
    if not current_cpu or not _is_cpu_x3d(current_cpu):
        return selected_parts, False

    probe_options = dict(options)
    probe_options['require_gaming_x3d_cpu'] = False
    build_priority = probe_options.get('build_priority', 'balanced')

    non_x3d_candidates = [
        part
        for part in PCPart.objects.filter(part_type='cpu').order_by('price')
        if part.id != current_cpu.id
        and not _is_cpu_x3d(part)
        and _is_part_suitable('cpu', part)
        and _matches_selection_options('cpu', part, options=probe_options)
    ]
    if not non_x3d_candidates:
        return selected_parts, False

    replacement = None
    if usage == 'gaming':
        replacement = _pick_amd_gaming_cpu(non_x3d_candidates, build_priority, require_x3d=False)
    elif usage == 'creator':
        replacement = _pick_creator_cpu_with_budget(non_x3d_candidates, budget, build_priority)
    elif usage in {'workstation', 'ai'}:
        ai_latest = [part for part in non_x3d_candidates if _is_ai_latest_generation_cpu(part)]
        pool = ai_latest or non_x3d_candidates
        if build_priority == 'spec':
            replacement = sorted(
                pool,
                key=lambda part: (
                    _get_cpu_perf_score(part) or 0,
                    _extract_cpu_core_count(part),
                    _extract_cpu_core_threads(part),
                    -int(getattr(part, 'price', 0) or 0),
                ),
                reverse=True,
            )[0]
        else:
            replacement = pool[0]
    else:
        if build_priority == 'spec':
            replacement = sorted(
                non_x3d_candidates,
                key=lambda part: (
                    _get_cpu_perf_score(part) or 0,
                    _extract_cpu_core_count(part),
                    _extract_cpu_core_threads(part),
                    -int(getattr(part, 'price', 0) or 0),
                ),
                reverse=True,
            )[0]
        else:
            if usage in {'general', 'business', 'standard'}:
                budget_tier = _classify_budget_tier_from_market_range(
                    budget,
                    market_range=probe_options.get('market_price_range'),
                )
                perf_floor_map = {
                    'low': 1200,
                    'middle': 1800,
                    'high': 2600,
                    'premium': 4000,
                }
                core_floor_map = {
                    'low': 4,
                    'middle': 4,
                    'high': 6,
                    'premium': 8,
                }
                current_score = int(_get_cpu_perf_score(current_cpu) or 0)
                perf_floor = int(perf_floor_map.get(budget_tier, 1000))
                if current_score > 0:
                    perf_floor = max(perf_floor, int(current_score * 0.65))
                core_floor = int(core_floor_map.get(budget_tier, 4))

                filtered = [
                    part
                    for part in non_x3d_candidates
                    if _extract_cpu_core_count(part) >= core_floor
                    and (_get_cpu_perf_score(part) or 0) >= perf_floor
                ]
                if not filtered:
                    filtered = [
                        part
                        for part in non_x3d_candidates
                        if _extract_cpu_core_count(part) >= core_floor
                    ]
                candidate_pool = filtered or non_x3d_candidates
                replacement = sorted(
                    candidate_pool,
                    key=lambda part: (
                        -int(_get_cpu_perf_score(part) or 0),
                        -_extract_cpu_core_count(part),
                        -_extract_cpu_core_threads(part),
                        int(getattr(part, 'price', 0) or 0),
                    ),
                )[0]
            else:
                replacement = non_x3d_candidates[0]

    if not replacement:
        return selected_parts, False

    original_total = _sum_selected_price(selected_parts)
    adjusted = dict(selected_parts)
    adjusted['cpu'] = replacement
    adjusted = _resolve_compatibility(adjusted, usage, options=probe_options)
    adjusted_cpu = adjusted.get('cpu')
    if not adjusted_cpu or _is_cpu_x3d(adjusted_cpu):
        return selected_parts, False

    adjusted_total = _sum_selected_price(adjusted)
    if original_total <= budget and adjusted_total > budget:
        return selected_parts, False

    return adjusted, True


def build_configuration_response(
    budget,
    usage,
    cooler_type='any',
    radiator_size='any',
    cooling_profile='balanced',
    case_size='any',
    case_fan_policy='auto',
    cpu_vendor='any',
    build_priority='balanced',
    storage_preference='ssd',
    storage2_part_id=None,
    storage3_part_id=None,
    os_edition='auto',
    custom_budget_weights=None,
    min_storage_capacity_gb=None,
    max_motherboard_chipset='any',
    enforce_gaming_x3d=True,
    persist=True,
    auto_adjust_reference_budget=None,
    require_gaming_x3d_cpu=False,
    duplicate_retry_count=0,
    configuration_name=None,
    cpu_part_id=None,
    selected_budget_tier=None,
):
    if not isinstance(budget, int) or budget < 50000 or budget > 1500000:
        return None, Response({'detail': 'budgetは50,000円以上1,500,000円以下で入力してください'}, status=status.HTTP_400_BAD_REQUEST)

    usage = _normalize_usage_code(usage)
    if usage is None:
        return None, Response({'detail': 'usage must be gaming, general, creator, business, or workstation'}, status=status.HTTP_400_BAD_REQUEST)

    requested_build_priority = _normalize_build_priority(build_priority)
    requested_budget_tier = _normalize_budget_tier_code(selected_budget_tier)

    input_budget = int(budget)
    market_price_range = _get_latest_market_price_range_from_db()

    budget, market_budget_adjusted, market_budget_note = _apply_scraped_market_budget_correction(
        input_budget,
        usage,
        build_priority,
        market_range=market_price_range,
    )

    if usage == 'gaming' and build_priority == 'spec' and _is_low_end_gaming_budget(budget, usage):
        fallback_response, fallback_error = build_configuration_response(
            budget,
            usage,
            cooler_type,
            radiator_size,
            cooling_profile,
            case_size,
            case_fan_policy,
            cpu_vendor,
            'cost',
            storage_preference,
            storage2_part_id,
            storage3_part_id,
            os_edition,
            custom_budget_weights,
            min_storage_capacity_gb,
            max_motherboard_chipset,
            enforce_gaming_x3d=False,
            persist=persist,
            auto_adjust_reference_budget=auto_adjust_reference_budget,
            require_gaming_x3d_cpu=require_gaming_x3d_cpu,
            duplicate_retry_count=duplicate_retry_count,
            configuration_name=configuration_name,
            selected_budget_tier=requested_budget_tier,
        )
        if fallback_error:
            return None, fallback_error
        fallback_response['message'] = '低予算のスペック重視は探索時間が長くなるため、コスト重視の近似構成へ自動切替しました。'
        fallback_response['requested_build_priority'] = requested_build_priority
        fallback_response['effective_build_priority'] = 'cost'
        fallback_response['build_priority_fallback_applied'] = True
        fallback_response['build_priority'] = requested_build_priority
        
        # 残りの予算内でパーツをアップグレード
        fallback_response = _upgrade_fallback_config_for_budget_utilization(
            fallback_response, budget, usage, options={'build_priority': requested_build_priority}
        )
        
        return fallback_response, None

    # dGPU解禁条件を満たす場合はfallbackをスキップしてdGPU構成を生成する
    _spec_dgpu_unlocked = (
        usage in {'general', 'business'}
        and budget >= SPEC_GPU_UNLOCK_BUDGET_THRESHOLD
    )
    if usage in IGPU_USAGES and requested_build_priority == 'spec' and _classify_budget_tier(budget, usage=usage) == 'low' and not _spec_dgpu_unlocked:
        fallback_response, fallback_error = build_configuration_response(
            budget,
            usage,
            cooler_type,
            radiator_size,
            cooling_profile,
            case_size,
            case_fan_policy,
            cpu_vendor,
            'cost',
            storage_preference,
            storage2_part_id,
            storage3_part_id,
            os_edition,
            custom_budget_weights,
            min_storage_capacity_gb,
            max_motherboard_chipset,
            enforce_gaming_x3d=enforce_gaming_x3d,
            persist=persist,
            auto_adjust_reference_budget=auto_adjust_reference_budget,
            require_gaming_x3d_cpu=require_gaming_x3d_cpu,
            duplicate_retry_count=duplicate_retry_count,
            configuration_name=configuration_name,
            selected_budget_tier=requested_budget_tier,
        )
        if fallback_error:
            return None, fallback_error
        fallback_response['message'] = '低予算の汎用スペック重視は探索時間が長くなるため、コスト重視の近似構成へ自動切替しました。'
        fallback_response['requested_build_priority'] = requested_build_priority
        fallback_response['effective_build_priority'] = 'cost'
        fallback_response['build_priority_fallback_applied'] = True
        fallback_response['build_priority'] = requested_build_priority
        
        # 残りの予算内でパーツをアップグレード
        fallback_response = _upgrade_fallback_config_for_budget_utilization(
            fallback_response, budget, usage, options={'build_priority': requested_build_priority}
        )
        
        return fallback_response, None

    selection_options = _normalize_selection_options(
        cooler_type=cooler_type,
        radiator_size=radiator_size,
        cooling_profile=cooling_profile,
        case_size=case_size,
        case_fan_policy=case_fan_policy,
        cpu_vendor=cpu_vendor,
        build_priority=build_priority,
        os_edition=os_edition,
        storage_preference=storage_preference,
        min_storage_capacity_gb=min_storage_capacity_gb,
        max_motherboard_chipset=max_motherboard_chipset,
    )
    selection_options['usage'] = usage
    selection_options['budget'] = budget
    if requested_budget_tier:
        selection_options['selected_budget_tier'] = requested_budget_tier
    selection_options['os_edition'] = _resolve_os_edition_by_usage(usage, selection_options['os_edition'])
    if usage in {'general', 'standard'} and selection_options.get('build_priority') == 'cost':
        # 汎用コスト重視は Home を優先する（Pro は明示要件がある用途でのみ選ぶ）。
        selection_options['os_edition'] = 'home'
    selection_options['minimum_gaming_gpu_perf_score'] = _minimum_gaming_low_end_gpu_perf_score(budget, usage)
    selection_options['_part_type_cache'] = {}
    if market_price_range is not None:
        selection_options['market_price_range'] = market_price_range
    if auto_adjust_reference_budget is not None:
        selection_options['auto_adjust_reference_budget'] = int(auto_adjust_reference_budget)
    if usage in {'general', 'business', 'standard'} and selection_options.get('build_priority') == 'cost':
        # 相場補正で予算が上振れしても、汎用costはユーザー入力予算を基準に価格感を維持する。
        anchor_budget = int(auto_adjust_reference_budget) if auto_adjust_reference_budget is not None else int(input_budget)
        selection_options['auto_adjust_reference_budget'] = anchor_budget
    # ローエンド gaming/cost では X3D CPU を優先（強制ではなく prefer）
    # GPU(RTX 3050) 予算を圧迫しすぎないよう、X3D 強制は避け、prefer にとどめる
    if usage == 'gaming' and selection_options.get('build_priority') == 'cost' and budget < _budget_tier_threshold(usage, 'low'):
        pass  # X3D 強制ではなく、_prefer_higher_gaming_cost_x3d_cpu の後段処理で prefer する
    if require_gaming_x3d_cpu:
        selection_options['require_gaming_x3d_cpu'] = True
    # gaming + cost は常に X3D 必須にする。
    if usage == 'gaming' and selection_options.get('build_priority') == 'cost':
        selection_options['require_gaming_x3d_cpu'] = True
    elif require_gaming_x3d_cpu:
        selection_options['require_gaming_x3d_cpu'] = True

    if usage == 'gaming':
        selection_options = dict(selection_options)
        if not selection_options.get('min_storage_capacity_gb'):
            if selection_options.get('build_priority') == 'spec':
                selection_options['min_storage_capacity_gb'] = 1000 if budget >= 220000 else 512
            elif selection_options.get('build_priority') == 'cost' and budget >= 500000:
                selection_options['min_storage_capacity_gb'] = 2000
            elif selection_options.get('build_priority') == 'cost' and budget >= 220000:
                selection_options['min_storage_capacity_gb'] = 1000

    if usage == 'gaming' and selection_options.get('build_priority') == 'spec':
        # gaming + spec はストレージ容量を優先するが、低予算では最低容量を抑える。
        selection_options['require_preferred_gaming_gpu'] = True
        selection_options['minimum_gaming_gpu_tier'] = _minimum_gaming_spec_gpu_tier(budget, usage, options=selection_options)

    # high帯(gaming + cost/spec): メモリは64GBまで、ストレージは1TBまで。
    if usage == 'gaming' and selection_options.get('build_priority') in {'cost', 'spec'}:
        budget_tier = _classify_budget_tier(budget, usage=usage)
        if budget_tier == 'high':
            selection_options = dict(selection_options)
            selection_options['max_memory_capacity_gb'] = 64
            selection_options['max_storage_capacity_gb'] = 1000
        elif budget_tier == 'premium' and selection_options.get('build_priority') == 'cost':
            selection_options = dict(selection_options)
            # premium + cost はGPUへ予算を回しつつ、容量過多を抑える。
            selection_options['max_memory_capacity_gb'] = 64
            selection_options['max_storage_capacity_gb'] = 2000
            selection_options['preferred_psu_wattage'] = 1000
        elif budget_tier == 'premium' and selection_options.get('build_priority') == 'spec':
            selection_options = dict(selection_options)
            selection_options['max_memory_capacity_gb'] = 64
            selection_options['min_storage_capacity_gb'] = max(
                int(selection_options.get('min_storage_capacity_gb') or 0),
                2000,
            )

    # すべてのユースケースでメインストレージの最低容量を設定（SSD候補を確保）
    if not selection_options.get('min_storage_capacity_gb'):
        selection_options = dict(selection_options)
        if usage in {'creator', 'workstation', 'ai'}:
            selection_options['min_storage_capacity_gb'] = 1000
        elif usage == 'general':
            selection_options['min_storage_capacity_gb'] = 512

    if _is_general_cost_low_tier(usage, selection_options.get('build_priority'), budget):
        selection_options = dict(selection_options)
        selection_options['max_storage_capacity_gb'] = min(
            int(selection_options.get('max_storage_capacity_gb') or 1024),
            1024,
        )

    if selection_options.get('max_storage_capacity_gb') and selection_options.get('min_storage_capacity_gb'):
        if int(selection_options['min_storage_capacity_gb']) > int(selection_options['max_storage_capacity_gb']):
            selection_options = dict(selection_options)
            selection_options['min_storage_capacity_gb'] = int(selection_options['max_storage_capacity_gb'])

    normalized_custom_budget_weights = _normalize_custom_budget_weights(custom_budget_weights)
    if custom_budget_weights is not None and normalized_custom_budget_weights is None:
        return None, Response({'detail': 'custom_budget_weights must be a positive numeric mapping for part categories'}, status=status.HTTP_400_BAD_REQUEST)

    use_igpu = usage in IGPU_USAGES
    # general/business: spec重視かつ実効予算がしきい値以上なら dGPU を許可
    # (standard は USAGE_COMPAT_ALIASES により general に正規化されるため general を対象とする)
    if use_igpu and usage in {'general', 'business'} \
            and selection_options.get('build_priority') == 'spec' \
            and budget >= SPEC_GPU_UNLOCK_BUDGET_THRESHOLD:
        use_igpu = False
    priority_weights = _apply_build_priority_weights(
        usage,
        selection_options['build_priority'],
        use_igpu,
        custom_budget_weights=normalized_custom_budget_weights,
        budget=budget,
    )

    selected_parts = {}
    total_price = 0
    initial_selected_parts_snapshot = {}

    for part_type in PART_ORDER:
        if use_igpu and part_type == 'gpu':
            continue  # 内蔵GPU使用のためdGPUをスキップ
        # マザーボード選定時は先に確定したCPUのソケットを絞り込み条件に追加
        effective_options = selection_options
        if part_type == 'motherboard':
            cpu_part = selected_parts.get('cpu')
            if cpu_part:
                cpu_socket = _get_spec(cpu_part, 'socket')
                if cpu_socket:
                    effective_options = dict(selection_options)
                    effective_options['cpu_socket'] = cpu_socket
        if part_type == 'cpu_cooler':
            cpu_part = selected_parts.get('cpu')
            if cpu_part:
                cpu_socket = _get_spec(cpu_part, 'socket')
                if cpu_socket:
                    effective_options = dict(effective_options)
                    effective_options['cpu_socket'] = cpu_socket
        if part_type == 'memory':
            motherboard_part = selected_parts.get('motherboard')
            if motherboard_part:
                mb_mem_type = _infer_motherboard_memory_type(motherboard_part)
                if mb_mem_type:
                    effective_options = dict(effective_options)
                    effective_options['motherboard_memory_type'] = mb_mem_type
            cpu_part = selected_parts.get('cpu')
            if cpu_part:
                min_memory_speed_mhz = _minimum_memory_speed_for_selected_cpu(cpu_part, usage, options=effective_options)
                if min_memory_speed_mhz:
                    effective_options = dict(effective_options)
                    effective_options['min_memory_speed_mhz'] = min_memory_speed_mhz
        if part_type == 'case':
            motherboard_part = selected_parts.get('motherboard')
            if motherboard_part:
                motherboard_form_factor = _infer_motherboard_form_factor(motherboard_part)
                if motherboard_form_factor not in {'', 'unknown'}:
                    effective_options = dict(effective_options)
                    effective_options['motherboard_form_factor'] = motherboard_form_factor
            gpu_part = selected_parts.get('gpu')
            if gpu_part:
                gpu_length_mm = _extract_numeric_mm(_get_spec(gpu_part, 'gpu_length_mm'))
                if gpu_length_mm:
                    effective_options = dict(effective_options)
                    effective_options['gpu_length_mm'] = gpu_length_mm
        if part_type == 'psu':
            effective_options = dict(effective_options)
            effective_options['required_psu_wattage'] = _required_psu_wattage(selected_parts, usage)
        part = _pick_part_by_target(
            part_type,
            budget,
            usage,
            weights_override=priority_weights,
            options=effective_options,
        )
        if part:
            selected_parts[part_type] = part
            total_price += part.price

    # CPUソケット情報をoptions に付与して、互換チェック・ダウングレード時に引き継ぐ
    cpu_part = selected_parts.get('cpu')
    if cpu_part:
        cpu_socket = _get_spec(cpu_part, 'socket')
        if cpu_socket:
            selection_options = dict(selection_options)
            selection_options['cpu_socket'] = cpu_socket

    motherboard_part = selected_parts.get('motherboard')
    if motherboard_part:
        mb_mem_type = _infer_motherboard_memory_type(motherboard_part)
        if mb_mem_type:
            selection_options = dict(selection_options)
            selection_options['motherboard_memory_type'] = mb_mem_type

    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)
    initial_selected_parts_snapshot = dict(selected_parts)

    selected_parts = _rebalance_gaming_spec_gpu_memory(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _enforce_gaming_spec_prefers_x3d_cpu(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    if not selected_parts:
        return None, Response({'detail': '該当する構成が見つかりません'}, status=status.HTTP_404_NOT_FOUND)

    selected_parts, total_price = _downgrade_selected_parts(
        selected_parts,
        total_price,
        budget,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

    selected_parts = _enforce_gaming_spec_gpu_not_lower_than_memory(
        selected_parts,
        usage,
        options=selection_options,
    )
    selected_parts = _enforce_gaming_spec_prefers_rx_xt(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    total_price = _sum_selected_price(selected_parts)

    selected_parts, total_price = _drop_until_budget(
        selected_parts,
        total_price,
        budget,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

    selected_parts = _rebalance_gaming_spec_gpu_memory(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts, total_price = _downgrade_selected_parts(
        selected_parts,
        total_price,
        budget,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

    selected_parts = _enforce_gaming_spec_gpu_not_lower_than_memory(
        selected_parts,
        usage,
        options=selection_options,
    )
    selected_parts = _enforce_gaming_spec_prefers_rx_xt(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    total_price = _sum_selected_price(selected_parts)

    selected_parts, total_price = _upgrade_memory_to_capacity_target(
        selected_parts,
        total_price,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

    selected_parts, total_price = _upgrade_memory_with_surplus(
        selected_parts,
        total_price,
        budget,
        usage,
        options=selection_options,
    )

    selected_parts = _enforce_gaming_spec_gpu_not_lower_than_memory(
        selected_parts,
        usage,
        options=selection_options,
    )
    selected_parts = _enforce_gaming_spec_prefers_rx_xt(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    total_price = _sum_selected_price(selected_parts)

    extra_storage_parts = {}
    selected_storage2 = _resolve_storage_part_by_id(storage2_part_id)
    selected_storage3 = _resolve_storage_part_by_id(storage3_part_id)
    if selected_storage2:
        extra_storage_parts['storage2'] = selected_storage2
        total_price += selected_storage2.price
    if selected_storage3:
        extra_storage_parts['storage3'] = selected_storage3
        total_price += selected_storage3.price

    # 余剰予算の再配分を常に評価する。
    # 実際にアップグレードするかどうかは _upgrade_parts_with_surplus 側で用途/方針ごとに判定する。
    selected_parts, total_price = _upgrade_parts_with_surplus(
        selected_parts,
        total_price,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts, total_price = _upgrade_memory_to_capacity_target(
        selected_parts,
        total_price,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_gaming_spec_prefers_x3d_cpu(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _rightsize_case_after_selection(
        selected_parts,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _rightsize_motherboard_for_gaming_spec(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_gaming_cost_gpu_policy(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    if (
        usage == 'gaming'
        and selection_options.get('build_priority') == 'cost'
        and _is_low_end_gaming_budget(budget, usage)
    ):
        current_gpu = selected_parts.get('gpu')
        if current_gpu and not _is_gaming_low_end_tier_gpu(current_gpu):
            low_end_candidates = [
                p
                for p in PCPart.objects.filter(part_type='gpu').order_by('price')
                if _is_part_suitable('gpu', p)
                and _matches_selection_options('gpu', p, options=selection_options)
                and _is_gaming_low_end_tier_gpu(p)
            ]
            if low_end_candidates:
                low_end_pick = _pick_gaming_low_end_gpu(low_end_candidates, budget, usage, 'cost')
                low_end_pick = low_end_pick or low_end_candidates[0]
                trial = dict(selected_parts)
                trial['gpu'] = low_end_pick
                trial = _resolve_compatibility(trial, usage, options=selection_options)
                trial_total = _sum_selected_price(trial)
                trial, trial_total = _downgrade_selected_parts(
                    trial,
                    trial_total,
                    budget,
                    options=selection_options,
                )
                if trial_total <= budget and _is_gaming_low_end_tier_gpu(trial.get('gpu')):
                    selected_parts = trial
                else:
                    detail = 'ゲーミングPC（コスト重視）のローエンド予算帯では、予算内で成立するローエンドGPU在庫がありません。'
                    return None, Response(
                        {
                            'detail': detail,
                            'low_end_gpu_required': True,
                            'received_usage': usage,
                            'received_build_priority': selection_options.get('build_priority'),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                detail = 'ゲーミングPC（コスト重視）のローエンド予算帯で選択可能なローエンドGPU在庫がありません。'
                return None, Response(
                    {
                        'detail': detail,
                        'low_end_gpu_required': True,
                        'received_usage': usage,
                        'received_build_priority': selection_options.get('build_priority'),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _upgrade_gpu_after_motherboard_rightsize(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    # マザーボードの右サイズ化後にGPU/メモリ価格バランスが崩れるケースを再調整する。
    selected_parts = _rebalance_gaming_spec_gpu_memory(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _rebalance_gaming_spec_gpu_for_storage(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _enforce_gaming_spec_gpu_not_lower_than_memory(
        selected_parts,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _rebalance_gaming_cost_cpu_to_storage(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts, total_price = _rebalance_gaming_cost_gpu_for_memory(
        selected_parts,
        total_price,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _prefer_higher_gaming_cost_x3d_cpu(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_memory_speed_floor(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts, cpu_guard_adjusted = _enforce_non_premium_gaming_cost_cpu_guard(
        selected_parts,
        budget,
        usage,
        build_priority,
        options=selection_options,
    )
    if cpu_guard_adjusted:
        selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
        total_price = _sum_selected_price(selected_parts)

    selected = _serialize_selected_parts(
        selected_parts,
        extra_storage_parts=extra_storage_parts,
        use_igpu=False,
    )

    if use_igpu:
        cpu_part = selected_parts.get('cpu')
        igpu_entry = {
            'category': 'gpu',
            'name': '内蔵GPU（統合グラフィックス）',
            'price': 0,
            'url': cpu_part.url if cpu_part else '',
        }
        cpu_index = next((i for i, p in enumerate(selected) if p['category'] == 'cpu'), -1)
        selected.insert(cpu_index + 1, igpu_entry)

    if usage == 'gaming' and selection_options.get('build_priority') == 'cost' and budget >= 250000:
        selected_parts, total_price = _upgrade_memory_to_capacity_target(
            selected_parts,
            total_price,
            budget,
            usage,
            options=selection_options,
        )
        selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
        total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_gaming_spec_prefers_nvme_storage(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_gaming_spec_best_value_gpu(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _upgrade_to_liquid_cooler_with_surplus(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _upgrade_case_for_cooling_with_surplus(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_creator_staged_requirements(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _enforce_main_storage_ssd(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
    selected_parts = _enforce_main_storage_ssd(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _rightsize_psu_after_selection(
        selected_parts,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _rescue_case_for_igpu_usage(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    # 最終ガード: gaming+spec で GPU 価格 < メモリ価格の逆転を防ぐ。
    final_guard_options = dict(selection_options)
    final_guard_options['budget'] = budget
    selected_parts = _enforce_gaming_spec_gpu_not_lower_than_memory(
        selected_parts,
        usage,
        options=final_guard_options,
    )
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    requested_budget = input_budget
    budget_auto_adjusted = bool(market_budget_adjusted)
    recommended_budget_min_for_x3d = None
    x3d_enforcement_failed = False

    should_enforce_gaming_x3d = (
        enforce_gaming_x3d
        and usage == 'gaming'
        and not _is_low_end_gaming_budget(budget, usage)
    )

    if should_enforce_gaming_x3d:
        selected_cpu = selected_parts.get('cpu')
        if not _is_gaming_cpu_x3d_preferred(selected_cpu):
            low_end_uplift_budget = _recommend_min_budget_for_gaming_x3d_from_low_end_config(
                selected_parts,
                budget,
                usage,
            )
            recommended_budget_min_for_x3d = _recommend_min_budget_for_gaming_x3d(
                budget,
                usage,
                cooler_type,
                radiator_size,
                cooling_profile,
                case_size,
                case_fan_policy,
                cpu_vendor,
                build_priority,
                storage_preference,
                storage2_part_id,
                storage3_part_id,
                os_edition,
                custom_budget_weights,
                min_storage_capacity_gb,
                max_motherboard_chipset,
            )

            probe_budgets = []
            if low_end_uplift_budget and low_end_uplift_budget > budget:
                probe_budgets.append((low_end_uplift_budget, True))
            if recommended_budget_min_for_x3d and recommended_budget_min_for_x3d > budget:
                probe_budgets.append((recommended_budget_min_for_x3d, False))

            for probe_budget, is_low_end_uplift in probe_budgets:
                adjusted_response, adjusted_error = build_configuration_response(
                    probe_budget,
                    usage,
                    cooler_type,
                    radiator_size,
                    cooling_profile,
                    case_size,
                    case_fan_policy,
                    cpu_vendor,
                    build_priority,
                    storage_preference,
                    storage2_part_id,
                    storage3_part_id,
                    os_edition,
                    custom_budget_weights,
                    min_storage_capacity_gb,
                    max_motherboard_chipset,
                    enforce_gaming_x3d=False,
                    persist=persist,
                    auto_adjust_reference_budget=requested_budget,
                    require_gaming_x3d_cpu=True,
                    configuration_name=configuration_name,
                )
                if adjusted_error:
                    continue
                if _response_has_gaming_x3d_cpu(adjusted_response):
                    adjusted_response['requested_budget'] = requested_budget
                    adjusted_response['budget_auto_adjusted'] = True
                    adjusted_response['recommended_budget_min_for_x3d'] = probe_budget
                    adjusted_response['x3d_enforced'] = True
                    if is_low_end_uplift:
                        adjusted_response['message'] = (
                            f"構成を自動調整しました。以前ローエンドに採用した構成を基準に、X3D CPU必須のため予算を¥{probe_budget:,}へ引き上げました。"
                        )
                    else:
                        adjusted_response['message'] = (
                            f"構成を自動調整しました。X3D CPUを必須にするため、推奨予算を¥{probe_budget:,}へ自動調整しました。"
                        )
                    return adjusted_response, None

            x3d_enforcement_failed = True
            recommended_budget_min_for_x3d = recommended_budget_min_for_x3d or low_end_uplift_budget

    # ユーザーが直接CPUを指定した場合は、自動選定結果を上書きする
    if cpu_part_id is not None:
        try:
            override_cpu = PCPart.objects.get(id=int(cpu_part_id), part_type='cpu')
            selected_parts['cpu'] = override_cpu
            total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})
        except (PCPart.DoesNotExist, ValueError, TypeError):
            pass  # 無効なIDの場合は自動選定のままにする

    selected = []
    for part_type in PART_ORDER:
        part = selected_parts.get(part_type)
        if not part:
            continue
        selected.append({
            'category': part_type,
            'name': part.name,
            'price': part.price,
            'url': part.url,
            'specs': part.specs,
        })

    for part_type in ('storage2', 'storage3'):
        part = extra_storage_parts.get(part_type)
        if not part:
            continue
        selected.append({
            'category': part_type,
            'name': part.name,
            'price': part.price,
            'url': part.url,
            'specs': part.specs,
        })

    if usage == 'gaming' and build_priority == 'cost':
        current_cpu = selected_parts.get('cpu')
        current_cpu_name = str(getattr(current_cpu, 'name', '') or '').lower()
        if int(budget) < GAMING_PREMIUM_BUDGET_MIN and '9850x3d' in current_cpu_name:
            replacement_cpu = None

            preferred_9800 = [
                part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
                if _is_part_suitable('cpu', part)
                and '9800x3d' in str(getattr(part, 'name', '') or '').lower()
            ]
            if preferred_9800:
                replacement_cpu = preferred_9800[0]
            else:
                fallback_pool = [
                    part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
                    if _is_part_suitable('cpu', part)
                    and '9850x3d' not in str(getattr(part, 'name', '') or '').lower()
                    and ('ryzen' in str(getattr(part, 'name', '') or '').lower() or 'amd' in str(getattr(part, 'name', '') or '').lower())
                ]
                replacement_cpu = _pick_amd_gaming_cpu(fallback_pool, 'cost', require_x3d=False)

            if replacement_cpu:
                selected_parts['cpu'] = replacement_cpu
                selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
                total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})
                cpu_index = next((i for i, p in enumerate(selected) if p['category'] == 'cpu'), -1)
                if cpu_index >= 0:
                    selected[cpu_index] = {
                        'category': 'cpu',
                        'name': replacement_cpu.name,
                        'price': replacement_cpu.price,
                        'url': replacement_cpu.url,
                        'specs': replacement_cpu.specs,
                    }

    enforce_cpu_tier = True
    if usage == 'gaming' and build_priority == 'cost':
        budget_tier_for_cost = _classify_budget_tier_from_market_range(
            budget,
            market_range=selection_options.get('market_price_range'),
        )
        enforce_cpu_tier = budget_tier_for_cost in {'high', 'premium'}

    if enforce_cpu_tier:
        selected_parts, selected, cpu_tier_adjusted = _enforce_gaming_x3d_cpu_by_budget_tier(
            selected_parts,
            selected,
            budget,
            usage,
            build_priority,
            options=selection_options,
        )
        if cpu_tier_adjusted:
            total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})

    selected_parts, final_cpu_guard_adjusted = _enforce_non_premium_gaming_cost_cpu_guard(
        selected_parts,
        budget,
        usage,
        build_priority,
        options=selection_options,
    )
    if final_cpu_guard_adjusted:
        total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})
        cpu_part = selected_parts.get('cpu')
        cpu_index = next((i for i, p in enumerate(selected) if p.get('category') == 'cpu'), -1)
        if cpu_part and cpu_index >= 0:
            selected[cpu_index] = {
                'category': 'cpu',
                'name': cpu_part.name,
                'price': cpu_part.price,
                'url': cpu_part.url,
                'specs': cpu_part.specs,
            }

    selected_parts, non_x3d_cpu_applied = _prefer_non_x3d_cpu_when_possible(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    if non_x3d_cpu_applied:
        selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
        total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})

    os_policy_message = None
    selected_parts, os_policy_budget, os_policy_message, os_policy_error = _enforce_required_os_with_budget_policy(
        selected_parts,
        budget,
        options=selection_options,
    )
    if os_policy_error:
        return None, os_policy_error
    if os_policy_budget > budget:
        budget = os_policy_budget
        budget_auto_adjusted = True
    total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})
    selected = _serialize_selected_parts(
        selected_parts,
        extra_storage_parts=extra_storage_parts,
        use_igpu=use_igpu,
    )

    estimated_power = _estimate_system_power_w({**selected_parts, **extra_storage_parts}, usage)
    selected_gpu_perf_score = 0
    selected_gpu = selected_parts.get('gpu')
    selected_gpu_gaming_tier_label = ''
    if selected_gpu:
        selected_gpu_perf_score = _infer_gpu_perf_score_for_requirement(selected_gpu)
        if usage == 'gaming':
            selected_gpu_gaming_tier_label = _infer_gaming_gpu_tier_label(selected_gpu)

    effective_budget = requested_budget
    if market_budget_adjusted:
        effective_budget = budget
        budget_auto_adjusted = True
    if usage == 'gaming' and selection_options.get('build_priority') == 'spec' and total_price < requested_budget:
        effective_budget = total_price
        budget_auto_adjusted = True
    if budget > effective_budget:
        effective_budget = budget
        budget_auto_adjusted = True

    if usage == 'gaming' and build_priority == 'cost' and int(budget) < GAMING_PREMIUM_BUDGET_MIN:
        current_cpu = selected_parts.get('cpu')
        current_cpu_name = str(getattr(current_cpu, 'name', '') or '').lower()
        if '9850x3d' in current_cpu_name:
            forced_9800 = next(
                (
                    part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
                    if '9800x3d' in str(getattr(part, 'name', '') or '').lower() and _is_part_suitable('cpu', part)
                ),
                None,
            )
            if forced_9800:
                selected_parts['cpu'] = forced_9800
                total_price = _sum_selected_price({**selected_parts, **extra_storage_parts})
                cpu_index = next((i for i, p in enumerate(selected) if p.get('category') == 'cpu'), -1)
                if cpu_index >= 0:
                    selected[cpu_index] = {
                        'category': 'cpu',
                        'name': forced_9800.name,
                        'price': forced_9800.price,
                        'url': forced_9800.url,
                        'specs': forced_9800.specs,
                    }

    def _part_adjustment_reason(part_type):
        if part_type == 'cpu':
            if cpu_vendor == 'intel':
                return 'IntelメーカーのCPUを優先したため、Intel CPU候補から再選定しました。'
            if cpu_vendor == 'amd':
                return 'AMDメーカーのCPUを優先したため、AMD CPU候補から再選定しました。'
            return '用途・予算帯・方針に合わせてCPUを再選定しました。'
        if part_type == 'gpu':
            return '用途・予算帯・性能条件に合わせてGPUを再選定しました。'
        if part_type == 'memory':
            return '容量・速度・予算バランスを満たすためメモリを調整しました。'
        if part_type in {'storage', 'storage2', 'storage3'}:
            return '容量要件と予算バランスを満たすためストレージを調整しました。'
        if part_type in {'motherboard', 'case', 'psu', 'cpu_cooler'}:
            return '互換性と冷却・電力条件を満たすためパーツを調整しました。'
        if part_type == 'os':
            return 'OS必須条件を満たすため選定を調整しました。'
        return '予算と構成条件に合わせてパーツを調整しました。'

    def _to_part_adjustment_entry(part_type, before_part, after_part):
        before_name = before_part.name if before_part else '未選択'
        after_name = after_part.name if after_part else '未選択'
        if before_name == after_name:
            return None
        return {
            'category': part_type,
            'category_label': PART_TYPE_LABELS.get(part_type, part_type),
            'from_name': before_name,
            'from_price': int(before_part.price) if before_part else 0,
            'to_name': after_name,
            'to_price': int(after_part.price) if after_part else 0,
            'reason': _part_adjustment_reason(part_type),
        }

    # ケースファン: 付属ファンなしケースが選択された場合に自動追加
    case_fan_part = None
    selected_case = selected_parts.get('case')
    if selected_case:
        case_specs = getattr(selected_case, 'specs', {}) or {}
        included_fan_count = _extract_numeric_fan_count(case_specs.get('included_fan_count'))
        if included_fan_count == 0 or included_fan_count is None:
            remaining_budget = max(0, budget - total_price)
            case_fan_part = _pick_case_fan_for_fanless_case(
                remaining_budget,
                selection_options.get('case_fan_policy', 'auto'),
                selection_options.get('build_priority', 'balanced'),
            )
            if case_fan_part:
                total_price += case_fan_part.price
                selected.append({
                    'category': 'case_fan',
                    'name': case_fan_part.name,
                    'price': case_fan_part.price,
                    'url': case_fan_part.url,
                    'specs': case_fan_part.specs,
                })

    before_parts = dict(initial_selected_parts_snapshot)
    after_parts = dict(selected_parts)
    after_parts.update(extra_storage_parts)
    part_adjustments = []
    for part_type in [*PART_ORDER, 'storage2', 'storage3']:
        change_entry = _to_part_adjustment_entry(part_type, before_parts.get(part_type), after_parts.get(part_type))
        if change_entry:
            part_adjustments.append(change_entry)

    configuration = None
    if persist:
        if duplicate_retry_count < 2:
            latest_config = Configuration.objects.filter(is_deleted=False).order_by('-created_at', '-id').first()
            if _has_same_configuration_signature(
                latest_config,
                usage,
                budget,
                selected_parts,
                extra_storage_parts,
                use_igpu,
                case_fan_part=case_fan_part,
            ):
                return build_configuration_response(
                    input_budget,
                    usage,
                    cooler_type,
                    radiator_size,
                    cooling_profile,
                    case_size,
                    case_fan_policy,
                    cpu_vendor,
                    build_priority,
                    storage_preference,
                    storage2_part_id,
                    storage3_part_id,
                    os_edition,
                    custom_budget_weights,
                    min_storage_capacity_gb,
                    max_motherboard_chipset,
                    enforce_gaming_x3d=enforce_gaming_x3d,
                    persist=persist,
                    auto_adjust_reference_budget=auto_adjust_reference_budget,
                    require_gaming_x3d_cpu=require_gaming_x3d_cpu,
                    duplicate_retry_count=duplicate_retry_count + 1,
                    configuration_name=configuration_name,
                )

        configuration = Configuration.objects.create(
            name=str(configuration_name or '').strip(),
            budget=effective_budget,
            usage=usage,
            total_price=total_price,
            cpu=selected_parts.get('cpu'),
            cpu_cooler=selected_parts.get('cpu_cooler'),
            gpu=None,  # iGPU構成はgpu=None、gaming/creatorは後で上書き
            motherboard=selected_parts.get('motherboard'),
            memory=selected_parts.get('memory'),
            storage=selected_parts.get('storage'),
            storage2=extra_storage_parts.get('storage2'),
            storage3=extra_storage_parts.get('storage3'),
            os=selected_parts.get('os'),
            psu=selected_parts.get('psu'),
            case=selected_parts.get('case'),
            case_fan=case_fan_part,
        ) if use_igpu else Configuration.objects.create(
            name=str(configuration_name or '').strip(),
            budget=effective_budget,
            usage=usage,
            total_price=total_price,
            cpu=selected_parts.get('cpu'),
            cpu_cooler=selected_parts.get('cpu_cooler'),
            gpu=selected_parts.get('gpu'),
            motherboard=selected_parts.get('motherboard'),
            memory=selected_parts.get('memory'),
            storage=selected_parts.get('storage'),
            storage2=extra_storage_parts.get('storage2'),
            storage3=extra_storage_parts.get('storage3'),
            os=selected_parts.get('os'),
            psu=selected_parts.get('psu'),
            case=selected_parts.get('case'),
            case_fan=case_fan_part,
        )

    budget_for_tier_display = _budget_for_tier_display(input_budget, usage, requested_build_priority)
    response_budget_tier = requested_budget_tier or _classify_budget_tier(budget_for_tier_display, usage=usage)

    response_data = {
        'name': str(configuration_name or '').strip(),
        'usage': usage,
        'budget': effective_budget,
        'budget_tier': response_budget_tier,
        'budget_tier_label': _budget_tier_label_jp(response_budget_tier),
        'cooler_type': selection_options['cooler_type'],
        'radiator_size': selection_options['radiator_size'],
        'cooling_profile': selection_options['cooling_profile'],
        'case_size': selection_options['case_size'],
        'case_fan_policy': selection_options['case_fan_policy'],
        'cpu_vendor': selection_options['cpu_vendor'],
        'build_priority': selection_options['build_priority'],
        'requested_build_priority': requested_build_priority,
        'effective_build_priority': selection_options['build_priority'],
        'build_priority_fallback_applied': requested_build_priority != selection_options['build_priority'],
        'storage_preference': selection_options['storage_preference'],
        'os_edition': selection_options['os_edition'],
        'custom_budget_weights': normalized_custom_budget_weights,
        'requested_budget': requested_budget,
        'budget_auto_adjusted': budget_auto_adjusted,
        'market_budget_adjusted': bool(market_budget_adjusted),
        'market_budget_note': market_budget_note,
        'recommended_budget_min_for_x3d': recommended_budget_min_for_x3d,
        'x3d_enforced': bool(should_enforce_gaming_x3d and not x3d_enforcement_failed),
        'x3d_required_unavailable': bool(x3d_enforcement_failed),
        'minimum_gaming_gpu_perf_score': int(selection_options.get('minimum_gaming_gpu_perf_score') or 0),
        'selected_gpu_perf_score': int(selected_gpu_perf_score or 0),
        'selected_gpu_gaming_tier_label': selected_gpu_gaming_tier_label,
        'configuration_id': configuration.id if configuration else None,
        'total_price': total_price,
        'estimated_power_w': estimated_power,
        'part_adjustments': part_adjustments,
        'parts': selected,
    }
    if x3d_enforcement_failed:
        response_data['message'] = '構成を自動調整しましたが、現在の予算ではX3D構成を自動確定できませんでした。'
    if os_policy_message:
        if response_data.get('message'):
            response_data['message'] = f"{response_data['message']} {os_policy_message}"
        else:
            response_data['message'] = os_policy_message
    return response_data, None


PART_TYPE_LABELS = {
    'cpu':         'CPU',
    'cpu_cooler':  'CPUクーラー',
    'gpu':         'GPU',
    'motherboard': 'マザーボード',
    'memory':      'メモリー',
    'storage':     'ストレージ',
    'os':          'OS',
    'psu':         '電源',
    'case':        'ケース',
    'case_fan':    'ケースファン',
}


def build_scraper_status_summary():
    latest = ScraperStatus.objects.order_by('-updated_at').first()
    total_parts = PCPart.objects.count()

    # カテゴリ別件数・価格帯を一括集計
    from django.db.models import Count as DbCount2, Min as DbMin, Max as DbMax
    rows = (
        PCPart.objects
        .values('part_type')
        .annotate(count=DbCount2('id'), min_price=DbMin('price'), max_price=DbMax('price'))
        .order_by('part_type')
    )
    category_stats = [
        {
            'part_type': r['part_type'],
            'label': PART_TYPE_LABELS.get(r['part_type'], r['part_type']),
            'count': r['count'],
            'min_price': r['min_price'],
            'max_price': r['max_price'],
        }
        for r in rows
    ]
    cached_categories = sorted([r['part_type'] for r in category_stats])

    return {
        'cache_enabled': latest.cache_enabled if latest else True,
        'cache_ttl_seconds': latest.cache_ttl_seconds if latest else 3600,
        'last_update_time': latest.updated_at.isoformat() if latest else None,
        'cached_categories': cached_categories,
        'category_stats': category_stats,
        'total_parts_in_db': total_parts,
        'retry_count': 3,
        'rate_limit_delay': 1.0,
    }


class PCPartViewSet(viewsets.ModelViewSet):
    """PC パーツの CRUD API"""
    queryset = PCPart.objects.all()
    serializer_class = PCPartSerializer
    filterset_fields = ['part_type']
    search_fields = ['name']

    @staticmethod
    def _normalize_storage_category(value):
        normalized = (value or '').strip().lower()
        if normalized in {'nvme', 'sata', 'other'}:
            return normalized
        return ''

    def get_queryset(self):
        queryset = PCPart.objects.all()
        part_type = (self.request.query_params.get('part_type') or '').strip().lower()
        storage_category = self._normalize_storage_category(self.request.query_params.get('storage_category'))
        if part_type == 'storage' and storage_category:
            queryset = queryset.filter(storage_detail__storage_category=storage_category)
        return queryset
    
    @action(detail=False, methods=['get'])
    def by_type(self, request):
        part_type = request.query_params.get('type')
        slot = (request.query_params.get('slot') or '').strip().lower()
        storage_category = self._normalize_storage_category(request.query_params.get('storage_category'))
        if not part_type:
            return Response({'error': 'type parameter required'}, status=status.HTTP_400_BAD_REQUEST)
        parts = PCPart.objects.filter(part_type=part_type)
        if part_type == 'storage' and storage_category:
            parts = parts.filter(storage_detail__storage_category=storage_category)
        # メインストレージ置換候補は API 側でも SSD のみ返す。
        if part_type == 'storage' and slot == 'storage':
            parts = [part for part in parts if _infer_storage_media_type(part) == 'ssd']
        serializer = self.get_serializer(parts, many=True)
        return Response(serializer.data)


class ConfigurationViewSet(viewsets.ModelViewSet):
    """PC 構成の CRUD API"""
    queryset = Configuration.objects.filter(is_deleted=False)
    serializer_class = ConfigurationSerializer
    filterset_fields = ['usage']

    def get_queryset(self):
        return Configuration.objects.filter(is_deleted=False)
    
    def perform_create(self, serializer):
        """構成作成時に合計金額を計算"""
        config = serializer.save()
        self._calculate_total_price(config)
    
    def perform_update(self, serializer):
        """構成更新時に合計金額を再計算"""
        config = serializer.save()
        self._calculate_total_price(config)
    
    def _calculate_total_price(self, config):
        """合計金額を計算"""
        total = 0
        for part_field in ['cpu', 'cpu_cooler', 'gpu', 'motherboard', 'memory', 'storage', 'os', 'psu', 'case']:
            part = getattr(config, part_field)
            if part:
                total += part.price
        for part_field in ['storage2', 'storage3']:
            part = getattr(config, part_field, None)
            if part:
                total += part.price
        config.total_price = total
        config.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        response_data, error_response = build_configuration_response(
            request.data.get('budget'),
            request.data.get('usage'),
            request.data.get('cooler_type'),
            request.data.get('radiator_size'),
            request.data.get('cooling_profile'),
            request.data.get('case_size'),
            request.data.get('case_fan_policy'),
            request.data.get('cpu_vendor'),
            request.data.get('build_priority'),
            request.data.get('storage_preference'),
            request.data.get('storage2_part_id'),
            request.data.get('storage3_part_id'),
            request.data.get('os_edition'),
            request.data.get('custom_budget_weights'),
            request.data.get('min_storage_capacity_gb'),
            request.data.get('max_motherboard_chipset'),
            configuration_name=request.data.get('name'),
            selected_budget_tier=request.data.get('selected_budget_tier'),
        )
        if error_response:
            error_response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            error_response['Pragma'] = 'no-cache'
            error_response['Expires'] = '0'
            return error_response
        response = Response(response_data)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response


class ScraperStatusViewSet(viewsets.ModelViewSet):
    """スクレイパー状態管理 API"""
    queryset = ScraperStatus.objects.all()
    serializer_class = ScraperStatusSerializer

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        return Response(build_scraper_status_summary())


class GenerateConfigAPIView(APIView):
    """Frontend互換: FastAPIの /generate-config 相当"""

    def post(self, request):
        # gaming + cost モードでは X3D cpu を強制しない（コスト削減のため）
        enforce_x3d = not (
            request.data.get('usage') == 'gaming' 
            and request.data.get('build_priority') == 'cost'
        )
        
        response_data, error_response = build_configuration_response(
            request.data.get('budget'),
            request.data.get('usage'),
            request.data.get('cooler_type'),
            request.data.get('radiator_size'),
            request.data.get('cooling_profile'),
            request.data.get('case_size'),
            request.data.get('case_fan_policy'),
            request.data.get('cpu_vendor'),
            request.data.get('build_priority'),
            request.data.get('storage_preference'),
            request.data.get('storage2_part_id'),
            request.data.get('storage3_part_id'),
            request.data.get('os_edition'),
            request.data.get('custom_budget_weights'),
            request.data.get('min_storage_capacity_gb'),
            request.data.get('max_motherboard_chipset'),
            enforce_gaming_x3d=enforce_x3d,
            configuration_name=request.data.get('name'),
            cpu_part_id=request.data.get('cpu_part_id'),
            selected_budget_tier=request.data.get('selected_budget_tier'),
        )
        if error_response:
            error_response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            error_response['Pragma'] = 'no-cache'
            error_response['Expires'] = '0'
            return error_response

        request_budget = int(request.data.get('budget') or 0)
        if (
            request.data.get('usage') == 'gaming'
            and request.data.get('build_priority') == 'cost'
            and request_budget < GAMING_PREMIUM_BUDGET_MIN
        ):
            cpu_index = next(
                (i for i, p in enumerate(response_data.get('parts', [])) if p.get('category') == 'cpu'),
                -1,
            )
            if cpu_index >= 0:
                cpu_item = response_data['parts'][cpu_index]
                cpu_name = str(cpu_item.get('name', '') or '').lower()
                if '9850x3d' in cpu_name:
                    replacement = next(
                        (
                            part for part in PCPart.objects.filter(part_type='cpu').order_by('price')
                            if '9800x3d' in str(getattr(part, 'name', '') or '').lower() and _is_part_suitable('cpu', part)
                        ),
                        None,
                    )
                    if replacement:
                        old_price = int(cpu_item.get('price') or 0)
                        response_data['parts'][cpu_index] = {
                            'category': 'cpu',
                            'name': replacement.name,
                            'price': replacement.price,
                            'url': replacement.url,
                            'specs': replacement.specs,
                        }
                        response_data['total_price'] = int(response_data.get('total_price') or 0) - old_price + int(replacement.price or 0)

        response = Response(response_data)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response


class ScraperStatusCompatAPIView(APIView):
    """Frontend互換: FastAPIの /scraper/status 相当"""

    def get(self, request):
        return Response(build_scraper_status_summary())


class MarketPriceRangeAPIView(APIView):
    """フロントエンド向け: ドスパラ相場レンジを返す"""

    def get(self, request):
        data = _get_latest_market_price_range_from_db()
        x3d_cpus = [
            part
            for part in PCPart.objects.filter(part_type='cpu').order_by('price')
            if _is_part_suitable('cpu', part) and _is_gaming_cpu_x3d_preferred(part)
        ]
        if x3d_cpus:
            x3d_cpu_floor = x3d_cpus[0].price
            # 低価格帯の実在部品構成を踏まえた実用下限。UIの推奨帯に利用する。
            data['gaming_x3d_cpu_floor'] = x3d_cpu_floor
            data['gaming_x3d_recommended_min'] = max(data.get('min', 50000), x3d_cpu_floor + 120000)
        return Response(data)


def _serialize_gpu_performance_entry(entry):
    return {
        'gpu_name': entry.gpu_name,
        'model_key': entry.model_key,
        'vendor': entry.vendor,
        'vram_gb': entry.vram_gb,
        'perf_score': entry.perf_score,
        'detail_url': entry.detail_url,
        'rank_global': entry.rank_global,
    }


class GpuPerformanceLatestAPIView(APIView):
    """GPU性能比較の最新スナップショットを返す"""

    def get(self, request):
        latest = GPUPerformanceSnapshot.objects.order_by('-fetched_at', '-id').first()
        if not latest:
            return Response({'detail': 'GPU performance snapshot not found.'}, status=status.HTTP_404_NOT_FOUND)

        entries = GPUPerformanceEntry.objects.filter(snapshot=latest, is_laptop=False).order_by('-perf_score', 'gpu_name')
        return Response(
            {
                'snapshot': {
                    'id': latest.id,
                    'source_name': latest.source_name,
                    'source_url': latest.source_url,
                    'updated_at_source': latest.updated_at_source,
                    'score_note': latest.score_note,
                    'parser_version': latest.parser_version,
                    'fetched_at': latest.fetched_at,
                },
                'entries': {
                    'count': entries.count(),
                    'next': None,
                    'previous': None,
                    'results': [_serialize_gpu_performance_entry(entry) for entry in entries],
                },
            }
        )


class GpuPerformanceCompareAPIView(APIView):
    """GPU性能比較を複数モデルで返す"""

    def _extract_requested_models(self, request):
        raw_values = []

        query_values = request.query_params.getlist('models')
        if query_values:
            raw_values.extend(query_values)

        if request.method == 'POST':
            payload_models = request.data.get('models', []) if isinstance(request.data, dict) else []
            if isinstance(payload_models, (list, tuple)):
                raw_values.extend(payload_models)
            elif payload_models:
                raw_values.append(payload_models)

        requested_models = []
        seen = set()
        for raw_value in raw_values:
            for token in str(raw_value).split(','):
                model = token.strip().upper()
                if not model or model in seen:
                    continue
                requested_models.append(model)
                seen.add(model)

        return requested_models

    def _compare(self, request):
        latest = GPUPerformanceSnapshot.objects.order_by('-fetched_at', '-id').first()
        if not latest:
            return Response({'detail': 'GPU performance snapshot not found.'}, status=status.HTTP_404_NOT_FOUND)

        requested_models = self._extract_requested_models(request)
        if not requested_models:
            return Response({'detail': 'models query parameter or models body is required.'}, status=status.HTTP_400_BAD_REQUEST)

        requested_model_keys = {
            _normalize_gpu_model_key(model): model
            for model in requested_models
            if _normalize_gpu_model_key(model)
        }

        entries = GPUPerformanceEntry.objects.filter(
            snapshot=latest,
            is_laptop=False,
        ).order_by('-perf_score', 'gpu_name')

        results = []
        matched_requested_keys = set()
        for entry in entries:
            normalized_entry_key = _normalize_gpu_model_key(entry.model_key)
            if normalized_entry_key not in requested_model_keys:
                continue
            matched_requested_keys.add(normalized_entry_key)
            results.append(_serialize_gpu_performance_entry(entry))

        missing_models = [
            model
            for model in requested_models
            if _normalize_gpu_model_key(model) not in matched_requested_keys
        ]

        return Response(
            {
                'snapshot_id': latest.id,
                'requested_models': requested_models,
                'missing_models': missing_models,
                'results': results,
            }
        )

    def get(self, request):
        return self._compare(request)

    def post(self, request):
        return self._compare(request)


def _serialize_cpu_selection_entry(entry):
    price = entry.get('price')
    perf_score = int(entry.get('perf_score', 0) or 0)
    price_value = int(price or 0) if price is not None else 0
    value_score = (perf_score / price_value) if price_value > 0 and perf_score > 0 else None
    return {
        'vendor': entry.get('vendor', ''),
        'model_name': entry.get('model_name', ''),
        'perf_score': perf_score,
        'price': price_value if price is not None else None,
        'value_score': value_score,
        'source_url': entry.get('source_url', ''),
    }


def _sort_cpu_selection_entries_for_cost(entries):
    def sort_key(entry):
        vendor = str(entry.get('vendor', '') or '').strip().lower()
        vendor_priority = 0 if vendor == 'amd' else 1
        value_score = entry.get('value_score')
        if value_score is None:
            value_score = 0
        perf_score = int(entry.get('perf_score', 0) or 0)
        price = int(entry.get('price', 0) or 0)
        return (
            vendor_priority,
            -float(value_score),
            -perf_score,
            price,
            entry.get('vendor', ''),
            entry.get('model_name', ''),
        )

    return sorted(entries, key=sort_key)


def _load_available_cpu_inventory_parts():
    return list(PCPart.objects.filter(part_type='cpu', is_active=True).order_by('price'))


def _match_available_cpu_part(model_name, inventory_parts=None):
    inventory_parts = inventory_parts or _load_available_cpu_inventory_parts()
    normalized_model = _normalize_cpu_model_query(model_name)
    if not normalized_model:
        return None

    matches = []
    for part in inventory_parts:
        normalized_part_name = _normalize_cpu_model_query(part.name)
        extracted_part_name = _extract_cpu_model_key_for_perf(part.name) or ''
        normalized_extracted = _normalize_cpu_model_query(extracted_part_name)
        candidate_keys = [normalized_part_name, normalized_extracted]
        if any(
            normalized_model == candidate_key
            or normalized_model in candidate_key
            or candidate_key in normalized_model
            for candidate_key in candidate_keys
            if candidate_key
        ):
            matches.append(part)

    if not matches:
        return None

    return sorted(matches, key=lambda part: int(part.price or 0))[0]


def _store_cpu_selection_snapshot(data):
    entries = data.get('entries', []) or []
    snapshot = CPUSelectionSnapshot.objects.create(
        source_name=data.get('source_name', 'pckoubou_cpu_spec_pages'),
        source_urls=data.get('source_urls', []) or [],
        exclude_intel_13_14=bool(data.get('exclude_intel_13_14', True)),
        entry_count=int(data.get('entry_count', len(entries)) or len(entries)),
        excluded_count=int(data.get('excluded_count', 0) or 0),
        parser_version=str(data.get('parser_version', 'v1') or 'v1'),
        fetched_at=timezone.now(),
    )

    normalized_entries = []
    for rank_global, entry in enumerate(sorted(entries, key=lambda row: int(row.get('perf_score', 0) or 0), reverse=True), 1):
        model_name = str(entry.get('model_name', '') or '').strip()
        perf_score = int(entry.get('perf_score', 0) or 0)
        if not model_name or perf_score <= 0:
            continue
        normalized_entries.append(CPUSelectionEntry(
            snapshot=snapshot,
            vendor=str(entry.get('vendor', '') or 'unknown').lower(),
            model_name=model_name,
            perf_score=perf_score,
            source_url=str(entry.get('source_url', '') or ''),
            rank_global=rank_global,
        ))

    if normalized_entries:
        CPUSelectionEntry.objects.bulk_create(normalized_entries)

    return snapshot


def _load_latest_cpu_selection_scores_from_db():
    snapshot = CPUSelectionSnapshot.objects.order_by('-fetched_at', '-id').first()
    if not snapshot:
        return None, {}, []

    entries = list(snapshot.entries.all().order_by('rank_global', '-perf_score', 'model_name'))
    scores = {}
    serialized = []
    for entry in entries:
        serialized_entry = {
            'vendor': entry.vendor,
            'model_name': entry.model_name,
            'perf_score': int(entry.perf_score),
            'source_url': entry.source_url,
            'rank_global': int(entry.rank_global or 0),
        }
        serialized.append(serialized_entry)
        model_name = _normalize_cpu_model_query(entry.model_name)
        if model_name:
            scores[model_name] = max(scores.get(model_name, 0), int(entry.perf_score))

    return snapshot, scores, serialized


def _normalize_cpu_model_query(value):
    return re.sub(r'\s+', ' ', (value or '').strip()).upper()


def _match_cpu_model_entry(entries, query):
    normalized_query = _normalize_cpu_model_query(query)
    if not normalized_query:
        return None

    for entry in entries:
        model_name = _normalize_cpu_model_query(entry.get('model_name'))
        if model_name == normalized_query:
            return entry
    for entry in entries:
        model_name = _normalize_cpu_model_query(entry.get('model_name'))
        if normalized_query in model_name or model_name in normalized_query:
            return entry
    return None


def _load_available_cpu_inventory_keys(inventory_parts=None):
    keys = []
    inventory_parts = inventory_parts or _load_available_cpu_inventory_parts()
    for part_name in [part.name for part in inventory_parts]:
        normalized_name = _normalize_cpu_model_query(part_name)
        if normalized_name:
            keys.append(normalized_name)
        extracted_name = _extract_cpu_model_key_for_perf(part_name)
        if extracted_name:
            keys.append(_normalize_cpu_model_query(extracted_name))
    return keys


def _filter_available_cpu_selection_entries(entries, inventory_parts=None):
    inventory_parts = inventory_parts or _load_available_cpu_inventory_parts()
    inventory_keys = _load_available_cpu_inventory_keys(inventory_parts)
    if not inventory_keys:
        return [], 0

    filtered_entries = []
    excluded_count = 0
    for entry in entries:
        normalized_name = _normalize_cpu_model_query(entry.get('model_name'))
        if not normalized_name:
            excluded_count += 1
            continue

        matched = any(
            normalized_name == inventory_key
            or normalized_name in inventory_key
            or inventory_key in normalized_name
            for inventory_key in inventory_keys
        )
        if not matched:
            excluded_count += 1
            continue

        filtered_entries.append(entry)

    return filtered_entries, excluded_count


class CpuSelectionMaterialLatestAPIView(APIView):
    """CPU選考資料の最新比較データを返す"""

    def get(self, request):
        snapshot = CPUSelectionSnapshot.objects.order_by('-fetched_at', '-id').first()
        if not snapshot:
            return Response({'detail': 'CPU selection snapshot not found.'}, status=status.HTTP_404_NOT_FOUND)

        entries = [
            {
                'vendor': row.vendor,
                'model_name': row.model_name,
                'perf_score': int(row.perf_score or 0),
                'source_url': row.source_url,
            }
            for row in snapshot.entries.all().order_by('rank_global', '-perf_score', 'model_name')
        ]
        inventory_parts = _load_available_cpu_inventory_parts()
        available_entries, removed_count = _filter_available_cpu_selection_entries(entries, inventory_parts=inventory_parts)

        serialized_entries = []
        for entry in available_entries:
            cpu_part = _match_available_cpu_part(entry.get('model_name', ''), inventory_parts=inventory_parts)
            serialized_entry = _serialize_cpu_selection_entry(entry)
            serialized_entry['cost_rank'] = _get_amd_cpu_rank_by_name(serialized_entry['model_name'], 'cost')
            if cpu_part is not None:
                serialized_entry['price'] = int(cpu_part.price or 0)
                serialized_entry['value_score'] = (
                    serialized_entry['perf_score'] / int(cpu_part.price or 0)
                    if int(cpu_part.price or 0) > 0 and serialized_entry['perf_score'] > 0
                    else None
                )
            serialized_entries.append(serialized_entry)

        serialized_entries = sorted(
            serialized_entries,
            key=lambda row: (
                0 if str(row.get('vendor', '') or '').strip().lower() == 'amd' else 1,
                row.get('cost_rank') if row.get('cost_rank') is not None else 10**9,
                -(row.get('value_score') or 0),
                -(row.get('perf_score') or 0),
                int(row.get('price') or 0),
                row.get('model_name', ''),
            ),
        )

        return Response(
            {
                'source_name': snapshot.source_name,
                'source_urls': snapshot.source_urls or [],
                'exclude_intel_13_14': bool(snapshot.exclude_intel_13_14),
                'entry_count': len(available_entries),
                'excluded_count': int(snapshot.excluded_count or 0) + removed_count,
                'entries': {
                    'count': len(serialized_entries),
                    'next': None,
                    'previous': None,
                    'results': serialized_entries,
                },
            }
        )


class CpuSelectionMaterialCompareAPIView(APIView):
    """CPU選考資料を指定モデルで比較して返す"""

    def _extract_requested_models(self, request):
        raw_values = []

        query_values = request.query_params.getlist('models')
        if query_values:
            raw_values.extend(query_values)

        if request.method == 'POST':
            payload_models = request.data.get('models', []) if isinstance(request.data, dict) else []
            if isinstance(payload_models, (list, tuple)):
                raw_values.extend(payload_models)
            elif payload_models:
                raw_values.append(payload_models)

        requested_models = []
        seen = set()
        for raw_value in raw_values:
            for token in str(raw_value).split(','):
                model = token.strip()
                if not model or model in seen:
                    continue
                requested_models.append(model)
                seen.add(model)

        return requested_models

    def _compare(self, request):
        requested_models = self._extract_requested_models(request)
        if not requested_models:
            return Response({'detail': 'models query parameter or models body is required.'}, status=status.HTTP_400_BAD_REQUEST)

        snapshot = CPUSelectionSnapshot.objects.order_by('-fetched_at', '-id').first()
        if not snapshot:
            return Response({'detail': 'CPU selection snapshot not found.'}, status=status.HTTP_404_NOT_FOUND)

        entries = [
            {
                'vendor': row.vendor,
                'model_name': row.model_name,
                'perf_score': int(row.perf_score or 0),
                'source_url': row.source_url,
            }
            for row in snapshot.entries.all().order_by('rank_global', '-perf_score', 'model_name')
        ]
        inventory_parts = _load_available_cpu_inventory_parts()
        available_entries, removed_count = _filter_available_cpu_selection_entries(entries, inventory_parts=inventory_parts)

        results = []
        missing_models = []
        for model in requested_models:
            matched = _match_cpu_model_entry(available_entries, model)
            if matched is None:
                missing_models.append(model)
                continue
            serialized_entry = _serialize_cpu_selection_entry(matched)
            cpu_part = _match_available_cpu_part(matched.get('model_name', ''), inventory_parts=inventory_parts)
            if cpu_part is not None:
                serialized_entry['price'] = int(cpu_part.price or 0)
                serialized_entry['value_score'] = (
                    serialized_entry['perf_score'] / int(cpu_part.price or 0)
                    if int(cpu_part.price or 0) > 0 and serialized_entry['perf_score'] > 0
                    else None
                )
            results.append(serialized_entry)

        results.sort(key=lambda row: row.get('perf_score', 0), reverse=True)

        return Response(
            {
                'requested_models': requested_models,
                'missing_models': missing_models,
                'excluded_count': int(snapshot.excluded_count or 0) + removed_count,
                'results': results,
            }
        )

    def get(self, request):
        return self._compare(request)

    def post(self, request):
        return self._compare(request)


PART_TYPE_LABELS = {
    'cpu':         'CPU',
    'cpu_cooler':  'CPUクーラー',
    'gpu':         'GPU',
    'motherboard': 'マザーボード',
    'memory':      'メモリ',
    'storage':     'ストレージ',
    'os':          'OS',
    'psu':         '電源ユニット',
    'case':        'PCケース',
}

STORAGE_INTERFACE_LABELS = {
    'nvme': 'NVMe',
    'sata': 'SATA',
    'other': 'その他',
}


def _format_storage_capacity_label(capacity_gb):
    if not capacity_gb:
        return '容量不明'
    if capacity_gb >= 1024:
        value_tb = capacity_gb / 1024
        if float(value_tb).is_integer():
            return f'{int(value_tb)}TB'
        return f'{value_tb:.1f}TB'
    return f'{capacity_gb}GB'


def _infer_storage_interface(part):
    interface = str(_get_spec(part, 'interface', '') or '').strip().upper()
    if interface == 'NVME':
        return 'nvme'
    if interface == 'SATA':
        return 'sata'

    try:
        detail = getattr(part, 'storage_detail', None)
        category = str(getattr(detail, 'storage_category', '') or '').strip().lower()
        if category in {'nvme', 'sata'}:
            return category
    except Exception:
        pass

    # Include comment/spec_text in search to detect interface mentions in product details.
    spec_text = str(_get_spec(part, 'spec_text', '') or '')
    comment = str(_get_spec(part, 'comment', '') or '')
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')} {spec_text} {comment}".lower()
    if 'serial ata' in text:
        return 'sata'
    if 'm.2 sata' in text or 'm2 sata' in text:
        return 'sata'
    if 'nvme' in text:
        return 'nvme'
    if 'sata' in text:
        return 'sata'
    # WD NVMe models: SN700, SN850, SN750, SN580, SN500
    if re.search(r'\bsn[5-9]\d{2}\b', text):
        return 'nvme'
    # WD SATA SSD models: SA500
    if re.search(r'\bsa\d{3}\b', text):
        return 'sata'
    # Samsung NVMe models: 970 EVO/PRO, 980 PRO, 990 PRO
    if re.search(r'\b(970|980|990)\s*(evo|pro)\b', text):
        return 'nvme'
    # M.2 in product name or spec_text → NVMe
    if 'm.2' in text:
        return 'nvme'
    return 'other'


def _serialize_storage_part(part):
    capacity_gb = _infer_storage_capacity_gb(part)
    interface_key = _infer_storage_interface(part)
    return {
        'id': part.id,
        'name': part.name,
        'price': part.price,
        'url': part.url,
        'capacity_gb': capacity_gb,
        'capacity_label': _format_storage_capacity_label(capacity_gb),
        'interface': interface_key,
        'interface_label': STORAGE_INTERFACE_LABELS.get(interface_key, 'その他'),
        'form_factor': _get_spec(part, 'form_factor'),
        'updated_at': part.updated_at,
    }


def _build_storage_inventory_summary(storage_category=''):
    queryset = PCPart.objects.filter(part_type='storage')
    if storage_category in {'nvme', 'sata', 'other'}:
        queryset = queryset.filter(storage_detail__storage_category=storage_category)
    storage_parts = list(queryset.order_by('price', 'name'))
    serialized_items = [_serialize_storage_part(part) for part in storage_parts]

    capacity_groups = defaultdict(list)
    interface_groups = defaultdict(list)
    latest_updated_at = None
    for item in serialized_items:
        capacity_groups[(item['capacity_gb'], item['capacity_label'])].append(item)
        interface_groups[item['interface']].append(item)
        updated_at = item['updated_at']
        if updated_at and (latest_updated_at is None or updated_at > latest_updated_at):
            latest_updated_at = updated_at

    capacity_summary = []
    for (capacity_gb, label), items in sorted(capacity_groups.items(), key=lambda entry: (entry[0][0], entry[0][1])):
        prices = [item['price'] for item in items]
        capacity_summary.append({
            'capacity_gb': capacity_gb,
            'label': label,
            'count': len(items),
            'min_price': min(prices) if prices else None,
            'max_price': max(prices) if prices else None,
            'avg_price': int(sum(prices) / len(prices)) if prices else None,
            'items': items,
        })

    interface_summary = []
    for interface_key in ('nvme', 'sata', 'other'):
        items = interface_groups.get(interface_key, [])
        prices = [item['price'] for item in items]
        interface_summary.append({
            'interface': interface_key,
            'label': STORAGE_INTERFACE_LABELS[interface_key],
            'count': len(items),
            'min_price': min(prices) if prices else None,
            'max_price': max(prices) if prices else None,
            'avg_price': int(sum(prices) / len(prices)) if prices else None,
        })

    return {
        'total_count': len(serialized_items),
        'latest_updated_at': latest_updated_at,
        'capacity_summary': capacity_summary,
        'interface_summary': interface_summary,
    }


class PartPriceRangesAPIView(APIView):
    """パーツ種別ごとの価格レンジ (min/max/avg/count) を DB 集計で返す"""

    def get(self, request):
        result = {}
        for pt, label in PART_TYPE_LABELS.items():
            agg = PCPart.objects.filter(part_type=pt).aggregate(
                min_price=Min('price'),
                max_price=Max('price'),
                avg_price=Avg('price'),
                total=DbCount('id'),
            )
            result[pt] = {
                'label': label,
                'min': agg['min_price'],
                'max': agg['max_price'],
                'avg': int(agg['avg_price']) if agg['avg_price'] else None,
                'count': agg['total'],
            }
        return Response(result)


class StorageInventoryAPIView(APIView):
    """ストレージDBの一覧と容量別・接続別サマリーを返す"""

    def get(self, request):
        storage_category = (request.query_params.get('storage_category') or '').strip().lower()
        return Response(_build_storage_inventory_summary(storage_category=storage_category))


def _pick_case_fan_for_fanless_case(budget_remaining, case_fan_policy, build_priority='balanced'):
    """付属ファンなしケース選択時にケースファン単品を選定する。"""
    all_fans = list(PCPart.objects.filter(part_type='case_fan').order_by('price'))
    if not all_fans:
        return None

    suitable = [p for p in all_fans if _is_part_suitable('case_fan', p)]
    if not suitable:
        suitable = all_fans

    # 予算内候補を優先するが、候補なしなら最安値から選ぶ
    affordable = [p for p in suitable if p.price <= max(budget_remaining, 0)]
    candidates = affordable or suitable

    if case_fan_policy in ('silent', 'airflow'):
        def _fan_score(part):
            text = f"{part.name} {part.url}".lower()
            score = 0
            for kw in CASE_FAN_POLICY_KEYWORDS.get(case_fan_policy, []):
                if kw in text:
                    score += 2
            return score
        return sorted(candidates, key=lambda p: (-_fan_score(p), p.price))[0]

    # auto / cost: 最安値
    return sorted(candidates, key=lambda p: p.price)[0]

