from __future__ import annotations

from importlib import import_module
from math import ceil
from typing import Any, Dict, Tuple

from flask import Flask, Response, request
from flask_cors import CORS

from django.core.serializers.json import DjangoJSONEncoder

from .django_bridge import bootstrap_django

bootstrap_django()


def _imports() -> dict[str, Any]:
    dospara_scraper = import_module("scraper.dospara_scraper")
    models = import_module("scraper.models")
    serializers = import_module("scraper.serializers")
    views = import_module("scraper.views")

    return {
        "fetch_dospara_market_price_range": getattr(dospara_scraper, "fetch_dospara_market_price_range"),
        "Configuration": getattr(models, "Configuration"),
        "GPUPerformanceEntry": getattr(models, "GPUPerformanceEntry"),
        "GPUPerformanceSnapshot": getattr(models, "GPUPerformanceSnapshot"),
        "PCPart": getattr(models, "PCPart"),
        "ScraperStatus": getattr(models, "ScraperStatus"),
        "ConfigurationSerializer": getattr(serializers, "ConfigurationSerializer"),
        "PCPartSerializer": getattr(serializers, "PCPartSerializer"),
        "ScraperStatusSerializer": getattr(serializers, "ScraperStatusSerializer"),
        "_build_storage_inventory_summary": getattr(views, "_build_storage_inventory_summary"),
        "build_configuration_response": getattr(views, "build_configuration_response"),
        "build_scraper_status_summary": getattr(views, "build_scraper_status_summary"),
    }


def _json_response(payload: Any, status: int = 200) -> Response:
    import json

    body = json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False)
    return Response(body, status=status, mimetype="application/json")


def _to_error_payload(error_response: Any) -> Tuple[Dict[str, Any], int]:
    if error_response is None:
        return {}, 500

    status_code = int(getattr(error_response, "status_code", 400))
    data = getattr(error_response, "data", {"detail": "Bad Request"})
    if isinstance(data, dict):
        return data, status_code
    return {"detail": data}, status_code


def _paginate(items: list[dict[str, Any]]) -> dict[str, Any]:
    page = request.args.get("page", default=1, type=int)
    page_size = request.args.get("page_size", default=200, type=int)

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 200

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    results = items[start:end]

    total_pages = ceil(total / page_size) if total else 1
    next_url = None
    prev_url = None

    if page < total_pages:
        next_url = f"{request.base_url}?page={page + 1}&page_size={page_size}"
    if page > 1:
        prev_url = f"{request.base_url}?page={page - 1}&page_size={page_size}"

    return {
        "count": total,
        "next": next_url,
        "previous": prev_url,
        "results": results,
    }


