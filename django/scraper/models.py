from django.db import models
from django.utils import timezone


class Manufacturer(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class SocketType(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class MemoryType(models.Model):
    name = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.name


class FormFactor(models.Model):
    name = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.name


class InterfaceType(models.Model):
    name = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.name


class EfficiencyGrade(models.Model):
    name = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.name


class OSFamily(models.Model):
    name = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.name


class OSEdition(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class LicenseType(models.Model):
    name = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.name

class PCPart(models.Model):
    """PC パーツモデル"""
    PART_CHOICES = [
        ('cpu', 'CPU'),
        ('cpu_cooler', 'CPU Cooler'),
        ('gpu', 'GPU'),
        ('motherboard', 'Motherboard'),
        ('memory', 'Memory'),
        ('storage', 'Storage'),
        ('os', 'OS'),
        ('psu', 'Power Supply'),
        ('case', 'Case'),
        ('case_fan', 'Case Fan'),
    ]
    
    part_type = models.CharField(max_length=20, choices=PART_CHOICES)
    name = models.CharField(max_length=200)
    price = models.IntegerField()
    specs = models.JSONField(default=dict)
    url = models.URLField()
    maker = models.CharField(max_length=100, blank=True, db_index=True)
    manufacturer = models.ForeignKey(Manufacturer, on_delete=models.SET_NULL, null=True, blank=True, related_name='parts')
    model_code = models.CharField(max_length=120, blank=True, db_index=True)
    shop_code = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    socket = models.CharField(max_length=50, blank=True, db_index=True)
    memory_type = models.CharField(max_length=20, blank=True, db_index=True)
    chipset = models.CharField(max_length=50, blank=True, db_index=True)
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    cores = models.PositiveIntegerField(null=True, blank=True)
    threads = models.PositiveIntegerField(null=True, blank=True)
    tdp_w = models.PositiveIntegerField(null=True, blank=True)
    base_clock_mhz = models.PositiveIntegerField(null=True, blank=True)
    boost_clock_mhz = models.PositiveIntegerField(null=True, blank=True)
    vram_gb = models.PositiveIntegerField(null=True, blank=True)
    vram_type = models.CharField(max_length=20, blank=True)
    wattage = models.PositiveIntegerField(null=True, blank=True)
    efficiency_grade = models.CharField(max_length=20, blank=True)
    capacity_gb = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    speed_mhz = models.PositiveIntegerField(null=True, blank=True)
    interface = models.CharField(max_length=30, blank=True, db_index=True)
    m2_slots = models.PositiveIntegerField(null=True, blank=True)
    pcie_x16_slots = models.PositiveIntegerField(null=True, blank=True)
    usb_total = models.PositiveIntegerField(null=True, blank=True)
    type_c_ports = models.PositiveIntegerField(null=True, blank=True)
    included_fan_count = models.PositiveIntegerField(null=True, blank=True)
    supported_fan_count = models.PositiveIntegerField(null=True, blank=True)
    max_tdp_w = models.PositiveIntegerField(null=True, blank=True)
    os_family = models.CharField(max_length=30, blank=True)
    os_edition = models.CharField(max_length=50, blank=True)
    license_type = models.CharField(max_length=30, blank=True)
    currency = models.CharField(max_length=3, default='JPY')
    stock_status = models.CharField(max_length=20, default='unknown')
    is_active = models.BooleanField(default=True, db_index=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    scraped_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('part_type', 'name')
        ordering = ['-updated_at']

    @staticmethod
    def _to_int(value):
        if value is None or value == '':
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _sync_normalized_fields(self):
        specs = self.specs if isinstance(self.specs, dict) else {}

        if not self.maker and self.name:
            self.maker = self.name.split()[0]

        if self.maker and not self.manufacturer:
            manufacturer, _ = Manufacturer.objects.get_or_create(name=self.maker)
            self.manufacturer = manufacturer

        self.shop_code = specs.get('code') or self.shop_code
        self.model_code = specs.get('model_code') or self.model_code
        self.socket = specs.get('socket') or self.socket
        self.memory_type = specs.get('memory_type') or self.memory_type
        self.chipset = specs.get('chipset') or self.chipset
        self.form_factor = specs.get('form_factor') or self.form_factor
        self.vram_type = specs.get('vram_type') or self.vram_type
        self.efficiency_grade = specs.get('efficiency_grade') or self.efficiency_grade
        self.interface = specs.get('interface') or self.interface

        self.cores = self._to_int(specs.get('cores')) if specs.get('cores') is not None else self.cores
        self.threads = self._to_int(specs.get('threads')) if specs.get('threads') is not None else self.threads
        self.tdp_w = self._to_int(specs.get('tdp_w')) if specs.get('tdp_w') is not None else self.tdp_w
        self.base_clock_mhz = self._to_int(specs.get('base_clock_mhz')) if specs.get('base_clock_mhz') is not None else self.base_clock_mhz
        self.boost_clock_mhz = self._to_int(specs.get('boost_clock_mhz')) if specs.get('boost_clock_mhz') is not None else self.boost_clock_mhz
        self.vram_gb = self._to_int(specs.get('vram_gb')) if specs.get('vram_gb') is not None else self.vram_gb
        self.wattage = self._to_int(specs.get('wattage')) if specs.get('wattage') is not None else self.wattage
        self.capacity_gb = self._to_int(specs.get('capacity_gb')) if specs.get('capacity_gb') is not None else self.capacity_gb
        self.speed_mhz = self._to_int(specs.get('speed_mhz')) if specs.get('speed_mhz') is not None else self.speed_mhz
        self.m2_slots = self._to_int(specs.get('m2_slots')) if specs.get('m2_slots') is not None else self.m2_slots
        self.pcie_x16_slots = self._to_int(specs.get('pcie_x16_slots')) if specs.get('pcie_x16_slots') is not None else self.pcie_x16_slots
        self.usb_total = self._to_int(specs.get('usb_total')) if specs.get('usb_total') is not None else self.usb_total
        self.type_c_ports = self._to_int(specs.get('type_c_ports')) if specs.get('type_c_ports') is not None else self.type_c_ports
        self.included_fan_count = self._to_int(specs.get('included_fan_count')) if specs.get('included_fan_count') is not None else self.included_fan_count
        self.supported_fan_count = self._to_int(specs.get('supported_fan_count')) if specs.get('supported_fan_count') is not None else self.supported_fan_count
        self.max_tdp_w = self._to_int(specs.get('max_tdp_w')) if specs.get('max_tdp_w') is not None else self.max_tdp_w

        if self.part_type == 'os' and self.name:
            lowered = self.name.lower()
            if 'windows' in lowered and not self.os_family:
                self.os_family = 'windows'
            if 'pro' in lowered and not self.os_edition:
                self.os_edition = 'pro'
            elif 'home' in lowered and not self.os_edition:
                self.os_edition = 'home'

    def save(self, *args, **kwargs):
        self._sync_normalized_fields()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.get_part_type_display()} - {self.name}"


class CPUDetail(models.Model):
    """CPU 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='cpu_detail')
    socket = models.CharField(max_length=50, blank=True, db_index=True)
    memory_type = models.CharField(max_length=20, blank=True, db_index=True)
    socket_ref = models.ForeignKey(SocketType, on_delete=models.SET_NULL, null=True, blank=True, related_name='cpu_details')
    memory_type_ref = models.ForeignKey(MemoryType, on_delete=models.SET_NULL, null=True, blank=True, related_name='cpu_details')
    cores = models.PositiveIntegerField(null=True, blank=True)
    threads = models.PositiveIntegerField(null=True, blank=True)
    tdp_w = models.PositiveIntegerField(null=True, blank=True)
    base_clock_mhz = models.PositiveIntegerField(null=True, blank=True)
    boost_clock_mhz = models.PositiveIntegerField(null=True, blank=True)


class GPUDetail(models.Model):
    """GPU 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='gpu_detail')
    vram_gb = models.PositiveIntegerField(null=True, blank=True)
    vram_type = models.CharField(max_length=20, blank=True)
    tdp_w = models.PositiveIntegerField(null=True, blank=True)
    interface = models.CharField(max_length=30, blank=True, db_index=True)
    interface_ref = models.ForeignKey(InterfaceType, on_delete=models.SET_NULL, null=True, blank=True, related_name='gpu_details')


class MotherboardDetail(models.Model):
    """Motherboard 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='motherboard_detail')
    socket = models.CharField(max_length=50, blank=True, db_index=True)
    memory_type = models.CharField(max_length=20, blank=True, db_index=True)
    socket_ref = models.ForeignKey(SocketType, on_delete=models.SET_NULL, null=True, blank=True, related_name='motherboard_details')
    memory_type_ref = models.ForeignKey(MemoryType, on_delete=models.SET_NULL, null=True, blank=True, related_name='motherboard_details')
    chipset = models.CharField(max_length=50, blank=True, db_index=True)
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    form_factor_ref = models.ForeignKey(FormFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name='motherboard_details')
    m2_slots = models.PositiveIntegerField(null=True, blank=True)
    pcie_x16_slots = models.PositiveIntegerField(null=True, blank=True)
    usb_total = models.PositiveIntegerField(null=True, blank=True)
    type_c_ports = models.PositiveIntegerField(null=True, blank=True)


class MemoryDetail(models.Model):
    """Memory 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='memory_detail')
    memory_type = models.CharField(max_length=20, blank=True, db_index=True)
    memory_type_ref = models.ForeignKey(MemoryType, on_delete=models.SET_NULL, null=True, blank=True, related_name='memory_details')
    capacity_gb = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    speed_mhz = models.PositiveIntegerField(null=True, blank=True)
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    form_factor_ref = models.ForeignKey(FormFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name='memory_details')


class StorageDetail(models.Model):
    """Storage 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='storage_detail')
    capacity_gb = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    interface = models.CharField(max_length=30, blank=True, db_index=True)
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    interface_ref = models.ForeignKey(InterfaceType, on_delete=models.SET_NULL, null=True, blank=True, related_name='storage_details')
    form_factor_ref = models.ForeignKey(FormFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name='storage_details')


class OSDetail(models.Model):
    """OS 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='os_detail')
    os_family = models.CharField(max_length=30, blank=True)
    os_edition = models.CharField(max_length=50, blank=True)
    license_type = models.CharField(max_length=30, blank=True)
    os_family_ref = models.ForeignKey(OSFamily, on_delete=models.SET_NULL, null=True, blank=True, related_name='os_details')
    os_edition_ref = models.ForeignKey(OSEdition, on_delete=models.SET_NULL, null=True, blank=True, related_name='os_details')
    license_type_ref = models.ForeignKey(LicenseType, on_delete=models.SET_NULL, null=True, blank=True, related_name='os_details')


