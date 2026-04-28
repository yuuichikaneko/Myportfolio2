from datetime import timedelta
import logging
import re

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .pckoubou_scraper import (
    _infer_part_type,
    scrape_pckoubou_all,
)
from .dospara_scraper import (
    fetch_dospara_cpu_selection_material,
    fetch_dospara_gpu_performance_table,
    fetch_dospara_market_price_range,
)
from .models import (
    CPUCoolerDetail,
    CPUDetail,
    CPUSelectionEntry,
    CPUSelectionSnapshot,
    CaseDetail,
    EfficiencyGrade,
    FormFactor,
    GPUDetail,
    GPUPerformanceEntry,
    GPUPerformanceSnapshot,
    InterfaceType,
    LicenseType,
    MarketPriceRangeSnapshot,
    MemoryDetail,
    MemoryType,
    MotherboardDetail,
    OSEdition,
    OSDetail,
    OSFamily,
    PCPart,
    PSUDetail,
    ScraperStatus,
    SocketType,
    StorageDetail,
)


logger = logging.getLogger(__name__)


ALL_DETAIL_MODELS = (
    CPUDetail,
    GPUDetail,
    MotherboardDetail,
    MemoryDetail,
    StorageDetail,
    OSDetail,
    PSUDetail,
    CaseDetail,
    CPUCoolerDetail,
)


def _ref_or_none(model, value):
    normalized = (value or '').strip()
    if not normalized:
        return None
    obj, _ = model.objects.get_or_create(name=normalized)
    return obj


def _build_detail_sync_payload(part):
    if part.part_type == 'cpu':
        return CPUDetail, {
            'socket': part.socket,
            'memory_type': part.memory_type,
            'socket_ref': _ref_or_none(SocketType, part.socket),
            'memory_type_ref': _ref_or_none(MemoryType, part.memory_type),
            'cores': part.cores,
            'threads': part.threads,
            'tdp_w': part.tdp_w,
            'base_clock_mhz': part.base_clock_mhz,
            'boost_clock_mhz': part.boost_clock_mhz,
        }
    if part.part_type == 'gpu':
        return GPUDetail, {
            'vram_gb': part.vram_gb,
            'vram_type': part.vram_type,
            'tdp_w': part.tdp_w,
            'interface': part.interface,
            'interface_ref': _ref_or_none(InterfaceType, part.interface),
        }
    if part.part_type == 'motherboard':
        return MotherboardDetail, {
            'socket': part.socket,
            'memory_type': part.memory_type,
            'socket_ref': _ref_or_none(SocketType, part.socket),
            'memory_type_ref': _ref_or_none(MemoryType, part.memory_type),
            'chipset': part.chipset,
            'form_factor': part.form_factor,
            'form_factor_ref': _ref_or_none(FormFactor, part.form_factor),
            'm2_slots': part.m2_slots,
            'pcie_x16_slots': part.pcie_x16_slots,
            'usb_total': part.usb_total,
            'type_c_ports': part.type_c_ports,
        }
    if part.part_type == 'memory':
        return MemoryDetail, {
            'memory_type': part.memory_type,
            'memory_type_ref': _ref_or_none(MemoryType, part.memory_type),
            'capacity_gb': part.capacity_gb,
            'speed_mhz': part.speed_mhz,
            'form_factor': part.form_factor,
            'form_factor_ref': _ref_or_none(FormFactor, part.form_factor),
        }
    if part.part_type == 'storage':
        return StorageDetail, {
            'capacity_gb': part.capacity_gb,
            'interface': part.interface,
            'form_factor': part.form_factor,
            'interface_ref': _ref_or_none(InterfaceType, part.interface),
            'form_factor_ref': _ref_or_none(FormFactor, part.form_factor),
        }
    if part.part_type == 'os':
        return OSDetail, {
            'os_family': part.os_family,
            'os_edition': part.os_edition,
            'license_type': part.license_type,
            'os_family_ref': _ref_or_none(OSFamily, part.os_family),
            'os_edition_ref': _ref_or_none(OSEdition, part.os_edition),
            'license_type_ref': _ref_or_none(LicenseType, part.license_type),
        }
    if part.part_type == 'psu':
        return PSUDetail, {
            'wattage': part.wattage,
            'efficiency_grade': part.efficiency_grade,
            'form_factor': part.form_factor,
            'efficiency_grade_ref': _ref_or_none(EfficiencyGrade, part.efficiency_grade),
            'form_factor_ref': _ref_or_none(FormFactor, part.form_factor),
        }
    if part.part_type == 'case':
        return CaseDetail, {
            'form_factor': part.form_factor,
            'form_factor_ref': _ref_or_none(FormFactor, part.form_factor),
            'included_fan_count': part.included_fan_count,
            'supported_fan_count': part.supported_fan_count,
        }
    if part.part_type == 'cpu_cooler':
        return CPUCoolerDetail, {
            'socket': part.socket,
            'socket_ref': _ref_or_none(SocketType, part.socket),
            'max_tdp_w': part.max_tdp_w,
            'form_factor': part.form_factor,
            'form_factor_ref': _ref_or_none(FormFactor, part.form_factor),
        }
    return None, None