def _recalculate_total_price(config: Any) -> None:
    total = 0
    for field in ["cpu", "cpu_cooler", "gpu", "motherboard", "memory", "storage", "os", "psu", "case"]:
        part = getattr(config, field)
        if part:
            total += int(part.price)
    for field in ["storage2", "storage3"]:
        part = getattr(config, field, None)
        if part:
            total += int(part.price)

    config.total_price = total
    config.save(update_fields=["total_price"])


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    refs = _imports()
    PCPart = refs["PCPart"]
    Configuration = refs["Configuration"]
    GPUPerformanceSnapshot = refs["GPUPerformanceSnapshot"]
    GPUPerformanceEntry = refs["GPUPerformanceEntry"]
    ScraperStatus = refs["ScraperStatus"]
    PCPartSerializer = refs["PCPartSerializer"]
    ConfigurationSerializer = refs["ConfigurationSerializer"]
    ScraperStatusSerializer = refs["ScraperStatusSerializer"]
    build_configuration_response = refs["build_configuration_response"]
    build_scraper_status_summary = refs["build_scraper_status_summary"]
    fetch_dospara_market_price_range = refs["fetch_dospara_market_price_range"]
    _build_storage_inventory_summary = refs["_build_storage_inventory_summary"]

    @app.get("/health")
    def health() -> Response:
        return _json_response({"status": "ok", "service": "flask-rest-bridge"})

    @app.get("/api/parts/")
    def list_parts() -> Response:
        queryset = PCPart.objects.all()
        part_type = request.args.get("part_type")
        search = request.args.get("search")

        if part_type:
            queryset = queryset.filter(part_type=part_type)
        if search:
            queryset = queryset.filter(name__icontains=search)

        data = PCPartSerializer(queryset, many=True).data
        return _json_response(_paginate(list(data)))

    @app.post("/api/parts/")
    def create_part() -> Response:
        payload = request.get_json(silent=True) or {}
        serializer = PCPartSerializer(data=payload)
        if not serializer.is_valid():
            return _json_response(serializer.errors, 400)
        serializer.save()
        return _json_response(serializer.data, 201)

    @app.get("/api/parts/by_type/")
    def list_parts_by_type() -> Response:
        part_type = request.args.get("type")
        if not part_type:
            return _json_response({"error": "type parameter required"}, 400)

        queryset = PCPart.objects.filter(part_type=part_type)
        data = PCPartSerializer(queryset, many=True).data
        return _json_response(data)

    @app.get("/api/parts/<int:part_id>/")
    def get_part(part_id: int) -> Response:
        try:
            part = PCPart.objects.get(pk=part_id)
        except PCPart.DoesNotExist:
            return _json_response({"detail": "Not found."}, 404)

        return _json_response(PCPartSerializer(part).data)

    @app.route("/api/parts/<int:part_id>/", methods=["PUT", "PATCH"])
    def update_part(part_id: int) -> Response:
        try:
            part = PCPart.objects.get(pk=part_id)
        except PCPart.DoesNotExist:
            return _json_response({"detail": "Not found."}, 404)

        payload = request.get_json(silent=True) or {}
        partial = request.method == "PATCH"
        serializer = PCPartSerializer(part, data=payload, partial=partial)
        if not serializer.is_valid():
            return _json_response(serializer.errors, 400)
        serializer.save()
        return _json_response(serializer.data)

    @app.delete("/api/parts/<int:part_id>/")
    def delete_part(part_id: int) -> Response:
        deleted, _ = PCPart.objects.filter(pk=part_id).delete()
        if not deleted:
            return _json_response({"detail": "Not found."}, 404)
        return Response(status=204)

    @app.get("/api/configurations/")
    def list_configurations() -> Response:
        queryset = Configuration.objects.filter(is_deleted=False)
        data = ConfigurationSerializer(queryset, many=True).data
        return _json_response(_paginate(list(data)))

    @app.post("/api/configurations/")
    def create_configuration() -> Response:
        payload = request.get_json(silent=True) or {}
        serializer = ConfigurationSerializer(data=payload)
        if not serializer.is_valid():
            return _json_response(serializer.errors, 400)

        config = serializer.save()
        _recalculate_total_price(config)
        return _json_response(ConfigurationSerializer(config).data, 201)

    @app.get("/api/configurations/<int:config_id>/")
    def get_configuration(config_id: int) -> Response:
        try:
            config = Configuration.objects.get(pk=config_id, is_deleted=False)
        except Configuration.DoesNotExist:
            return _json_response({"detail": "Not found."}, 404)

        return _json_response(ConfigurationSerializer(config).data)

    @app.route("/api/configurations/<int:config_id>/", methods=["PUT", "PATCH"])
    def update_configuration(config_id: int) -> Response:
        try:
            config = Configuration.objects.get(pk=config_id, is_deleted=False)
        except Configuration.DoesNotExist:
            return _json_response({"detail": "Not found."}, 404)

        payload = request.get_json(silent=True) or {}
        partial = request.method == "PATCH"
        serializer = ConfigurationSerializer(config, data=payload, partial=partial)
        if not serializer.is_valid():
            return _json_response(serializer.errors, 400)

        config = serializer.save()
        _recalculate_total_price(config)
        return _json_response(ConfigurationSerializer(config).data)

    @app.delete("/api/configurations/<int:config_id>/")
    def delete_configuration(config_id: int) -> Response:
        try:
            config = Configuration.objects.get(pk=config_id, is_deleted=False)
        except Configuration.DoesNotExist:
            return _json_response({"detail": "Not found."}, 404)

        config.soft_delete()
        return Response(status=204)

    @app.post("/api/configurations/generate/")
    def generate_configuration() -> Response:
        payload = request.get_json(silent=True) or {}
        response_data, error_response = build_configuration_response(
            payload.get("budget"),
            payload.get("usage"),
            payload.get("cooler_type"),
            payload.get("radiator_size"),
            payload.get("cooling_profile"),
            payload.get("case_size"),
            payload.get("case_fan_policy"),
            payload.get("cpu_vendor"),
            payload.get("build_priority"),
            payload.get("storage_preference"),
            payload.get("storage2_part_id"),
            payload.get("storage3_part_id"),
            payload.get("os_edition"),
            payload.get("custom_budget_weights"),
            payload.get("min_storage_capacity_gb"),
            payload.get("max_motherboard_chipset"),
            configuration_name=payload.get("name"),
        )
        if error_response:
            error_payload, status_code = _to_error_payload(error_response)
            return _json_response(error_payload, status_code)
        return _json_response(response_data)

    @app.get("/api/scraper-status/")
    def list_scraper_status() -> Response:
        queryset = ScraperStatus.objects.all()
        data = ScraperStatusSerializer(queryset, many=True).data
        return _json_response(_paginate(list(data)))

    @app.get("/api/scraper-status/summary/")
    def scraper_status_summary() -> Response:
        return _json_response(build_scraper_status_summary())

    @app.get("/api/market-price-range/")
    def market_price_range() -> Response:
        data = fetch_dospara_market_price_range(timeout=15)
        return _json_response(data)

    @app.get("/api/gpu-performance/latest/")
    def latest_gpu_performance() -> Response:
        latest = GPUPerformanceSnapshot.objects.order_by("-fetched_at", "-id").first()
        if not latest:
            return _json_response({"detail": "GPU performance snapshot not found."}, 404)

        entries = GPUPerformanceEntry.objects.filter(snapshot=latest, is_laptop=False).order_by("-perf_score", "gpu_name")
        payload = []
        for entry in entries:
            payload.append(
                {
                    "gpu_name": entry.gpu_name,
                    "model_key": entry.model_key,
                    "vendor": entry.vendor,
                    "vram_gb": entry.vram_gb,
                    "perf_score": entry.perf_score,
                    "detail_url": entry.detail_url,
                    "rank_global": entry.rank_global,
                }
            )

        return _json_response(
            {
                "snapshot": {
                    "id": latest.id,
                    "source_name": latest.source_name,
                    "source_url": latest.source_url,
                    "updated_at_source": latest.updated_at_source,
                    "score_note": latest.score_note,
                    "parser_version": latest.parser_version,
                    "fetched_at": latest.fetched_at,
                },
                "entries": _paginate(payload),
            }
        )

    @app.get("/api/gpu-performance/compare/")
    def compare_gpu_performance() -> Response:
        latest = GPUPerformanceSnapshot.objects.order_by("-fetched_at", "-id").first()
        if not latest:
            return _json_response({"detail": "GPU performance snapshot not found."}, 404)

        models_query = request.args.get("models", default="", type=str)
        if not models_query.strip():
            return _json_response({"detail": "models query parameter required."}, 400)

        requested_models = [m.strip().upper() for m in models_query.split(",") if m.strip()]
        if not requested_models:
            return _json_response({"detail": "at least one model is required."}, 400)

        entries = (
            GPUPerformanceEntry.objects.filter(snapshot=latest, is_laptop=False, model_key__in=requested_models)
            .order_by("-perf_score", "gpu_name")
        )

        items = []
        matched_models = set()
        for entry in entries:
            matched_models.add(entry.model_key)
            items.append(
                {
                    "gpu_name": entry.gpu_name,
                    "model_key": entry.model_key,
                    "vendor": entry.vendor,
                    "vram_gb": entry.vram_gb,
                    "perf_score": entry.perf_score,
                    "detail_url": entry.detail_url,
                    "rank_global": entry.rank_global,
                }
            )

        missing_models = [model for model in requested_models if model not in matched_models]

        return _json_response(
            {
                "snapshot_id": latest.id,
                "requested_models": requested_models,
                "missing_models": missing_models,
                "results": items,
            }
        )

    @app.get("/api/part-price-ranges/")
    def part_price_ranges() -> Response:
        from django.db.models import Avg, Count, Max, Min

        part_type_labels = {
            "cpu": "CPU",
            "cpu_cooler": "CPUクーラー",
            "gpu": "GPU",
            "motherboard": "マザーボード",
            "memory": "メモリ",
            "storage": "ストレージ",
            "os": "OS",
            "psu": "電源ユニット",
            "case": "PCケース",
        }

        result = {}
        for pt, label in part_type_labels.items():
            agg = PCPart.objects.filter(part_type=pt).aggregate(
                min_price=Min("price"),
                max_price=Max("price"),
                avg_price=Avg("price"),
                total=Count("id"),
            )
            result[pt] = {
                "label": label,
                "min": agg["min_price"],
                "max": agg["max_price"],
                "avg": int(agg["avg_price"]) if agg["avg_price"] else None,
                "count": agg["total"],
            }

        return _json_response(result)

    @app.get("/api/storage-inventory/")
    def storage_inventory() -> Response:
        return _json_response(_build_storage_inventory_summary())

    @app.post("/api/generate-config/")
    def compat_generate_config() -> Response:
        return generate_configuration()

    @app.get("/api/scraper/status")
    def compat_scraper_status() -> Response:
        return scraper_status_summary()

    @app.post("/generate-config")
    def legacy_generate_config() -> Response:
        return generate_configuration()

    @app.get("/scraper/status")
    def legacy_scraper_status() -> Response:
        return scraper_status_summary()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8002, debug=True)