class PSUDetail(models.Model):
    """PSU 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='psu_detail')
    wattage = models.PositiveIntegerField(null=True, blank=True)
    efficiency_grade = models.CharField(max_length=20, blank=True)
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    efficiency_grade_ref = models.ForeignKey(EfficiencyGrade, on_delete=models.SET_NULL, null=True, blank=True, related_name='psu_details')
    form_factor_ref = models.ForeignKey(FormFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name='psu_details')


class CaseDetail(models.Model):
    """Case 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='case_detail')
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    form_factor_ref = models.ForeignKey(FormFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name='case_details')
    included_fan_count = models.PositiveIntegerField(null=True, blank=True)
    supported_fan_count = models.PositiveIntegerField(null=True, blank=True)


class CPUCoolerDetail(models.Model):
    """CPU Cooler 詳細(2NF分離)"""
    part = models.OneToOneField(PCPart, on_delete=models.CASCADE, related_name='cpu_cooler_detail')
    socket = models.CharField(max_length=50, blank=True, db_index=True)
    socket_ref = models.ForeignKey(SocketType, on_delete=models.SET_NULL, null=True, blank=True, related_name='cpu_cooler_details')
    max_tdp_w = models.PositiveIntegerField(null=True, blank=True)
    form_factor = models.CharField(max_length=30, blank=True, db_index=True)
    form_factor_ref = models.ForeignKey(FormFactor, on_delete=models.SET_NULL, null=True, blank=True, related_name='cpu_cooler_details')


