from django.db import migrations


def _create_if_missing(model, part_id, defaults):
    model.objects.update_or_create(part_id=part_id, defaults=defaults)


def _backfill_part_details(apps, schema_editor):
    PCPart = apps.get_model('scraper', 'PCPart')
    CPUDetail = apps.get_model('scraper', 'CPUDetail')
    GPUDetail = apps.get_model('scraper', 'GPUDetail')
    MotherboardDetail = apps.get_model('scraper', 'MotherboardDetail')
    MemoryDetail = apps.get_model('scraper', 'MemoryDetail')
    StorageDetail = apps.get_model('scraper', 'StorageDetail')
    OSDetail = apps.get_model('scraper', 'OSDetail')
    PSUDetail = apps.get_model('scraper', 'PSUDetail')
    CaseDetail = apps.get_model('scraper', 'CaseDetail')
    CPUCoolerDetail = apps.get_model('scraper', 'CPUCoolerDetail')

    for part in PCPart.objects.all().iterator():
        if part.part_type == 'cpu':
            _create_if_missing(
                CPUDetail,
                part.id,
                {
                    'socket': part.socket,
                    'memory_type': part.memory_type,
                    'cores': part.cores,
                    'threads': part.threads,
                    'tdp_w': part.tdp_w,
                    'base_clock_mhz': part.base_clock_mhz,
                    'boost_clock_mhz': part.boost_clock_mhz,
                },
            )
        elif part.part_type == 'gpu':
            _create_if_missing(
                GPUDetail,
                part.id,
                {
                    'vram_gb': part.vram_gb,
                    'vram_type': part.vram_type,
                    'tdp_w': part.tdp_w,
                    'interface': part.interface,
                },
            )
        elif part.part_type == 'motherboard':
            _create_if_missing(
                MotherboardDetail,
                part.id,
                {
                    'socket': part.socket,
                    'memory_type': part.memory_type,
                    'chipset': part.chipset,
                    'form_factor': part.form_factor,
                    'm2_slots': part.m2_slots,
                    'pcie_x16_slots': part.pcie_x16_slots,
                    'usb_total': part.usb_total,
                    'type_c_ports': part.type_c_ports,
                },
            )
        elif part.part_type == 'memory':
            _create_if_missing(
                MemoryDetail,
                part.id,
                {
                    'memory_type': part.memory_type,
                    'capacity_gb': part.capacity_gb,
                    'speed_mhz': part.speed_mhz,
                    'form_factor': part.form_factor,
                },
            )
        elif part.part_type == 'storage':
            _create_if_missing(
                StorageDetail,
                part.id,
                {
                    'capacity_gb': part.capacity_gb,
                    'interface': part.interface,
                    'form_factor': part.form_factor,
                },
            )
        elif part.part_type == 'os':
            _create_if_missing(
                OSDetail,
                part.id,
                {
                    'os_family': part.os_family,
                    'os_edition': part.os_edition,
                    'license_type': part.license_type,
                },
            )
        elif part.part_type == 'psu':
            _create_if_missing(
                PSUDetail,
                part.id,
                {
                    'wattage': part.wattage,
                    'efficiency_grade': part.efficiency_grade,
                    'form_factor': part.form_factor,
                },
            )
        elif part.part_type == 'case':
            _create_if_missing(
                CaseDetail,
                part.id,
                {
                    'form_factor': part.form_factor,
                    'included_fan_count': part.included_fan_count,
                    'supported_fan_count': part.supported_fan_count,
                },
            )
        elif part.part_type == 'cpu_cooler':
            _create_if_missing(
                CPUCoolerDetail,
                part.id,
                {
                    'socket': part.socket,
                    'max_tdp_w': part.max_tdp_w,
                    'form_factor': part.form_factor,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0008_storagedetail_psudetail_osdetail_motherboarddetail_and_more'),
    ]

    operations = [
        migrations.RunPython(_backfill_part_details, migrations.RunPython.noop),
    ]
