from django.db import migrations


def _to_int(value):
	if value is None or value == '':
		return None
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return None


def _backfill_pcpart_normalized_fields(apps, schema_editor):
	PCPart = apps.get_model('scraper', 'PCPart')

	for part in PCPart.objects.all().iterator():
		specs = part.specs if isinstance(part.specs, dict) else {}

		if not part.maker and part.name:
			part.maker = part.name.split()[0]

		part.dospara_code = specs.get('code') or part.dospara_code
		part.model_code = specs.get('model_code') or part.model_code
		part.socket = specs.get('socket') or part.socket
		part.memory_type = specs.get('memory_type') or part.memory_type
		part.chipset = specs.get('chipset') or part.chipset
		part.form_factor = specs.get('form_factor') or part.form_factor
		part.vram_type = specs.get('vram_type') or part.vram_type
		part.efficiency_grade = specs.get('efficiency_grade') or part.efficiency_grade
		part.interface = specs.get('interface') or part.interface

		if specs.get('cores') is not None:
			part.cores = _to_int(specs.get('cores'))
		if specs.get('threads') is not None:
			part.threads = _to_int(specs.get('threads'))
		if specs.get('tdp_w') is not None:
			part.tdp_w = _to_int(specs.get('tdp_w'))
		if specs.get('base_clock_mhz') is not None:
			part.base_clock_mhz = _to_int(specs.get('base_clock_mhz'))
		if specs.get('boost_clock_mhz') is not None:
			part.boost_clock_mhz = _to_int(specs.get('boost_clock_mhz'))
		if specs.get('vram_gb') is not None:
			part.vram_gb = _to_int(specs.get('vram_gb'))
		if specs.get('wattage') is not None:
			part.wattage = _to_int(specs.get('wattage'))
		if specs.get('capacity_gb') is not None:
			part.capacity_gb = _to_int(specs.get('capacity_gb'))
		if specs.get('speed_mhz') is not None:
			part.speed_mhz = _to_int(specs.get('speed_mhz'))
		if specs.get('m2_slots') is not None:
			part.m2_slots = _to_int(specs.get('m2_slots'))
		if specs.get('pcie_x16_slots') is not None:
			part.pcie_x16_slots = _to_int(specs.get('pcie_x16_slots'))
		if specs.get('usb_total') is not None:
			part.usb_total = _to_int(specs.get('usb_total'))
		if specs.get('type_c_ports') is not None:
			part.type_c_ports = _to_int(specs.get('type_c_ports'))
		if specs.get('included_fan_count') is not None:
			part.included_fan_count = _to_int(specs.get('included_fan_count'))
		if specs.get('supported_fan_count') is not None:
			part.supported_fan_count = _to_int(specs.get('supported_fan_count'))
		if specs.get('max_tdp_w') is not None:
			part.max_tdp_w = _to_int(specs.get('max_tdp_w'))

		if part.part_type == 'os' and part.name:
			lowered = part.name.lower()
			if 'windows' in lowered and not part.os_family:
				part.os_family = 'windows'
			if 'pro' in lowered and not part.os_edition:
				part.os_edition = 'pro'
			elif 'home' in lowered and not part.os_edition:
				part.os_edition = 'home'

		part.save(update_fields=[
			'maker',
			'model_code',
			'dospara_code',
			'socket',
			'memory_type',
			'chipset',
			'form_factor',
			'cores',
			'threads',
			'tdp_w',
			'base_clock_mhz',
			'boost_clock_mhz',
			'vram_gb',
			'vram_type',
			'wattage',
			'efficiency_grade',
			'capacity_gb',
			'speed_mhz',
			'interface',
			'm2_slots',
			'pcie_x16_slots',
			'usb_total',
			'type_c_ports',
			'included_fan_count',
			'supported_fan_count',
			'max_tdp_w',
			'os_family',
			'os_edition',
			'updated_at',
		])


class Migration(migrations.Migration):

	dependencies = [
		('scraper', '0006_pcpart_base_clock_mhz_pcpart_boost_clock_mhz_and_more'),
	]

	operations = [
		migrations.RunPython(_backfill_pcpart_normalized_fields, migrations.RunPython.noop),
	]