class Configuration(models.Model):
    """PC 構成モデル"""
    USAGE_CHOICES = [
        ('gaming', 'Gaming'),
        ('video_editing', 'Video Editing'),
        ('general', 'General'),
    ]
    
    name = models.CharField(max_length=120, blank=True, default='', db_index=True)
    budget = models.IntegerField()
    usage = models.CharField(max_length=20, choices=USAGE_CHOICES)
    cpu = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_cpu')
    cpu_cooler = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_cpu_cooler')
    gpu = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_gpu')
    motherboard = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_mobo')
    memory = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_memory')
    storage = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_storage')
    storage2 = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, blank=True, related_name='cfg_storage2')
    storage3 = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, blank=True, related_name='cfg_storage3')
    os = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_os')
    psu = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_psu')
    case = models.ForeignKey(PCPart, on_delete=models.SET_NULL, null=True, related_name='cfg_case')
    total_price = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        label = self.name.strip() or self.get_usage_display()
        return f"{label} - ¥{self.total_price}"

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])


class ScraperStatus(models.Model):
    """スクレイパー状態トラッキング"""
    last_run = models.DateTimeField(null=True, blank=True)
    next_run = models.DateTimeField(null=True, blank=True)
    total_scraped = models.IntegerField(default=0)
    success_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    cache_enabled = models.BooleanField(default=True)
    cache_ttl_seconds = models.IntegerField(default=3600)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = 'Scraper Status'
    
    def __str__(self):
        return f"Scraper Status (Last: {self.last_run})"