def _sync_part_detail(part):
    target_model, defaults = _build_detail_sync_payload(part)
    if target_model is None:
        return

    for model in ALL_DETAIL_MODELS:
        if model is target_model:
            continue
        model.objects.filter(part_id=part.id).delete()

    target_model.objects.update_or_create(part_id=part.id, defaults=defaults)


def _sync_details_for_parts(parts):
    for part in parts:
        _sync_part_detail(part)


def _extract_gpu_model_key(text: str):
    value = re.sub(r"\s+", " ", (text or "").upper()).strip()
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
        match = re.search(pattern, value)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _extract_gpu_vram_gb(text: str):
    match = re.search(r"(\d+(?:\.\d+)?)\s*GB", (text or ""), re.IGNORECASE)
    if not match:
        return None
    return int(float(match.group(1)))


def _build_perf_index(entries):
    indexed = {}
    for entry in entries:
        if entry.get("is_laptop"):
            continue
        model_key = entry.get("model_key")
        perf_score = entry.get("perf_score")
        if not model_key or perf_score is None:
            continue
        indexed.setdefault(model_key, []).append(entry)
    return indexed


def _pick_best_perf_entry(candidates, vram_gb):
    if not candidates:
        return None
    if vram_gb is not None:
        exact = [entry for entry in candidates if entry.get("vram_gb") == vram_gb]
        if exact:
            return sorted(exact, key=lambda e: e.get("perf_score", 0), reverse=True)[0]
    with_vram = [entry for entry in candidates if entry.get("vram_gb") is not None]
    target = with_vram or candidates
    return sorted(target, key=lambda e: e.get("perf_score", 0), reverse=True)[0]


def _persist_gpu_perf_snapshot(data):
    entries = data.get("entries", [])
    with transaction.atomic():
        snapshot = GPUPerformanceSnapshot.objects.create(
            source_name=data.get("source_name") or "dospara_gpu_performance_page",
            source_url=data.get("source_url") or "",
            updated_at_source=data.get("updated_at_source") or "",
            score_note=data.get("score_note") or "",
            parser_version="v1",
        )

        bulk_entries = []
        rank = 1
        # 同一モデル/VRAMは最高スコアを優先して1件化。
        dedup = {}
        for entry in entries:
            model_key = entry.get("model_key")
            perf_score = entry.get("perf_score")
            if not model_key or perf_score is None:
                continue
            key = (model_key, entry.get("vram_gb"), bool(entry.get("is_laptop")))
            prev = dedup.get(key)
            if prev is None or int(perf_score) > int(prev.get("perf_score", 0)):
                dedup[key] = entry

        for entry in sorted(dedup.values(), key=lambda row: int(row.get("perf_score", 0)), reverse=True):
            bulk_entries.append(
                GPUPerformanceEntry(
                    snapshot=snapshot,
                    gpu_name=entry.get("name") or entry.get("model_key") or "unknown",
                    model_key=entry.get("model_key"),
                    vendor=entry.get("vendor") or "unknown",
                    vram_gb=entry.get("vram_gb"),
                    perf_score=int(entry.get("perf_score", 0)),
                    detail_url=entry.get("detail_url") or "",
                    is_laptop=bool(entry.get("is_laptop")),
                    rank_global=rank,
                )
            )
            rank += 1

        GPUPerformanceEntry.objects.bulk_create(bulk_entries, batch_size=500)

    return snapshot, len(bulk_entries)


