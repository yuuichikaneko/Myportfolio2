from django.db import migrations


def _get_or_none(model, value):
    if not value:
        return None
    obj, _ = model.objects.get_or_create(name=str(value).strip())
    return obj


def _backfill_3nf_reference_masters(apps, schema_editor):
    PCPart = apps.get_model('scraper', 'PCPart')
    Manufacturer = apps.get_model('scraper', 'Manufacturer')
    SocketType = apps.get_model('scraper', 'SocketType')
    MemoryType = apps.get_model('scraper', 'MemoryType')
    FormFactor = apps.get_model('scraper', 'FormFactor')
    InterfaceType = apps.get_model('scraper', 'InterfaceType')
    EfficiencyGrade = apps.get_model('scraper', 'EfficiencyGrade')
    OSFamily = apps.get_model('scraper', 'OSFamily')
    OSEdition = apps.get_model('scraper', 'OSEdition')
    LicenseType = apps.get_model('scraper', 'LicenseType')

    CPUDetail = apps.get_model('scraper', 'CPUDetail')
    GPUDetail = apps.get_model('scraper', 'GPUDetail')
    MotherboardDetail = apps.get_model('scraper', 'MotherboardDetail')
    MemoryDetail = apps.get_model('scraper', 'MemoryDetail')
    StorageDetail = apps.get_model('scraper', 'StorageDetail')
    OSDetail = apps.get_model('scraper', 'OSDetail')
    PSUDetail = apps.get_model('scraper', 'PSUDetail')
    CaseDetail = apps.get_model('scraper', 'CaseDetail')
    CPUCoolerDetail = apps.get_model('scraper', 'CPUCoolerDetail')

    for p in PCPart.objects.all().iterator():
        m = _get_or_none(Manufacturer, p.maker)
        if m and p.manufacturer_id != m.id:
            p.manufacturer_id = m.id
            p.save(update_fields=['manufacturer'])

    for d in CPUDetail.objects.all().iterator():
        socket = _get_or_none(SocketType, d.socket)
        mem = _get_or_none(MemoryType, d.memory_type)
        updates = []
        if socket and d.socket_ref_id != socket.id:
            d.socket_ref_id = socket.id
            updates.append('socket_ref')
        if mem and d.memory_type_ref_id != mem.id:
            d.memory_type_ref_id = mem.id
            updates.append('memory_type_ref')
        if updates:
            d.save(update_fields=updates)

    for d in GPUDetail.objects.all().iterator():
        iface = _get_or_none(InterfaceType, d.interface)
        if iface and d.interface_ref_id != iface.id:
            d.interface_ref_id = iface.id
            d.save(update_fields=['interface_ref'])

    for d in MotherboardDetail.objects.all().iterator():
        socket = _get_or_none(SocketType, d.socket)
        mem = _get_or_none(MemoryType, d.memory_type)
        ff = _get_or_none(FormFactor, d.form_factor)
        updates = []
        if socket and d.socket_ref_id != socket.id:
            d.socket_ref_id = socket.id
            updates.append('socket_ref')
        if mem and d.memory_type_ref_id != mem.id:
            d.memory_type_ref_id = mem.id
            updates.append('memory_type_ref')
        if ff and d.form_factor_ref_id != ff.id:
            d.form_factor_ref_id = ff.id
            updates.append('form_factor_ref')
        if updates:
            d.save(update_fields=updates)

    for d in MemoryDetail.objects.all().iterator():
        mem = _get_or_none(MemoryType, d.memory_type)
        ff = _get_or_none(FormFactor, d.form_factor)
        updates = []
        if mem and d.memory_type_ref_id != mem.id:
            d.memory_type_ref_id = mem.id
            updates.append('memory_type_ref')
        if ff and d.form_factor_ref_id != ff.id:
            d.form_factor_ref_id = ff.id
            updates.append('form_factor_ref')
        if updates:
            d.save(update_fields=updates)

    for d in StorageDetail.objects.all().iterator():
        iface = _get_or_none(InterfaceType, d.interface)
        ff = _get_or_none(FormFactor, d.form_factor)
        updates = []
        if iface and d.interface_ref_id != iface.id:
            d.interface_ref_id = iface.id
            updates.append('interface_ref')
        if ff and d.form_factor_ref_id != ff.id:
            d.form_factor_ref_id = ff.id
            updates.append('form_factor_ref')
        if updates:
            d.save(update_fields=updates)

    for d in OSDetail.objects.all().iterator():
        fam = _get_or_none(OSFamily, d.os_family)
        edi = _get_or_none(OSEdition, d.os_edition)
        lic = _get_or_none(LicenseType, d.license_type)
        updates = []
        if fam and d.os_family_ref_id != fam.id:
            d.os_family_ref_id = fam.id
            updates.append('os_family_ref')
        if edi and d.os_edition_ref_id != edi.id:
            d.os_edition_ref_id = edi.id
            updates.append('os_edition_ref')
        if lic and d.license_type_ref_id != lic.id:
            d.license_type_ref_id = lic.id
            updates.append('license_type_ref')
        if updates:
            d.save(update_fields=updates)

    for d in PSUDetail.objects.all().iterator():
        eg = _get_or_none(EfficiencyGrade, d.efficiency_grade)
        ff = _get_or_none(FormFactor, d.form_factor)
        updates = []
        if eg and d.efficiency_grade_ref_id != eg.id:
            d.efficiency_grade_ref_id = eg.id
            updates.append('efficiency_grade_ref')
        if ff and d.form_factor_ref_id != ff.id:
            d.form_factor_ref_id = ff.id
            updates.append('form_factor_ref')
        if updates:
            d.save(update_fields=updates)

    for d in CaseDetail.objects.all().iterator():
        ff = _get_or_none(FormFactor, d.form_factor)
        if ff and d.form_factor_ref_id != ff.id:
            d.form_factor_ref_id = ff.id
            d.save(update_fields=['form_factor_ref'])

    for d in CPUCoolerDetail.objects.all().iterator():
        socket = _get_or_none(SocketType, d.socket)
        ff = _get_or_none(FormFactor, d.form_factor)
        updates = []
        if socket and d.socket_ref_id != socket.id:
            d.socket_ref_id = socket.id
            updates.append('socket_ref')
        if ff and d.form_factor_ref_id != ff.id:
            d.form_factor_ref_id = ff.id
            updates.append('form_factor_ref')
        if updates:
            d.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0010_efficiencygrade_formfactor_interfacetype_licensetype_and_more'),
    ]

    operations = [
        migrations.RunPython(_backfill_3nf_reference_masters, migrations.RunPython.noop),
    ]