class MarketPriceRangeSnapshot(models.Model):
    """相場レンジの取得スナップショット。構成生成時は本テーブルを参照する。"""
    source_name = models.CharField(max_length=80, db_index=True, default='dospara_tc30_market')
    market_min = models.IntegerField()
    market_max = models.IntegerField()
    suggested_default = models.IntegerField()
    currency = models.CharField(max_length=3, default='JPY')
    sources = models.JSONField(default=dict, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-fetched_at']

    def __str__(self):
        return f"{self.source_name} @ {self.fetched_at:%Y-%m-%d %H:%M:%S}"


class GPUPerformanceSnapshot(models.Model):
    """GPU性能比較ソースの取り込みスナップショット。"""
    source_name = models.CharField(max_length=80, db_index=True)
    source_url = models.URLField()
    updated_at_source = models.CharField(max_length=40, blank=True)
    score_note = models.CharField(max_length=200, blank=True)
    parser_version = models.CharField(max_length=30, default='v1')
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-fetched_at']

    def __str__(self):
        return f"{self.source_name} @ {self.fetched_at:%Y-%m-%d %H:%M:%S}"


class GPUPerformanceEntry(models.Model):
    """GPU性能比較の正規化エントリ。"""
    VENDOR_CHOICES = [
        ('nvidia', 'NVIDIA'),
        ('amd', 'AMD'),
        ('intel', 'Intel'),
        ('igpu', 'iGPU'),
        ('unknown', 'Unknown'),
    ]

    snapshot = models.ForeignKey(GPUPerformanceSnapshot, on_delete=models.CASCADE, related_name='entries')
    gpu_name = models.CharField(max_length=120)
    model_key = models.CharField(max_length=80, db_index=True)
    vendor = models.CharField(max_length=20, choices=VENDOR_CHOICES, default='unknown', db_index=True)
    vram_gb = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    perf_score = models.PositiveIntegerField()
    detail_url = models.URLField(blank=True)
    is_laptop = models.BooleanField(default=False, db_index=True)
    rank_global = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-perf_score', 'gpu_name']
        unique_together = ('snapshot', 'model_key', 'vram_gb', 'is_laptop')

    def __str__(self):
        suffix = f" {self.vram_gb}GB" if self.vram_gb else ""
        return f"{self.model_key}{suffix} ({self.perf_score})"


class CPUSelectionSnapshot(models.Model):
    """CPU選考資料の取得スナップショット。"""
    source_name = models.CharField(max_length=80, db_index=True)
    source_urls = models.JSONField(default=list, blank=True)
    exclude_intel_13_14 = models.BooleanField(default=True)
    entry_count = models.PositiveIntegerField(default=0)
    excluded_count = models.PositiveIntegerField(default=0)
    parser_version = models.CharField(max_length=30, default='v1')
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-fetched_at']

    def __str__(self):
        return f"{self.source_name} @ {self.fetched_at:%Y-%m-%d %H:%M:%S}"


class CPUSelectionEntry(models.Model):
    """CPU選考資料の正規化エントリ。"""
    snapshot = models.ForeignKey(CPUSelectionSnapshot, on_delete=models.CASCADE, related_name='entries')
    vendor = models.CharField(max_length=20, db_index=True)
    model_name = models.CharField(max_length=120)
    perf_score = models.PositiveIntegerField()
    source_url = models.URLField(blank=True)
    rank_global = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-perf_score', 'model_name']
        unique_together = ('snapshot', 'model_name')

    def __str__(self):
        return f"{self.model_name} ({self.perf_score})"
