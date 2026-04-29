from rest_framework import serializers
from .models import PCPart, Configuration, ScraperStatus


def _classify_budget_tier(budget):
    if budget <= 220000:
        return 'low'
    if budget <= 300000:
        return 'middle'
    if budget <= 500000:
        return 'high'
    return 'premium'


def _budget_tier_label_jp(budget_tier):
    return {
        'low': 'ローエンド',
        'middle': 'ミドル',
        'high': 'ハイエンド',
        'premium': 'プレミアム',
    }.get(budget_tier, '不明')


class PCPartSerializer(serializers.ModelSerializer):
    part_type_display = serializers.CharField(source='get_part_type_display', read_only=True)
    
    class Meta:
        model = PCPart
        fields = ['id', 'part_type', 'part_type_display', 'name', 'price', 'specs', 'url', 'scraped_at', 'updated_at']
        read_only_fields = ['id', 'scraped_at', 'updated_at']


class ConfigurationSerializer(serializers.ModelSerializer):
    usage_display = serializers.CharField(source='get_usage_display', read_only=True)
    budget_tier = serializers.SerializerMethodField()
    budget_tier_label = serializers.SerializerMethodField()
    cpu_data = PCPartSerializer(source='cpu', read_only=True)
    cpu_cooler_data = PCPartSerializer(source='cpu_cooler', read_only=True)
    gpu_data = PCPartSerializer(source='gpu', read_only=True)
    motherboard_data = PCPartSerializer(source='motherboard', read_only=True)
    memory_data = PCPartSerializer(source='memory', read_only=True)
    storage_data = PCPartSerializer(source='storage', read_only=True)
    storage2_data = PCPartSerializer(source='storage2', read_only=True)
    storage3_data = PCPartSerializer(source='storage3', read_only=True)
    os_data = PCPartSerializer(source='os', read_only=True)
    psu_data = PCPartSerializer(source='psu', read_only=True)
    case_data = PCPartSerializer(source='case', read_only=True)
    case_fan_data = PCPartSerializer(source='case_fan', read_only=True)

    class Meta:
        model = Configuration
        fields = [
            'id', 'name', 'budget', 'budget_tier', 'budget_tier_label', 'usage', 'usage_display', 'total_price',
            'cpu', 'cpu_cooler', 'gpu', 'motherboard', 'memory', 'storage', 'storage2', 'storage3', 'os', 'psu', 'case', 'case_fan',
            'cpu_data', 'cpu_cooler_data', 'gpu_data', 'motherboard_data', 'memory_data', 'storage_data', 'storage2_data', 'storage3_data', 'os_data', 'psu_data', 'case_data', 'case_fan_data',
            'created_at'
        ]
        read_only_fields = ['id', 'total_price', 'created_at']

    def get_budget_tier(self, obj):
        return _classify_budget_tier(int(getattr(obj, 'budget', 0) or 0))

    def get_budget_tier_label(self, obj):
        return _budget_tier_label_jp(self.get_budget_tier(obj))


class ScraperStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScraperStatus
        fields = ['id', 'last_run', 'next_run', 'total_scraped', 'success_count', 'error_count', 'cache_enabled', 'cache_ttl_seconds', 'updated_at']
        read_only_fields = ['id', 'updated_at']