def _apply_gpu_perf_scores(entries, updated_at_source, source_url, score_note):
    perf_index = _build_perf_index(entries)
    if not perf_index:
        return {"matched": 0, "updated": 0, "skipped": 0}

    matched = 0
    updated = 0
    skipped = 0

    for part in PCPart.objects.filter(part_type="gpu"):
        model_key = _extract_gpu_model_key(part.name)
        if not model_key:
            skipped += 1
            continue

        candidates = perf_index.get(model_key)
        if not candidates:
            skipped += 1
            continue

        vram_gb = part.vram_gb or _extract_gpu_vram_gb(part.name)
        best = _pick_best_perf_entry(candidates, vram_gb)
        if not best:
            skipped += 1
            continue

        matched += 1
        specs = dict(part.specs or {})
        old_score = specs.get("gpu_perf_score")
        new_score = int(best.get("perf_score", 0))

        specs.update(
            {
                "gpu_perf_source": "dospara",
                "gpu_perf_score": new_score,
                "gpu_perf_score_updated_at": updated_at_source,
                "gpu_perf_model": best.get("model_key") or model_key,
                "gpu_perf_vram_gb": best.get("vram_gb"),
                "gpu_perf_detail_url": best.get("detail_url") or source_url,
                "gpu_perf_note": score_note,
            }
        )

        changed = old_score != new_score or part.specs != specs
        if best.get("vram_gb") and not part.vram_gb:
            part.vram_gb = int(best["vram_gb"])
            changed = True

        if changed:
            part.specs = specs
            part.save(update_fields=["specs", "vram_gb", "updated_at"])
            updated += 1

    return {"matched": matched, "updated": updated, "skipped": skipped}


@shared_task
def import_gpu_performance_scores_task(timeout=20):
    """Import Dospara GPU performance table to normalized snapshot/entry tables, then sync specs."""
    data = fetch_dospara_gpu_performance_table(timeout=timeout)
    entries = data.get("entries", [])
    snapshot, saved_entries = _persist_gpu_perf_snapshot(data)
    legacy_specs_sync_enabled = bool(getattr(settings, "GPU_PERF_ENABLE_LEGACY_SPECS_SYNC", True))
    if legacy_specs_sync_enabled:
        result = _apply_gpu_perf_scores(
            entries,
            data.get("updated_at_source"),
            data.get("source_url"),
            data.get("score_note"),
        )
    else:
        result = {"matched": 0, "updated": 0, "skipped": 0}
    return {
        "status": "success",
        "source": data.get("source_name"),
        "entries": len(entries),
        "snapshot_id": snapshot.id,
        "saved_entries": saved_entries,
        "legacy_specs_sync_enabled": legacy_specs_sync_enabled,
        **result,
    }


@shared_task
def import_market_price_range_task(timeout=20):
    """Import market price range from Dospara and persist snapshot for DB-first reads."""
    data = fetch_dospara_market_price_range(timeout=timeout)
    market_min = int(data.get('min') or 0)
    market_max = int(data.get('max') or 0)
    suggested_default = int(data.get('default') or 0)

    if market_min <= 0 or market_max <= 0 or market_max <= market_min:
        raise ValueError('invalid market price range from scraper')

    snapshot = MarketPriceRangeSnapshot.objects.create(
        source_name='dospara_tc30_market',
        market_min=market_min,
        market_max=market_max,
        suggested_default=suggested_default,
        currency=str(data.get('currency') or 'JPY'),
        sources=data.get('sources') or {},
    )

    return {
        'status': 'success',
        'snapshot_id': snapshot.id,
        'min': market_min,
        'max': market_max,
        'default': suggested_default,
    }


@shared_task
def import_cpu_selection_material_task(timeout=20):
    """Import CPU selection material and persist normalized snapshot/entry tables."""
    data = fetch_dospara_cpu_selection_material(timeout=timeout, exclude_intel_13_14=True)
    entries = data.get('entries', []) or []

    with transaction.atomic():
        snapshot = CPUSelectionSnapshot.objects.create(
            source_name=data.get('source_name', 'dospara_cpu_comparison_pages'),
            source_urls=data.get('source_urls', []) or [],
            exclude_intel_13_14=bool(data.get('exclude_intel_13_14', True)),
            entry_count=int(data.get('entry_count', len(entries)) or len(entries)),
            excluded_count=int(data.get('excluded_count', 0) or 0),
            parser_version=str(data.get('parser_version', 'v1') or 'v1'),
            fetched_at=timezone.now(),
        )

        rows = []
        for rank_global, entry in enumerate(sorted(entries, key=lambda row: int(row.get('perf_score', 0) or 0), reverse=True), 1):
            model_name = str(entry.get('model_name', '') or '').strip()
            perf_score = int(entry.get('perf_score', 0) or 0)
            if not model_name or perf_score <= 0:
                continue
            rows.append(CPUSelectionEntry(
                snapshot=snapshot,
                vendor=str(entry.get('vendor', '') or 'unknown').lower(),
                model_name=model_name,
                perf_score=perf_score,
                source_url=str(entry.get('source_url', '') or ''),
                rank_global=rank_global,
            ))

        if rows:
            CPUSelectionEntry.objects.bulk_create(rows, batch_size=500)

    return {
        'status': 'success',
        'snapshot_id': snapshot.id,
        'entry_count': len(rows),
        'excluded_count': int(snapshot.excluded_count or 0),
    }


