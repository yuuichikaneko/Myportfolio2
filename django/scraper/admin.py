from django.contrib import admin
from django.template.response import TemplateResponse
from .models import (
    Configuration,
    GPUPerformanceEntry,
    GPUPerformanceSnapshot,
    PCPart,
    ScraperStatus,
)
from .views import (
    GENERAL_HOME_CPU_ALLOWED_TIERS_BY_BUDGET,
    GENERAL_HOME_CPU_NAME_PATTERNS_BY_TIER,
)


def general_cpu_tier_table_admin_view(request):
    budget_order = ['low', 'middle', 'high', 'premium']
    tier_order = ['entry', 'mainstream', 'performance', 'enthusiast']

    budget_rows = [
        {
            'budget_tier': budget_tier,
            'allowed_tiers': [t for t in tier_order if t in GENERAL_HOME_CPU_ALLOWED_TIERS_BY_BUDGET.get(budget_tier, set())],
        }
        for budget_tier in budget_order
    ]

    pattern_rows = [
        {
            'tier': tier,
            'patterns': list(GENERAL_HOME_CPU_NAME_PATTERNS_BY_TIER.get(tier, ())),
        }
        for tier in tier_order
    ]

    context = {
        **admin.site.each_context(request),
        'title': '汎用CPUティア表',
        'budget_rows': budget_rows,
        'pattern_rows': pattern_rows,
        'tier_order': tier_order,
        'budget_order': budget_order,
        'rule_notes': [
            'Intel 第13/14世代は候補から除外します。',
            'non-premium では premium 専用CPUを除外します。',
            'non-premium で Core Ultra 250系と265系が同時候補なら 250系を優先します。',
            'premium のみ AM5/LGA1851 のCPUを許可します。',
        ],
    }
    return TemplateResponse(request, 'admin/general_cpu_tier_table.html', context)


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

