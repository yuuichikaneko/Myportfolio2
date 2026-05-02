from django.contrib import admin
from .models import (
    Configuration,
    GPUPerformanceEntry,
    GPUPerformanceSnapshot,
    PCPart,
    ScraperStatus,
)


@admin.register(PCPart)
class PCPartAdmin(admin.ModelAdmin):
    list_display = ['name', 'part_type', 'price', 'updated_at']
    list_filter = ['part_type', 'updated_at']
    search_fields = ['name']
    readonly_fields = ['scraped_at', 'updated_at']


@admin.register(Configuration)
class ConfigurationAdmin(admin.ModelAdmin):
    list_display = ['id', 'usage', 'budget', 'total_price', 'created_at']
    list_filter = ['usage', 'created_at']
    readonly_fields = ['created_at']


@admin.register(ScraperStatus)
class ScraperStatusAdmin(admin.ModelAdmin):
    list_display = ['last_run', 'total_scraped', 'success_count', 'error_count', 'cache_enabled']
    readonly_fields = ['updated_at']


@admin.register(GPUPerformanceSnapshot)
class GPUPerformanceSnapshotAdmin(admin.ModelAdmin):
    list_display = ['id', 'source_name', 'updated_at_source', 'parser_version', 'fetched_at']
    list_filter = ['source_name', 'parser_version', 'fetched_at']
    search_fields = ['source_name', 'source_url']
    readonly_fields = ['fetched_at']


@admin.register(GPUPerformanceEntry)
class GPUPerformanceEntryAdmin(admin.ModelAdmin):
    list_display = ['gpu_name', 'model_key', 'vendor', 'vram_gb', 'perf_score', 'is_laptop', 'snapshot']
    list_filter = ['vendor', 'is_laptop', 'snapshot']
    search_fields = ['gpu_name', 'model_key']