def _normalize_part_types():
    changed = 0
    merged = 0
    for part in PCPart.objects.all().order_by('id'):
        inferred = _infer_part_type(part.name)
        if not inferred or inferred == part.part_type:
            continue

        existing = PCPart.objects.filter(part_type=inferred, name=part.name).exclude(id=part.id).first()
        if existing:
            existing.price = part.price
            existing.url = part.url
            existing.specs = part.specs or existing.specs
            existing.save(update_fields=['price', 'url', 'specs', 'updated_at'])
            _sync_part_detail(existing)
            part.delete()
            merged += 1
            continue

        part.part_type = inferred
        part.save(update_fields=['part_type', 'updated_at'])
        _sync_part_detail(part)
        changed += 1

    return changed, merged


@shared_task
def run_scraper_task():
    """
    定期的に実行されるスクレイパータスク（PC工房版）
    """
    status, _ = ScraperStatus.objects.get_or_create(id=1)

    try:
        logger.info('scraper_task_started source=pckoubou_parts')
        scraped_parts = scrape_pckoubou_all(max_items_per_category=500)

        saved_count = 0
        normalized_count = 0
        merged_count = 0
        with transaction.atomic():
            upserted_parts = []
            for part in scraped_parts:
                upserted, created = PCPart.objects.update_or_create(
                    part_type=part['part_type'],
                    name=part['name'],
                    defaults={
                        'price': part['price'],
                        'url': part['url'],
                        'specs': part.get('specs', {'source': 'pckoubou'}),
                        'stock_status': part.get('stock_status', 'unknown'),
                        'is_active': part.get('is_active', True),
                    },
                )
                upserted_parts.append(upserted)
                saved_count += 1 if created else 0

            _sync_details_for_parts(upserted_parts)

        normalized_count, merged_count = _normalize_part_types()
        gpu_perf_result = {}
        market_range_result = {}
        cpu_selection_result = {}
        try:
            gpu_perf_result = import_gpu_performance_scores_task(timeout=20)
        except Exception:
            logger.exception('gpu_perf_import_failed source=dospara_gpu_performance')
            gpu_perf_result = {'status': 'error'}

        try:
            market_range_result = import_market_price_range_task(timeout=20)
        except Exception:
            logger.exception('market_range_import_failed source=dospara_tc30_market')
            market_range_result = {'status': 'error'}

        try:
            cpu_selection_result = import_cpu_selection_material_task(timeout=20)
        except Exception:
            logger.exception('cpu_selection_import_failed source=dospara_cpu_comparison_pages')
            cpu_selection_result = {'status': 'error'}

        status.last_run = timezone.now()
        status.next_run = timezone.now() + timedelta(days=1)
        status.total_scraped = len(scraped_parts)
        status.success_count += 1
        status.save()

        updated_count = len(scraped_parts) - saved_count
        logger.info(
            'scraper_task_completed source=pckoubou_parts fetched=%s created=%s updated=%s normalized=%s merged=%s gpu_perf=%s market_range=%s cpu_selection=%s',
            len(scraped_parts),
            saved_count,
            updated_count,
            normalized_count,
            merged_count,
            gpu_perf_result,
            market_range_result,
            cpu_selection_result,
        )

        return {
            'status': 'success',
            'source': 'pckoubou_parts',
            'fetched': len(scraped_parts),
            'created': saved_count,
            'updated': updated_count,
            'normalized': normalized_count,
            'merged': merged_count,
            'gpu_perf': gpu_perf_result,
            'market_range': market_range_result,
            'cpu_selection': cpu_selection_result,
        }
    except Exception as e:
        status.last_run = timezone.now()
        status.error_count += 1
        status.save(update_fields=['last_run', 'error_count', 'updated_at'])
        logger.exception('scraper_task_failed source=pckoubou_parts error=%s', e)
        return {'status': 'error', 'message': str(e)}


@shared_task
def update_scraper_status(total=0, success=0, error=0):
    """
    スクレイパー状態を更新
    """
    status, _ = ScraperStatus.objects.get_or_create(id=1)
    status.last_run = timezone.now()
    status.total_scraped = total
    status.success_count = success
    status.error_count = error
    status.save()
    return {'status': 'updated'}
