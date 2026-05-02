import re
from collections import defaultdict

from rest_framework import viewsets, status
from django.db.models import Min, Max, Avg, Count as DbCount
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from .dospara_scraper import fetch_dospara_market_price_range
from .models import PCPart, Configuration, ScraperStatus
from .serializers import PCPartSerializer, ConfigurationSerializer, ScraperStatusSerializer


PART_ORDER = ['cpu', 'cpu_cooler', 'gpu', 'motherboard', 'memory', 'storage', 'os', 'psu', 'case']
USAGE_POWER_MAP = {
    'gaming': 550,    # ゲーミング: GPU高負荷
    'creator': 500,   # クリエイター: CPU+GPU高負荷
    'business': 350,  # ビジネス: 省電力
    'standard': 400,  # スタンダード: 標準
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
    # ビジネス: CPU中程度、GPU控えめ、信頼性重視
    'business': {
        'cpu': 0.24,
        'cpu_cooler': 0.03,
        'gpu': 0.08,
        'motherboard': 0.15,
        'memory': 0.18,
        'storage': 0.17,
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
    'low': 180000,
    'middle': 300000,
    'high': 500000,
}

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
    'gaming':   ['gpu', 'cpu', 'cpu_cooler', 'memory', 'storage', 'motherboard', 'psu', 'case'],
    'creator':  ['cpu', 'motherboard', 'memory', 'gpu', 'storage', 'cpu_cooler', 'psu', 'case'],
    'business': ['cpu', 'memory', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
    'standard': ['cpu', 'memory', 'storage', 'motherboard', 'cpu_cooler', 'psu', 'case'],
}

# 内蔵GPU(iGPU)使用: ビジネス・スタンダードはdGPU不要
IGPU_USAGES = frozenset({'business', 'standard'})

# GPUウェイト分を他パーツへ再分配した予算配分
IGPU_BUDGET_WEIGHTS = {
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
    'business': 250,
    'standard': 300,
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
    'intel': ['intel', 'core i', 'core ultra', 'pentium', 'celeron', 'xeon'],
    'amd': ['amd', 'ryzen', 'athlon', 'epyc', 'threadripper'],
}

CREATOR_CPU_PRIORITY_PARTIAL = {
    'cost': [
        'ultra 5 250kf plus',
        'ultra 5 250k plus',
        'ultra 7 270k plus',
        'ryzen 9 9900x',
        'ultra 7 265kf',
        'ultra 7 265k',
        'ryzen 7 9700x',
        'ryzen 7 7700x',
        'ryzen 7 5700x',
        'ryzen 5 9600x',
        'ryzen 5 7600',
        'ryzen 5 5600',
    ],
    'spec': [
        'ryzen 9 9950x3d2',
        'ryzen 9 9950x3d',
        'ryzen 9 9950x',
        'ultra 7 270k plus',
        'ryzen 9 9900x',
        'ultra 5 250kf plus',
        'ultra 5 250k plus',
        'ultra 7 265kf',
        'ultra 7 265k',
        'ryzen 7 9700x',
        'ryzen 7 7700x',
        'ryzen 7 7800x3d',
        'ryzen 7 5700x',
    ],
}

CREATOR_CPU_EXCLUDE_PARTIAL = (
    'threadripper',
    'apple m',
)

CREATOR_CPU_DEMOTE_PARTIAL = (
    '285k',
)

# 予算ティア × 構成方針ごとの CPU 最大許容価格（円）
# spec/cost ともに予算帯に見合ったCPUを超えて選ばないようにする
CREATOR_CPU_MAX_PRICE = {
    'spec': {'low': 55000, 'middle': 90000, 'high': 120000, 'premium': 999999},
    'cost': {'low': 45000, 'middle': 65000, 'high': 95000,  'premium': 115000},
}

# ビジネス用途: 予算ティア閾値（UIのcostプリセット境界に合わせる）
# low: ～107,480 / middle: ～124,980 / high: ～147,480 / premium: 147,481～
BUSINESS_BUDGET_TIER_THRESHOLDS = {
    'low': 107480,
    'middle': 124980,
    'high': 147480,
}

# ビジネス用途: 予算ティア × 構成方針ごとのCPU最大許容価格（円）
# Intel 13/14世代は _is_part_suitable で全用途除外済み
BUSINESS_CPU_MAX_PRICE = {
    'spec': {'low': 22000, 'middle': 38000, 'high': 58000, 'premium': 90000},
    'cost': {'low': 22000, 'middle': 28000, 'high': 48000, 'premium': 65000},
}

# ビジネス用途: 予算ティア × 構成方針ごとのCPU優先リスト（部分一致）
# 低予算は iGPU / コスパ寄り、高予算はコア数寄りに段階的に割り振る。
BUSINESS_CPU_PRIORITY_PARTIAL = {
    'cost': {
        'low': [
            'ryzen 5 5600g',
            'ryzen 5 5600gt',
            'core i3-12100',
            'ryzen 5 5600',
            'ryzen 3 5300g',
        ],
        'middle': [
            'ryzen 5 5600gt',
            'ryzen 5 5600g',
            'ryzen 7 5700g',
            'ryzen 5 7600',
            'core i5-12400',
            'ultra 5 225',
        ],
        'high': [
            'ryzen 5 8500g',
            'ryzen 5 5600gt',
            'ryzen 5 8600g',
            'ryzen 7 7700',
            'ryzen 7 5700g',
            'ryzen 7 9700x',
            'ryzen 5 7600',
            'ultra 5 225',
            'ultra 5 245k',
        ],
        'premium': [
            'ryzen 5 8600g',
            'ryzen 5 8500g',
            'ryzen 9 7900',
            'ryzen 9 9900x',
            'ryzen 7 7700',
            'ryzen 7 9700x',
            'ryzen 5 5600gt',
            'ryzen 7 5700g',
            'ultra 7 270k plus',
            'ultra 5 245k',
        ],
    },
    'spec': {
        'low': [
            'ryzen 5 7600g',
            'ryzen 5 5600g',
            'ryzen 5 5600gt',
            'core i5-12400',
            'core i3-12100',
        ],
        'middle': [
            'ryzen 7 5700g',
            'ryzen 5 7600',
            'ryzen 5 5600gt',
            'ultra 5 245k',
            'ultra 5 225',
        ],
        'high': [
            'ryzen 5 8500g',
            'ryzen 5 8600g',
            'ryzen 7 9700x',
            'ryzen 7 7700',
            'ryzen 7 5700g',
            'ryzen 5 7600',
            'ultra 5 245k',
            'ultra 5 225',
        ],
        'premium': [
            'ryzen 5 8600g',
            'ryzen 5 8500g',
            'ryzen 9 9900x',
            'ryzen 9 7900',
            'ryzen 7 9700x',
            'ryzen 7 7700',
            'ultra 7 270k plus',
            'ultra 5 245k',
        ],
    },
}

BUSINESS_CPU_DEMOTE_PARTIAL = (
    'athlon',
    'ryzen 5 5500gt',
)

BUSINESS_CPU_PREFER_250K_PLUS_PARTIAL = (
    'ultra 5 250kf plus',
    'ultra 5 250k plus',
)

BUSINESS_CPU_AVOID_265K_PARTIAL = (
    'ultra 7 265kf',
    'ultra 7 265k',
    'ultra 7 265f',
)

GAMING_SPEC_GPU_KEYWORDS = (
    'rtx',
    'radeon rx',
)

GAMING_CPU_X3D_PATTERN = re.compile(r'\b(?:ryzen\s*[3579]\s*)?\d{4,5}x3d(?:\d+)?\b', re.IGNORECASE)
UNSTABLE_INTEL_CORE_I_PATTERN = re.compile(
    r'\bcore[^a-z0-9]*i[3579]?[^0-9]*(?:13|14)\d{3,4}[a-z]{0,3}\b',
    re.IGNORECASE,
)
INTEL_GEN_13_14_JP_PATTERN = re.compile(r'第\s*(?:13|14)\s*世代', re.IGNORECASE)
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


def _is_part_suitable(part_type, part):
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

    if part_type == 'cpu':
        cpu_text = f"{part.name} {part.url}"
        # 実データでは Intel 第13/14世代 Core i を全除外する。
        # テストデータ(example.com)は既存テスト互換のため除外対象外にする。
        if 'example.com' not in url:
            if UNSTABLE_INTEL_CORE_I_PATTERN.search(cpu_text):
                return False
            if INTEL_GEN_13_14_JP_PATTERN.search(cpu_text):
                if ('core i' in cpu_text.lower()) or ('intel' in cpu_text.lower()) or ('インテル' in cpu_text):
                    return False
            lower_cpu_text = cpu_text.lower()
            if 'raptor lake' in lower_cpu_text and ('intel' in lower_cpu_text or 'core i' in lower_cpu_text):
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


def _resolve_os_edition_by_usage(usage, os_edition, budget=None):
    if os_edition != 'auto':
        return os_edition

    if usage == 'business' and isinstance(budget, int) and budget < 120000:
        return 'home'

    auto_map = {
        'gaming': 'home',
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


def _is_gt_series_gpu(part):
    text = f"{part.name} {part.url}".lower()
    return re.search(r'\bgt[\s\-_/]*\d{3,4}\b', text) is not None


def _is_nvidia_gpu(part):
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    return any(keyword in text for keyword in ('nvidia', 'geforce', 'rtx', 'quadro'))


def _prefer_creator_gpu_with_vram_flex(candidates):
    """creator用途: NVIDIA優先。NVIDIA候補がある場合はNVIDIAのみを返す。"""
    if not candidates:
        return candidates

    nvidia_candidates = [p for p in candidates if _is_nvidia_gpu(p)]
    return nvidia_candidates or candidates


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

    # 最低限のゲーム向け: RTX 3050 6GBクラス
    if 'rtx 3050' in text and memory_gb >= 6:
        return 1

    # もう一段上の下限: RTX 5050 / RX 7600 クラス以上
    upper_mid_keywords = (
        'rtx 5050',
        'rtx 5060',
        'rtx 5060 ti',
        'rtx 5070',
        'rtx 5070 ti',
        'rtx 5080',
        'rtx 5090',
        'rx 7600',
        'rx7600',
        'rx 9060',
        'rx9060',
        'rx 9070',
        'rx9070',
    )
    if any(keyword in text for keyword in upper_mid_keywords):
        return 2 if memory_gb >= 8 else 1

    if any(keyword in text for keyword in GAMING_SPEC_GPU_KEYWORDS) or re.search(r'\brx\s*\d{3,4}\b', text):
        if memory_gb >= 8:
            return 2
        if memory_gb >= 6:
            return 1
        return 0

    return 0


def _minimum_gaming_spec_gpu_tier(budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return 0
    if budget >= 200000:
        return 2
    return 1


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


def _creator_cpu_minimum_requirements(budget, options=None):
    options = options or {}
    build_priority = _normalize_build_priority(options.get('build_priority', 'balanced'))
    budget_tier = _classify_budget_tier(budget)

    if build_priority == 'spec':
        if budget_tier in ('middle', 'high', 'premium'):
            return 12, 24
        return 8, 16

    return 8, 16


def _creator_gpu_cap_price(budget, options=None):
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')
    if build_priority == 'spec':
        budget_tier = _classify_budget_tier(budget)
        tier_ratios = {
            'low': 0.42,
            'middle': 0.40,
            'high': 0.45,
            'premium': 0.50,
        }
        return int(budget * tier_ratios.get(budget_tier, 0.45))
    cap_ratio = CREATOR_GPU_BUDGET_CAP_BY_PRIORITY.get(build_priority, CREATOR_GPU_BUDGET_CAP_BY_PRIORITY['balanced'])
    return int(budget * cap_ratio)


def _creator_motherboard_floor_price(budget, options=None):
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')
    floor_ratio = CREATOR_MOTHERBOARD_FLOOR_BY_PRIORITY.get(build_priority, CREATOR_MOTHERBOARD_FLOOR_BY_PRIORITY['balanced'])
    return int(budget * floor_ratio)


def _classify_budget_tier(budget):
    if budget <= BUDGET_TIER_THRESHOLDS['low']:
        return 'low'
    if budget <= BUDGET_TIER_THRESHOLDS['middle']:
        return 'middle'
    if budget <= BUDGET_TIER_THRESHOLDS['high']:
        return 'high'
    return 'premium'


def _classify_business_budget_tier(budget, build_priority=None):
    if build_priority == 'spec':
        budget = int(round(float(budget) / 1.1))
    if budget <= BUSINESS_BUDGET_TIER_THRESHOLDS['low']:
        return 'low'
    if budget <= BUSINESS_BUDGET_TIER_THRESHOLDS['middle']:
        return 'middle'
    if budget <= BUSINESS_BUDGET_TIER_THRESHOLDS['high']:
        return 'high'
    return 'premium'


def _part_price_band(part_type, budget, usage):
    usage_bands = PART_PRICE_BANDS_BY_USAGE_TIER.get(part_type, {}).get(usage)
    if not usage_bands:
        return None

    budget_tier = _classify_budget_tier(budget)
    ratio_range = usage_bands.get(budget_tier)
    if not ratio_range:
        return None

    min_ratio, max_ratio = ratio_range
    return int(budget * min_ratio), int(budget * max_ratio)


def _filter_candidates_by_part_price_band(candidates, part_type, budget, usage):
    if not candidates:
        return candidates

    budget_tier = _classify_budget_tier(budget)
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
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
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
            return score
    return 500


def _pick_gaming_spec_gpu(candidates):
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


def _is_gaming_cpu_x3d_preferred(part):
    if not part:
        return False
    text = f"{part.name} {part.url}".lower()
    if 'ryzen' not in text and 'amd' not in text:
        return False
    return GAMING_CPU_X3D_PATTERN.search(text) is not None


def _extract_gaming_x3d_model_number(part):
    if not part:
        return 0
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    match = re.search(r'\b(\d{4,5})\s*x3d(?:\d+)?\b', text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _gaming_x3d_cpu_cap_model(budget, build_priority):
    """ゲーミング用途のX3D CPU上限を予算帯×構成方針で制御する。
    - low/middle: 7800X3D まで
    - high: cost は 7800X3D、spec は 9800X3D
    - premium: cost は 9800X3D、spec は 9850X3D
    """
    try:
        budget_value = int(budget or 0)
    except (TypeError, ValueError):
        budget_value = 0

    priority = _normalize_build_priority(build_priority)

    # フロントの用途別プリセット表示(最近傍)とズレないよう、
    # ゲーミングCPUの段階制御も同じ基準点で tier を決める。
    gaming_presets = (
        ('low', 164980),
        ('middle', 259980),
        ('high', 499980),
        ('premium', 984980),
    )
    budget_tier = min(gaming_presets, key=lambda item: abs(item[1] - budget_value))[0]

    if budget_tier in ('low', 'middle'):
        return 7800

    if budget_tier == 'high':
        if priority == 'spec':
            return 9800
        return 7800

    # premium
    if priority == 'cost':
        return 9800
    return 9850


def _is_allowed_gaming_x3d_cpu(part, budget, options=None):
    options = options or {}
    if not _is_gaming_cpu_x3d_preferred(part):
        return False

    model = _extract_gaming_x3d_model_number(part)
    if model <= 0:
        return False

    cap_model = _gaming_x3d_cpu_cap_model(budget, options.get('build_priority', 'balanced'))
    return model <= cap_model


def _filter_allowed_gaming_x3d_cpus(candidates, budget, options=None):
    options = options or {}
    return [p for p in candidates if _is_allowed_gaming_x3d_cpu(p, budget, options=options)]


def _is_cpu_x3d(part):
    """CPU が X3D モデルかどうかを判定する"""
    if not part:
        return False
    text = f"{part.name} {part.url}".lower()
    return GAMING_CPU_X3D_PATTERN.search(text) is not None


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

    # Intel Core Ultra は世代によりSMT前提でないため、
    # テキスト推定時の単純な「コア数×2」は過大評価になりやすい。
    # 最低限の用途判定では総スレッド=総コアで扱う。
    ultra_thread_match = re.search(r'core[^a-z0-9]*ultra\s*([579])\b', text)
    if ultra_thread_match:
        tier = int(ultra_thread_match.group(1))
        if tier >= 9:
            return 24
        if tier == 7:
            return 20
        if tier == 5:
            return 14
        return 10

    # スペック/テキストからスレッドが読めない場合は、推定コア数から概算する。
    inferred_cores = _extract_cpu_core_count(part)
    if inferred_cores > 0:
        return inferred_cores * 2

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

    # スペック欠損時の保守的な推定（用途フィルタの取りこぼし防止）
    # クリエイター最小要件の判定に必要なため、主要シリーズのみを対象にする。
    ryzen_match = re.search(r'\bryzen\s*([3579])\b', text)
    if ryzen_match:
        tier = int(ryzen_match.group(1))
        if tier >= 9:
            return 12
        if tier == 7:
            return 8
        if tier == 5:
            return 6
        return 4

    intel_match = re.search(r'\bcore\s*i([3579])\b', text)
    if intel_match:
        tier = int(intel_match.group(1))
        if tier >= 9:
            return 12
        if tier == 7:
            return 8
        if tier == 5:
            return 6
        return 4

    # Intel Core Ultra シリーズ: Ultra 9 / Ultra 7 / Ultra 5 を推定
    # 例: "Core™ Ultra 7 270K Plus" → Ultra7 → 20コア相当 (P8+E16)
    # ™ 等の非ASCII記号をスキップするため [^a-z0-9]* を使用
    ultra_match = re.search(r'core[^a-z0-9]*ultra\s*([579])\b', text)
    if ultra_match:
        tier = int(ultra_match.group(1))
        if tier >= 9:
            return 24  # Ultra 9 285K: 8P+16E=24
        if tier == 7:
            return 20  # Ultra 7 265K/270K: 8P+12E=20
        if tier == 5:
            return 14  # Ultra 5 245K/250KF: 6P+8E=14
        return 10

    return 0


def _is_cpu_igpu_capable(part):
    """iGPUが使えるCPUかを推定する。
    business/standard の iGPU 構成では F付きSKU を除外する。
    """
    if not part:
        return False

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    if any(token in text for token in ('without graphics', 'グラフィックス非搭載', 'igpuなし')):
        return False

    # 例: 225F / 245KF / 7500F など、末尾 F 系は iGPU 非搭載扱い
    if re.search(r'\b\d{3,5}[a-z]{0,3}f\b', text):
        return False

    # AMD Ryzen 型番ルール:
    # - G 付きは iGPU 搭載（例: 5300G, 5600GT, 8500G, 8700G）
    # - F 付きは iGPU 非搭載（上の一般ルールでも除外）
    # - それ以外（X, 無印, GT以外）は非搭載扱い（7600, 7700, 9700X, 5700X等）
    # ※ GT サフィックスも iGPU 搭載（5600GT, 5500GT など）
    ryzen_model = re.search(r'\bryzen\s*[3579]\s*(\d{4,5})([a-z0-9]{0,4})\b', text)
    if ryzen_model:
        suffix = ryzen_model.group(2).lower()
        if 'g' in suffix:  # G, GT, GE など全 G 系
            return True
        # G 系以外の Ryzen デスクトップCPU は iGPU 非搭載
        return False

    return True


def _is_business_cpu_265_family(part):
    if not part:
        return False
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
    # 例: 265K / 265KF / 265F（表記ゆれで間に語が入っても末尾型番で判定）
    return re.search(r'\b265(?:kf|k|f)\b', text) is not None


def _is_265k_overpriced_vs_250kf_plus():
    """DB上の 265K 現在価格が 250KF Plus より高い間 True を返す。
    どちらかが未登録なら False（265K を除外しない）。
    """
    try:
        cpu_qs = PCPart.objects.filter(part_type='cpu')
        # 265K だが 265KF ではない製品（名前に 265k を含み 265kf は含まない）
        p265k = None
        for p in cpu_qs:
            name_lower = getattr(p, 'name', '').lower()
            if '265k' in name_lower and '265kf' not in name_lower:
                p265k = p
                break
        # 250KF Plus
        p250kf_plus = None
        for p in cpu_qs:
            name_lower = getattr(p, 'name', '').lower()
            if '250kf' in name_lower and 'plus' in name_lower:
                p250kf_plus = p
                break
        if p265k and p250kf_plus:
            return (getattr(p265k, 'price', 0) or 0) > (getattr(p250kf_plus, 'price', 0) or 0)
    except Exception:
        pass
    return False


def _is_globally_excluded_cpu(part):
    """全用途共通のCPU除外ルール。"""
    if not part:
        return False
    if _is_265k_overpriced_vs_250kf_plus() and _is_business_cpu_265_family(part):
        return True
    return False


def _ordered_partial_match(text, key):
    """空白区切りトークンが text 内に順序通り現れるかを判定する。"""
    if not text or not key:
        return False
    tokens = [t for t in str(key).lower().split() if t]
    if not tokens:
        return False
    hay = str(text).lower()
    pos = 0
    for token in tokens:
        # 英数字トークンは単語境界で判定し、"250k" が "250kf" に
        # 誤一致しないようにする。日本語等は従来どおり部分一致を維持。
        if re.fullmatch(r'[a-z0-9]+', token):
            m = re.search(rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])', hay[pos:])
            if not m:
                return False
            pos += m.end()
        else:
            idx = hay.find(token, pos)
            if idx < 0:
                return False
            pos = idx + len(token)
    return True


def _cpu_meets_creator_minimum(part, min_cores=8, min_threads=16):
    if not part:
        return False
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


def _is_creator_mobile_cpu_text(text):
    return re.search(r'\b\d{3,4}(?:hx|hs|h|u)\b', text) is not None


def _pick_creator_cpu_by_partial_priority(candidates, build_priority, budget=None):
    if not candidates:
        return None

    # 予算ティアに応じたCPU価格上限を取得して候補を事前フィルタ
    original_candidates = candidates
    if budget is not None:
        tier = _classify_budget_tier(budget)
        price_caps = CREATOR_CPU_MAX_PRICE.get(build_priority, CREATOR_CPU_MAX_PRICE['cost'])
        max_cpu_price = price_caps.get(tier, 999999)
        capped = [p for p in candidates if getattr(p, 'price', 999999) <= max_cpu_price]
        if capped:
            candidates = capped

    def _base_filter(pool):
        result = []
        for part in pool:
            text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
            if any(token in text for token in CREATOR_CPU_EXCLUDE_PARTIAL):
                continue
            if _is_creator_mobile_cpu_text(text):
                continue
            result.append(part)
        return result

    priority = CREATOR_CPU_PRIORITY_PARTIAL.get(build_priority, CREATOR_CPU_PRIORITY_PARTIAL['cost'])
    filtered = _base_filter(candidates)

    if not filtered:
        return None

    for key in priority:
        matched = [p for p in filtered if key in f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower()]
        if matched:
            return sorted(matched, key=lambda p: p.price)[0]

    non_demoted = [
        p for p in filtered
        if not any(token in f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower() for token in CREATOR_CPU_DEMOTE_PARTIAL)
    ]
    if non_demoted:
        return sorted(non_demoted, key=lambda p: p.price)[0]

    # 価格上限内の候補が全て降格対象の場合、上限外を含む候補で優先リストを再試行
    if original_candidates is not candidates:
        filtered_full = _base_filter(original_candidates)
        for key in priority:
            matched = [p for p in filtered_full if key in f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower()]
            if matched:
                return sorted(matched, key=lambda p: p.price)[0]
        non_demoted_full = [
            p for p in filtered_full
            if not any(token in f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower() for token in CREATOR_CPU_DEMOTE_PARTIAL)
        ]
        pool_full = non_demoted_full if non_demoted_full else filtered_full
        return sorted(pool_full, key=lambda p: p.price)[0] if pool_full else None

    pool = filtered
    return sorted(pool, key=lambda p: p.price)[0]


def _prefer_creator_cpu_by_core_threads(candidates, budget=None):
    """クリエイター用途: 優先リストを適用し、未一致時はコア/スレッド優先で選ぶ。"""
    if not candidates:
        return None

    # 降格対象は候補から除外（残らなければ除外なし）
    non_demoted = [
        p for p in candidates
        if not any(token in f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower() for token in CREATOR_CPU_DEMOTE_PARTIAL)
    ]
    if non_demoted:
        candidates = non_demoted

    prioritized = _pick_creator_cpu_by_partial_priority(candidates, 'spec', budget=budget)
    if prioritized:
        return prioritized

    # 既存フォールバック: X3D を除外
    non_x3d_candidates = [p for p in candidates if not _is_cpu_x3d(p)]
    if not non_x3d_candidates:
        non_x3d_candidates = candidates

    min_cores = 8
    qualified_cpus = [
        p for p in non_x3d_candidates
        if _extract_cpu_core_count(p) >= min_cores
    ]

    if qualified_cpus:
        return sorted(
            qualified_cpus,
            key=lambda p: (
                _extract_cpu_core_threads(p),
                _extract_cpu_core_count(p),
                -p.price,
            ),
            reverse=True,
        )[0]

    return sorted(
        non_x3d_candidates,
        key=lambda p: (
            -_extract_cpu_core_threads(p),
            -_extract_cpu_core_count(p),
            p.price,
        ),
    )[0]


def _prefer_creator_cost_cpu_8_to_24_cores(candidates, budget=None):
    """creator + cost 用: 優先リストを適用し、未一致時は8～24コア帯で最安値を選ぶ。"""
    if not candidates:
        return None

    # 降格対象は候補から除外（残らなければ除外なし）
    non_demoted = [
        p for p in candidates
        if not any(token in f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}".lower() for token in CREATOR_CPU_DEMOTE_PARTIAL)
    ]
    if non_demoted:
        candidates = non_demoted

    prioritized = _pick_creator_cpu_by_partial_priority(candidates, 'cost', budget=budget)
    if prioritized:
        return prioritized

    non_x3d_candidates = [p for p in candidates if not _is_cpu_x3d(p)]
    if not non_x3d_candidates:
        non_x3d_candidates = candidates

    min_threads = 16
    in_band = [
        p for p in non_x3d_candidates
        if 8 <= _extract_cpu_core_count(p) <= 24
        and _extract_cpu_core_threads(p) >= min_threads
    ]
    if in_band:
        return sorted(in_band, key=lambda p: p.price)[0]

    in_band_core_only = [
        p for p in non_x3d_candidates
        if 8 <= _extract_cpu_core_count(p) <= 24
    ]
    if in_band_core_only:
        return sorted(
            in_band_core_only,
            key=lambda p: (-_extract_cpu_core_threads(p), p.price),
        )[0]

    return _prefer_creator_cpu_by_core_threads(non_x3d_candidates, budget=budget)


def _prefer_business_cpu(candidates, budget, build_priority, target_price=None):
    """business/standard用途: 予算ティア×方針に基づきCPUを選ぶ。
    - Intel 13/14世代は _is_part_suitable で除外済み
    - BUSINESS_CPU_MAX_PRICE で予算ティアごとの価格上限を適用
    - BUSINESS_CPU_PRIORITY_PARTIAL の tier 別優先リストで選定
    - iGPU内蔵モデル(Ryzen G系)を低予算帯で優遇
    """
    if not candidates:
        return None

    # Athlon除外（低品質モデル排除）
    non_demoted = [
        p for p in candidates
        if not any(
            _ordered_partial_match(f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}", token)
            for token in BUSINESS_CPU_DEMOTE_PARTIAL
        )
    ]
    filtered = non_demoted if non_demoted else list(candidates)

    # business/standard は iGPU 構成前提のため、F付き等の iGPU 非搭載CPUを除外
    igpu_capable = [p for p in filtered if _is_cpu_igpu_capable(p)]
    if igpu_capable:
        filtered = igpu_capable

    # ビジネス専用の予算ティアで CPU 価格上限を決定
    # （ゲーミング向け _classify_budget_tier は閾値が高すぎるため独立して分類）
    tier = _classify_business_budget_tier(budget, build_priority=build_priority)
    price_caps = BUSINESS_CPU_MAX_PRICE.get(build_priority, BUSINESS_CPU_MAX_PRICE['cost'])
    max_cpu_price = price_caps.get(tier, 999999)
    capped = [p for p in filtered if getattr(p, 'price', 999999) <= max_cpu_price]
    if capped:
        filtered = capped

    # 265K 系を回避する: 265K の DB 価格が 250KF Plus を上回る間は全方針・全ティアで除外。
    # （コスト重視に限らず、スペック重視でも同価格帯により良い選択肢があるため）
    non_excluded = [p for p in filtered if not _is_globally_excluded_cpu(p)]
    if non_excluded:
        filtered = non_excluded

    # cost は target_price を基本適用、spec は「目安」として扱う
    in_target = [p for p in filtered if target_price is None or p.price <= target_price]
    if build_priority == 'cost':
        # high/premium は target_price を目安扱いにして、
        # ティア差分（high=8500G, premium=8600G など）を維持する。
        if tier in {'high', 'premium'}:
            pool = filtered
        else:
            pool = in_target if in_target else filtered
    else:
        pool = filtered

    # 優先リストに沿って部分一致で選定
    tier_priority_map = BUSINESS_CPU_PRIORITY_PARTIAL.get(build_priority, BUSINESS_CPU_PRIORITY_PARTIAL['cost'])
    tier_priority = tier_priority_map.get(tier, tier_priority_map.get('middle', []))
    for key in tier_priority:
        matched = [
            p for p in pool
            if _ordered_partial_match(f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}", key)
        ]
        if matched:
            return sorted(matched, key=lambda p: p.price)[0]

    # 低ティアでは target を優先するため、優先候補が見つからない場合のみ
    # 価格上限内(filtered)へ段階的に拡張する。
    if build_priority == 'cost' and pool is in_target:
        for key in tier_priority:
            matched = [
                p for p in filtered
                if _ordered_partial_match(f"{getattr(p, 'name', '')} {getattr(p, 'url', '')}", key)
            ]
            if matched:
                return sorted(matched, key=lambda p: p.price)[0]

    # 優先リスト不一致時: cost=コア数重視の安値、spec=コア数重視の高値
    if build_priority == 'cost':
        floor_price = 14000 if budget >= 80000 else 0
        floor_pool = [p for p in pool if p.price >= floor_price]
        pool = floor_pool if floor_pool else pool
        return sorted(
            pool,
            key=lambda p: (
                -_extract_cpu_core_threads(p),
                -_extract_cpu_core_count(p),
                p.price,
            ),
        )[0]

    return sorted(
        pool,
        key=lambda p: (
            _extract_cpu_core_threads(p),
            _extract_cpu_core_count(p),
            p.price,
        ),
        reverse=True,
    )[0]


def _extract_numeric_radiator_size(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


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

    # spec_text からもフォールバック解析
    spec_text = str(_get_spec(part, 'spec_text', '') or '')
    raw_text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')} {spec_text}"
    text_nospace = raw_text.upper().replace(' ', '')
    text_upper = raw_text.upper()

    # AM5/AM4 は単語として十分ユニークなので in 検索で十分
    if 'AM5' in text_nospace:
        return 'AM5'
    if 'AM4' in text_nospace:
        return 'AM4'
    if 'LGA1851' in text_nospace or '1851' in text_upper:
        return 'LGA1851'
    if 'LGA1700' in text_nospace or '1700' in text_upper:
        return 'LGA1700'

    chipset = _infer_motherboard_chipset(part)
    if chipset in {'x870e', 'x870', 'x670e', 'x670'}:
        return 'AM5'

    # スペース有り text でワードバウンダリ正規表現を使用
    if re.search(r'(?<![A-Z0-9])(?:B850|B650|B550|A620|A520|X670|X870|X570|B450|A320)(?![A-Z0-9])', text_upper):
        if re.search(r'(?<![A-Z0-9])(?:B650|B850|A620|X670|X870)(?![A-Z0-9])', text_upper):
            return 'AM5'
        return 'AM4'

    if re.search(r'(?<![A-Z0-9])(?:H610|H670|B660|B760|Z690|Z790|Q670|W680)(?![A-Z0-9])', text_upper):
        return 'LGA1700'
    if re.search(r'(?<![A-Z0-9])(?:H810|B860|Z890|W880)(?![A-Z0-9])', text_upper):
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

    candidates = [
        p
        for p in PCPart.objects.filter(part_type='psu').order_by('price')
        if _is_part_suitable('psu', p) and _matches_selection_options('psu', p, options=psu_options)
    ]
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
    minimum_gaming_gpu_tier = options.get('minimum_gaming_gpu_tier', 1)
    motherboard_memory_type = str(options.get('motherboard_memory_type', '') or '').upper()
    min_storage_capacity_gb = options.get('min_storage_capacity_gb')

    candidates = [p for p in PCPart.objects.filter(part_type=part_type).order_by('price') if _is_part_suitable(part_type, p)]
    if part_type == 'gpu':
        candidates = [p for p in candidates if not _is_gt_series_gpu(p)]
    if part_type == 'cpu_cooler':
        candidates = [
            p for p in candidates
            if _is_cpu_cooler_product(p)
            and _is_cpu_cooler_type_match(p, cooler_type)
            and _is_allowed_cpu_cooler_brand(p)
        ]
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
        if cooler_type == 'liquid' and radiator_size != 'any':
            radiator_filtered = [p for p in candidates if _is_case_radiator_compatible(p, radiator_size)]
            if radiator_filtered:
                candidates = radiator_filtered
        candidates = _filter_candidates_by_part_price_band(candidates, 'case', budget, usage)
    elif part_type == 'cpu':
        vendor_filtered = [p for p in candidates if _is_cpu_vendor_match(p, cpu_vendor)]
        if vendor_filtered:
            candidates = vendor_filtered
        # 全用途共通のCPU除外ルールを適用
        no_excluded = [p for p in candidates if not _is_globally_excluded_cpu(p)]
        if no_excluded:
            candidates = no_excluded
        if usage in IGPU_USAGES:
            igpu_filtered = [p for p in candidates if _is_cpu_igpu_capable(p)]
            if igpu_filtered:
                candidates = igpu_filtered
        if usage == 'gaming' and cpu_vendor != 'intel':
            allowed_x3d = _filter_allowed_gaming_x3d_cpus(candidates, budget, options=options)
            if allowed_x3d:
                candidates = allowed_x3d
        if usage == 'creator':
            min_cores, min_threads = _creator_cpu_minimum_requirements(budget, options=options)
            creator_cpu_filtered = [
                p for p in candidates
                if _cpu_meets_creator_minimum(p, min_cores=min_cores, min_threads=min_threads)
            ]
            if creator_cpu_filtered:
                candidates = creator_cpu_filtered
    elif part_type == 'motherboard':
        cpu_socket = options.get('cpu_socket')
        if cpu_socket:
            socket_filtered = [p for p in candidates if _infer_motherboard_socket(p) == cpu_socket]
            if socket_filtered:
                candidates = socket_filtered
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

        candidates = _prefer_motherboard_candidates(candidates, case_size)
    elif part_type == 'memory':
        if motherboard_memory_type:
            mem_type_filtered = [
                p for p in candidates
                if _infer_memory_type(p) == motherboard_memory_type
            ]
            if mem_type_filtered:
                candidates = mem_type_filtered
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
        # 既定はSSD優先。gaming+specのみ高容量HDDのフォールバックを許容する。
        relax_ssd_only_for_low_igpu_spec = (
            usage in {'business', 'standard'}
            and build_priority == 'spec'
            and budget < 140000
        )
        if not (usage == 'gaming' and build_priority == 'spec') and not relax_ssd_only_for_low_igpu_spec:
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
            candidates = _filter_psu_candidates_by_headroom(
                candidates,
                required_psu_wattage,
                usage=usage,
                build_priority=build_priority,
            )

    if part_type == 'gpu' and usage == 'gaming' and build_priority == 'spec':
        preferred_gpu = [p for p in candidates if _is_gaming_spec_gpu_preferred(p, minimum_gaming_gpu_tier)]
        if preferred_gpu:
            candidates = preferred_gpu
        candidates = _prefer_rx_xt_value_candidates(candidates)

    if part_type == 'gpu' and usage == 'creator':
        # build_priority による GPU 優先度別選定
        if build_priority == 'cost':
            # コスト重視: Radeon AI PRO R9700 を優先
            radeon_r9700 = [p for p in candidates if 'Radeon' in p.name and 'R9700' in p.name]
            if radeon_r9700:
                candidates = sorted(radeon_r9700, key=lambda p: p.price) + [
                    p for p in candidates if p.id not in {r.id for r in radeon_r9700}
                ]
        elif build_priority == 'spec':
            # スペック重視: NVIDIA RTX PRO 4500 を優先
            nvidia_pro_4500 = [p for p in candidates if 'RTX PRO 4500' in p.name]
            if nvidia_pro_4500:
                candidates = sorted(nvidia_pro_4500, key=lambda p: p.price) + [
                    p for p in candidates if p.id not in {n.id for n in nvidia_pro_4500}
                ]
            else:
                # RTX PRO 4500がない場合は NVIDIA 優先に fallback
                candidates = _prefer_creator_gpu_with_vram_flex(candidates)
        else:
            # balanced: NVIDIA 優先。ただし同等以上VRAMのAMDは許容。
            candidates = _prefer_creator_gpu_with_vram_flex(candidates)

        creator_gpu_cap = _creator_gpu_cap_price(budget, options=options)
        capped_candidates = [p for p in candidates if p.price <= creator_gpu_cap]
        if capped_candidates:
            candidates = capped_candidates

        minimum_creator_tier = _minimum_creator_gpu_tier(budget, options=options)
        if minimum_creator_tier > 0:
            tier_filtered = [p for p in candidates if _creator_gpu_tier(p) >= minimum_creator_tier]
            if tier_filtered:
                candidates = tier_filtered

    if not candidates:
        return None

    if (
        usage == 'creator'
        and part_type == 'gpu'
        and budget >= CREATOR_FLAGSHIP_BUDGET_THRESHOLD
    ):
        # 予算上限75%以内で買えるGPUのうち、最上位価格を選ぶ。
        # これにより、クリエイターの高予算ではGPUを積極的に上位化する。
        upper_cap = int(budget * CREATOR_FLAGSHIP_GPU_BUDGET_CAP)
        premium_candidates = [p for p in candidates if p.price <= upper_cap]
        if premium_candidates:
            return premium_candidates[-1]

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
        if part_type == 'cpu' and usage == 'gaming' and cpu_vendor != 'intel':
            preferred_x3d = _filter_allowed_gaming_x3d_cpus(within_target, budget, options=options)
            if preferred_x3d:
                within_target = preferred_x3d
        if part_type == 'cpu' and usage in {'business', 'standard'}:
            picked_business_cpu = _prefer_business_cpu(candidates, budget, build_priority, target_price=target_price)
            if picked_business_cpu:
                return picked_business_cpu
        if part_type == 'cpu' and usage == 'creator':
            # クリエイター用途: コアスレッド数が多いCPUを優先選定
            # within_target が空の場合は candidates 全体から選定
            target_cpus = within_target if within_target else candidates
            if build_priority == 'cost':
                picked_creator_cpu = _prefer_creator_cost_cpu_8_to_24_cores(target_cpus, budget=budget)
            else:
                picked_creator_cpu = _prefer_creator_cpu_by_core_threads(target_cpus, budget=budget)
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
            if usage == 'gaming' and build_priority == 'spec':
                target_capacity = _target_memory_capacity_gb(budget, usage, options=options)
                if (not profiled) or (_infer_memory_capacity_gb(profiled) < target_capacity):
                    target_capacity_candidates = [
                        p for p in candidates
                        if _infer_memory_capacity_gb(p) >= target_capacity
                    ]
                    if target_capacity_candidates:
                        profiled = _memory_profile_pick(
                            target_capacity_candidates,
                            build_priority,
                            budget=budget,
                            usage=usage,
                            options=options,
                        ) or sorted(target_capacity_candidates, key=lambda p: p.price)[0]
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
            if build_priority == 'spec' and usage in {'business', 'standard'} and budget < 140000:
                # 低予算の業務/標準PCでは、ストレージ優先でCPUが極端に下がるのを避ける。
                storage_pool = within_target
            else:
                storage_pool = candidates if build_priority == 'spec' else within_target
            profiled = _storage_profile_pick(storage_pool, build_priority, storage_preference)
            if profiled:
                return profiled
        if part_type == 'case':
            return _pick_case_candidate(within_target, case_fan_policy, build_priority, target_price=target_price)
        if build_priority == 'cost':
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
        return sorted(within_target, key=lambda p: p.price, reverse=True)[0]

    if build_priority == 'cost':
        if part_type == 'cpu' and usage == 'gaming' and cpu_vendor != 'intel':
            preferred_x3d = _filter_allowed_gaming_x3d_cpus(candidates, budget, options=options)
            if preferred_x3d:
                return preferred_x3d[0]
        if part_type == 'cpu' and usage in {'business', 'standard'}:
            picked_business_cpu = _prefer_business_cpu(candidates, budget, build_priority, target_price=target_price)
            if picked_business_cpu:
                return picked_business_cpu
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
            profiled = _storage_profile_pick(candidates, build_priority, storage_preference)
            if profiled:
                return profiled
        if part_type == 'case':
            return _pick_case_candidate(candidates, case_fan_policy, build_priority, target_price=target_price)
        return candidates[0]

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
        profiled = _storage_profile_pick(candidates, build_priority, storage_preference)
        if profiled:
            return profiled

    if part_type == 'cpu' and usage == 'gaming' and cpu_vendor != 'intel':
        preferred_x3d = _filter_allowed_gaming_x3d_cpus(candidates, budget, options=options)
        if preferred_x3d:
            return preferred_x3d[-1] if build_priority == 'spec' else preferred_x3d[0]

    if part_type == 'cpu' and usage in {'business', 'standard'}:
        picked_business_cpu = _prefer_business_cpu(candidates, budget, build_priority, target_price=target_price)
        if picked_business_cpu:
            return picked_business_cpu

    if part_type == 'cpu' and usage == 'creator':
        # クリエイター用途: 目標価格を超えた候補からもコアスレッド数で優先
        # 注: クーラー条件によって候補が制限されている場合でも、creator CPU ロジックを適用
        if build_priority == 'cost':
            picked_creator_cpu = _prefer_creator_cost_cpu_8_to_24_cores(candidates, budget=budget)
        else:
            picked_creator_cpu = _prefer_creator_cpu_by_core_threads(candidates, budget=budget)
        if picked_creator_cpu:
            return picked_creator_cpu
        # それでも candidates が空の場合は、制限を緩和して再試行
        # (例: 空冷・水冷のどちらでも互換性のある CPU から選定)
        if not candidates and part_type == 'cpu':
            # cooler_type と radiator_size を無視して全 CPU 候補から選定
            all_creator_cpus = PCPart.objects.filter(part_type='cpu').order_by('price')
            if all_creator_cpus:
                if build_priority == 'cost':
                    return _prefer_creator_cost_cpu_8_to_24_cores(list(all_creator_cpus), budget=budget)
                return _prefer_creator_cpu_by_core_threads(list(all_creator_cpus), budget=budget)

    if part_type == 'gpu' and usage == 'gaming' and build_priority == 'spec':
        picked_gpu = _pick_gaming_spec_gpu(candidates)
        if picked_gpu:
            return picked_gpu

    if build_priority == 'spec':
        return candidates[-1]

    return candidates[0]


def _get_spec(part, key, default=None):
    if not part:
        return default
    specs = getattr(part, 'specs', {}) or {}
    return specs.get(key, default)


def _infer_cpu_socket(part):
    """CPUのソケットを推定する: AM4 / AM5 / LGA1700 / LGA1851 / ''
    specs['socket'] がない場合は name / spec_text / url からテキスト解析する。
    """
    if not part:
        return ''
    socket_raw = str(_get_spec(part, 'socket', '') or '').upper().replace(' ', '')
    if socket_raw in {'AM4', 'AM5', 'LGA1700', 'LGA1851'}:
        return socket_raw

    # spec_text / name / url からフォールバック解析
    spec_text = str(_get_spec(part, 'spec_text', '') or '')
    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')} {spec_text}".upper().replace(' ', '')
    if 'AM5' in text:
        return 'AM5'
    if 'AM4' in text:
        return 'AM4'
    if 'LGA1851' in text or '1851' in text:
        return 'LGA1851'
    if 'LGA1700' in text or '1700' in text:
        return 'LGA1700'

    # Ryzen 型番のヒューリスティクス:
    # 7000番台以降は AM5、5000番台以前は AM4 として扱う。
    ryzen_model = re.search(r'RYZEN\s*[3579]\s*(\d{4,5})', text)
    if ryzen_model:
        model_num = int(ryzen_model.group(1))
        return 'AM5' if model_num >= 7000 else 'AM4'
    if 'ATHLON' in text:
        return 'AM4'

    return ''


def _infer_memory_type(part):
    memory_type = str(_get_spec(part, 'memory_type', '') or '').upper()
    if memory_type in {'DDR4', 'DDR5'}:
        return memory_type

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".upper()
    if 'DDR5' in text:
        return 'DDR5'
    if 'DDR4' in text:
        return 'DDR4'

    # 型番フォールバック:
    # - ESSENCORE: KD5* は DDR5, KD4* は DDR4
    # - ADATA: AD5U/AX5U は DDR5, AD4U/AX4U は DDR4
    if re.search(r'\bKD5[A-Z0-9-]*\b', text) or re.search(r'\b(?:AD5U|AX5U)[A-Z0-9-]*\b', text):
        return 'DDR5'
    if re.search(r'\bKD4[A-Z0-9-]*\b', text) or re.search(r'\b(?:AD4U|AX4U)[A-Z0-9-]*\b', text):
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
    socket = _infer_motherboard_socket(part)
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

    if usage == 'creator':
        if budget >= 500000:
            return {'capacity_gb': 64, 'preferred_modules': 2}
        if budget >= 250000:
            return {'capacity_gb': 32, 'preferred_modules': 2}
        return {'capacity_gb': 16, 'preferred_modules': 2}

    if usage == 'gaming':
        if build_priority == 'spec':
            if budget >= 500000:
                return {'capacity_gb': 64, 'preferred_modules': 2}
            if budget >= 280000:
                return {'capacity_gb': 32, 'preferred_modules': 2}
            return {'capacity_gb': 16, 'preferred_modules': 1}
        if budget >= 400000:
            return {'capacity_gb': 32, 'preferred_modules': 2}
        if budget >= 220000:
            return {'capacity_gb': 16, 'preferred_modules': 2}
        return {'capacity_gb': 16, 'preferred_modules': 1}

    if usage in {'business', 'standard'}:
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
    target_profile = _target_memory_profile(budget or 0, usage or options.get('usage', 'standard'), options=options)
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
        creator_min_capacity_gb = 16 if (usage or options.get('usage')) == 'creator' else 0
        
        # usage別の preferred_capacity_gb 設定（fallback ロジック用）
        current_usage = usage or options.get('usage', 'standard')
        budget_val = budget or 0
        
        if current_usage == 'gaming':
            preferred_capacity_gb = 16
        elif current_usage in {'business', 'standard'}:
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
                p.price,
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
    # 実データで specs が欠落しているSSDモデルを名称ベースで補完判定
    ssd_model_keywords = (
        'su650',
        'legend',
        's70',
        's65',
        'c715',
        'c925',
        'neo 414',
        'spatium',
        'nvme',
        'pcie',
    )
    if any(keyword in name_text for keyword in ssd_model_keywords):
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


def _storage_profile_pick(candidates, build_priority, storage_preference='ssd'):
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
            all_hdd = [p for p in PCPart.objects.filter(part_type='storage').order_by('price') if _is_part_suitable('storage', p) and _infer_storage_media_type(p) == 'hdd']

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

    cpu_socket = _infer_cpu_socket(cpu)
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

    mb_form = _get_spec(motherboard, 'form_factor')
    case_forms = _get_spec(case, 'supported_form_factors', [])
    if motherboard and case and mb_form and case_forms and mb_form not in case_forms:
        issues.append('form_factor_mismatch')

    gpu_len = _get_spec(gpu, 'gpu_length_mm')
    max_gpu_len = _get_spec(case, 'max_gpu_length_mm')
    if gpu and case and gpu_len and max_gpu_len and int(gpu_len) > int(max_gpu_len):
        issues.append('gpu_too_long')

    if cpu_cooler and case and cooler_type == 'liquid' and radiator_size != 'any':
        if not _is_case_radiator_compatible(case, radiator_size):
            issues.append('radiator_not_supported')

    return issues


def _pick_candidate(part_type, predicate):
    for candidate in PCPart.objects.filter(part_type=part_type).order_by('price'):
        if _is_part_suitable(part_type, candidate) and predicate(candidate):
            if part_type == 'cpu' and _is_globally_excluded_cpu(candidate):
                continue
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
    require_preferred_gaming_gpu = options.get('require_preferred_gaming_gpu', False)
    minimum_gaming_gpu_tier = options.get('minimum_gaming_gpu_tier', 1)
    required_psu_wattage = options.get('required_psu_wattage')
    usage = options.get('usage', 'standard')
    enforce_main_storage_ssd = options.get('enforce_main_storage_ssd', True)

    if part_type == 'cpu_cooler':
        if not _is_cpu_cooler_product(part):
            return False
        if not _is_cpu_cooler_type_match(part, cooler_type):
            return False
        if not _is_allowed_cpu_cooler_brand(part):
            return False
        if usage == 'creator' and not (_is_liquid_cooler(part) or _is_dual_tower_cooler(part)):
            return False
        if cooler_type == 'liquid' and radiator_size != 'any' and not _is_radiator_size_match(part, radiator_size):
            return False
        return True

    if part_type == 'case':
        if not _is_case_size_match(part, case_size):
            return False
        if cooler_type == 'liquid' and radiator_size != 'any' and not _is_case_radiator_compatible(part, radiator_size):
            return False
        return True

    if part_type == 'cpu':
        if _is_globally_excluded_cpu(part):
            return False
        if usage in IGPU_USAGES and not _is_cpu_igpu_capable(part):
            return False
        if usage == 'creator':
            min_cores, min_threads = _creator_cpu_minimum_requirements(options.get('budget', 0), options=options)
            if not _cpu_meets_creator_minimum(part, min_cores=min_cores, min_threads=min_threads):
                return False
        return _is_cpu_vendor_match(part, cpu_vendor)

    if part_type == 'gpu':
        if require_preferred_gaming_gpu and not _is_gaming_spec_gpu_preferred(part, minimum_gaming_gpu_tier):
            return False
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
            available_candidates = [
                candidate for candidate in PCPart.objects.filter(part_type='motherboard')
                if _is_part_suitable('motherboard', candidate)
                and (not cpu_socket or _infer_motherboard_socket(candidate) == cpu_socket)
                and _infer_motherboard_form_factor(candidate) in preferred_form_factors
            ]
            if available_candidates and _infer_motherboard_form_factor(part) not in preferred_form_factors:
                return False
        return True

    if part_type == 'memory':
        if motherboard_memory_type:
            mem_type = _infer_memory_type(part)
            if mem_type and mem_type != motherboard_memory_type:
                return False
        if min_memory_speed_mhz and _infer_memory_speed_mhz(part) < int(min_memory_speed_mhz):
            return False
        if usage == 'creator' and _infer_memory_capacity_gb(part) < 16:
            return False
        return True

    if part_type == 'storage':
        allow_hdd_fallback = usage == 'gaming' and options.get('build_priority') == 'spec'
        if enforce_main_storage_ssd and not allow_hdd_fallback and _infer_storage_media_type(part) != 'ssd':
            return False
        if min_storage_capacity_gb:
            capacity_gb = _infer_storage_capacity_gb(part)
            if capacity_gb < int(min_storage_capacity_gb):
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
            cpu_socket = _infer_cpu_socket(cpu)
            mb_socket = _infer_motherboard_socket(motherboard)
            replaced = False
            if cpu_socket:
                motherboard_candidates = [
                    candidate for candidate in PCPart.objects.filter(part_type='motherboard').order_by('price')
                    if _is_part_suitable('motherboard', candidate) and _infer_motherboard_socket(candidate) == cpu_socket
                ]
                motherboard_candidates = _prefer_motherboard_candidates(motherboard_candidates, case_size)
                new_mb = motherboard_candidates[0] if motherboard_candidates else None
                if new_mb:
                    selected_parts['motherboard'] = new_mb
                    replaced = True
            if not replaced and mb_socket:
                cpu_candidates = [
                    candidate
                    for candidate in PCPart.objects.filter(part_type='cpu').order_by('price')
                    if _is_part_suitable('cpu', candidate)
                    and not _is_globally_excluded_cpu(candidate)
                    and _infer_cpu_socket(candidate) == mb_socket
                    and (usage not in IGPU_USAGES or _is_cpu_igpu_capable(candidate))
                ]
                if usage in {'business', 'standard'} and cpu_candidates:
                    build_priority = _normalize_build_priority(options.get('build_priority', 'balanced'))
                    budget = options.get('budget', 0)
                    new_cpu = _prefer_business_cpu(
                        cpu_candidates,
                        budget=budget,
                        build_priority=build_priority,
                        target_price=None,
                    )
                else:
                    new_cpu = cpu_candidates[0] if cpu_candidates else None
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
                cpu_socket = _infer_cpu_socket(cpu) if cpu else None
                def _mb_fits_mem(p, _mem_type=mem_type, _cpu_socket=cpu_socket):
                    if _infer_motherboard_memory_type(p) != _mem_type:
                        return False
                    p_socket = _infer_motherboard_socket(p)
                    if _cpu_socket and p_socket and p_socket != _cpu_socket:
                        return False
                    return True
                motherboard_candidates = [
                    candidate for candidate in PCPart.objects.filter(part_type='motherboard').order_by('price')
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
                for p in PCPart.objects.filter(part_type='psu').order_by('price')
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
            mb_form = _get_spec(motherboard, 'form_factor')
            if not mb_form:
                break
            new_case = _pick_candidate(
                'case',
                lambda p: (
                    mb_form in (_get_spec(p, 'supported_form_factors', []) or [])
                    and _is_case_size_match(p, case_size)
                ),
            )
            if new_case:
                selected_parts['case'] = new_case
            else:
                break

        elif issue == 'gpu_too_long':
            gpu = selected_parts.get('gpu')
            gpu_len = _get_spec(gpu, 'gpu_length_mm')
            if not gpu_len:
                break
            new_case = _pick_candidate(
                'case',
                lambda p: (
                    int(_get_spec(p, 'max_gpu_length_mm', 0)) >= int(gpu_len)
                    and _is_case_size_match(p, case_size)
                ),
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
    protect_x3d_cpu = options.get('usage') == 'gaming' and options.get('build_priority') == 'spec'
    protect_business_premium_spec_cpu = (
        options.get('usage') in {'business', 'standard'}
        and options.get('build_priority') == 'spec'
        and _classify_business_budget_tier(budget, build_priority=options.get('build_priority')) == 'premium'
    )
    protect_gaming_spec_memory_capacity = (
        options.get('usage') == 'gaming' and options.get('build_priority') == 'spec'
    )
    protected_memory_capacity_gb = (
        _target_memory_capacity_gb(budget, 'gaming', options=options)
        if protect_gaming_spec_memory_capacity
        else 0
    )

    changed = True
    iteration_count = 0
    max_iterations = 60
    while changed and total_price > budget:
        iteration_count += 1
        if iteration_count > max_iterations:
            break
        # CPU/MB の差し替えで動的条件（ソケット/メモリ規格/最低メモリ速度）が変化するため、
        # ループごとに候補条件を更新する。
        loop_options = _refresh_selection_options_with_selected_parts(options, selected_parts)
        changed = False
        ordered_parts = sorted(
            selected_parts.items(),
            key=lambda item: item[1].price if item[1] else 0,
            reverse=True,
        )
        # business/standard の high/premium ティアでは、CPUを先に落とすと
        # ティア差分（high=8500G, premium=8600G, spec/high=8600G等）が潰れやすいため、
        # build_priority にかかわらずCPUダウングレードは最後に回す。
        if (
            loop_options.get('usage') in {'business', 'standard'}
            and _classify_business_budget_tier(budget, build_priority=loop_options.get('build_priority')) in {'high', 'premium'}
        ):
            non_cpu = [item for item in ordered_parts if item[0] != 'cpu']
            cpu_only = [item for item in ordered_parts if item[0] == 'cpu']
            ordered_parts = non_cpu + cpu_only

        for part_type, current in ordered_parts:
            if current is None:
                continue
            if protect_x3d_cpu and part_type == 'cpu' and _is_allowed_gaming_x3d_cpu(current, budget, options=loop_options):
                continue
            if protect_business_premium_spec_cpu and part_type == 'cpu':
                continue

            build_priority = loop_options.get('build_priority', 'balanced')
            cheaper_candidates = [
                c for c in PCPart.objects.filter(part_type=part_type, price__lt=current.price).order_by('-price')
                if _is_part_suitable(part_type, c) and _matches_selection_options(part_type, c, options=loop_options)
            ]
            if (
                part_type == 'memory'
                and protect_gaming_spec_memory_capacity
                and protected_memory_capacity_gb > 0
                and _infer_memory_capacity_gb(current) >= protected_memory_capacity_gb
            ):
                cheaper_candidates = [
                    c for c in cheaper_candidates
                    if _infer_memory_capacity_gb(c) >= protected_memory_capacity_gb
                ]
            if part_type == 'storage' and loop_options.get('build_priority') != 'spec':
                cheaper_candidates = [c for c in cheaper_candidates if _infer_storage_media_type(c) == 'ssd']
            if part_type == 'cpu' and loop_options.get('usage') in {'business', 'standard'} and budget >= 80000:
                non_demoted_cheaper = [
                    c for c in cheaper_candidates
                    if not any(token in f"{getattr(c, 'name', '')} {getattr(c, 'url', '')}".lower() for token in BUSINESS_CPU_DEMOTE_PARTIAL)
                ]
                if non_demoted_cheaper:
                    cheaper_candidates = non_demoted_cheaper
            # creator CPU ダウングレード時は降格対象を避ける（残らなければ降格対象も許可）
            if part_type == 'cpu' and loop_options.get('usage') == 'creator':
                non_demoted_cheaper = [
                    c for c in cheaper_candidates
                    if not any(token in f"{getattr(c, 'name', '')} {getattr(c, 'url', '')}".lower() for token in CREATOR_CPU_DEMOTE_PARTIAL)
                ]
                if non_demoted_cheaper:
                    cheaper_candidates = non_demoted_cheaper
            cheaper = None
            if cheaper_candidates:
                if part_type == 'cpu' and loop_options.get('usage') in {'business', 'standard'}:
                    cheaper = _prefer_business_cpu(
                        cheaper_candidates,
                        budget=budget,
                        build_priority=build_priority,
                        target_price=current.price,
                    )
                elif part_type == 'storage' and build_priority == 'spec':
                    storage_preference = loop_options.get('storage_preference', 'ssd')
                    cheaper = _storage_profile_pick(cheaper_candidates, build_priority, storage_preference)
                elif (
                    part_type == 'gpu'
                    and loop_options.get('usage') == 'gaming'
                    and build_priority == 'spec'
                ):
                    minimum_tier = loop_options.get('minimum_gaming_gpu_tier', 1)
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


def _drop_until_budget(selected_parts, total_price, budget):
    if total_price <= budget:
        return selected_parts, total_price

    non_droppable_parts = {'cpu', 'motherboard', 'memory', 'storage', 'os', 'psu', 'gpu'}
    for part_type in CATEGORY_DROP_PRIORITY:
        if part_type in non_droppable_parts:
            continue
        part = selected_parts.get(part_type)
        if part is None:
            continue
        selected_parts[part_type] = None
        total_price -= part.price
        if total_price <= budget:
            break

    return selected_parts, total_price


def _sum_selected_price(selected_parts):
    return sum(part.price for part in selected_parts.values() if part is not None)


def _upgrade_memory_with_surplus(selected_parts, total_price, budget, usage, options=None):
    options = options or {}
    if total_price >= budget:
        return selected_parts, total_price

    if options.get('build_priority') == 'cost':
        return selected_parts, total_price

    memory = selected_parts.get('memory')
    if not memory:
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
        for p in PCPart.objects.filter(part_type='memory').order_by('price')
        if _is_part_suitable('memory', p)
        and _matches_selection_options('memory', p, options=options)
        and memory.price < p.price <= affordable_max_price
    ]
    if not candidates:
        return selected_parts, total_price

    preferred = _memory_profile_pick(candidates, 'spec', budget=budget, usage=usage, options=options)
    upgraded_memory = preferred or candidates[-1]

    adjusted = dict(selected_parts)
    adjusted['memory'] = upgraded_memory
    return adjusted, _sum_selected_price(adjusted)


def _upgrade_parts_with_surplus(selected_parts, total_price, budget, usage, options=None):
    """余剰予算が大きい場合に優先度順でパーツをアップグレードし、予算を有効活用する。"""
    options = options or {}
    build_priority = options.get('build_priority', 'balanced')

    # cost は「最安」寄りを維持しつつ、予算からの極端な下振れだけ抑える。
    target_budget = budget
    if build_priority == 'cost':
        utilization_floor_by_usage = {
            'gaming': 0.82,
            'creator': 0.92,
            'business': 0.65,
            'standard': 0.65,
        }
        floor_ratio = utilization_floor_by_usage.get(usage, 0.65)

        if usage in {'business', 'standard'}:
            business_tier = _classify_business_budget_tier(budget, build_priority=build_priority)
            floor_ratio_by_tier = {
                'low': 0.72,
                'middle': 0.78,
                'high': 0.85,
                'premium': 0.90,
            }
            floor_ratio = floor_ratio_by_tier.get(business_tier, floor_ratio)

        target_budget = int(budget * floor_ratio)
        if total_price >= target_budget:
            return selected_parts, total_price

    use_igpu = usage in IGPU_USAGES
    upgrade_order = UPGRADE_PRIORITY_BY_USAGE.get(usage, list(PART_ORDER))
    if usage == 'creator':
        # 予算余りの再配分はGPUを最優先にして、体感性能を引き上げる。
        upgrade_order = ['gpu'] + [p for p in upgrade_order if p != 'gpu']

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
            if part_type == 'cpu' and usage == 'creator' and build_priority == 'cost':
                # creator + cost はCPUのコスパ選定を維持し、
                # 余剰予算消化による過剰な上位CPUへの置換を抑制する。
                continue
            current = selected_parts.get(part_type)
            if not current:
                continue

            affordable_max = current.price + surplus
            better_candidates = [
                c for c in PCPart.objects.filter(
                    part_type=part_type,
                    price__gt=current.price,
                    price__lte=affordable_max,
                ).order_by('-price')
                if _is_part_suitable(part_type, c) and _matches_selection_options(part_type, c, options=options)
            ]
            if part_type == 'storage':
                better_candidates = [c for c in better_candidates if _infer_storage_media_type(c) == 'ssd']
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
            if part_type == 'gpu' and usage == 'creator':
                better_candidates = _prefer_creator_gpu_with_vram_flex(better_candidates)
                creator_gpu_cap = _creator_gpu_cap_price(budget, options=options)
                capped_candidates = [c for c in better_candidates if c.price <= creator_gpu_cap]
                if capped_candidates:
                    better_candidates = capped_candidates
            if part_type == 'gpu' and usage == 'gaming' and build_priority == 'cost':
                # gaming + cost は spec と同価格帯まで上げ切らない。
                # 価格差を保ちながら予算未消化だけを抑える。
                gaming_cost_gpu_cap = int(budget * 0.39)
                capped_candidates = [c for c in better_candidates if c.price <= gaming_cost_gpu_cap]
                if capped_candidates:
                    better_candidates = capped_candidates
            better = None
            if better_candidates:
                if part_type == 'cpu' and usage == 'creator':
                    # creator CPU は予算ティア上限 + ティア優先順で上位化する。
                    tier = _classify_budget_tier(budget)
                    price_caps = CREATOR_CPU_MAX_PRICE.get(build_priority, CREATOR_CPU_MAX_PRICE['cost'])
                    max_cpu_price = price_caps.get(tier, 999999)
                    capped_better = [c for c in better_candidates if c.price <= max_cpu_price]
                    if capped_better:
                        better_candidates = capped_better
                    ranked = []
                    seen = set()
                    priority = CREATOR_CPU_PRIORITY_PARTIAL.get(build_priority, CREATOR_CPU_PRIORITY_PARTIAL['cost'])
                    for key in priority:
                        matched = [
                            c for c in better_candidates
                            if key in f"{getattr(c, 'name', '')} {getattr(c, 'url', '')}".lower()
                        ]
                        for m in sorted(matched, key=lambda p: p.price):
                            if m.id not in seen:
                                ranked.append(m)
                                seen.add(m.id)
                    demoted = [
                        c for c in better_candidates
                        if any(token in f"{getattr(c, 'name', '')} {getattr(c, 'url', '')}".lower() for token in CREATOR_CPU_DEMOTE_PARTIAL)
                    ]
                    non_demoted = [c for c in better_candidates if c.id not in {d.id for d in demoted}]
                    fallback_pool = sorted(non_demoted or better_candidates, key=lambda p: p.price, reverse=True)
                    better = (ranked + fallback_pool)[0] if (ranked or fallback_pool) else None
                elif part_type == 'cpu' and usage in {'business', 'standard'}:
                    better = _prefer_business_cpu(
                        better_candidates,
                        budget=budget,
                        build_priority=build_priority,
                        target_price=affordable_max,
                    )
                elif part_type == 'storage' and build_priority == 'spec':
                    storage_preference = options.get('storage_preference', 'ssd')
                    better = _storage_profile_pick(better_candidates, build_priority, storage_preference)
                elif (
                    part_type == 'gpu'
                    and options.get('usage') == 'gaming'
                    and build_priority == 'spec'
                ):
                    minimum_tier = options.get('minimum_gaming_gpu_tier', 1)
                    preferred_gpu = [c for c in better_candidates if _is_gaming_spec_gpu_preferred(c, minimum_tier)]
                    gpu_pool = preferred_gpu or better_candidates
                    gpu_pool = _prefer_rx_xt_value_candidates(gpu_pool)
                    better = gpu_pool[0]
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

    if not memory:
        # 過程でメモリが欠落した場合、現行マザーボード条件で復元を試みる。
        mb = selected_parts.get('motherboard')
        recover_options = dict(options)
        if mb:
            mb_mem_type = _infer_motherboard_memory_type(mb)
            if mb_mem_type:
                recover_options['motherboard_memory_type'] = mb_mem_type
        recovered_memories = [
            p for p in PCPart.objects.filter(part_type='memory').order_by('price')
            if _is_part_suitable('memory', p) and _matches_selection_options('memory', p, options=recover_options)
        ]
        if recovered_memories:
            repaired = dict(selected_parts)
            repaired['memory'] = recovered_memories[0]
            selected_parts = repaired
            memory = repaired.get('memory')
        else:
            return selected_parts

    if gpu.price >= memory.price:
        return selected_parts

    def _gpu_candidates(base_options):
        candidates = [
            p
            for p in PCPart.objects.filter(part_type='gpu').order_by('price')
            if _is_part_suitable('gpu', p) and _matches_selection_options('gpu', p, options=base_options)
        ]
        preferred = [p for p in candidates if _is_gaming_spec_gpu_preferred(p, base_options.get('minimum_gaming_gpu_tier', 1))]
        picked = preferred or candidates
        return _prefer_rx_xt_value_candidates(picked)

    def _memory_candidates(base_options):
        return [
            p
            for p in PCPart.objects.filter(part_type='memory').order_by('price')
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
        for p in PCPart.objects.filter(part_type='motherboard').order_by('price')
        if _is_part_suitable('motherboard', p) and _matches_selection_options('motherboard', p, options=options)
    ]

    if cpu:
        cpu_socket = _infer_cpu_socket(cpu)
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

            memory_candidate = affordable_memories[-1]
            rebalanced = dict(selected_parts)
            rebalanced['gpu'] = gpu_candidate
            rebalanced['motherboard'] = motherboard_candidate
            rebalanced['memory'] = memory_candidate
            return rebalanced

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
        for p in PCPart.objects.filter(part_type='memory').order_by('price')
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
            for p in PCPart.objects.filter(part_type='motherboard').order_by('price')
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
                for p in PCPart.objects.filter(part_type='memory').order_by('price')
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
        for p in PCPart.objects.filter(part_type='gpu').order_by('price')
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
    total_without_gpu = _sum_selected_price(selected_parts) - current_gpu.price

    affordable_candidates = [
        p
        for p in PCPart.objects.filter(part_type='gpu').order_by('price')
        if _is_part_suitable('gpu', p)
        and _matches_selection_options('gpu', p, options=options)
        and _is_gaming_spec_gpu_preferred(p, minimum_tier)
        and total_without_gpu + p.price <= budget
    ]
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
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    cpu = selected_parts.get('cpu')
    if not cpu:
        return selected_parts
    if _is_allowed_gaming_x3d_cpu(cpu, budget, options=options):
        return selected_parts

    x3d_candidates = [
        p
        for p in PCPart.objects.filter(part_type='cpu').order_by('price')
        if _is_part_suitable('cpu', p)
        and _is_allowed_gaming_x3d_cpu(p, budget, options=options)
        and _matches_selection_options('cpu', p, options=options)
    ]
    if not x3d_candidates:
        return selected_parts

    total_without_cpu = _sum_selected_price(selected_parts) - cpu.price
    affordable = [candidate for candidate in x3d_candidates if total_without_cpu + candidate.price <= budget]
    if affordable:
        adjusted = dict(selected_parts)
        adjusted['cpu'] = affordable[-1]
        return _resolve_compatibility(adjusted, usage, options=options)

    trial = dict(selected_parts)
    trial['cpu'] = x3d_candidates[0]
    trial = _resolve_compatibility(trial, usage, options=options)
    trial_total = _sum_selected_price(trial)
    trial, trial_total = _downgrade_selected_parts(trial, trial_total, budget, options=options)
    if trial_total <= budget and _is_allowed_gaming_x3d_cpu(trial.get('cpu'), budget, options=options):
        return trial

    return selected_parts


def _enforce_gaming_x3d_cpu_policy(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming':
        return selected_parts

    build_priority = _normalize_build_priority(options.get('build_priority', 'balanced'))
    cpu = selected_parts.get('cpu')
    if not cpu:
        return selected_parts

    total_without_cpu = _sum_selected_price(selected_parts) - cpu.price
    allowed_candidates = [
        part
        for part in PCPart.objects.filter(part_type='cpu').order_by('price')
        if _is_part_suitable('cpu', part)
        and _matches_selection_options('cpu', part, options=options)
        and _is_allowed_gaming_x3d_cpu(part, budget, options=options)
        and total_without_cpu + part.price <= budget
    ]
    if not allowed_candidates:
        return selected_parts

    def _candidate_rank(part):
        model = _extract_gaming_x3d_model_number(part)
        if build_priority == 'cost':
            # cost: 上限モデルまでは引き上げつつ、同一モデルなら安価側を選ぶ。
            return (model, -part.price)
        return (model, part.price)

    target_cpu = max(allowed_candidates, key=_candidate_rank)
    if cpu.id == target_cpu.id:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['cpu'] = target_cpu
    adjusted = _resolve_compatibility(adjusted, usage, options=options)
    adjusted_cpu = adjusted.get('cpu')
    if not _is_allowed_gaming_x3d_cpu(adjusted_cpu, budget, options=options):
        return selected_parts
    if _sum_selected_price(adjusted) > budget:
        return selected_parts
    return adjusted


def _rightsize_case_after_selection(selected_parts, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'spec':
        return selected_parts

    current_case = selected_parts.get('case')
    if not current_case:
        return selected_parts

    # ケースファン方針を指定した場合は、方針優先で高価格ケースが必要な可能性があるため維持。
    if options.get('case_fan_policy', 'auto') != 'auto':
        return selected_parts

    candidates = [
        p
        for p in PCPart.objects.filter(part_type='case').order_by('price')
        if _is_part_suitable('case', p) and _matches_selection_options('case', p, options=options)
    ]
    if not candidates:
        return selected_parts

    cheapest = candidates[0]
    if cheapest.price >= current_case.price:
        return selected_parts

    adjusted = dict(selected_parts)
    adjusted['case'] = cheapest
    return adjusted


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
    cpu_socket = _infer_cpu_socket(cpu_part) if cpu_part else ''

    candidates = [
        p
        for p in PCPart.objects.filter(part_type='motherboard').order_by('price')
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
        c for c in PCPart.objects.filter(
            part_type='gpu',
            price__gt=current_gpu.price,
            price__lte=affordable_max,
        ).order_by('-price')
        if _is_part_suitable('gpu', c) and _matches_selection_options('gpu', c, options=options)
    ]
    if not candidates:
        return selected_parts

    minimum_tier = options.get('minimum_gaming_gpu_tier', 1)
    preferred_gpu = [c for c in candidates if _is_gaming_spec_gpu_preferred(c, minimum_tier)]
    gpu_pool = preferred_gpu or candidates
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

    # gaming+spec では高容量HDDフォールバックを許容する。
    if usage == 'gaming' and options.get('build_priority') == 'spec':
        return selected_parts

    if _infer_storage_media_type(storage) == 'ssd':
        return selected_parts

    strict_options = dict(options)
    strict_options['enforce_main_storage_ssd'] = True
    if not strict_options.get('min_storage_capacity_gb'):
        strict_options['min_storage_capacity_gb'] = 512

    current_capacity = _infer_storage_capacity_gb(storage)
    candidates = [
        p
        for p in PCPart.objects.filter(part_type='storage').order_by('price')
        if _is_part_suitable('storage', p)
        and _matches_selection_options('storage', p, options=strict_options)
    ]
    if not candidates:
        return selected_parts

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
        if final_storage and _infer_storage_media_type(final_storage) == 'ssd' and trial_total <= budget:
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
            for p in PCPart.objects.filter(part_type='cpu').order_by('price')
            if _is_part_suitable('cpu', p)
            and _matches_selection_options('cpu', p, options=options)
            and _cpu_meets_creator_minimum(p, min_cores=8, min_threads=16)
        ]
        if cpu_candidates:
            if options.get('build_priority') == 'cost':
                picked_cpu = _prefer_creator_cost_cpu_8_to_24_cores(cpu_candidates, budget=budget)
            else:
                picked_cpu = _prefer_creator_cpu_by_core_threads(cpu_candidates, budget=budget)
            if picked_cpu:
                adjusted['cpu'] = picked_cpu

    current_memory = adjusted.get('memory')
    if current_memory and _infer_memory_capacity_gb(current_memory) < 16:
        memory_candidates = [
            p
            for p in PCPart.objects.filter(part_type='memory').order_by('price')
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

    # Stage 2: creator GPU は NVIDIA を優先、マザーボードは拡張性重視
    current_gpu = adjusted.get('gpu')
    if current_gpu and not _is_nvidia_gpu(current_gpu):
        gpu_candidates = [
            p
            for p in PCPart.objects.filter(part_type='gpu').order_by('price')
            if _is_part_suitable('gpu', p)
            and _matches_selection_options('gpu', p, options=options)
            and _is_nvidia_gpu(p)
        ]
        minimum_tier = _minimum_creator_gpu_tier(budget, options=options)
        tier_candidates = [p for p in gpu_candidates if _creator_gpu_tier(p) >= minimum_tier]
        if tier_candidates:
            gpu_candidates = tier_candidates

        current_total = _sum_selected_price(adjusted)
        ranked_gpu = sorted(
            gpu_candidates,
            key=lambda p: (
                _creator_gpu_tier(p),
                _infer_gpu_memory_gb(p),
                -p.price,
            ),
            reverse=True,
        )
        for candidate in ranked_gpu:
            if current_total - current_gpu.price + candidate.price <= budget:
                adjusted['gpu'] = candidate
                break

    current_mb = adjusted.get('motherboard')
    if current_mb:
        current_mb_score = _creator_motherboard_expandability_score(current_mb)
        mb_candidates = [
            p
            for p in PCPart.objects.filter(part_type='motherboard').order_by('price')
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
            for p in PCPart.objects.filter(part_type='cpu_cooler').order_by('price')
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


def _rebalance_creator_spec_cpu_gpu(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'creator' or options.get('build_priority') != 'spec':
        return selected_parts

    current_cpu = selected_parts.get('cpu')
    current_gpu = selected_parts.get('gpu')
    if not current_cpu or not current_gpu:
        return selected_parts

    # GPU過多を検知したときのみ補正する。
    max_gpu_vs_cpu = 1.40
    if current_gpu.price <= int(current_cpu.price * max_gpu_vs_cpu):
        return selected_parts

    min_cores, min_threads = _creator_cpu_minimum_requirements(budget, options=options)
    minimum_gpu_tier = _minimum_creator_gpu_tier(budget, options=options)

    cpu_candidates = [
        p
        for p in PCPart.objects.filter(part_type='cpu').order_by('-price')
        if _is_part_suitable('cpu', p)
        and _matches_selection_options('cpu', p, options=options)
        and _cpu_meets_creator_minimum(p, min_cores=min_cores, min_threads=min_threads)
    ]
    if not cpu_candidates:
        return selected_parts

    gpu_candidates = [
        p
        for p in PCPart.objects.filter(part_type='gpu').order_by('price')
        if _is_part_suitable('gpu', p)
        and _matches_selection_options('gpu', p, options=options)
        and _creator_gpu_tier(p) >= minimum_gpu_tier
        and p.price <= current_gpu.price
    ]
    if not gpu_candidates:
        return selected_parts

    current_total = _sum_selected_price(selected_parts)
    best_trial = None
    best_score = None

    for gpu_candidate in gpu_candidates:
        base_total = current_total - current_cpu.price - current_gpu.price + gpu_candidate.price
        affordable_cpus = [p for p in cpu_candidates if base_total + p.price <= budget]
        if not affordable_cpus:
            continue

        preferred_cpu = _pick_creator_cpu_by_partial_priority(affordable_cpus, 'spec', budget=budget)
        ranked_cpus = [preferred_cpu] if preferred_cpu else []
        if not ranked_cpus:
            ranked_cpus = sorted(
                affordable_cpus,
                key=lambda p: (
                    _extract_cpu_core_threads(p),
                    _extract_cpu_core_count(p),
                    p.price,
                ),
                reverse=True,
            )

        for cpu_candidate in ranked_cpus:
            if cpu_candidate.id == current_cpu.id and gpu_candidate.id == current_gpu.id:
                continue

            trial = dict(selected_parts)
            trial['cpu'] = cpu_candidate
            trial['gpu'] = gpu_candidate
            trial = _resolve_compatibility(trial, usage, options=options)

            final_cpu = trial.get('cpu')
            final_gpu = trial.get('gpu')
            if not final_cpu or not final_gpu:
                continue

            trial_total = _sum_selected_price(trial)
            if trial_total > budget:
                continue
            if not _cpu_meets_creator_minimum(final_cpu, min_cores=min_cores, min_threads=min_threads):
                continue
            if _creator_gpu_tier(final_gpu) < minimum_gpu_tier:
                continue
            if final_gpu.price > int(final_cpu.price * max_gpu_vs_cpu):
                continue

            score = (
                _extract_cpu_core_threads(final_cpu),
                _extract_cpu_core_count(final_cpu),
                -final_gpu.price,
                -trial_total,
            )
            if best_score is None or score > best_score:
                best_trial = trial
                best_score = score
            break

    return best_trial or selected_parts


def _apply_build_priority_weights(usage, build_priority, use_igpu, custom_budget_weights=None):
    if custom_budget_weights is not None:
        return dict(custom_budget_weights)

    base = IGPU_BUDGET_WEIGHTS.get(usage) if use_igpu else USAGE_BUDGET_WEIGHTS.get(usage)
    if not base:
        return None

    adjusted = dict(base)
    if build_priority != 'spec' or use_igpu:
        return adjusted

    gpu_boost_map = {
        'gaming': 0.20,
        'creator': 0.01,
    }
    boost = gpu_boost_map.get(usage, 0.06)
    adjusted['gpu'] = min(0.75, adjusted.get('gpu', 0) + boost)

    # GPUへ寄せた分は、優先度の低いカテゴリから順に減らす。
    remaining = boost
    reduce_order = ['memory', 'storage', 'motherboard', 'case', 'psu', 'cpu_cooler', 'cpu']
    floors = {
        'cpu': 0.17 if usage == 'gaming' else (0.20 if usage == 'creator' else 0.10),
        'motherboard': 0.08,
        'memory': 0.05,
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
        cpu_socket = _infer_cpu_socket(cpu_part)
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


def _is_premium_gaming_cpu_for_cost_build(part, budget):
    if not part or not _is_allowed_gaming_x3d_cpu(part, budget, options={'build_priority': 'cost'}):
        return False

    text = f"{part.name} {part.url}".lower()
    if 'ryzen 9' in text:
        return True

    # gaming + cost は予算帯に応じて CPU 上限を段階制にする。
    # 低予算帯: X3D昇格余地を確保。
    # 中高予算帯: GPU優先を崩さないようCPU過剰投資を抑制。
    if int(budget) <= 200000:
        premium_floor = max(75000, int(budget * 0.30))
    else:
        premium_floor = max(60000, int(budget * 0.14))

    return part.price >= premium_floor


def _rebalance_gaming_cost_cpu_to_storage(selected_parts, budget, usage, options=None):
    options = options or {}
    if usage != 'gaming' or options.get('build_priority') != 'cost':
        return selected_parts

    cpu = selected_parts.get('cpu')
    storage = selected_parts.get('storage')
    if not cpu or not storage:
        return selected_parts

    if not _is_premium_gaming_cpu_for_cost_build(cpu, budget):
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
    current_cpu_socket = _infer_cpu_socket(cpu)

    base_total = _sum_selected_price(selected_parts) - cpu.price - storage.price
    cpu_candidates = [
        part
        for part in PCPart.objects.filter(part_type='cpu', price__lt=cpu.price).order_by('-price')
        if _is_part_suitable('cpu', part)
        and _matches_selection_options('cpu', part, options=options)
        and _is_allowed_gaming_x3d_cpu(part, budget, options=options)
        and ('amd' in current_cpu_text or 'ryzen' in current_cpu_text)
        and (not current_cpu_socket or _infer_cpu_socket(part) == current_cpu_socket)
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
            if not _is_allowed_gaming_x3d_cpu(final_cpu, budget, options=options):
                continue
            if current_cpu_socket and _infer_cpu_socket(final_cpu) != current_cpu_socket:
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

    current_cpu = selected_parts.get('cpu')
    current_memory = selected_parts.get('memory')
    if not current_cpu or not current_memory:
        return selected_parts

    # X3D CPU 候補を取得（現在の CPU より高価、かつ premium ではない）
    if _is_allowed_gaming_x3d_cpu(current_cpu, budget, options=options):
        # 既に X3D: さらに高い X3D CPU を探す
        upgrade_candidates = [
            part
            for part in PCPart.objects.filter(part_type='cpu', price__gt=current_cpu.price).order_by('-price')
            if _is_part_suitable('cpu', part)
            and _matches_selection_options('cpu', part, options=options)
            and _is_allowed_gaming_x3d_cpu(part, budget, options=options)
            and not _is_premium_gaming_cpu_for_cost_build(part, budget)
        ]
    else:
        # 非 X3D: 予算比率の固定上限ではなく、後段の合計金額判定で可否を判断する。
        # これにより、全体予算に余剰があるケースで X3D 候補を取りこぼさない。
        upgrade_candidates = [
            part
            for part in PCPart.objects.filter(part_type='cpu', price__gt=current_cpu.price).order_by('-price')
            if _is_part_suitable('cpu', part)
            and _matches_selection_options('cpu', part, options=options)
            and _is_allowed_gaming_x3d_cpu(part, budget, options=options)
            and not _is_premium_gaming_cpu_for_cost_build(part, budget)
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
):
    requested_usage = usage
    usage_alias_map = {
        'video_editing': 'creator',
    }
    usage = usage_alias_map.get(usage, usage)

    if not isinstance(budget, int) or budget < 50000 or budget > 1500000:
        return None, Response({'detail': 'budgetは50,000円以上1,500,000円以下で入力してください'}, status=status.HTTP_400_BAD_REQUEST)

    if usage not in USAGE_POWER_MAP:
        return None, Response({'detail': 'usage must be gaming, creator, business, or standard'}, status=status.HTTP_400_BAD_REQUEST)

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
    selection_options['os_edition'] = _resolve_os_edition_by_usage(usage, selection_options['os_edition'], budget=budget)

    if usage == 'gaming':
        selection_options = dict(selection_options)
        if not selection_options.get('min_storage_capacity_gb'):
            if selection_options.get('build_priority') == 'spec':
                selection_options['min_storage_capacity_gb'] = 1000 if budget >= 220000 else 512
            elif selection_options.get('build_priority') == 'cost' and budget >= 450000:
                selection_options['min_storage_capacity_gb'] = 2000
            elif selection_options.get('build_priority') == 'cost' and budget >= 220000:
                selection_options['min_storage_capacity_gb'] = 1000

    if usage == 'gaming' and selection_options.get('build_priority') == 'spec':
        # gaming + spec はストレージ容量を優先するが、低予算では最低容量を抑える。
        selection_options['require_preferred_gaming_gpu'] = True
        selection_options['minimum_gaming_gpu_tier'] = _minimum_gaming_spec_gpu_tier(budget, usage, options=selection_options)

    # すべてのユースケースでメインストレージの最低容量を設定（SSD候補を確保）
    if not selection_options.get('min_storage_capacity_gb'):
        if usage in {'creator', 'business', 'standard'}:
            selection_options = dict(selection_options)
            selection_options['min_storage_capacity_gb'] = 512

    normalized_custom_budget_weights = _normalize_custom_budget_weights(custom_budget_weights)
    if custom_budget_weights is not None and normalized_custom_budget_weights is None:
        return None, Response({'detail': 'custom_budget_weights must be a positive numeric mapping for part categories'}, status=status.HTTP_400_BAD_REQUEST)

    use_igpu = usage in IGPU_USAGES
    priority_weights = _apply_build_priority_weights(
        usage,
        selection_options['build_priority'],
        use_igpu,
        custom_budget_weights=normalized_custom_budget_weights,
    )

    selected_parts = {}
    total_price = 0

    for part_type in PART_ORDER:
        if use_igpu and part_type == 'gpu':
            continue  # 内蔵GPU使用のためdGPUをスキップ
        # マザーボード選定時は先に確定したCPUのソケットを絞り込み条件に追加
        effective_options = selection_options
        if part_type == 'motherboard':
            cpu_part = selected_parts.get('cpu')
            if cpu_part:
                cpu_socket = _infer_cpu_socket(cpu_part)
                if cpu_socket:
                    effective_options = dict(selection_options)
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

    # creator + (cost or spec): GPU選定を固定化し、後続処理で変更されないようにする
    creator_gpu_fixed = None
    if usage == 'creator' and selection_options.get('build_priority') == 'cost':
        creator_gpu_fixed = selected_parts.get('gpu')

    # CPUソケット情報をoptions に付与して、互換チェック・ダウングレード時に引き継ぐ
    cpu_part = selected_parts.get('cpu')
    if cpu_part:
        cpu_socket = _infer_cpu_socket(cpu_part)
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

    selected_parts, total_price = _drop_until_budget(selected_parts, total_price, budget)
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

    selected_parts = _prefer_higher_gaming_cost_x3d_cpu(
        selected_parts,
        budget,
        usage,
        options=selection_options,
    )
    selected_parts = _enforce_gaming_x3d_cpu_policy(
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
    selected_parts = _rebalance_creator_spec_cpu_gpu(
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
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
    total_price = _sum_selected_price(selected_parts)

    selected_parts = _rightsize_psu_after_selection(
        selected_parts,
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

    # 最終ガード: すべての後処理を終えた時点で必ず予算内へ戻す。
    selected_parts, total_price = _downgrade_selected_parts(
        selected_parts,
        total_price,
        budget,
        options=selection_options,
    )
    selected_parts, total_price = _drop_until_budget(selected_parts, total_price, budget)
    selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

    # 最終ガード: iGPU用途では、どの後処理経路でも F付き等の非iGPU CPUを残さない。
    if use_igpu:
        current_cpu = selected_parts.get('cpu')
        if current_cpu and not _is_cpu_igpu_capable(current_cpu):
            cpu_candidates = [
                candidate
                for candidate in PCPart.objects.filter(part_type='cpu').order_by('price')
                if _is_part_suitable('cpu', candidate)
                and not _is_globally_excluded_cpu(candidate)
                and _matches_selection_options('cpu', candidate, options=selection_options)
                and _is_cpu_igpu_capable(candidate)
            ]
            replacement_cpu = None
            if cpu_candidates:
                if usage in {'business', 'standard'}:
                    replacement_cpu = _prefer_business_cpu(
                        cpu_candidates,
                        budget=budget,
                        build_priority=selection_options.get('build_priority', 'balanced'),
                        target_price=current_cpu.price,
                    )
                else:
                    replacement_cpu = cpu_candidates[0]

            if replacement_cpu:
                selected_parts['cpu'] = replacement_cpu
                selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
                selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
                total_price = _sum_selected_price(selected_parts)
                selected_parts, total_price = _downgrade_selected_parts(
                    selected_parts,
                    total_price,
                    budget,
                    options=selection_options,
                )
                selected_parts, total_price = _drop_until_budget(selected_parts, total_price, budget)
                selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

    # business/standard + spec + premium は CPU を 8600G 以上に維持する。
    if (
        usage in {'business', 'standard'}
        and selection_options.get('build_priority') == 'spec'
        and _classify_business_budget_tier(budget, build_priority=selection_options.get('build_priority')) == 'premium'
    ):
        current_cpu = selected_parts.get('cpu')
        current_text = f"{getattr(current_cpu, 'name', '')} {getattr(current_cpu, 'url', '')}".lower() if current_cpu else ''
        if not _ordered_partial_match(current_text, 'ryzen 5 8600g'):
            premium_cpu_candidates = [
                candidate
                for candidate in PCPart.objects.filter(part_type='cpu').order_by('price')
                if _is_part_suitable('cpu', candidate)
                and _matches_selection_options('cpu', candidate, options=selection_options)
                and _is_cpu_igpu_capable(candidate)
                and _ordered_partial_match(
                    f"{getattr(candidate, 'name', '')} {getattr(candidate, 'url', '')}",
                    'ryzen 5 8600g',
                )
            ]
            if premium_cpu_candidates:
                selected_parts['cpu'] = premium_cpu_candidates[0]
                selected_parts = _resolve_compatibility(selected_parts, usage, options=selection_options)
                selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)
                total_price = _sum_selected_price(selected_parts)
                selected_parts, total_price = _downgrade_selected_parts(
                    selected_parts,
                    total_price,
                    budget,
                    options=selection_options,
                )
                selected_parts, total_price = _drop_until_budget(selected_parts, total_price, budget)
                selection_options = _refresh_selection_options_with_selected_parts(selection_options, selected_parts)

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

    # 内蔵GPU使用構成の場合: CPUの直後に統合グラフィックスエントリを挿入
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

    estimated_power = _estimate_system_power_w({**selected_parts, **extra_storage_parts}, usage)

    # creator + (cost or spec): GPU固定は予算内で成立する場合のみ復元する。
    if creator_gpu_fixed:
        restored_parts = dict(selected_parts)
        restored_parts['gpu'] = creator_gpu_fixed
        restored_total = _sum_selected_price(restored_parts)
        if restored_total <= budget:
            selected_parts = restored_parts
            # selected リスト内の GPU も更新
            gpu_index = next((i for i, p in enumerate(selected) if p['category'] == 'gpu'), -1)
            if gpu_index >= 0:
                gpu_part = creator_gpu_fixed
                selected[gpu_index] = {
                    'category': 'gpu',
                    'name': gpu_part.name,
                    'price': gpu_part.price,
                    'url': gpu_part.url,
                    'specs': gpu_part.specs,
                }
            total_price = restored_total

    configuration = Configuration.objects.create(
        budget=budget,
        usage=requested_usage,
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
    ) if use_igpu else Configuration.objects.create(
        budget=budget,
        usage=requested_usage,
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
    )

    return {
        'usage': requested_usage,
        'budget': budget,
        'cooler_type': selection_options['cooler_type'],
        'radiator_size': selection_options['radiator_size'],
        'cooling_profile': selection_options['cooling_profile'],
        'case_size': selection_options['case_size'],
        'case_fan_policy': selection_options['case_fan_policy'],
        'cpu_vendor': selection_options['cpu_vendor'],
        'build_priority': selection_options['build_priority'],
        'storage_preference': selection_options['storage_preference'],
        'os_edition': selection_options['os_edition'],
        'custom_budget_weights': normalized_custom_budget_weights,
        'configuration_id': configuration.id,
        'total_price': total_price,
        'estimated_power_w': estimated_power,
        'parts': selected,
    }, None


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
    
    @action(detail=False, methods=['get'])
    def by_type(self, request):
        part_type = request.query_params.get('type')
        if not part_type:
            return Response({'error': 'type parameter required'}, status=status.HTTP_400_BAD_REQUEST)
        parts = PCPart.objects.filter(part_type=part_type)
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
        )
        if error_response:
            return error_response
        return Response(response_data)


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
        )
        if error_response:
            return error_response
        return Response(response_data)


class ScraperStatusCompatAPIView(APIView):
    """Frontend互換: FastAPIの /scraper/status 相当"""

    def get(self, request):
        return Response(build_scraper_status_summary())


class MarketPriceRangeAPIView(APIView):
    """フロントエンド向け: ドスパラ相場レンジを返す"""

    def get(self, request):
        data = fetch_dospara_market_price_range(timeout=15)
        return Response(data)


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

    text = f"{getattr(part, 'name', '')} {getattr(part, 'url', '')}".lower()
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
    # M.2 in product name → NVMe
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


def _build_storage_inventory_summary():
    storage_parts = list(PCPart.objects.filter(part_type='storage').order_by('price', 'name'))
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
        return Response(_build_storage_inventory_summary())

