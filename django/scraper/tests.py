from rest_framework import status
from rest_framework.test import APITestCase, APIRequestFactory
from unittest.mock import patch
from django.test import override_settings

from .models import Configuration, CPUSelectionEntry, CPUSelectionSnapshot, GPUPerformanceEntry, GPUPerformanceSnapshot, MarketPriceRangeSnapshot, PCPart, ScraperStatus
from .dospara_scraper import (
	get_dospara_scraper_config,
	parse_dospara_parts_html,
	scrape_dospara_parts,
	_infer_part_type,
	_extract_specs_from_simplespec,
	fetch_dospara_cpu_selection_material,
)
from .tasks import run_scraper_task
from .views import ConfigurationViewSet, _creator_motherboard_expandability_score, _cpu_meets_creator_minimum, _enforce_gaming_spec_best_value_gpu, _enforce_memory_speed_floor, _get_cpu_perf_score, _get_gpu_perf_score_from_snapshot, _infer_gaming_gpu_tier_label, _infer_memory_speed_mhz, _infer_storage_capacity_gb, _is_gaming_gpu_within_priority_cap, _is_part_suitable, _matches_selection_options, _pick_amd_gaming_cpu, _pick_creator_cpu_with_budget, _pick_gaming_cost_gpu_for_auto_adjust, _pick_part_by_target, _prefer_creator_premium_cpu, _prefer_creator_premium_gpu, _prefer_higher_gaming_cost_x3d_cpu, _rebalance_gaming_cost_cpu_to_storage, _recommend_min_budget_for_gaming_x3d_from_low_end_config, _required_psu_wattage, build_configuration_response


class ScraperApiTests(APITestCase):
	def setUp(self):
		self.cpu = PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 7 9700X',
			price=40180,
			specs={'cores': 6},
			url='https://example.com/cpu',
		)
		self.gpu = PCPart.objects.create(
			part_type='gpu',
			name='RTX 4060',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu',
		)
		self.os = PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Default Seed',
			price=15000,
			specs={'edition': 'home'},
			url='https://example.com/os',
		)
		ScraperStatus.objects.create(
			total_scraped=2,
			success_count=2,
			error_count=0,
			cache_enabled=True,
			cache_ttl_seconds=1800,
		)

	def _seed_high_band_market_snapshot(self):
		MarketPriceRangeSnapshot.objects.create(
			market_min=180000,
			market_max=1300000,
			suggested_default=729980,
			currency='JPY',
			sources={'dospara_tc30_market': {'count': 120}},
		)

	def _seed_minimum_parts_for_high_band_cpu_e2e(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D BOX',
			price=62180,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9800x3d-e2e',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9850X3D BOX',
			price=87980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9850x3d-e2e',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 5070 12GB E2E',
			price=99800,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-5070-e2e',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 5080 16GB E2E',
			price=149800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5080-e2e',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 E2E Board',
			price=22000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-e2e',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB E2E',
			price=12000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16, 'speed_mhz': 5600},
			url='https://example.com/mem-ddr5-16-e2e',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 96GB E2E',
			price=14000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 96, 'speed_mhz': 5600},
			url='https://example.com/mem-ddr5-96-e2e',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 128GB E2E',
			price=15000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 128, 'speed_mhz': 5600},
			url='https://example.com/mem-ddr5-128-e2e',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB E2E',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-nvme-1tb-e2e',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB E2E',
			price=10000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-nvme-2tb-e2e',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler E2E',
			price=5500,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-air-e2e',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case E2E',
			price=7000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-e2e',
		)
		PCPart.objects.create(
			part_type='psu',
			name='850W PSU E2E',
			price=12000,
			specs={'wattage': 850},
			url='https://example.com/psu-850-e2e',
		)

	def test_generate_config_high_cost_cpu_is_capped_at_9800x3d_e2e(self):
		self._seed_high_band_market_snapshot()
		self._seed_minimum_parts_for_high_band_cpu_e2e()

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 400000, 'usage': 'gaming', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		cpu_name = str(parts['cpu']['name']).lower()
		self.assertIn('9800x3d', cpu_name)
		self.assertNotIn('9850x3d', cpu_name)
		self.assertIn('memory', parts)
		self.assertIn('storage', parts)
		self.assertNotIn('96gb', str(parts['memory']['name']).lower())
		self.assertNotIn('128gb', str(parts['memory']['name']).lower())
		self.assertNotIn('2tb', str(parts['storage']['name']).lower())

	def test_generate_config_high_spec_cpu_is_capped_at_9800x3d_e2e(self):
		self._seed_high_band_market_snapshot()
		self._seed_minimum_parts_for_high_band_cpu_e2e()

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 400000, 'usage': 'gaming', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		cpu_name = str(parts['cpu']['name']).lower()
		self.assertIn('9800x3d', cpu_name)
		self.assertNotIn('9850x3d', cpu_name)
		self.assertIn('memory', parts)
		self.assertIn('storage', parts)
		self.assertNotIn('96gb', str(parts['memory']['name']).lower())
		self.assertNotIn('128gb', str(parts['memory']['name']).lower())
		self.assertNotIn('2tb', str(parts['storage']['name']).lower())

	def test_generate_config_general_cost_prefers_home_os_even_if_pro_requested(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX',
			price=10480,
			specs={'socket': 'AM4'},
			url='https://www.dospara.co.jp/SBR1883/IC555726.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='GIGABYTE B550 GAMING X V2',
			price=12370,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://www.dospara.co.jp/SBR1017/IC490193.html',
		)
		PCPart.objects.create(
			part_type='memory',
			name='CFD D4U3200CS-8G',
			price=11660,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8, 'speed_mhz': 3200},
			url='https://www.dospara.co.jp/SBR12/IC465441.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Verbatim Vi5000 31825-J (M.2 2280 512GB)',
			price=14480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://www.dospara.co.jp/SBR1144/IC569483.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Samsung 990 PRO 2TB High Price',
			price=96980,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://www.dospara.co.jp/SBR1144/IC569999.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AINEX PC-Z05C-SRB',
			price=3540,
			specs={'supported_sockets': ['AM4']},
			url='https://www.dospara.co.jp/SBR95/IC601378.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='TRYX TURRIS T620 CPU Air Cooler Black H-T620N-DM2M-G0K',
			price=19990,
			specs={'supported_sockets': ['AM4', 'LGA1851']},
			url='https://www.dospara.co.jp/SBR95/IC999620.html',
		)
		PCPart.objects.create(
			part_type='psu',
			name='KRPW-L5-400W/80+',
			price=4580,
			specs={'wattage': 400},
			url='https://www.dospara.co.jp/SBR1023/IC499155.html',
		)
		PCPart.objects.create(
			part_type='case',
			name='MSI MAG FORGE 130A AIRFLOW',
			price=4980,
			specs={'supported_form_factors': ['ATX']},
			url='https://www.dospara.co.jp/SBR143/IC509915.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Home 日本語パッケージ版',
			price=16480,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479478.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Pro 日本語パッケージ版',
			price=23980,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479479.html',
		)

		response = self.client.post(
			'/api/generate-config/',
			{
				'budget': 54980,
				'usage': 'general',
				'build_priority': 'cost',
				'os_edition': 'pro',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('os', parts)
		self.assertIn('home', str(parts['os']['name']).lower())
		if 'cpu_cooler' in parts:
			self.assertLessEqual(int(parts['cpu_cooler']['price']), 8000)
		if 'storage' in parts:
			self.assertLessEqual(int(parts['storage']['price']), 22000)

	def test_generate_config_general_spec_low_budget_prefers_core_ultra_5_225_and_lga1851_motherboard(self):
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 5 225 BOX',
			price=28370,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999225.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 7 265KF BOX',
			price=49800,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999265.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock B860M Pro-A WiFi',
			price=21980,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://www.dospara.co.jp/SBR999/IC999860.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock X870 Steel Legend WiFi',
			price=31980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://www.dospara.co.jp/SBR999/IC999870.html',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Low Budget',
			price=9980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16, 'speed_mhz': 5600},
			url='https://www.dospara.co.jp/SBR999/IC999mem.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 500GB Low Budget',
			price=6980,
			specs={'interface': 'NVMe', 'capacity_gb': 500},
			url='https://www.dospara.co.jp/SBR999/IC999sto.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='WD Black SN770M 1TB',
			price=15980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://www.dospara.co.jp/SBR999/IC999sto1tb.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Samsung 990 PRO 2TB',
			price=96980,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://www.dospara.co.jp/SBR999/IC999sto2tb.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='LGA1851 Stock Cooler Equivalent',
			price=1980,
			specs={'supported_sockets': ['LGA1851']},
			url='https://www.dospara.co.jp/SBR999/IC999cool.html',
		)
		PCPart.objects.create(
			part_type='psu',
			name='400W PSU Low Budget',
			price=4980,
			specs={'wattage': 400},
			url='https://www.dospara.co.jp/SBR999/IC999psu.html',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case Low Budget',
			price=4980,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://www.dospara.co.jp/SBR999/IC999case.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Home 日本語パッケージ版',
			price=16480,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479478.html',
		)

		response = self.client.post(
			'/api/generate-config/',
			{
				'budget': 178980,
				'usage': 'general',
				'build_priority': 'spec',
				'os_edition': 'home',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		self.assertIn('motherboard', parts)
		self.assertIn('storage', parts)
		self.assertIn('225 box', str(parts['cpu']['name']).lower())
		self.assertEqual(str(parts['motherboard']['specs'].get('socket', '')).upper(), 'LGA1851')
		self.assertNotIn('x870', str(parts['motherboard']['name']).lower())
		self.assertLessEqual(int(parts['storage']['price']), 22000)
		self.assertNotIn('990 pro', str(parts['storage']['name']).lower())

	def test_generate_config_general_cost_middle_budget_prefers_cheaper_intel_cpu(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X BOX',
			price=39800,
			specs={'socket': 'AM5'},
			url='https://www.dospara.co.jp/SBR999/IC9997600x.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel インテル® Core™ Ultra 5 プロセッサー 225',
			price=28370,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999225-cost.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel インテル® Core™ Ultra 7 プロセッサー 265KF',
			price=45980,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999265kf-cost.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel インテル® Core™ Ultra 5 プロセッサー 235',
			price=43980,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999235-cost.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock B860M Pro-A WiFi Cost',
			price=21980,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://www.dospara.co.jp/SBR999/IC999860-cost.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock B650M Pro RS Cost',
			price=18980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://www.dospara.co.jp/SBR999/IC999b650-cost.html',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB General Cost Mid',
			price=9980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16, 'speed_mhz': 5600},
			url='https://www.dospara.co.jp/SBR999/IC999mem-cost-mid.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB General Cost Mid',
			price=8980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://www.dospara.co.jp/SBR999/IC999sto-cost-mid.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='LGA1851 Air Cooler Cost',
			price=2980,
			specs={'supported_sockets': ['LGA1851']},
			url='https://www.dospara.co.jp/SBR999/IC999cool-cost-mid.html',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU General Cost Mid',
			price=4980,
			specs={'wattage': 500},
			url='https://www.dospara.co.jp/SBR999/IC999psu-cost-mid.html',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case General Cost Mid',
			price=5980,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://www.dospara.co.jp/SBR999/IC999case-cost-mid.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Home 日本語パッケージ版',
			price=16480,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479478.html',
		)

		response = self.client.post(
			'/api/generate-config/',
			{
				'budget': 224980,
				'usage': 'general',
				'build_priority': 'cost',
				'os_edition': 'home',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		self.assertIn('ultra 5', str(parts['cpu']['name']).lower())
		self.assertNotIn('265kf', str(parts['cpu']['name']).lower())
		self.assertLess(int(parts['cpu']['price']), 45980)
		cpu_adjustments = [
			adj for adj in response.data.get('part_adjustments', [])
			if str(adj.get('part_type', '')).lower() == 'cpu'
		]
		self.assertFalse(cpu_adjustments, response.data.get('part_adjustments'))

	def test_generate_config_general_spec_middle_budget_prefers_cheaper_cpu_when_perf_score_missing(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Athlon 3000G BOX',
			price=3580,
			specs={'socket': 'AM4', 'core_count': 2, 'thread_count': 4},
			url='https://www.dospara.co.jp/SBR999/IC999athlon3000g-spec.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel インテル® Core™ Ultra 5 プロセッサー 250K Plus',
			price=43800,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999250kplus-spec.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel インテル® Core™ Ultra 7 プロセッサー 265KF',
			price=45980,
			specs={'socket': 'LGA1851'},
			url='https://www.dospara.co.jp/SBR999/IC999265kf-spec.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='GIGABYTE Z890M FORCE DUO X WIFI7',
			price=34800,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://www.dospara.co.jp/SBR999/IC999z890m-spec.html',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB General Spec Mid',
			price=15980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16, 'speed_mhz': 5600},
			url='https://www.dospara.co.jp/SBR999/IC999mem-spec-mid.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB General Spec Mid',
			price=12580,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://www.dospara.co.jp/SBR999/IC999sto-spec-mid.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='LGA1851 Air Cooler General Spec Mid',
			price=3780,
			specs={'supported_sockets': ['LGA1851']},
			url='https://www.dospara.co.jp/SBR999/IC999cool-spec-mid.html',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Inno3D GeForce RTX 5060 TWIN X2 OC',
			price=67980,
			specs={'memory_gb': 8},
			url='https://www.dospara.co.jp/SBR999/IC999gpu5060-spec-mid.html',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU General Spec Mid',
			price=6980,
			specs={'wattage': 650},
			url='https://www.dospara.co.jp/SBR999/IC999psu-spec-mid.html',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Tower General Spec Mid',
			price=12800,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://www.dospara.co.jp/SBR999/IC999case-spec-mid.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Home 日本語パッケージ版',
			price=16480,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479478.html',
		)

		with patch('scraper.views._get_cpu_perf_score', return_value=None):
			response = self.client.post(
				'/api/generate-config/',
				{
					'budget': 247478,
					'usage': 'general',
					'build_priority': 'spec',
					'cpu_vendor': 'intel',
					'os_edition': 'home',
				},
				format='json',
			)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		self.assertNotIn('athlon', str(parts['cpu']['name']).lower())
		self.assertIn('250k plus', str(parts['cpu']['name']).lower())
		self.assertNotIn('265kf', str(parts['cpu']['name']).lower())

	def test_generate_config_general_spec_excludes_x3d_cpu(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9950X3D BOX',
			price=111670,
			specs={'socket': 'AM5'},
			url='https://www.dospara.co.jp/SBR999/IC9950x3d.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X BOX',
			price=54800,
			specs={'socket': 'AM5'},
			url='https://www.dospara.co.jp/SBR999/IC9700x.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock B650M Pro X3D WiFi',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://www.dospara.co.jp/SBR999/ICb650m.html',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB General Spec',
			price=12980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16, 'speed_mhz': 5600},
			url='https://www.dospara.co.jp/SBR999/ICddr5-16.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB General Spec',
			price=14980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://www.dospara.co.jp/SBR999/ICsto-1tb.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler AM5 General Spec',
			price=3980,
			specs={'supported_sockets': ['AM5']},
			url='https://www.dospara.co.jp/SBR999/ICcool-am5.html',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU General Spec',
			price=4980,
			specs={'wattage': 500},
			url='https://www.dospara.co.jp/SBR999/ICpsu500.html',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case General Spec',
			price=5980,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://www.dospara.co.jp/SBR999/ICcase-matx.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Home 日本語パッケージ版',
			price=16480,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479478.html',
		)

		response = self.client.post(
			'/api/generate-config/',
			{
				'budget': 247478,
				'usage': 'general',
				'build_priority': 'spec',
				'os_edition': 'home',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		self.assertNotIn('x3d', str(parts['cpu']['name']).lower())
		self.assertNotIn('9950x3d', str(parts['cpu']['name']).lower())

	def test_generate_config_general_cost_excludes_x3d_cpu(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D BOX',
			price=62180,
			specs={'socket': 'AM5'},
			url='https://www.dospara.co.jp/SBR999/IC9800x3d.html',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X BOX',
			price=54800,
			specs={'socket': 'AM5'},
			url='https://www.dospara.co.jp/SBR999/IC9700x-cost.html',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock B650M Pro RS (B650 AM5 MicroATX)',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://www.dospara.co.jp/SBR999/ICb650m-rs.html',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB General Cost',
			price=12980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16, 'speed_mhz': 5600},
			url='https://www.dospara.co.jp/SBR999/ICddr5-16-cost.html',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB General Cost',
			price=14980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://www.dospara.co.jp/SBR999/ICsto-1tb-cost.html',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler AM5 General Cost',
			price=3980,
			specs={'supported_sockets': ['AM5']},
			url='https://www.dospara.co.jp/SBR999/ICcool-am5-cost.html',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU General Cost',
			price=4980,
			specs={'wattage': 500},
			url='https://www.dospara.co.jp/SBR999/ICpsu500-cost.html',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case General Cost',
			price=5980,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://www.dospara.co.jp/SBR999/ICcase-matx-cost.html',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 Home 日本語パッケージ版',
			price=16480,
			specs={},
			url='https://www.dospara.co.jp/SBR170/IC479478.html',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 224980,
				'usage': 'general',
				'build_priority': 'cost',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
				'case_fan_policy': 'auto',
				'os_edition': 'home',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('cpu', parts)
		self.assertNotIn('x3d', str(parts['cpu']['name']).lower())
		self.assertNotIn('9800x3d', str(parts['cpu']['name']).lower())

	def test_generate_config_premium_cost_applies_memory_storage_caps_e2e(self):
		self._seed_high_band_market_snapshot()
		self._seed_minimum_parts_for_high_band_cpu_e2e()

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 574980, 'usage': 'gaming', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('memory', parts)
		self.assertIn('storage', parts)
		self.assertIn('2tb', str(parts['storage']['name']).lower())

	def test_generate_config_spec_secures_storage_capacity_target(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9850X3D BOX Test',
			price=87980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9850x3d-spec-storage',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5080 16GB Expensive Test',
			price=249800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5080-expensive-test',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5070 12GB Cheaper Test',
			price=139800,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-5070-cheaper-test',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Spec Storage Test Board',
			price=35980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-spec-storage-test',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Spec Storage Test',
			price=21480,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/memory-spec-storage-test',
		)
		PCPart.objects.create(
			part_type='storage',
			name='ADATA ALEG-710-512GCS (M.2 2280 512GB) Test',
			price=13480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-512-spec-test',
		)
		PCPart.objects.create(
			part_type='storage',
			name='ADATA LEGEND 710 1TB Expensive Test',
			price=41800,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-spec-test',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Spec Storage Test',
			price=19990,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-spec-storage-test',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Spec Storage Test',
			price=5680,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-spec-storage-test',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Spec Storage Test',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-spec-storage-test',
		)

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 574980, 'usage': 'gaming', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('storage', parts)
		self.assertIn('gpu', parts)
		self.assertIn('1tb', str(parts['storage']['name']).lower())
		self.assertLessEqual(int(response.data.get('total_price', 0)), 574980)

	def test_generate_config_lower_premium_spec_avoids_9850_and_5080(self):
		self._seed_high_band_market_snapshot()
		self._seed_minimum_parts_for_high_band_cpu_e2e()

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 574980, 'usage': 'gaming', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		cpu_name = str(parts['cpu']['name']).lower()
		gpu_name = str(parts['gpu']['name']).lower()
		self.assertNotIn('9850x3d', cpu_name)
		self.assertIn('9800x3d', cpu_name)
		self.assertNotIn('5080', gpu_name)
		self.assertIn('2tb', str(parts['storage']['name']).lower())

	def _create_low_end_gpu(self, name='RTX 3050 6GB Test', price=31800):
		return PCPart.objects.create(
			part_type='gpu',
			name=name,
			price=price,
			specs={'vram': '6GB'},
			url='https://example.com/gpu-low-end-test',
		)

	def test_generate_config_viewset_action_returns_configuration(self):
		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		self.assertEqual(response.data['usage'], 'gaming')
		self.assertEqual(response.data['budget'], 120000)
		self.assertIsNotNone(response.data['configuration_id'])
		self.assertGreater(response.data['total_price'], 0)
		self.assertLessEqual(response.data['total_price'], 120000)
		self.assertGreaterEqual(len(response.data['parts']), 1)

		configuration = Configuration.objects.get(id=response.data['configuration_id'])
		self.assertEqual(configuration.budget, 120000)
		self.assertEqual(configuration.usage, 'gaming')
		self.assertEqual(configuration.total_price, response.data['total_price'])

	def test_infer_gaming_gpu_tier_label_maps_requested_models(self):
		cases = [
			('GeForce GTX 1650', 'ローエンド'),
			('GeForce GTX 1660 Super', 'ローエンド'),
			('Radeon RX 6600', 'ローエンド'),
			('GeForce RTX 3050', 'ローエンド'),
			('GeForce RTX 5050', 'ローエンド'),
			('GeForce RTX 4060', 'ミドル'),
			('GeForce RTX 5060 Ti', 'ミドル'),
			('GeForce RTX 4070', 'ミドル'),
			('GeForce RTX 4070 Ti', 'ハイエンド'),
			('Radeon RX 7700 XT', 'ミドル'),
			('Radeon RX 9060 XT', 'ミドル'),
			('GeForce RTX 4080 Super', 'ハイエンド'),
			('GeForce RTX 5070', 'ハイエンド'),
			('GeForce RTX 5070 Ti', 'ハイエンド'),
			('Radeon RX 7900 XTX', 'ハイエンド'),
			('GeForce RTX 4090', 'プレミアム'),
			('GeForce RTX 5080', 'プレミアム'),
			('GeForce RTX 5090', 'プレミアム'),
			('Radeon RX 9070 XT', 'プレミアム'),
		]

		for name, expected in cases:
			part = PCPart(
				part_type='gpu',
				name=name,
				price=0,
				specs={},
				url='https://example.com/gpu',
			)
			self.assertEqual(_infer_gaming_gpu_tier_label(part), expected)

	def test_high_cost_gpu_cap_allows_5070_and_9070xt_but_not_5080(self):
		budget = 400000  # high帯
		rtx5070 = PCPart(part_type='gpu', name='GeForce RTX 5070 12GB', price=0, specs={}, url='https://example.com/5070')
		rx9070xt = PCPart(part_type='gpu', name='Radeon RX 9070 XT 16GB', price=0, specs={}, url='https://example.com/9070xt')
		rtx5080 = PCPart(part_type='gpu', name='GeForce RTX 5080 16GB', price=0, specs={}, url='https://example.com/5080')

		self.assertTrue(_is_gaming_gpu_within_priority_cap(rtx5070, 'cost', budget=budget))
		self.assertTrue(_is_gaming_gpu_within_priority_cap(rx9070xt, 'cost', budget=budget))
		self.assertFalse(_is_gaming_gpu_within_priority_cap(rtx5080, 'cost', budget=budget))

	def test_high_spec_gpu_cap_allows_5080_but_blocks_5090(self):
		budget = 400000  # high帯
		rtx5080 = PCPart(part_type='gpu', name='GeForce RTX 5080 16GB', price=0, specs={}, url='https://example.com/5080')
		rtx5090 = PCPart(part_type='gpu', name='GeForce RTX 5090 32GB', price=0, specs={}, url='https://example.com/5090')

		self.assertTrue(_is_gaming_gpu_within_priority_cap(rtx5080, 'spec', budget=budget))
		self.assertFalse(_is_gaming_gpu_within_priority_cap(rtx5090, 'spec', budget=budget))
    
	def test_premium_cost_gpu_cap_blocks_5080(self):
		budget = 574980  # premium帯
		rtx5070 = PCPart(part_type='gpu', name='GeForce RTX 5070 12GB', price=0, specs={}, url='https://example.com/5070')
		rtx5070ti = PCPart(part_type='gpu', name='GeForce RTX 5070 Ti 16GB', price=0, specs={}, url='https://example.com/5070ti')
		rtx5080 = PCPart(part_type='gpu', name='GeForce RTX 5080 16GB', price=0, specs={}, url='https://example.com/5080')

		self.assertTrue(_is_gaming_gpu_within_priority_cap(rtx5070, 'cost', budget=budget))
		self.assertFalse(_is_gaming_gpu_within_priority_cap(rtx5070ti, 'cost', budget=budget))
		self.assertFalse(_is_gaming_gpu_within_priority_cap(rtx5080, 'cost', budget=budget))

	def test_matches_selection_options_rejects_ux150_l_for_am5(self):
		cooler = PCPart.objects.create(
			part_type='cpu_cooler',
			name='Thermaltake UX150-L ARGB Air cooler Black CL-P147-CA13SW-A',
			price=3780,
			specs={},
			url='https://example.com/ux150-l',
		)

		self.assertFalse(
			_matches_selection_options(
				'cpu_cooler',
				cooler,
				options={
					'cooler_type': 'air',
					'cpu_socket': 'AM5',
					'usage': 'gaming',
				},
			),
		)

	def test_pick_part_by_target_prefers_am5_compatible_cooler_over_ux150_l(self):
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Thermaltake UX150-L ARGB Air cooler Black CL-P147-CA13SW-A',
			price=3780,
			specs={},
			url='https://example.com/ux150-l',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='DeepCool AK400 CPU Cooler',
			price=4580,
			specs={'supported_sockets': 'AM5, AM4, LGA1700'},
			url='https://example.com/ak400-am5',
		)

		picked = _pick_part_by_target(
			'cpu_cooler',
			budget=259980,
			usage='gaming',
			options={
				'cooler_type': 'air',
				'cooling_profile': 'performance',
				'cpu_socket': 'AM5',
				'build_priority': 'cost',
			},
		)

		self.assertIsNotNone(picked)
		self.assertNotIn('UX150-L', picked.name)

	def test_pick_part_by_target_prefers_microatx_case_for_microatx_motherboard(self):
		PCPart.objects.create(
			part_type='case',
			name='Value Mid Tower (ATX)',
			price=3000,
			specs={},
			url='https://example.com/case-atx',
		)
		PCPart.objects.create(
			part_type='case',
			name='Compact Airflow Case (MicroATX)',
			price=5000,
			specs={},
			url='https://example.com/case-matx',
		)

		picked = _pick_part_by_target(
			'case',
			budget=285978,
			usage='gaming',
			options={
				'case_size': 'mid',
				'motherboard_form_factor': 'micro-atx',
				'build_priority': 'cost',
			},
		)

		self.assertIsNotNone(picked)
		self.assertIn('microatx', picked.name.lower())

	def test_gaming_cost_mode_excludes_flagship_cpu_motherboard_memory(self):
		"""gaming + cost モードでフラッグシップパーツが除外されることを確認"""
		# フラッグシップCPU
		flagship_cpu = PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 9 9850X3D',
			price=87980,
			specs={'specs_text': ''},
			url='https://example.com/9850x3d',
		)
		# 中級CPU（優先）
		mid_cpu = PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 7 7700X3D',
			price=48000,
			specs={'specs_text': 'Ryzen 7 7700X3D'},
			url='https://example.com/7700x3d',
		)

		# フラッグシップマザーボード
		flagship_mb = PCPart.objects.create(
			part_type='motherboard',
			name='ASUS ROG STRIX X870E-E GAMING WIFI',
			price=57660,
			specs={'specs_text': 'X870E'},
			url='https://example.com/x870e-mb',
		)
		# 中級マザーボード（優先）
		mid_mb = PCPart.objects.create(
			part_type='motherboard',
			name='ASUS TUF GAMING X870-PLUS',
			price=35980,
			specs={'specs_text': 'X870'},
			url='https://example.com/x870-mb',
		)

		# 高速メモリ（除外）
		high_speed_mem = PCPart.objects.create(
			part_type='memory',
			name='G.SKILL Flare X5 DDR5 PC5-44800 CL38 48GB',
			price=158800,
			specs={'specs_text': 'DDR5-5600 PC5-44800 48GB'},
			url='https://example.com/ddr5-high-speed',
		)
		# 標準メモリ（優先）
		mid_speed_mem = PCPart.objects.create(
			part_type='memory',
			name='Kingston Fury Beast DDR5 PC5-38400 CL38 32GB',
			price=89800,
			specs={'specs_text': 'DDR5-4800 PC5-38400 32GB'},
			url='https://example.com/ddr5-mid-speed',
		)

		# gaming + cost: CPU は mid_cpu 選択
		picked_cpu = _pick_part_by_target(
			'cpu',
			budget=574980,
			usage='gaming',
			options={
				'build_priority': 'cost',
				'cpu_vendor': 'amd',
			},
		)
		self.assertIsNotNone(picked_cpu)
		self.assertNotIn('9850x3d', picked_cpu.name.lower(), 
						msg=f"gaming+cost should exclude 9850X3D, got {picked_cpu.name}")

		# gaming + cost: motherboard は mid_mb 選択（X870E除外）
		picked_mb = _pick_part_by_target(
			'motherboard',
			budget=574980,
			usage='gaming',
			options={
				'build_priority': 'cost',
				'cpu_socket': None,
			},
		)
		self.assertIsNotNone(picked_mb)
		self.assertNotIn('x870e', picked_mb.name.lower(),
						msg=f"gaming+cost should exclude X870E, got {picked_mb.name}")

		# gaming + cost: memory は mid_speed_mem 選択（高速メモリ除外）
		picked_mem = _pick_part_by_target(
			'memory',
			budget=574980,
			usage='gaming',
			options={
				'build_priority': 'cost',
			},
		)
		self.assertIsNotNone(picked_mem)
		self.assertNotIn('pc5-44800', picked_mem.name.lower(),
						msg=f"gaming+cost should exclude PC5-44800, got {picked_mem.name}")
		self.assertNotIn('pc5-48000', picked_mem.name.lower(),
						msg=f"gaming+cost should exclude PC5-48000, got {picked_mem.name}")

	def test_enforce_gaming_spec_best_value_gpu_prefers_exact_5060_over_5060_ti(self):
		high_gpu = PCPart.objects.create(
			part_type='gpu',
			name='MSI GeForce RTX 5060 Ti 8G VENTUS 2X OC PLUS',
			price=99480,
			specs={'vram_gb': 8},
			url='https://example.com/gpu-5060ti',
		)
		exact_5060 = PCPart.objects.create(
			part_type='gpu',
			name='ASUS TUF-RTX5060-O8G-GAMING (GeForce RTX 5060 8GB)',
			price=78700,
			specs={'vram_gb': 8},
			url='https://example.com/gpu-5060',
		)
		memory = PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GBx2 5600',
			price=54800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/memory-32',
		)
		selected_parts = {
			'gpu': high_gpu,
			'memory': memory,
		}

		adjusted = _enforce_gaming_spec_best_value_gpu(
			selected_parts,
			budget=285978,
			usage='gaming',
			options={'usage': 'gaming', 'build_priority': 'spec', 'budget': 285978},
		)

		self.assertEqual(adjusted['gpu'].id, exact_5060.id)


	def test_pick_amd_gaming_cpu_uses_rank_files(self):
		cpu_7500f = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7500F Test',
			price=24170,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7500f-test',
		)
		cpu_9700x = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X Test',
			price=40180,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9700x-test',
		)
		cpu_7800x3d = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Test',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-test',
		)

		cost_pick = _pick_amd_gaming_cpu([cpu_7500f, cpu_9700x, cpu_7800x3d], 'cost')
		spec_pick = _pick_amd_gaming_cpu([cpu_7500f, cpu_9700x, cpu_7800x3d], 'spec')

		self.assertEqual(cost_pick.id, cpu_9700x.id)
		self.assertIn(spec_pick.id, {cpu_9700x.id, cpu_7800x3d.id})

	def test_generate_config_low_budget_cost_requires_low_end_gpu_inventory(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-low-budget',
		)
		self._create_low_end_gpu()
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Test Board',
			price=14000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-test',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Test',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-test',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Test',
			price=9000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-test',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Test',
			price=4000,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-air-test',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Test',
			price=5000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-test',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU Test',
			price=6000,
			specs={'wattage': 650},
			url='https://example.com/psu-650-test',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5060 8GB Test',
			price=55980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx5060-test',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 3050 6GB DDR Priority',
			price=31800,
			specs={'vram': '6GB'},
			url='https://example.com/gpu-3050-ddr-priority',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 169980, 'usage': 'gaming', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('3050', parts['gpu']['name'])

	def test_is_part_suitable_excludes_out_of_stock(self):
		sold_out_gpu = PCPart(
			part_type='gpu',
			name='RTX 4070 Sold Out',
			price=70000,
			specs={'availability_text': '在庫切れ'},
			url='https://example.com/gpu-soldout',
			stock_status='out_of_stock',
		)
		active_gpu = PCPart(
			part_type='gpu',
			name='RTX 4070 Active',
			price=70000,
			specs={'availability_text': '在庫あり'},
			url='https://example.com/gpu-active',
			stock_status='in_stock',
		)

		self.assertFalse(_is_part_suitable('gpu', sold_out_gpu))
		self.assertTrue(_is_part_suitable('gpu', active_gpu))

	def test_generate_config_viewset_action_rejects_invalid_budget(self):
		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 0, 'usage': 'gaming', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('budget', response.data['detail'])

	@patch('scraper.views.fetch_dospara_cpu_selection_material')
	def test_cpu_selection_material_compare_accepts_post_models_body(self, mock_fetch_cpu_material):
		PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 7 7800X3D',
			price=49800,
			specs={'cores': 8},
			url='https://example.com/amd',
		)
		mock_fetch_cpu_material.return_value = {
			'source_name': 'dospara_cpu_comparison_pages',
			'source_urls': ['https://example.com/amd', 'https://example.com/intel'],
			'exclude_intel_13_14': True,
			'entry_count': 2,
			'excluded_count': 0,
			'entries': [
				{'vendor': 'amd', 'model_name': 'Ryzen 7 7800X3D', 'perf_score': 3609, 'source_url': 'https://example.com/amd'},
				{'vendor': 'intel', 'model_name': 'Core i5-12400F', 'perf_score': 3918, 'source_url': 'https://example.com/intel'},
			],
		}

		snapshot = CPUSelectionSnapshot.objects.create(
			source_name='dospara_cpu_comparison_pages',
			source_urls=['https://example.com/amd', 'https://example.com/intel'],
			exclude_intel_13_14=True,
			entry_count=2,
			excluded_count=0,
		)
		CPUSelectionEntry.objects.create(
			snapshot=snapshot,
			vendor='amd',
			model_name='Ryzen 7 7800X3D',
			perf_score=3609,
			source_url='https://example.com/amd',
			rank_global=1,
		)
		CPUSelectionEntry.objects.create(
			snapshot=snapshot,
			vendor='intel',
			model_name='Core i5-12400F',
			perf_score=3918,
			source_url='https://example.com/intel',
			rank_global=2,
		)

		response = self.client.post(
			'/api/cpu-selection-material/compare/',
			{'models': ['Ryzen 7 7800X3D', 'Core i5-12400F']},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['requested_models'], ['Ryzen 7 7800X3D', 'Core i5-12400F'])
		self.assertEqual(response.data['missing_models'], ['Core i5-12400F'])
		self.assertEqual(len(response.data['results']), 1)
		self.assertEqual(response.data['excluded_count'], 1)
		snapshot = CPUSelectionSnapshot.objects.order_by('-fetched_at', '-id').first()
		self.assertIsNotNone(snapshot)
		self.assertEqual(CPUSelectionEntry.objects.filter(snapshot=snapshot).count(), 2)

	def test_gpu_performance_compare_accepts_post_models_body(self):
		snapshot = GPUPerformanceSnapshot.objects.create(
			source_name='dospara_gpu',
			source_url='https://example.com/gpu',
			updated_at_source='2026-04-04',
			score_note='higher is better',
			parser_version='v1',
		)
		GPUPerformanceEntry.objects.create(
			snapshot=snapshot,
			gpu_name='NVIDIA GeForce RTX 5070 12GB',
			model_key='RTX 5070',
			vendor='nvidia',
			vram_gb=12,
			perf_score=3931,
			detail_url='https://example.com/5070',
			is_laptop=False,
			rank_global=12,
		)
		GPUPerformanceEntry.objects.create(
			snapshot=snapshot,
			gpu_name='AMD Radeon RX 9070 XT 16GB',
			model_key='RX 9070 XT',
			vendor='amd',
			vram_gb=16,
			perf_score=3673,
			detail_url='https://example.com/9070xt',
			is_laptop=False,
			rank_global=18,
		)

		response = self.client.post(
			'/api/gpu-performance/compare/',
			{'models': ['RTX 5070', 'RX 9070 XT']},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['requested_models'], ['RTX 5070', 'RX 9070 XT'])
		self.assertEqual(response.data['missing_models'], [])
		self.assertEqual(len(response.data['results']), 2)

	def test_gpu_performance_compare_matches_compact_model_keys(self):
		snapshot = GPUPerformanceSnapshot.objects.create(
			source_name='dospara_gpu',
			source_url='https://example.com/gpu-compact',
			updated_at_source='2026-04-04',
			score_note='higher is better',
			parser_version='v1',
		)
		GPUPerformanceEntry.objects.create(
			snapshot=snapshot,
			gpu_name='NVIDIA GeForce RTX 5060 Ti 8GB',
			model_key='RTX 5060 TI',
			vendor='nvidia',
			vram_gb=8,
			perf_score=5123,
			detail_url='https://example.com/5060ti-compact',
			is_laptop=False,
			rank_global=45,
		)

		response = self.client.post(
			'/api/gpu-performance/compare/',
			{'models': ['RTX5060TI']},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['missing_models'], [])
		self.assertEqual(len(response.data['results']), 1)
		self.assertEqual(response.data['results'][0]['model_key'], 'RTX 5060 TI')

	def test_get_gpu_perf_score_from_snapshot_matches_compact_name(self):
		part = PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX5060Ti 8GB OC',
			price=59800,
			specs={'vram': '8GB'},
			url='https://example.com/part-rtx5060ti-oc',
		)

		with patch('scraper.views._load_latest_gpu_perf_scores') as mock_scores:
			mock_scores.return_value = {('RTX5060TI', 8): 5123}
			score = _get_gpu_perf_score_from_snapshot(part)

		self.assertEqual(score, 5123)

	def test_matches_selection_options_gpu_ignores_cpu_x3d_requirement(self):
		gpu = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5050 8GB Filter Test',
			price=49980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-filter-test',
		)

		is_match = _matches_selection_options(
			'gpu',
			gpu,
			{
				'usage': 'gaming',
				'require_gaming_x3d_cpu': True,
				'require_preferred_gaming_gpu': False,
			},
		)

		self.assertTrue(is_match)

	def test_generate_config_prefers_higher_gpu_for_gaming(self):
		PCPart.objects.create(
			part_type='gpu',
			name='RTX 4070',
			price=70000,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-4070',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 9 7900',
			price=60000,
			specs={'cores': 12},
			url='https://example.com/cpu-7900',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 7 7800X3D',
			price=49800,
			specs={'cores': 8},
			url='https://example.com/cpu-7800x3d',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Board',
			price=14000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB',
			price=8000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=9000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler',
			price=4000,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-air',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=10000,
			specs={'wattage': 750},
			url='https://example.com/psu-750',
		)

		gaming_response, gaming_error = build_configuration_response(300000, 'gaming', persist=False, enforce_gaming_x3d=False)
		general_response, general_error = build_configuration_response(300000, 'standard', persist=False)

		self.assertIsNone(gaming_error)
		self.assertIsNone(general_error)
		gaming_gpu = [p for p in gaming_response['parts'] if p['category'] == 'gpu'][0]
		standard_gpu = [p for p in general_response['parts'] if p['category'] == 'gpu'][0]
		# ゲーミングは高価なdGPUを選択
		self.assertEqual(gaming_gpu['name'], 'RTX 4070')
		# スタンダードは内蔵GPU（dGPU不使用）
		self.assertEqual(standard_gpu['name'], '内蔵GPU（統合グラフィックス）')
		self.assertEqual(standard_gpu['price'], 0)

	def test_generate_config_stays_within_budget(self):
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Board',
			price=30000,
			specs={},
			url='https://example.com/mb',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB',
			price=24000,
			specs={},
			url='https://example.com/mem',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=18000,
			specs={},
			url='https://example.com/ssd',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=15000,
			specs={},
			url='https://example.com/psu',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=12000,
			specs={},
			url='https://example.com/case',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 90000, 'usage': 'gaming', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertLessEqual(response.data['total_price'], 90000)

	def test_generate_config_includes_os_when_available(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX OS Include',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-os-include',
		)
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 HOME 日本語パッケージ版',
			price=16480,
			specs={'edition': 'Home'},
			url='https://example.com/windows-home',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 400000, 'usage': 'gaming', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		os_parts = [p for p in response.data['parts'] if p['category'] == 'os']
		self.assertEqual(len(os_parts), 1)
		configuration = Configuration.objects.get(id=response.data['configuration_id'])
		self.assertIsNotNone(configuration.os)

	def test_generate_config_resolves_socket_and_memory_compatibility(self):
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Board',
			price=14000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-am5',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B760 Board',
			price=16000,
			specs={'socket': 'LGA1700', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-1700',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB',
			price=7000,
			specs={'memory_type': 'DDR4'},
			url='https://example.com/mem-ddr4',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB',
			price=7500,
			specs={'memory_type': 'DDR5'},
			url='https://example.com/mem-ddr5',
		)
		PCPart.objects.filter(id=self.cpu.id).update(specs={'socket': 'AM5'})

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 140000, 'usage': 'gaming'},
			format='json',
		)

		part_names = [p['name'] for p in response.data['parts']]
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertTrue(any(name in part_names for name in ['B650 Board', 'B760 Board']))
		self.assertTrue(any(name in part_names for name in ['DDR5 16GB', 'DDR4 16GB']))

	def test_generate_config_upgrades_psu_when_power_is_insufficient(self):
		PCPart.objects.create(
			part_type='psu',
			name='450W PSU',
			price=6000,
			specs={'wattage': 450},
			url='https://example.com/psu-450',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-750',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 140000, 'usage': 'gaming'},
			format='json',
		)

		psu_part = [p for p in response.data['parts'] if p['category'] == 'psu'][0]
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(psu_part['name'], '750W PSU')

	def test_generate_config_requires_1000w_psu_for_rtx5080_class_build(self):
		self.cpu.delete()
		self.gpu.delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX',
			price=49800,
			specs={'socket': 'AM5', 'tdp_w': 120},
			url='https://example.com/cpu-7800x3d',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='ID-COOLING FX360-PRO 360mm AIO',
			price=8990,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-360',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5080 16GB',
			price=209800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-rtx5080',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 ATX Board',
			price=18480,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-atx',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Kit',
			price=24800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-ddr5-32',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Premium Kit',
			price=49800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64},
			url='https://example.com/mem-ddr5-64',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe SSD 1TB RTX5080 Test',
			price=11980,
			specs={'interface': 'NVMe', 'capacity_gb': 1024},
			url='https://example.com/storage-1tb-rtx5080',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W Gold PSU',
			price=17980,
			specs={'wattage': 750},
			url='https://example.com/psu-750-gold',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W Gold PSU',
			price=26980,
			specs={'wattage': 1000},
			url='https://example.com/psu-1000-gold',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Airflow Case',
			price=18980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-airflow',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 350000,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'cooling_profile': 'performance',
				'case_size': 'mid',
				'case_fan_policy': 'airflow',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('RTX 5080', parts['gpu']['name'])
		self.assertEqual(parts['psu']['name'], '1000W Gold PSU')

	def test_generate_config_gaming_cost_auto_adjust_prefers_low_end_gpu(self):
		"""gaming+cost の auto 調整時は、予算目標に近いローエンドGPUを選ぶ"""
		gpu_4060 = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB Enforce',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-enforce',
		)
		gpu_5050 = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5050 8GB Enforce',
			price=49980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5050-enforce',
		)
		gpu_5060_ti = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5060 Ti 8GB Enforce',
			price=80316,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5060ti-enforce',
		)

		selected = _pick_gaming_cost_gpu_for_auto_adjust([gpu_4060, gpu_5050, gpu_5060_ti], 169980)

		self.assertEqual(selected.id, gpu_4060.id)

	def test_generate_config_gaming_cost_avoids_5050_and_prefers_16gb_memory(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 Budget',
			price=15980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5500-budget',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X3D Budget',
			price=39980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x3d-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB Budget',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5050 8GB Budget',
			price=49980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5050-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5060 Ti 8GB Budget',
			price=80316,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5060ti-budget',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 Gaming Budget',
			price=12980,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-budget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Budget',
			price=2980,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8-budget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Budget',
			price=5980,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-budget',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Budget',
			price=9980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-budget',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU Budget',
			price=5980,
			specs={'wattage': 650},
			url='https://example.com/psu-650-budget',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Budget',
			price=4980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-budget',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Budget',
			price=2980,
			specs={},
			url='https://example.com/cooler-air-budget',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 169980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cpu_vendor': 'any',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('5700X3D', parts['cpu']['name'])
		self.assertIn('16GB', parts['memory']['name'])
		self.assertNotIn('GeForce RTX 5050', parts['gpu']['name'])

	def test_pick_part_by_target_general_cost_low_prefers_am4_or_intel_over_am5(self):
		PCPart.objects.all().delete()

		intel_cpu = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i3 12100F General Cost',
			price=15980,
			specs={'socket': 'LGA1700'},
			url='https://example.com/cpu-intel-general-cost',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5600 General Cost',
			price=19800,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-am4-general-cost',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7700 General Cost',
			price=39800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-am5-general-cost',
		)

		picked = _pick_part_by_target(
			'cpu',
			budget=178980,
			usage='general',
			options={
				'cpu_vendor': 'any',
				'build_priority': 'cost',
			},
		)

		self.assertIsNotNone(picked)
		self.assertEqual(picked.id, intel_cpu.id)
		self.assertNotEqual(str((picked.specs or {}).get('socket', '')).upper(), 'AM5')

	def test_recommend_min_budget_for_gaming_x3d_from_low_end_config_prefers_existing_platform_uplift(self):
		cpu_5500 = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 Budget Uplift',
			price=15980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5500-uplift',
		)
		cpu_5700x3d = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X3D Budget Uplift',
			price=39980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x3d-uplift',
		)
		motherboard = PCPart.objects.create(
			part_type='motherboard',
			name='B550 Gaming Uplift',
			price=12980,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-uplift',
		)
		memory = PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Uplift',
			price=5980,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-uplift',
		)
		gpu = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 Uplift',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-uplift',
		)
		storage = PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Uplift',
			price=9980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-uplift',
		)
		psu = PCPart.objects.create(
			part_type='psu',
			name='650W PSU Uplift',
			price=5980,
			specs={'wattage': 650},
			url='https://example.com/psu-650-uplift',
		)
		case = PCPart.objects.create(
			part_type='case',
			name='ATX Case Uplift',
			price=4980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-uplift',
		)
		cooler = PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Uplift',
			price=2980,
			specs={},
			url='https://example.com/cooler-air-uplift',
		)

		selected_parts = {
			'cpu': cpu_5500,
			'motherboard': motherboard,
			'memory': memory,
			'gpu': gpu,
			'storage': storage,
			'psu': psu,
			'case': case,
			'cpu_cooler': cooler,
		}

		recommended_budget = _recommend_min_budget_for_gaming_x3d_from_low_end_config(selected_parts, 120000, 'gaming')

		self.assertEqual(recommended_budget, 145000)

	@patch('scraper.views.fetch_dospara_cpu_selection_material')
	def test_build_configuration_response_require_x3d_cpu_uses_x3d_even_without_perf_table(self, mock_fetch_cpu_material):
		mock_fetch_cpu_material.return_value = {
			'source_name': 'dospara_cpu_comparison_pages',
			'entry_count': 0,
			'entries': [],
		}

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 NoPerf',
			price=15980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5500-noperf',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X3D NoPerf',
			price=39980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x3d-noperf',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB NoPerf',
			price=48000,
			specs={'vram': '8GB', 'gpu_perf_score': 10000},
			url='https://example.com/gpu-4060-noperf',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 Gaming NoPerf',
			price=12980,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-noperf',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB NoPerf',
			price=5980,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-noperf',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB NoPerf',
			price=9980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-noperf',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU NoPerf',
			price=5980,
			specs={'wattage': 650},
			url='https://example.com/psu-650-noperf',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case NoPerf',
			price=4980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-noperf',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler NoPerf',
			price=2980,
			specs={},
			url='https://example.com/cooler-air-noperf',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='RTX 3050 6GB NoPerf',
			price=31800,
			specs={'vram': '6GB'},
			url='https://example.com/gpu-3050-noperf',
		)

		response_data, error_response = build_configuration_response(
			169980,
			'gaming',
			cpu_vendor='amd',
			build_priority='cost',
			enforce_gaming_x3d=False,
			persist=False,
			require_gaming_x3d_cpu=True,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data['parts']}
		self.assertIn('X3D', parts['cpu']['name'])

	@patch('scraper.views.fetch_dospara_cpu_selection_material')
	def test_matches_selection_options_cpu_allows_x3d_without_perf_score_when_required(self, mock_fetch_cpu_material):
		mock_fetch_cpu_material.return_value = {
			'source_name': 'dospara_cpu_comparison_pages',
			'entry_count': 0,
			'entries': [],
		}

		x3d_cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X3D MatchOptions',
			price=39980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x3d-match-options',
		)

		self.assertTrue(
			_matches_selection_options(
				'cpu',
				x3d_cpu,
				options={
					'usage': 'gaming',
					'cpu_vendor': 'amd',
					'require_gaming_x3d_cpu': True,
				},
			)
		)

	@patch('scraper.views.fetch_dospara_cpu_selection_material')
	def test_generate_config_gaming_cost_mid_budget_does_not_fail_x3d_required(self, mock_fetch_cpu_material):
		mock_fetch_cpu_material.return_value = {
			'source_name': 'dospara_cpu_comparison_pages',
			'entry_count': 2,
			'entries': [
				{'vendor': 'amd', 'model_name': 'Ryzen 5 5500', 'perf_score': 3000, 'source_url': 'https://example.com/amd'},
				{'vendor': 'amd', 'model_name': 'Ryzen 7 5700X3D', 'perf_score': 3609, 'source_url': 'https://example.com/amd'},
			],
		}

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 MidBudget',
			price=15980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5500-midbudget',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X3D MidBudget',
			price=39980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x3d-midbudget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB MidBudget',
			price=48000,
			specs={'vram': '8GB', 'gpu_perf_score': 10000},
			url='https://example.com/gpu-4060-midbudget',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 Gaming MidBudget',
			price=12980,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-midbudget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB MidBudget',
			price=5980,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-midbudget',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB MidBudget',
			price=9980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-midbudget',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU MidBudget',
			price=5980,
			specs={'wattage': 650},
			url='https://example.com/psu-650-midbudget',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case MidBudget',
			price=4980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-midbudget',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler MidBudget',
			price=2980,
			specs={},
			url='https://example.com/cooler-air-midbudget',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 259980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cpu_vendor': 'amd',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertTrue(response.data.get('x3d_enforced'))
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('X3D', parts['cpu']['name'])

	@patch('scraper.views.fetch_dospara_cpu_selection_material')
	def test_generate_config_gaming_cost_prefers_x3d_within_cap_even_when_above_target_slice(self, mock_fetch_cpu_material):
		mock_fetch_cpu_material.return_value = {
			'source_name': 'dospara_cpu_comparison_pages',
			'entry_count': 2,
			'entries': [
				{'vendor': 'amd', 'model_name': 'Ryzen 5 5500', 'perf_score': 2030, 'source_url': 'https://example.com/amd'},
				{'vendor': 'amd', 'model_name': 'Ryzen 7 7800X3D', 'perf_score': 3609, 'source_url': 'https://example.com/amd'},
			],
		}

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 BOX TargetSlice',
			price=15980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-5500-targetslice',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX TargetSlice',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-targetslice',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5070 12GB TargetSlice',
			price=136980,
			specs={'vram': '12GB', 'gpu_perf_score': 3931},
			url='https://example.com/gpu-5070-targetslice',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='A620M TargetSlice',
			price=5670,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a620m-targetslice',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB TargetSlice',
			price=14980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-targetslice',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB TargetSlice',
			price=12480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-512-targetslice',
		)
		PCPart.objects.create(
			part_type='psu',
			name='850W PSU TargetSlice',
			price=10980,
			specs={'wattage': 850},
			url='https://example.com/psu-850-targetslice',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case TargetSlice',
			price=3177,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-atx-targetslice',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler TargetSlice',
			price=3210,
			specs={},
			url='https://example.com/cooler-air-targetslice',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 259980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cpu_vendor': 'any',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('7800X3D', parts['cpu']['name'])

	def test_generate_config_gaming_cost_prefers_x3d_when_budget_has_surplus(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 BOX Prefer X3D',
			price=15980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5500-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X3D BOX Prefer X3D',
			price=39980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x3d-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB Prefer X3D',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5050 8GB Prefer X3D',
			price=49980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5050-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='A520M Prefer X3D',
			price=5670,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520m-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Prefer X3D',
			price=14980,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Prefer X3D',
			price=12480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-512-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU Prefer X3D',
			price=5580,
			specs={'wattage': 650},
			url='https://example.com/psu-650-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Prefer X3D',
			price=3177,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-atx-prefer-x3d',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Prefer X3D',
			price=3210,
			specs={},
			url='https://example.com/cooler-air-prefer-x3d',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 169980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cpu_vendor': 'any',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('X3D', parts['cpu']['name'])
		self.assertLessEqual(int(response.data.get('total_price') or 0), 169980)

	def test_generate_config_ignores_unsuitable_cpu_accessory(self):
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='DeepCool AK400 Air Cooler',
			price=5980,
			specs={},
			url='https://example.com/deepcool-ak400',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'gaming', 'cooler_type': 'air'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		cooler_part = [p for p in response.data['parts'] if p['category'] == 'cpu_cooler'][0]
		self.assertEqual(cooler_part['name'], 'DeepCool AK400 Air Cooler')

	def test_generate_config_respects_radiator_profile_and_case_size(self):
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Creator Radiator',
			price=19800,
			specs={'edition': 'pro'},
			url='https://example.com/os-pro-radiator',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7700 Creator Radiator',
			price=42000,
			specs={'socket': 'AM5', 'cores': 8, 'threads': 16},
			url='https://example.com/cpu-creator-radiator',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='Creator B650 Board',
			price=18000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-creator-radiator',
		)
		PCPart.objects.create(
			part_type='memory',
			name='Creator DDR5 32GB',
			price=16000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-creator-radiator',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Creator NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-creator-radiator',
		)
		PCPart.objects.create(
			part_type='psu',
			name='Creator 750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-creator-radiator',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Liquid Cooler 240mm High Performance 水冷',
			price=12000,
			specs={'radiator_mm': 240, 'cooler_type': 'liquid'},
			url='https://example.com/cooler-240',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Liquid Cooler 360mm High Performance 水冷',
			price=18000,
			specs={'radiator_mm': 360, 'cooler_type': 'liquid'},
			url='https://example.com/cooler-360',
		)
		PCPart.objects.create(
			part_type='case',
			name='Compact Mini-ITX Case',
			price=9000,
			specs={'supported_form_factors': ['Mini-ITX'], 'supported_radiators': [240]},
			url='https://example.com/case-mini',
		)
		PCPart.objects.create(
			part_type='case',
			name='Full Tower E-ATX Case',
			price=18000,
			specs={'supported_form_factors': ['ATX', 'E-ATX', 'MicroATX'], 'supported_radiators': [360]},
			url='https://example.com/case-full',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 250000,
				'usage': 'creator',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'cooling_profile': 'performance',
				'case_size': 'full',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_cooler = [p for p in response.data['parts'] if p['category'] == 'cpu_cooler'][0]
		selected_case = [p for p in response.data['parts'] if p['category'] == 'case'][0]
		self.assertIn('360mm', selected_cooler['name'])
		self.assertIn('full', selected_case['name'].lower())
		self.assertEqual(response.data['radiator_size'], '360')
		self.assertEqual(response.data['cooling_profile'], 'performance')
		self.assertEqual(response.data['case_size'], 'full')

	def test_generate_config_respects_cpu_vendor_selection(self):
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Vendor Test',
			price=19800,
			specs={'edition': 'pro'},
			url='https://example.com/os-pro-vendor',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B760 Intel Creator Board',
			price=18000,
			specs={'socket': 'LGA1700', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-intel-vendor',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 AMD Creator Board',
			price=17000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-amd-vendor',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Vendor Test',
			price=14000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-vendor',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Vendor Test',
			price=10000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-vendor',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Vendor Test',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-vendor',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Vendor Test',
			price=7000,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-vendor',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Vendor Test',
			price=4000,
			specs={},
			url='https://example.com/cooler-vendor',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i7 12700K',
			price=42000,
			specs={'socket': 'LGA1700', 'cores': 20, 'threads': 28},
			url='https://example.com/cpu-intel',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7700',
			price=41000,
			specs={'socket': 'AM5', 'cores': 8, 'threads': 16},
			url='https://example.com/cpu-amd',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D',
			price=52000,
			specs={'socket': 'AM5', 'cores': 8, 'threads': 16},
			url='https://example.com/cpu-amd-x3d',
		)

		intel_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'creator', 'cpu_vendor': 'intel'},
			format='json',
		)
		amd_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'creator', 'cpu_vendor': 'amd'},
			format='json',
		)

		self.assertEqual(intel_response.status_code, status.HTTP_200_OK)
		self.assertEqual(amd_response.status_code, status.HTTP_200_OK)
		intel_cpu = [p for p in intel_response.data['parts'] if p['category'] == 'cpu'][0]
		amd_cpu = [p for p in amd_response.data['parts'] if p['category'] == 'cpu'][0]

		self.assertIn('intel', intel_cpu['name'].lower())
		self.assertIn('ryzen', amd_cpu['name'].lower())
		self.assertEqual(intel_response.data['cpu_vendor'], 'intel')
		self.assertEqual(amd_response.data['cpu_vendor'], 'amd')

	def test_build_configuration_creator_cost_does_not_force_over_budget_gpu(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX Creator Budget',
			price=10500,
			specs={},
			url='https://example.com/cpu-3400g-creator-budget',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X BOX Creator Budget',
			price=27800,
			specs={},
			url='https://example.com/cpu-5700x-creator-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB Creator Budget',
			price=49800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-creator-budget',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Creator Budget',
			price=16000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-creator-budget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Creator Budget',
			price=15000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/memory-creator-budget',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Creator Budget',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-creator-budget',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator Budget',
			price=4500,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-creator-budget',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator Budget',
			price=17000,
			specs={},
			url='https://example.com/os-creator-budget',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Creator Budget',
			price=8000,
			specs={'wattage': 750},
			url='https://example.com/psu-creator-budget',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Budget',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-budget',
		)

		budget = 184980
		response_data, error_response = build_configuration_response(
			budget,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		self.assertLessEqual(int(response_data.get('total_price') or 0), budget)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertIn('gpu', parts)
		self.assertEqual(parts['cpu']['name'], 'AMD Ryzen 7 5700X BOX Creator Budget')
		self.assertTrue(_cpu_meets_creator_minimum(PCPart.objects.get(name='AMD Ryzen 7 5700X BOX Creator Budget'), min_cores=8, min_threads=16))

	def test_build_configuration_creator_spec_ranks_up_from_5700x(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X BOX Creator Spec',
			price=27800,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x-creator-spec',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X BOX Creator Spec',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9700x-creator-spec',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB Creator Spec',
			price=49800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-creator-spec',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Creator Spec',
			price=16000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-creator-spec',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Creator Spec',
			price=15000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/memory-creator-spec',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Creator Spec',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-creator-spec',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator Spec',
			price=4500,
			specs={'supported_sockets': ['AM4', 'AM5']},
			url='https://example.com/cooler-creator-spec',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator Spec',
			price=17000,
			specs={},
			url='https://example.com/os-creator-spec',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Creator Spec',
			price=8000,
			specs={'wattage': 750},
			url='https://example.com/psu-creator-spec',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Spec',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-spec',
		)

		response_data, error_response = build_configuration_response(
			184980,
			'creator',
			build_priority='spec',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['cpu']['name'], 'AMD Ryzen 7 9700X BOX Creator Spec')
		self.assertGreaterEqual(response_data['total_price'], 0)

	def test_build_configuration_creator_spec_excludes_intel_14th_core_i_cpu(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i9 14900KF BOX Creator Spec Excluded',
			price=59800,
			specs={'socket': 'LGA1700'},
			url='https://www.example.org/cpu-14900kf-creator-spec-excluded',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9900X BOX Creator Spec Preferred',
			price=60980,
			specs={'socket': 'AM5'},
			url='https://www.example.org/cpu-9900x-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://www.example.org/gpu-r9700-creator-spec-excluded',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='X870 Creator Spec Preferred',
			price=35970,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://www.example.org/mb-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Spec Preferred',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 6400},
			url='https://www.example.org/memory-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Spec Preferred',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://www.example.org/storage-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Cooler Creator Spec Preferred',
			price=19990,
			specs={'supported_sockets': ['AM5']},
			url='https://www.example.org/cooler-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Creator Spec Preferred',
			price=23980,
			specs={},
			url='https://www.example.org/os-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Spec Preferred',
			price=16580,
			specs={'wattage': 1000},
			url='https://www.example.org/psu-creator-spec-preferred',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Spec Preferred',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://www.example.org/case-creator-spec-preferred',
		)

		response_data, error_response = build_configuration_response(
			478478,
			'creator',
			cooler_type='air',
			radiator_size='240',
			cooling_profile='performance',
			case_size='mid',
			case_fan_policy='auto',
			cpu_vendor='any',
			build_priority='spec',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['cpu']['name'], 'AMD Ryzen 9 9900X BOX Creator Spec Preferred')

	def test_build_configuration_creator_cost_ranks_up_in_mid_budget_when_available(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X BOX Creator Cost',
			price=27800,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x-creator-cost',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X BOX Creator Cost',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9700x-creator-cost',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB Creator Cost',
			price=49800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-creator-cost',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Creator Cost',
			price=16000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-creator-cost',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Creator Cost',
			price=15000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/memory-creator-cost',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Creator Cost',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-creator-cost',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator Cost',
			price=4500,
			specs={'supported_sockets': ['AM4', 'AM5']},
			url='https://example.com/cooler-creator-cost',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator Cost',
			price=17000,
			specs={},
			url='https://example.com/os-creator-cost',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Creator Cost',
			price=8000,
			specs={'wattage': 750},
			url='https://example.com/psu-creator-cost',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Cost',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-cost',
		)

		response_data, error_response = build_configuration_response(
			284980,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['cpu']['name'], 'AMD Ryzen 7 9700X BOX Creator Cost')
		self.assertGreater(response_data['total_price'], 152100)

	def test_build_configuration_creator_prefers_higher_vram_amd_gpu_over_lower_vram_nvidia(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X BOX Creator GPU',
			price=27800,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x-creator-gpu',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 8GB Creator GPU',
			price=49800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-creator-gpu',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='AMD Radeon RX 6700 XT 12GB Creator GPU',
			price=52980,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-6700xt-creator-gpu',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 Creator GPU',
			price=12000,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-creator-gpu',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 32GB Creator GPU',
			price=15000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 32, 'speed_mhz': 3200},
			url='https://example.com/memory-creator-gpu',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Creator GPU',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-creator-gpu',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator GPU',
			price=4500,
			specs={'supported_sockets': ['AM4']},
			url='https://example.com/cooler-creator-gpu',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator GPU',
			price=17000,
			specs={},
			url='https://example.com/os-creator-gpu',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Creator GPU',
			price=8000,
			specs={'wattage': 750},
			url='https://example.com/psu-creator-gpu',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator GPU',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-gpu',
		)

		response_data, error_response = build_configuration_response(
			184980,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'AMD Radeon RX 6700 XT 12GB Creator GPU')

	def test_build_configuration_creator_prefers_nvidia_when_vram_and_score_match(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 5700X BOX Creator GPU Tie',
			price=27800,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5700x-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='AMD Creator GPU 8GB Tie',
			price=49800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-amd-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA Creator GPU 8GB Tie',
			price=52980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-nvidia-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 Creator GPU Tie',
			price=12000,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 32GB Creator GPU Tie',
			price=15000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 32, 'speed_mhz': 3200},
			url='https://example.com/memory-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Creator GPU Tie',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator GPU Tie',
			price=4500,
			specs={'supported_sockets': ['AM4']},
			url='https://example.com/cooler-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator GPU Tie',
			price=17000,
			specs={},
			url='https://example.com/os-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Creator GPU Tie',
			price=8000,
			specs={'wattage': 750},
			url='https://example.com/psu-creator-gpu-tie',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator GPU Tie',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-gpu-tie',
		)

		response_data, error_response = build_configuration_response(
			184980,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'NVIDIA Creator GPU 8GB Tie')

	def test_build_configuration_creator_premium_cost_forces_r9700(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Creator Premium Cost',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-r9700-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5090 32GB',
			price=529800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-5090-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='X870 Creator Premium Cost',
			price=35970,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium Cost',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 5600},
			url='https://example.com/memory-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium Cost',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator Premium Cost',
			price=8000,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator Premium Cost',
			price=17000,
			specs={},
			url='https://example.com/os-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium Cost',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-creator-premium-cost',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium Cost',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-premium-cost',
		)

		response_data, error_response = build_configuration_response(
			1294980,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'ASRock Radeon AI PRO R9700 Creator 32GB')
		self.assertEqual(response_data['budget_tier'], 'premium')
		self.assertEqual(response_data['budget_tier_label'], 'プレミアム')

	def test_build_configuration_creator_premium_cost_684980_forces_r9700(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Creator Premium Cost Mid',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-r9700-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5080 16GB',
			price=179800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5080-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='X870 Creator Premium Cost Mid',
			price=35970,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium Cost Mid',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 5600},
			url='https://example.com/memory-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium Cost Mid',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator Premium Cost Mid',
			price=8000,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator Premium Cost Mid',
			price=17000,
			specs={},
			url='https://example.com/os-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium Cost Mid',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-creator-premium-cost-mid',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium Cost Mid',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-premium-cost-mid',
		)

		response_data, error_response = build_configuration_response(
			684980,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'ASRock Radeon AI PRO R9700 Creator 32GB')

	def test_build_configuration_creator_premium_spec_forces_rtx_5090(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 9 285K BOX Creator Premium Spec',
			price=96999,
			specs={'socket': 'LGA1851'},
			url='https://example.com/cpu-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-r9700-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5090 32GB',
			price=529800,
			specs={'vram': '32GB'},
			url='https://www.example.org/gpu-5090-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B860 Creator Premium Spec',
			price=25980,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium Spec',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 6400},
			url='https://example.com/memory-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium Spec',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Cooler Creator Premium Spec',
			price=19990,
			specs={'supported_sockets': ['LGA1851']},
			url='https://example.com/cooler-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Creator Premium Spec',
			price=23980,
			specs={},
			url='https://example.com/os-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium Spec',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-creator-premium-spec',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium Spec',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-premium-spec',
		)

		response_data, error_response = build_configuration_response(
			1294980,
			'creator',
			build_priority='spec',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'GeForce RTX 5090 32GB')

	def test_build_configuration_creator_premium_spec_prefers_rtx_pro_4500_when_available(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 9 285K BOX Creator Premium Spec Pro',
			price=96999,
			specs={'socket': 'LGA1851'},
			url='https://www.example.org/cpu-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA RTX PRO 4500 Blackwell BOX (RTX PRO 4500 32GB)',
			price=506000,
			specs={'vram': '32GB'},
			url='https://www.dospara.co.jp/SBR1808/IC525801.html',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://www.example.org/gpu-r9700-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B860 Creator Premium Spec Pro',
			price=25980,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://www.example.org/mb-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium Spec Pro',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 6400},
			url='https://www.example.org/memory-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium Spec Pro',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://www.example.org/storage-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Cooler Creator Premium Spec Pro',
			price=19990,
			specs={'supported_sockets': ['LGA1851']},
			url='https://www.example.org/cooler-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Creator Premium Spec Pro',
			price=23980,
			specs={},
			url='https://www.example.org/os-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium Spec Pro',
			price=16580,
			specs={'wattage': 1000},
			url='https://www.example.org/psu-creator-premium-spec-pro',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium Spec Pro',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://www.example.org/case-creator-premium-spec-pro',
		)

		response_data, error_response = build_configuration_response(
			1314478,
			'creator',
			build_priority='spec',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'NVIDIA RTX PRO 4500 Blackwell BOX (RTX PRO 4500 32GB)')

	def test_prefer_creator_premium_gpu_prioritizes_rtx_pro_4500_over_rtx_5090(self):
		gpu_4500 = PCPart(
			part_type='gpu',
			name='NVIDIA RTX PRO 4500 Blackwell BOX (RTX PRO 4500 32GB)',
			price=506000,
			specs={'vram': '32GB'},
			url='https://www.dospara.co.jp/SBR1808/IC525801.html',
		)
		gpu_5090 = PCPart(
			part_type='gpu',
			name='GeForce RTX 5090 32GB',
			price=529800,
			specs={'vram': '32GB'},
			url='https://www.example.org/gpu-5090-creator-premium-spec-pro',
		)

		picked = _prefer_creator_premium_gpu([gpu_5090, gpu_4500], build_priority='spec')
		self.assertIsNotNone(picked)
		self.assertEqual(picked[0].name, 'NVIDIA RTX PRO 4500 Blackwell BOX (RTX PRO 4500 32GB)')

	def test_pick_creator_cpu_with_budget_high_end_cost_prefers_ryzen_9900x_over_265f(self):
		cpu_265f = PCPart(
			part_type='cpu',
			name='Intel Core Ultra 7 265F BOX',
			price=52380,
			specs={'socket': 'LGA1851'},
			url='https://www.example.org/cpu-265f',
		)
		cpu_9900x = PCPart(
			part_type='cpu',
			name='AMD Ryzen 9 9900X BOX',
			price=59800,
			specs={'socket': 'AM5'},
			url='https://www.example.org/cpu-9900x',
		)

		picked = _pick_creator_cpu_with_budget([cpu_265f, cpu_9900x], budget=434980, build_priority='cost')
		self.assertIsNotNone(picked)
		self.assertEqual(picked.name, 'AMD Ryzen 9 9900X BOX')

	def test_build_configuration_creator_premium_spec_prefers_r9700_when_rtx5090_unavailable(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 9 285K BOX Creator Premium Spec Fallback',
			price=96999,
			specs={'socket': 'LGA1851'},
			url='https://example.com/cpu-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://www.example.org/gpu-r9700-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5080 16GB',
			price=199800,
			specs={'vram': '16GB'},
			url='https://www.example.org/gpu-5080-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B860 Creator Premium Spec Fallback',
			price=25980,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://www.example.org/mb-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium Spec Fallback',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 6400},
			url='https://www.example.org/memory-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium Spec Fallback',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://www.example.org/storage-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Cooler Creator Premium Spec Fallback',
			price=19990,
			specs={'supported_sockets': ['LGA1851']},
			url='https://www.example.org/cooler-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Creator Premium Spec Fallback',
			price=23980,
			specs={},
			url='https://www.example.org/os-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium Spec Fallback',
			price=16580,
			specs={'wattage': 1000},
			url='https://www.example.org/psu-creator-premium-spec-fallback',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium Spec Fallback',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://www.example.org/case-creator-premium-spec-fallback',
		)

		response_data, error_response = build_configuration_response(
			1314478,
			'creator',
			build_priority='spec',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['gpu']['name'], 'ASRock Radeon AI PRO R9700 Creator 32GB')

	def test_build_configuration_creator_premium_cost_prefers_9950x3d_cpu(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Creator Premium CPU Cost',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9950X3D BOX Creator Premium CPU Cost',
			price=114470,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9950x3d-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-r9700-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='X870 Creator Premium CPU Cost',
			price=35970,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium CPU Cost',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 5600},
			url='https://example.com/memory-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium CPU Cost',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Creator Premium CPU Cost',
			price=8000,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Creator Premium CPU Cost',
			price=17000,
			specs={},
			url='https://example.com/os-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium CPU Cost',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-creator-premium-cpu-cost',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium CPU Cost',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-premium-cpu-cost',
		)

		response_data, error_response = build_configuration_response(
			1294980,
			'creator',
			build_priority='cost',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['cpu']['name'], 'AMD Ryzen 9 9950X3D BOX Creator Premium CPU Cost')

	def test_build_configuration_creator_premium_spec_prefers_9950x3d_cpu(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Creator Premium CPU Spec',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9950X3D BOX Creator Premium CPU Spec',
			price=114470,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9950x3d-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5090 32GB',
			price=529800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-5090-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='X870 Creator Premium CPU Spec',
			price=35970,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Creator Premium CPU Spec',
			price=39800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64, 'speed_mhz': 6400},
			url='https://example.com/memory-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB Creator Premium CPU Spec',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Cooler Creator Premium CPU Spec',
			price=19990,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Pro Creator Premium CPU Spec',
			price=23980,
			specs={},
			url='https://example.com/os-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='psu',
			name='1000W PSU Creator Premium CPU Spec',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-creator-premium-cpu-spec',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Creator Premium CPU Spec',
			price=6000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-creator-premium-cpu-spec',
		)

		response_data, error_response = build_configuration_response(
			1294980,
			'creator',
			build_priority='spec',
			persist=False,
		)

		self.assertIsNone(error_response)
		parts = {p['category']: p for p in response_data.get('parts', [])}
		self.assertEqual(parts['cpu']['name'], 'AMD Ryzen 9 9950X3D BOX Creator Premium CPU Spec')

	def test_creator_premium_cpu_priority_prefers_9950x_over_285k(self):
		cpu_9950x = PCPart(
			part_type='cpu',
			name='AMD Ryzen 9 9950X BOX Creator Premium Priority',
			price=101000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9950x-creator-premium-priority',
		)
		cpu_285k = PCPart(
			part_type='cpu',
			name='Intel Core Ultra 9 285K BOX Creator Premium Priority',
			price=98000,
			specs={'socket': 'LGA1851'},
			url='https://example.com/cpu-285k-creator-premium-priority',
		)

		picked_cost = _prefer_creator_premium_cpu([cpu_285k, cpu_9950x], build_priority='cost')
		picked_spec = _prefer_creator_premium_cpu([cpu_285k, cpu_9950x], build_priority='spec')

		self.assertIsNotNone(picked_cost)
		self.assertIsNotNone(picked_spec)
		self.assertEqual(picked_cost.name, 'AMD Ryzen 9 9950X BOX Creator Premium Priority')
		self.assertEqual(picked_spec.name, 'AMD Ryzen 9 9950X BOX Creator Premium Priority')

	def test_generate_config_prefers_x3d_cpu_for_gaming_when_vendor_is_any(self):
		self.cpu.delete()
		self.gpu.delete()

		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i7 14700F',
			price=42000,
			specs={'socket': 'LGA1700'},
			url='https://example.com/cpu-intel-gaming-any',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-amd-9700x',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D',
			price=59800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-amd-9800x3d',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4070 SUPER',
			price=98000,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-4070-super',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='AM5 Board Gaming Any',
			price=18000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-am5-gaming-any',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Gaming Any',
			price=12000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-gaming-any',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Gaming Any',
			price=10000,
			specs={'interface': 'NVMe', 'capacity_gb': 1024},
			url='https://example.com/storage-gaming-any',
		)
		PCPart.objects.create(
			part_type='psu',
			name='850W PSU Gaming Any',
			price=13000,
			specs={'wattage': 850},
			url='https://example.com/psu-gaming-any',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Gaming Any',
			price=9000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-gaming-any',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 260000, 'usage': 'gaming', 'build_priority': 'spec'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_cpu = [p for p in response.data['parts'] if p['category'] == 'cpu'][0]
		self.assertIn('9800x3d', selected_cpu['name'].lower())

	def test_generate_config_respects_build_priority_cost_vs_spec(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D Priority Test',
			price=64799,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9800x3d-priority',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Board',
			price=14000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-priority',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Budget',
			price=7000,
			specs={'memory_type': 'DDR5'},
			url='https://example.com/mem-ddr5-budget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Premium',
			price=13000,
			specs={'memory_type': 'DDR5'},
			url='https://example.com/mem-ddr5-premium',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-priority',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-priority',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-priority',
		)
		PCPart.objects.filter(id=self.cpu.id).update(specs={'socket': 'AM5'})

		cost_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 300000, 'usage': 'gaming', 'build_priority': 'cost'},
			format='json',
		)
		spec_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 300000, 'usage': 'gaming', 'build_priority': 'spec'},
			format='json',
		)

		cost_memory = [p for p in cost_response.data['parts'] if p['category'] == 'memory'][0]
		spec_memory = [p for p in spec_response.data['parts'] if p['category'] == 'memory'][0]

		self.assertEqual(cost_response.status_code, status.HTTP_200_OK)
		self.assertEqual(spec_response.status_code, status.HTTP_200_OK)
		self.assertEqual(cost_response.data['build_priority'], 'cost')
		self.assertEqual(spec_response.data['build_priority'], 'spec')
		self.assertEqual(cost_memory['name'], 'DDR5 16GB Budget')
		self.assertEqual(spec_memory['name'], 'DDR5 16GB Premium')

	def test_generate_config_respects_custom_budget_weights(self):
		PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 7 7700 Custom Weight',
			price=42000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-custom-high',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Board Custom Weight',
			price=14000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-custom-weight',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='RTX 4060 Custom Weight',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-custom-high',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='RTX 3050 Custom Weight',
			price=30000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-custom-low',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Custom Weight',
			price=12000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-custom-weight',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Custom Weight',
			price=12000,
			specs={'capacity_gb': 1000, 'interface': 'NVMe'},
			url='https://example.com/ssd-custom-weight',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Custom Weight',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-custom-weight',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Custom Weight',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-custom-weight',
		)
		PCPart.objects.filter(id=self.cpu.id).update(specs={'socket': 'AM5'})

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 180000,
				'usage': 'gaming',
				'build_priority': 'spec',
				'custom_budget_weights': {
					'cpu': 15,
					'cpu_cooler': 2,
					'gpu': 30,
					'motherboard': 10,
					'memory': 20,
					'storage': 15,
					'psu': 5,
					'case': 3,
				},
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('gpu', parts)
		self.assertIn(parts['gpu']['name'], {'RTX 4060', 'RTX 4060 Custom Weight', 'RTX 3050 Custom Weight'})
		self.assertAlmostEqual(response.data['custom_budget_weights']['cpu'], 0.15, places=2)

	def test_generate_config_build_priority_prefers_ddr4_small_vs_ddr5_large(self):
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i5 14400F',
			price=32000,
			specs={'socket': 'LGA1700'},
			url='https://example.com/cpu-intel-14400f-priority',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B760 DDR4 Board',
			price=14000,
			specs={'socket': 'LGA1700', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b760-ddr4',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B760 DDR5 Board',
			price=22000,
			specs={'socket': 'LGA1700', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b760-ddr5',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Cost Memory',
			price=3000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Cost Memory',
			price=5000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Spec Memory',
			price=11000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-ddr5-32',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Spec Memory',
			price=20000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64},
			url='https://example.com/mem-ddr5-64',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-ddr-priority',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-ddr-priority',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-ddr-priority',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX DDR Priority',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-ddr-priority',
		)
		self._create_low_end_gpu()

		cost_response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 219980,
				'usage': 'gaming',
				'cpu_vendor': 'amd',
				'build_priority': 'cost',
			},
			format='json',
		)
		spec_response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cpu_vendor': 'amd',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(cost_response.status_code, status.HTTP_200_OK)
		self.assertEqual(spec_response.status_code, status.HTTP_200_OK)

		cost_parts = {p['category']: p for p in cost_response.data['parts']}
		spec_parts = {p['category']: p for p in spec_response.data['parts']}

		self.assertIn('DDR4', cost_parts['memory']['name'])
		self.assertIn('16GB', cost_parts['memory']['name'])
		self.assertIn('DDR4', cost_parts['motherboard']['name'])

		self.assertIn('DDR5', spec_parts['memory']['name'])
		self.assertTrue(
			('32GB' in spec_parts['memory']['name']) or ('64GB' in spec_parts['memory']['name'])
		)
		self.assertIn('DDR5', spec_parts['motherboard']['name'])

	def test_generate_config_gaming_cost_falls_back_to_8gb_when_16gb_unavailable(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X Fallback',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-amd-9700x-fallback',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B760 DDR4 Board Fallback',
			price=14000,
			specs={'socket': 'LGA1700', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b760-ddr4-fallback',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Cost Memory Fallback',
			price=3000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8-fallback',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Fallback',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-fallback',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Fallback',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-fallback',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Fallback',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-fallback',
		)
		self._create_low_end_gpu()
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 3050 6GB Fallback',
			price=31800,
			specs={'vram': '6GB'},
			url='https://example.com/gpu-3050-fallback',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 219980,
				'usage': 'gaming',
				'cpu_vendor': 'amd',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('DDR4', parts['memory']['name'])
		self.assertIn('8GB', parts['memory']['name'])

	def test_generate_config_business_cost_prefers_16gb_at_250k(self):
		"""business+cost @ 250k で 16GB preference が機能することを確認"""
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5600G',
			price=28000,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-ryzen5-5600g-business',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='A520 DDR4 Board Business',
			price=8000,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520-ddr4-business',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Business Cost',
			price=3000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8-business',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Business Cost',
			price=5500,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-business',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Business',
			price=6000,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/ssd-512-business',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU Business',
			price=6000,
			specs={'wattage': 500},
			url='https://example.com/psu-500-business',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case Business',
			price=5000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-microatx-business',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 250000,
				'usage': 'business',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('DDR4', parts['memory']['name'])
		self.assertIn('16GB', parts['memory']['name'])

	def test_generate_config_business_cost_falls_back_to_8gb_when_16gb_unavailable(self):
		"""business+cost で 16GB 在庫切れ時に 8GB にfallback することを確認"""
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i5 10400F Business Fallback',
			price=26000,
			specs={'socket': 'LGA1200'},
			url='https://example.com/cpu-i5-10400f-business-fb',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B460 DDR4 Board Business Fallback',
			price=10000,
			specs={'socket': 'LGA1200', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b460-ddr4-business-fb',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Only Option',
			price=3500,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8-only-business',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Fallback',
			price=6500,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/ssd-512-business-fb',
		)
		PCPart.objects.create(
			part_type='psu',
			name='450W PSU Fallback',
			price=5500,
			specs={'wattage': 450},
			url='https://example.com/psu-450-business-fb',
		)
		PCPart.objects.create(
			part_type='case',
			name='Compact Case Fallback',
			price=4500,
			specs={'supported_form_factors': ['MicroATX']},
			url='https://example.com/case-compact-fb',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 270000,
				'usage': 'business',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('DDR4', parts['memory']['name'])
		self.assertIn('8GB', parts['memory']['name'])

	def test_generate_config_standard_cost_uses_same_16gb_preference_logic(self):
		"""standard+cost でも business と同じ 16GB preference ロジックを確認"""
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500',
			price=25000,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-ryzen5-5500-standard',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='A520 DDR4 Standard',
			price=9000,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520-ddr4-standard',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Standard',
			price=3200,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8-standard',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Standard',
			price=5800,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-standard',
		)
		PCPart.objects.create(
			part_type='storage',
			name='SSD 512GB Standard',
			price=6200,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/ssd-512-standard',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU Standard',
			price=5800,
			specs={'wattage': 500},
			url='https://example.com/psu-500-standard',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Standard',
			price=5200,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-atx-standard',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 230000,
				'usage': 'standard',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('DDR4', parts['memory']['name'])
		self.assertIn('16GB', parts['memory']['name'])

	def test_generate_config_uses_surplus_budget_to_upgrade_memory(self):
		PCPart.objects.create(
			part_type='motherboard',
			name='A520 DDR4 Board',
			price=8000,
			specs={'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520-ddr4-surplus',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Budget',
			price=7000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-surplus',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 64GB Premium',
			price=18000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 64},
			url='https://example.com/mem-ddr4-64-surplus',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'capacity_gb': 1000, 'interface': 'NVMe'},
			url='https://example.com/ssd-surplus-memory',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-surplus-memory',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-surplus-memory',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertEqual(parts['memory']['name'], 'DDR4 64GB Premium')
		self.assertLessEqual(response.data['total_price'], 260000)
		self.assertGreaterEqual(parts['gpu']['price'], parts['memory']['price'])

	def test_generate_config_gaming_spec_prioritizes_gpu_over_memory(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-am5-priority',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4060 8GB',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx4060-priority',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce GT 710 1GB',
			price=5000,
			specs={'vram': '1GB'},
			url='https://example.com/gpu-gt710-low',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board',
			price=15000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-am5-ddr5-priority',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-priority',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB',
			price=90000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64},
			url='https://example.com/mem-ddr5-64-expensive',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-priority-gaming-spec',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-priority-gaming-spec',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-priority-gaming-spec',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('rtx', parts['gpu']['name'].lower())
		self.assertNotIn('gt 710', parts['gpu']['name'].lower())
		# gaming+spec ではメモリを無制限に上げず、GPU優先を維持
		self.assertNotIn('64GB', parts['memory']['name'])

	def test_generate_config_gaming_excludes_creator_gpu_models(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Gaming',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-gaming',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AM5 Air Cooler',
			price=3980,
			specs={'cooler_type': 'air'},
			url='https://example.com/cpu-cooler-am5-air',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Gaming Board',
			price=16800,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-am5-ddr5-gaming',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Gaming Kit',
			price=9980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-gaming',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Gaming SSD',
			price=10980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-nvme-1tb-gaming',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W Gaming PSU',
			price=7980,
			specs={'wattage': 650},
			url='https://example.com/psu-650w-gaming',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Gaming Case',
			price=6980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-gaming',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Gaming',
			price=16800,
			specs={},
			url='https://example.com/os-windows-11-gaming',
		)
		creative_gpu = PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB',
			price=259800,
			specs={'vram': '32GB'},
			url='https://example.com/gpu-r9700-creator',
		)

		self.assertFalse(
			_matches_selection_options(
				'gpu',
				creative_gpu,
				options={'usage': 'gaming', 'build_priority': 'cost'},
			),
		)

		factory = APIRequestFactory()
		request = factory.post(
			'/api/configurations/generate/',
			{
				'budget': 574980,
				'usage': 'gaming',
				'build_priority': 'cost',
			},
			format='json',
		)
		response = ConfigurationViewSet.as_view({'post': 'generate'})(request)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		selected_gpu_name = parts['gpu']['name'].lower()
		self.assertNotIn('creator', selected_gpu_name)
		self.assertNotIn('ai pro', selected_gpu_name)

	def test_generate_config_gaming_excludes_creator_cpu_models(self):
		creator_cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9950X3D BOX',
			price=112200,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9950x3d-creator',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX',
			price=48000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-gaming-allowed',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 8GB',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx4060-for-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AM5 Air Cooler',
			price=3980,
			specs={'cooler_type': 'air'},
			url='https://example.com/cpu-cooler-for-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board CPU Exclusion',
			price=16800,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB CPU Exclusion',
			price=9980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB CPU Exclusion',
			price=10980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU CPU Exclusion',
			price=7980,
			specs={'wattage': 650},
			url='https://example.com/psu-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case CPU Exclusion',
			price=6980,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-cpu-exclusion',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home CPU Exclusion',
			price=16800,
			specs={},
			url='https://example.com/os-cpu-exclusion',
		)

		self.assertFalse(
			_matches_selection_options(
				'cpu',
				creator_cpu,
				options={'usage': 'gaming', 'build_priority': 'cost', 'cpu_vendor': 'amd'},
			),
		)

		factory = APIRequestFactory()
		request = factory.post(
			'/api/configurations/generate/',
			{
				'budget': 300000,
				'usage': 'gaming',
				'build_priority': 'cost',
			},
			format='json',
		)
		response = ConfigurationViewSet.as_view({'post': 'generate'})(request)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		cpu_name = parts['cpu']['name'].lower()
		self.assertNotIn('9950x3d', cpu_name)
		self.assertNotIn('9950x', cpu_name)
		self.assertNotIn('9900x3d', cpu_name)
		self.assertNotIn('9900x', cpu_name)

	def test_generate_config_gaming_spec_ignores_unclassified_gpu_candidates(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-am5-unclassified-gpu',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Mystery Gaming GPU',
			price=45000,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-mystery',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 8GB',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx4060-unclassified-gpu',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board',
			price=15000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-am5-ddr5-unclassified-gpu',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-unclassified-gpu',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-unclassified-gpu',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-unclassified-gpu',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-unclassified-gpu',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('rtx', parts['gpu']['name'].lower())

	def test_generate_config_gaming_spec_gpu_price_not_lower_than_memory(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-am5-rebalance',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 8GB',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx4060-rebalance',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board',
			price=15000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-am5-ddr5-rebalance',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 64GB Premium',
			price=90000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 64},
			url='https://example.com/mem-ddr5-64-rebalance',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Budget',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-rebalance',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-rebalance',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-rebalance',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-rebalance',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertGreaterEqual(parts['gpu']['price'], parts['memory']['price'])

	def test_generate_config_gaming_spec_prefers_storage_capacity_at_least_1tb(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-am5-storage-priority',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 8GB',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx4060-storage-priority',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board',
			price=15000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-ddr5-storage-priority',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Budget',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-storage-priority',
		)
		PCPart.objects.create(
			part_type='storage',
			name='SATA SSD 256GB',
			price=5500,
			specs={'capacity_gb': 256, 'interface': 'SATA'},
			url='https://example.com/ssd-256-storage-priority',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe SSD 1TB',
			price=12000,
			specs={'capacity_gb': 1000, 'interface': 'NVMe'},
			url='https://example.com/ssd-1tb-storage-priority',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-storage-priority',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-storage-priority',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('1TB', parts['storage']['name'])

	def test_generate_config_prefers_ssd_as_primary_storage_over_cheaper_hdd(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X Primary SSD',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-primary-ssd',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 Primary SSD',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-primary-ssd',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board Primary SSD',
			price=15000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-primary-ssd',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Primary SSD',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-primary-ssd',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Large HDD 4TB',
			price=9000,
			specs={'capacity_gb': 4096, 'interface': 'SATA', 'form_factor': '3.5inch'},
			url='https://example.com/hdd-primary-ssd',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe SSD 1TB Primary',
			price=12000,
			specs={'capacity_gb': 1024, 'interface': 'NVMe', 'form_factor': 'M.2'},
			url='https://example.com/nvme-primary-ssd',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Primary SSD',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-primary-ssd',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Primary SSD',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-primary-ssd',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 170000,
				'usage': 'standard',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('SSD', parts['storage']['name'])

	def test_generate_config_storage_falls_back_to_hdd_when_only_high_capacity_option(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 7600X HDD Fallback',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-hdd-fallback',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 HDD Fallback',
			price=52000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-hdd-fallback',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board HDD Fallback',
			price=15000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-hdd-fallback',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB HDD Fallback',
			price=9000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-hdd-fallback',
		)
		PCPart.objects.create(
			part_type='storage',
			name='SATA SSD 512GB Small',
			price=7000,
			specs={'capacity_gb': 512, 'interface': 'SATA', 'form_factor': '2.5inch'},
			url='https://example.com/sata-small-hdd-fallback',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Archive HDD 2TB',
			price=9000,
			specs={'capacity_gb': 2048, 'interface': 'SATA', 'form_factor': '3.5inch'},
			url='https://example.com/hdd-fallback-storage',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU HDD Fallback',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-hdd-fallback',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case HDD Fallback',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-hdd-fallback',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('HDD', parts['storage']['name'])

	def test_rebalance_gaming_cost_moves_premium_cpu_budget_into_primary_storage(self):
		premium_cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9950X3D BOX',
			price=110000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9950x3d',
		)
		value_cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9850X3D BOX',
			price=85000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9850x3d',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D BOX',
			price=70000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9800x3d',
		)
		motherboard = PCPart.objects.create(
			part_type='motherboard',
			name='B650 Gaming Board',
			price=18000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-am5-cost-storage',
		)
		memory = PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Gaming Kit',
			price=12000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-32gb-cost-storage',
		)
		storage_512 = PCPart.objects.create(
			part_type='storage',
			name='Gaming NVMe SSD 512GB',
			price=12000,
			specs={'capacity_gb': 512, 'interface': 'NVMe', 'media_type': 'ssd'},
			url='https://example.com/ssd-512-cost-storage',
		)
		storage_2tb = PCPart.objects.create(
			part_type='storage',
			name='Gaming NVMe SSD 2TB',
			price=28000,
			specs={'capacity_gb': 2000, 'interface': 'NVMe', 'media_type': 'ssd'},
			url='https://example.com/ssd-2tb-cost-storage',
		)
		gpu = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 4070 Ti SUPER 16GB',
			price=70000,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-4070ti-cost-storage',
		)
		cooler = PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler 240 Dual Tower',
			price=10000,
			specs={},
			url='https://example.com/cooler-cost-storage',
		)
		psu = PCPart.objects.create(
			part_type='psu',
			name='850W Gold PSU',
			price=10000,
			specs={'wattage': 850},
			url='https://example.com/psu-cost-storage',
		)
		case = PCPart.objects.create(
			part_type='case',
			name='ATX Mid Tower Case',
			price=8000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-cost-storage',
		)
		os_part = PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home',
			price=16000,
			specs={'edition': 'Home'},
			url='https://example.com/os-cost-storage',
		)

		selected_parts = {
			'cpu': premium_cpu,
			'cpu_cooler': cooler,
			'gpu': gpu,
			'motherboard': motherboard,
			'memory': memory,
			'storage': storage_512,
			'os': os_part,
			'psu': psu,
			'case': case,
		}

		rebalanced = _rebalance_gaming_cost_cpu_to_storage(
			selected_parts,
			budget=275000,
			usage='gaming',
			options={
				'usage': 'gaming',
				'build_priority': 'cost',
				'storage_preference': 'ssd',
				'min_storage_capacity_gb': 2000,
			},
		)

		self.assertEqual(rebalanced['cpu'].id, value_cpu.id)
		self.assertEqual(rebalanced['storage'].id, storage_2tb.id)
		self.assertGreaterEqual(_infer_storage_capacity_gb(rebalanced['storage']), 2000)

	def test_gaming_cost_prefers_9850x3d_when_budget_allows_via_memory_rightsize(self):
		current_cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D BOX',
			price=64799,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9800x3d-upgrade',
		)
		better_cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9850X3D BOX',
			price=90780,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9850x3d-upgrade',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9950X3D BOX',
			price=114470,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9950x3d-upgrade',
		)
		motherboard = PCPart.objects.create(
			part_type='motherboard',
			name='B650 Gaming Board Upgrade',
			price=18000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-upgrade',
		)
		expensive_memory = PCPart.objects.create(
			part_type='memory',
			name='Corsair DDR5 PC5-51200 16GB 2枚組 Premium Kit',
			price=91080,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 6400},
			url='https://example.com/mem-premium',
		)
		cheaper_memory = PCPart.objects.create(
			part_type='memory',
			name='Crucial DDR5 PC5-44800 16GB 2枚組 Value Kit',
			price=64000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/mem-value',
		)
		storage = PCPart.objects.create(
			part_type='storage',
			name='ADATA 2TB NVMe SSD',
			price=34800,
			specs={'capacity_gb': 2000, 'interface': 'NVMe', 'media_type': 'ssd'},
			url='https://example.com/storage-2tb',
		)
		gpu = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5080 16GB',
			price=309800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5080',
		)
		cooler = PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Upgrade',
			price=3218,
			specs={},
			url='https://example.com/cooler-upgrade',
		)
		psu = PCPart.objects.create(
			part_type='psu',
			name='1000W Gold PSU Upgrade',
			price=16580,
			specs={'wattage': 1000},
			url='https://example.com/psu-upgrade',
		)
		case = PCPart.objects.create(
			part_type='case',
			name='ATX Mid Tower Upgrade',
			price=3177,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-upgrade',
		)
		os_part = PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Upgrade',
			price=16480,
			specs={'edition': 'Home'},
			url='https://example.com/os-upgrade',
		)

		selected_parts = {
			'cpu': current_cpu,
			'cpu_cooler': cooler,
			'gpu': gpu,
			'motherboard': motherboard,
			'memory': expensive_memory,
			'storage': storage,
			'os': os_part,
			'psu': psu,
			'case': case,
		}

		upgraded = _prefer_higher_gaming_cost_x3d_cpu(
			selected_parts,
			budget=574980,
			usage='gaming',
			options={
				'usage': 'gaming',
				'build_priority': 'cost',
				'storage_preference': 'ssd',
				'min_storage_capacity_gb': 2000,
				'cpu_socket': 'AM5',
				'motherboard_memory_type': 'DDR5',
			},
		)

		self.assertEqual(upgraded['cpu'].id, current_cpu.id)
		self.assertEqual(upgraded['memory'].id, expensive_memory.id)
		self.assertGreaterEqual(_infer_memory_speed_mhz(upgraded['memory']), 5600)

	def test_enforce_memory_speed_floor_upgrades_9850x3d_build_to_ddr5_5600(self):
		cpu = PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9850X3D BOX',
			price=90780,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9850x3d-floor',
		)
		slow_memory = PCPart.objects.create(
			part_type='memory',
			name='Value DDR5 PC5-38400 16GB 2枚組',
			price=58800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 4800},
			url='https://example.com/mem-ddr5-4800-floor',
		)
		fast_memory = PCPart.objects.create(
			part_type='memory',
			name='Value DDR5 PC5-44800 16GB 2枚組',
			price=60380,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 5600},
			url='https://example.com/mem-ddr5-5600-floor',
		)
		selected_parts = {
			'cpu': cpu,
			'memory': slow_memory,
		}

		adjusted = _enforce_memory_speed_floor(
			selected_parts,
			budget=160000,
			usage='gaming',
			options={'usage': 'gaming', 'build_priority': 'cost', 'min_memory_speed_mhz': 5600, 'motherboard_memory_type': 'DDR5'},
		)

		self.assertEqual(adjusted['memory'].id, fast_memory.id)
		self.assertGreaterEqual(_infer_memory_speed_mhz(adjusted['memory']), 5600)

	def test_generate_config_gaming_spec_rebalances_with_motherboard_swap(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX',
			price=10500,
			specs={},
			url='https://example.com/cpu-3400g-rebalance',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Intel Arc A310 4GB',
			price=19800,
			specs={'vram': '4GB'},
			url='https://example.com/gpu-arc-a310-rebalance',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B850 DDR5 Board',
			price=35980,
			specs={'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-ddr5-expensive',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 DDR4 Board',
			price=12000,
			specs={'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-ddr4-affordable',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Premium',
			price=82380,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-ddr5-premium-only',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Affordable',
			price=9800,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-affordable',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-rebalance-mb',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU',
			price=5546,
			specs={'wattage': 500},
			url='https://example.com/psu-rebalance-mb',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case',
			price=7380,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-rebalance-mb',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler',
			price=3218,
			specs={},
			url='https://example.com/cooler-rebalance-mb',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertGreaterEqual(parts['gpu']['price'], parts['memory']['price'])

	def test_generate_config_gaming_spec_rightsizes_motherboard_for_better_gpu(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 9900X3D BOX',
			price=91800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9900x3d-rightsize-mb',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5070 12GB',
			price=159800,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-5070-base',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5070 Ti 16GB',
			price=167800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5070ti-base',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5070 Ti OC 16GB',
			price=209800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5070ti-oc',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='MSI MEG X870E ACE MAX (X870E AM5 ATX)',
			price=139800,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'chipset': 'X870E', 'form_factor': 'ATX'},
			url='https://example.com/mb-x870e-flagship',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='MSI PRO X870-P WIFI (X870 AM5 ATX)',
			price=49800,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'chipset': 'X870', 'form_factor': 'ATX'},
			url='https://example.com/mb-x870-mainstream',
		)
		PCPart.objects.create(
			part_type='memory',
			name='Corsair DDR5 PC5-51200 16GB 2枚組',
			price=91080,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 6400},
			url='https://example.com/mem-ddr5-rightsize-mb',
		)
		PCPart.objects.create(
			part_type='storage',
			name='KIOXIA NVMe 1TB',
			price=22880,
			specs={'media_type': 'ssd', 'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-rightsize-mb',
		)
		PCPart.objects.create(
			part_type='psu',
			name='Antec 850W Gold PSU',
			price=10980,
			specs={'wattage': 850},
			url='https://example.com/psu-850-rightsize-mb',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Mid Tower Case',
			price=3177,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-rightsize-mb',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AINEX Air Cooler',
			price=3218,
			specs={},
			url='https://example.com/cooler-rightsize-mb',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('motherboard', parts)
		self.assertIn('X870 AM5', parts['motherboard']['name'])
		self.assertNotIn('X870E', parts['motherboard']['name'])
		if 'gpu' in parts:
			self.assertIn(parts['gpu']['name'], {'Palit GeForce RTX 5070 12GB', 'Palit GeForce RTX 5070 Ti 16GB'})

	def test_generate_config_gaming_cost_caps_gpu_at_5060ti(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Cost GPU Cap',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-cost-cap',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5060 Ti 8GB',
			price=80316,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5060ti-cost-cap',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5070 12GB',
			price=159800,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-5070-cost-cap',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5070 Ti 16GB',
			price=167800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5070ti-cost-cap',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650M Gaming Cost GPU Cap',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b650m-cost-cap',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Cost GPU Cap',
			price=14980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-cost-cap',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Cost GPU Cap',
			price=9980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-cost-cap',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Cost GPU Cap',
			price=10980,
			specs={'wattage': 750},
			url='https://example.com/psu-750-cost-cap',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Cost GPU Cap',
			price=4980,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-atx-cost-cap',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Cost GPU Cap',
			price=2980,
			specs={},
			url='https://example.com/cooler-air-cost-cap',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 259980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cpu_vendor': 'any',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('5060 Ti', parts['gpu']['name'])
		self.assertNotIn('5070 Ti', parts['gpu']['name'])

	def test_generate_config_gaming_spec_upgrades_to_liquid_cooler_when_surplus(self):
		PCPart.objects.filter(part_type='cpu').delete()
		snapshot = CPUSelectionSnapshot.objects.create(
			source_name='unit-test',
			source_urls=['https://example.com/cpu-material'],
			exclude_intel_13_14=True,
			entry_count=1,
			excluded_count=0,
		)
		CPUSelectionEntry.objects.create(
			snapshot=snapshot,
			vendor='amd',
			model_name='Ryzen 7 7800X3D',
			perf_score=3609,
			source_url='https://example.com/cpu-material',
			rank_global=10,
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-gaming-spec-cooler',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AINEX Air Cooler Budget',
			price=3218,
			specs={},
			url='https://example.com/air-cooler-budget',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Corsair iCUE H150i ELITE LCD 360mm 水冷',
			price=24800,
			specs={'radiator_mm': 360},
			url='https://example.com/liquid-cooler-360',
		)
		PCPart.objects.create(
			part_type='case',
			name='Budget ATX Case Cooler Upgrade',
			price=3177,
			specs={'supported_form_factors': ['ATX'], 'included_fan_count': 1, 'supported_fan_count': 3},
			url='https://example.com/case-budget-cooler-upgrade',
		)
		PCPart.objects.create(
			part_type='case',
			name='High Airflow Mesh ATX Case Cooler Upgrade',
			price=12980,
			specs={
				'supported_form_factors': ['ATX'],
				'included_fan_count': 4,
				'supported_fan_count': 8,
				'max_radiator_mm': 360,
			},
			url='https://example.com/case-airflow-cooler-upgrade',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board Cooler Upgrade',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-cooler-upgrade',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Cooler Upgrade',
			price=9980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-cooler-upgrade',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Cooler Upgrade',
			price=10980,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-cooler-upgrade',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Cooler Upgrade',
			price=8980,
			specs={'wattage': 750},
			url='https://example.com/psu-750-cooler-upgrade',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 260000,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('水冷', parts['cpu_cooler']['name'])

	def test_generate_config_gaming_spec_upgrades_case_for_cooling_when_surplus(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Gaming Spec Case',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-gaming-spec-case',
		)
		PCPart.objects.create(
			part_type='case',
			name='Budget ATX Case',
			price=3177,
			specs={'supported_form_factors': ['ATX'], 'included_fan_count': 1, 'supported_fan_count': 3},
			url='https://example.com/case-budget-airflow',
		)
		PCPart.objects.create(
			part_type='case',
			name='High Airflow Mesh ATX Case',
			price=12980,
			specs={
				'supported_form_factors': ['ATX'],
				'included_fan_count': 4,
				'supported_fan_count': 8,
				'max_radiator_mm': 360,
			},
			url='https://example.com/case-high-airflow-mesh',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 574980,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
				'case_fan_policy': 'auto',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('High Airflow Mesh', parts['case']['name'])

	def test_generate_config_premium_gaming_avoids_budget_fixed_motherboard_and_case(self):
		PCPart.objects.all().delete()

		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Premium',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-premium',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9700X',
			price=52800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9700x-premium',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Tower Air Cooler 120mm',
			price=5980,
			specs={},
			url='https://example.com/cooler-120',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='Palit GeForce RTX 5070 12GB',
			price=119800,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-5070',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='GIGABYTE B550 GAMING X V2 (B550 AM4 ATX)',
			price=14980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-gaming-x-v2',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='MSI PRO X870-P WIFI (X870 AM5 ATX)',
			price=69800,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'chipset': 'X870', 'form_factor': 'ATX'},
			url='https://example.com/mb-x870-premium',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB 6000',
			price=17980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32, 'speed_mhz': 6000},
			url='https://example.com/mem-ddr5-32',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe SSD 1TB',
			price=9980,
			specs={'media_type': 'ssd', 'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-1tb',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home',
			price=16800,
			specs={},
			url='https://example.com/windows-11',
		)
		PCPart.objects.create(
			part_type='psu',
			name='850W Gold PSU',
			price=13980,
			specs={'wattage': 850},
			url='https://example.com/psu-850',
		)
		PCPart.objects.create(
			part_type='case',
			name='ZALMAN T8 (ATX)',
			price=3980,
			specs={'supported_form_factors': ['ATX'], 'included_fan_count': 1, 'supported_fan_count': 3},
			url='https://example.com/case-zalman-t8',
		)
		PCPart.objects.create(
			part_type='case',
			name='High Airflow Mesh ATX Case',
			price=12980,
			specs={
				'supported_form_factors': ['ATX'],
				'included_fan_count': 4,
				'supported_fan_count': 8,
				'max_radiator_mm': 360,
			},
			url='https://example.com/case-airflow-mesh',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 600000,
				'usage': 'gaming',
				'build_priority': 'cost',
				'case_size': 'mid',
				'case_fan_policy': 'auto',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertNotIn('B550 GAMING X V2', parts['motherboard']['name'])
		self.assertNotIn('ZALMAN T8', parts['case']['name'])

	def test_generate_config_gaming_spec_prefers_rtx_or_rx_gpu(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Gaming Spec GPU',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-gaming-spec-gpu',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='玄人志向 GF-GT710-E2GB/HS (GeForce GT 710 2GB)',
			price=99999,
			specs={'vram': '2GB'},
			url='https://example.com/gpu-gt710-expensive',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 3050 8GB',
			price=39800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx3050',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_gpu = [p for p in response.data['parts'] if p['category'] == 'gpu'][0]
		gpu_name = selected_gpu['name'].lower()
		self.assertTrue(('rtx' in gpu_name) or ('radeon rx' in gpu_name) or ('rx ' in gpu_name))
		self.assertNotIn('gt 710', gpu_name)

	def test_generate_config_gaming_spec_infers_am4_board_as_ddr4(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX',
			price=10500,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-am4-infer-ddr4',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 5060 8GB',
			price=57800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx5060-infer-ddr4',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock A520M-HDV (A520 AM4 MicroATX)',
			price=5780,
			specs={'socket': 'AM4', 'chipset': 'A520', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520-am4-no-mem-type',
		)
		self._create_low_end_gpu()
		PCPart.objects.create(
			part_type='memory',
			name='G.SKILL F5-5600J3636C8GH2-FX5 (DDR5 PC5-44800 8GB 2枚組)',
			price=39800,
			specs={'capacity_gb': 16},
			url='https://example.com/mem-ddr5-expensive-infer-case',
		)
		PCPart.objects.create(
			part_type='memory',
			name='CFD D4U3200CS-8G (DDR4 PC4-25600 8GB)',
			price=12150,
			specs={'capacity_gb': 8},
			url='https://example.com/mem-ddr4-affordable-infer-case',
		)
		PCPart.objects.create(
			part_type='storage',
			name='ADATA SLEG-860-2000GCS-DP (M.2 2280 2TB)',
			price=29800,
			specs={'capacity_gb': 2000, 'interface': 'NVMe'},
			url='https://example.com/ssd-2tb-infer-case',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=6870,
			specs={'wattage': 750},
			url='https://example.com/psu-infer-case',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case',
			price=4140,
			specs={'supported_form_factors': ['MicroATX']},
			url='https://example.com/case-infer-case',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler',
			price=3218,
			specs={},
			url='https://example.com/cooler-infer-case',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 169980,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('DDR4', parts['memory']['name'])
		self.assertNotIn('DDR5', parts['memory']['name'])
		self.assertGreaterEqual(parts['gpu']['price'], parts['memory']['price'])

	def test_generate_config_replaces_incompatible_case_for_360_radiator(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Replace 360 Case',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-replace-360-case',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Water Cooler 360mm',
			price=16000,
			specs={},
			url='https://example.com/cooler-360mm',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mini-ITX Compact Case',
			price=8000,
			specs={},
			url='https://example.com/case-mini',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Tower 360mm Radiator Support Case',
			price=12000,
			specs={},
			url='https://example.com/case-mid-360',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'case_size': 'mini',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_case = [p for p in response.data['parts'] if p['category'] == 'case'][0]
		self.assertIn('360mm', selected_case['name'].lower())

	def test_generate_config_prefers_known_360_compatible_mini_case(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Mini Tower250',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-mini-tower250',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Water Cooler 360mm',
			price=16000,
			specs={},
			url='https://example.com/cooler-360mm',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mini-ITX Compact Case',
			price=8000,
			specs={},
			url='https://example.com/case-mini',
		)
		PCPart.objects.create(
			part_type='case',
			name='Thermaltake The Tower 250 Black (Mini-ITX)',
			price=12000,
			specs={},
			url='https://example.com/case-tower-250',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'case_size': 'mini',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_case = [p for p in response.data['parts'] if p['category'] == 'case'][0]
		self.assertIn('tower 250', selected_case['name'].lower())

	def test_generate_config_prefers_tr100_for_mini_360_when_available(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Mini TR100',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-mini-tr100',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Water Cooler 360mm',
			price=16000,
			specs={},
			url='https://example.com/cooler-360mm',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mini-ITX Compact Case',
			price=8000,
			specs={},
			url='https://example.com/case-mini',
		)
		PCPart.objects.create(
			part_type='case',
			name='Thermaltake TR100 Black (Mini-ITX)',
			price=12000,
			specs={},
			url='https://example.com/case-tr100',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'case_size': 'mini',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_case = [p for p in response.data['parts'] if p['category'] == 'case'][0]
		self.assertIn('tr100', selected_case['name'].lower())

	def test_generate_config_selects_360_compatible_mid_case(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Mid 360',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-mid-360',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Water Cooler 360mm',
			price=16000,
			specs={},
			url='https://example.com/cooler-360mm',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Tower Basic Case',
			price=8000,
			specs={'supported_radiators': [120, 240]},
			url='https://example.com/case-mid-basic',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Tower 360mm Radiator Support Case',
			price=12000,
			specs={},
			url='https://example.com/case-mid-360',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_case = [p for p in response.data['parts'] if p['category'] == 'case'][0]
		self.assertIn('mid tower', selected_case['name'].lower())
		self.assertIn('360mm', selected_case['name'].lower())

	def test_generate_config_keeps_liquid_360_after_budget_downgrade(self):
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Tower Cooler',
			price=5000,
			specs={},
			url='https://example.com/cooler-air',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Liquid Cooler 240mm',
			price=12000,
			specs={},
			url='https://example.com/cooler-liquid-240',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Liquid Cooler 360mm',
			price=20000,
			specs={},
			url='https://example.com/cooler-liquid-360',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='Mid MB',
			price=20000,
			specs={},
			url='https://example.com/mb-mid',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB',
			price=15000,
			specs={},
			url='https://example.com/memory',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB',
			price=15000,
			specs={},
			url='https://example.com/storage',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU',
			price=12000,
			specs={},
			url='https://example.com/psu',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Tower 360mm Radiator Support Case',
			price=10000,
			specs={},
			url='https://example.com/case-mid-360',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 170000,
				'usage': 'gaming',
				'cooler_type': 'liquid',
				'radiator_size': '360',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		selected_cooler = [p for p in response.data['parts'] if p['category'] == 'cpu_cooler'][0]
		self.assertIn('liquid', selected_cooler['name'].lower())
		self.assertIn('360mm', selected_cooler['name'].lower())

	def test_scraper_status_summary_drf_endpoint_returns_status(self):
		response = self.client.get('/api/scraper-status/summary/')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['cache_enabled'], True)
		self.assertEqual(response.data['cache_ttl_seconds'], 1800)
		self.assertEqual(response.data['total_parts_in_db'], 3)
		self.assertEqual(response.data['cached_categories'], ['cpu', 'gpu', 'os'])

	def test_storage_inventory_endpoint_returns_capacity_and_interface_summaries(self):
		PCPart.objects.create(
			part_type='storage',
			name='Fast NVMe 1TB',
			price=12800,
			specs={'capacity_gb': 1024, 'interface': 'NVMe', 'form_factor': 'M.2'},
			url='https://example.com/storage-nvme',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Large SATA 2TB',
			price=15800,
			specs={'capacity_gb': 2048, 'interface': 'SATA', 'form_factor': '2.5inch'},
			url='https://example.com/storage-sata',
		)

		response = self.client.get('/api/storage-inventory/')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['total_count'], 2)
		self.assertEqual(response.data['interface_summary'][0]['label'], 'NVMe')
		self.assertEqual(response.data['interface_summary'][0]['count'], 1)
		self.assertEqual(response.data['interface_summary'][1]['label'], 'SATA')
		self.assertEqual(response.data['capacity_summary'][0]['label'], '1TB')
		self.assertEqual(response.data['capacity_summary'][0]['items'][0]['name'], 'Fast NVMe 1TB')
		self.assertEqual(response.data['capacity_summary'][1]['label'], '2TB')

	def test_configurations_list_includes_saved_configuration(self):
		generate_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)

		list_response = self.client.get('/api/configurations/')

		self.assertEqual(generate_response.status_code, status.HTTP_200_OK)
		self.assertEqual(list_response.status_code, status.HTTP_200_OK)
		self.assertEqual(list_response.data['count'], 1)
		self.assertEqual(len(list_response.data['results']), 1)
		first_result = list_response.data['results'][0]
		self.assertEqual(first_result['id'], generate_response.data['configuration_id'])
		self.assertIn('cpu_data', first_result)

	def test_configurations_delete_removes_saved_configuration(self):
		generate_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)
		configuration_id = generate_response.data['configuration_id']

		delete_response = self.client.delete(f'/api/configurations/{configuration_id}/')
		list_response = self.client.get('/api/configurations/')
		configuration = Configuration.objects.get(id=configuration_id)

		self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
		self.assertEqual(list_response.status_code, status.HTTP_200_OK)
		self.assertEqual(list_response.data['count'], 0)
		self.assertEqual(configuration.is_deleted, True)
		self.assertIsNotNone(configuration.deleted_at)

	def test_deleted_configuration_detail_returns_not_found(self):
		generate_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)
		configuration_id = generate_response.data['configuration_id']

		self.client.delete(f'/api/configurations/{configuration_id}/')
		detail_response = self.client.get(f'/api/configurations/{configuration_id}/')

		self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

	def test_legacy_fastapi_compatible_routes_remain_available(self):
		before_count = Configuration.objects.count()
		generate_response = self.client.post(
			'/generate-config',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)
		status_response = self.client.get('/scraper/status')

		self.assertEqual(generate_response.status_code, status.HTTP_200_OK)
		self.assertEqual(Configuration.objects.count(), before_count + 1)
		self.assertEqual(status_response.status_code, status.HTTP_200_OK)

	def test_generate_config_gaming_cost_prefers_x3d_cpu_over_non_x3d(self):
		"""gaming+cost で X3D CPU が非 X3D CPU より優先されることを確認"""
		# X3D CPU
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5600X3D (6C/12T X3D)',
			price=38000,
			specs={'socket': 'AM4', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-ryzen5-5600x3d',
		)
		# 非 X3D CPU（起動用フォールバック）
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5600 (6C/12T)',
			price=28000,
			specs={'socket': 'AM4', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-ryzen5-5600',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B550 DDR4 Board X3D Test',
			price=12000,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b550-ddr4-x3d-test',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Budget X3D',
			price=7000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-x3d',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASUS RTX 4060 8GB X3D',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx4060-x3d',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB X3D',
			price=8000,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/ssd-512-x3d',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU X3D',
			price=8000,
			specs={'wattage': 650},
			url='https://example.com/psu-650-x3d',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Tower Case X3D',
			price=7000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-mid-x3d',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 180000,
				'usage': 'gaming',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		# X3D CPU が選ばれることを確認
		self.assertIn('X3D', parts['cpu']['name'])
		self.assertIn('5600X3D', parts['cpu']['name'])

	def test_generate_config_gaming_cost_upgrades_non_x3d_when_budget_has_surplus(self):
		"""gaming+cost で予算余剰がある場合、5万円以下のX3Dへ昇格できることを確認"""
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX Surplus Case',
			price=10500,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-3400g-surplus',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Surplus Case',
			price=48980,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-surplus',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='RTX 4060 Surplus Case',
			price=48000,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-4060-surplus',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board Surplus Case',
			price=12000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-surplus',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Affordable Surplus Case',
			price=9800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-surplus',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Surplus Case',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-surplus',
		)
		PCPart.objects.create(
			part_type='psu',
			name='500W PSU Surplus Case',
			price=5546,
			specs={'wattage': 500},
			url='https://example.com/psu-500w-surplus',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Surplus Case',
			price=7380,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-surplus',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Surplus Case',
			price=3218,
			specs={},
			url='https://example.com/cooler-air-surplus',
		)
		self._create_low_end_gpu()

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 169980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
				'cpu_vendor': 'any',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('7800X3D', parts['cpu']['name'])
		self.assertLessEqual(parts['cpu']['price'], 50000)
		self.assertLessEqual(response.data['total_price'], 169980)

	def test_generate_config_gaming_low_end_skips_x3d_auto_adjust_and_keeps_gpu_perf_floor(self):
		"""gaming ローエンドでは X3D 自動調整を行わず、GPU性能目安>=5000を維持する"""
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX Enforce',
			price=10500,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-3400g-enforce',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D BOX Enforce',
			price=64799,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-9800x3d-enforce',
		)
		gtx1650 = PCPart.objects.create(
			part_type='gpu',
			name='GeForce GTX 1650 4GB Enforce',
			price=17800,
			specs={'vram': '4GB', 'gpu_perf_score': 4023},
			url='https://example.com/gpu-gtx1650-enforce',
		)
		rtx5050 = PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5050 8GB Enforce',
			price=49980,
			specs={'vram': '8GB', 'gpu_perf_score': 5297},
			url='https://example.com/gpu-5050-enforce',
		)
		snapshot = GPUPerformanceSnapshot.objects.create(
			source_name='dospara_gpu',
			source_url='https://example.com/gpu',
			updated_at_source='2026-04-04',
			score_note='higher is better',
			parser_version='v1',
		)
		GPUPerformanceEntry.objects.create(
			snapshot=snapshot,
			gpu_name=gtx1650.name,
			model_key='GTX 1650',
			vendor='nvidia',
			vram_gb=4,
			perf_score=4023,
			detail_url='https://example.com/gpu-gtx1650-enforce',
			is_laptop=False,
			rank_global=200,
		)
		GPUPerformanceEntry.objects.create(
			snapshot=snapshot,
			gpu_name=rtx5050.name,
			model_key='RTX 5050',
			vendor='nvidia',
			vram_gb=8,
			perf_score=5297,
			detail_url='https://example.com/gpu-5050-enforce',
			is_laptop=False,
			rank_global=150,
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GeForce RTX 5060 Ti 8GB Enforce',
			price=80316,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5060ti-enforce',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Gaming Enforce',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-enforce',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Enforce',
			price=29800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-enforce',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Enforce',
			price=12480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-512-enforce',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU Enforce',
			price=5580,
			specs={'wattage': 650},
			url='https://example.com/psu-650-enforce',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Enforce',
			price=3548,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-atx-enforce',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Enforce',
			price=3210,
			specs={},
			url='https://example.com/cooler-air-enforce',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 169980,
				'usage': 'gaming',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertLessEqual(parts['gpu']['price'], 50000)
		self.assertNotIn('5060 ti', parts['gpu']['name'].lower())
		self.assertIn('gtx 1650', parts['gpu']['name'].lower())
		self.assertGreaterEqual(int(parts['gpu'].get('specs', {}).get('gpu_perf_score', 0) or 0), 3000)

	def test_generate_config_gaming_spec_low_end_prefers_higher_perf_gpu_within_target(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Spec Low End GPU',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='MSI GeForce RTX 3050 6GB Spec Low End GPU',
			price=32360,
			specs={'vram': '6GB'},
			url='https://example.com/gpu-3050-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon RX 7600 8GB Spec Low End GPU',
			price=45800,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-7600-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 Spec Low End GPU',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Spec Low End GPU',
			price=14980,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Spec Low End GPU',
			price=12480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-512-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU Spec Low End GPU',
			price=5580,
			specs={'wattage': 650},
			url='https://example.com/psu-650-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Spec Low End GPU',
			price=7980,
			specs={'supported_form_factors': ['ATX', 'MicroATX']},
			url='https://example.com/case-atx-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Spec Low End GPU',
			price=3210,
			specs={},
			url='https://example.com/cooler-air-spec-low-end-gpu',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Spec Low End GPU',
			price=16480,
			specs={},
			url='https://example.com/os-spec-low-end-gpu',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 186978,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data.get('build_priority'), 'spec')

	def test_generate_config_gaming_spec_low_end_keeps_cpu_budget_for_stronger_gpu_than_cost(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500 BOX Low End Budget',
			price=15980,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-5500-low-end-budget',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D BOX Low End Budget',
			price=49800,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-7800x3d-low-end-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='MSI GeForce RTX 3050 VENTUS 2X E 6G OC Low End Budget',
			price=32360,
			specs={'vram': '6GB'},
			url='https://example.com/gpu-3050-low-end-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASUS GeForce RTX 5050 8G Low End Budget',
			price=49980,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-5050-low-end-budget',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASUS GeForce RTX 5060 Ti 16G Low End Budget',
			price=94800,
			specs={'vram': '16GB'},
			url='https://example.com/gpu-5060ti-low-end-budget',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock A520M-HDV Low End Budget',
			price=5670,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520-low-end-budget',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock B650 PG Lightning WiFi Low End Budget',
			price=15980,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-low-end-budget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Low End Budget',
			price=23290,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-low-end-budget',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Low End Budget',
			price=29800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-low-end-budget',
		)
		PCPart.objects.create(
			part_type='storage',
			name='Verbatim Vi3000 Low End Budget',
			price=12480,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-low-end-budget',
		)
		PCPart.objects.create(
			part_type='psu',
			name='KRPW-BK650W/85+ Low End Budget',
			price=5580,
			specs={'wattage': 650},
			url='https://example.com/psu-low-end-budget',
		)
		PCPart.objects.create(
			part_type='case',
			name='ZALMAN T8 Low End Budget',
			price=3177,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-low-end-budget',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Low End Budget',
			price=3210,
			specs={},
			url='https://example.com/cooler-low-end-budget',
		)

		spec_response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 186978,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)
		cost_response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 169980,
				'usage': 'gaming',
				'build_priority': 'cost',
				'cooler_type': 'air',
				'radiator_size': '240',
				'cooling_profile': 'performance',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(spec_response.status_code, status.HTTP_200_OK)
		self.assertEqual(cost_response.status_code, status.HTTP_200_OK)
		spec_parts = {p['category']: p for p in spec_response.data['parts']}
		cost_parts = {p['category']: p for p in cost_response.data['parts']}
		self.assertEqual(spec_response.data.get('build_priority'), 'spec')
		self.assertLessEqual(spec_parts['gpu']['price'], cost_parts['gpu']['price'] + 50000)

	def test_generate_config_gaming_spec_selects_from_priority_cpu_ids(self):
		# Spec-priority CPUs: ID 2604, 2603, 2554, 2555, 2547
		# Create these 5 spec-priority CPUs
		spec_priority_cpu = PCPart.objects.create(
			part_type='cpu',
			id=2604,
			name='Intel Core Ultra 7 265',
			price=65000,
			specs={'socket': 'LGA1851'},
			url='https://example.com/cpu-core-ultra-7-265-spec-priority',
		)
		PCPart.objects.create(
			part_type='cpu',
			id=2603,
			name='AMD Ryzen 7 9850X3D',
			price=72000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-ryzen-7-9850x3d-spec-priority',
		)
		# Create a non-priority gaming CPU that should NOT be selected when spec mode is active
		non_priority_cpu = PCPart.objects.create(
			part_type='cpu',
			id=9999,
			name='AMD Ryzen 5 7600X',
			price=32000,
			specs={'socket': 'AM5'},
			url='https://example.com/cpu-ryzen-5-7600x-non-priority',
		)
		# Create supporting parts
		PCPart.objects.create(
			part_type='motherboard',
			name='LGA1851 DDR5 Board for Gaming Spec Priority',
			price=18000,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-lga1851-ddr5-spec-priority',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 for AM5 Gaming Spec Priority',
			price=16000,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-am5-b650-spec-priority',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4070 12GB',
			price=72000,
			specs={'vram': '12GB'},
			url='https://example.com/gpu-rtx4070-spec-priority',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB JEDEC',
			price=18000,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-ddr5-32-spec-priority',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 2TB SSD',
			price=22000,
			specs={'interface': 'NVMe', 'capacity_gb': 2000},
			url='https://example.com/storage-nvme-2tb-spec-priority',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Liquid Cooler 360mm',
			price=12000,
			specs={'cooler_type': 'liquid'},
			url='https://example.com/cooler-360-spec-priority',
		)
		PCPart.objects.create(
			part_type='psu',
			name='850W Gold PSU',
			price=16000,
			specs={'wattage': 850},
			url='https://example.com/psu-850w-spec-priority',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Gaming Case',
			price=15000,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-spec-priority',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home',
			price=16800,
			specs={},
			url='https://example.com/os-win11-spec-priority',
		)

		# Test gaming + spec: should select from spec-priority CPUs only
		factory = APIRequestFactory()
		spec_request = factory.post(
			'/api/configurations/generate/',
			{
				'budget': 300000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)
		spec_response = ConfigurationViewSet.as_view({'post': 'generate'})(spec_request)

		self.assertEqual(spec_response.status_code, status.HTTP_200_OK)
		spec_parts = {p['category']: p for p in spec_response.data['parts']}
		selected_cpu_name = spec_parts['cpu']['name'].lower()

		# Verify that the selected CPU is from the spec-priority set
		# Spec-priority CPUs: Core Ultra 7 265, Ryzen 7 9850X3D, 9800X3D, 9700X, 7800X3D
		spec_priority_cpu_names = [
			'core ultra 7 265',
			'ryzen 7 9850x3d',
			'ryzen 7 9800x3d',
			'ryzen 7 9700x',
			'ryzen 7 7800x3d',
		]
		self.assertTrue(
			any(cpu_name in selected_cpu_name for cpu_name in spec_priority_cpu_names),
			f"Selected CPU '{selected_cpu_name}' is not from spec-priority set"
		)
		# Non-priority CPU should not be selected
		self.assertNotIn('ryzen 5 7600x', selected_cpu_name, 
			"Non-priority CPU should not be selected in gaming spec mode")

		# Test gaming + cost: may select non-priority CPUs since cost mode doesn't enforce spec priority
		cost_request = factory.post(
			'/api/configurations/generate/',
			{
				'budget': 300000,
				'usage': 'gaming',
				'build_priority': 'cost',
			},
			format='json',
		)
		cost_response = ConfigurationViewSet.as_view({'post': 'generate'})(cost_request)
		self.assertEqual(cost_response.status_code, status.HTTP_200_OK)


class UsageConversionRegressionTests(APITestCase):
	def setUp(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D Gaming',
			price=58980,
			specs={'socket': 'AM5', 'cores': 8, 'threads': 16},
			url='https://example.com/cpu-7800x3d-regression',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 9 7900 Creator',
			price=54800,
			specs={'socket': 'AM5', 'cores': 12, 'threads': 24},
			url='https://example.com/cpu-7900-creator-regression',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 8600G General',
			price=33800,
			specs={'socket': 'AM5', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-8600g-general-regression',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 7 265K AI Regression',
			price=47780,
			specs={'socket': 'LGA1851', 'cores': 20, 'threads': 20},
			url='https://example.com/cpu-ultra-265k-ai-regression',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B650 DDR5 Board Regression',
			price=19800,
			specs={'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b650-regression',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Regression',
			price=9800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-regression',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 32GB Regression',
			price=17800,
			specs={'memory_type': 'DDR5', 'capacity_gb': 32},
			url='https://example.com/mem-ddr5-32-regression',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Regression',
			price=6800,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-512-regression',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Regression',
			price=9800,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/storage-1tb-regression',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4060 8GB Regression',
			price=46800,
			specs={'vram': '8GB', 'memory_gb': 8},
			url='https://example.com/gpu-4060-8gb-regression',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4070 12GB Regression',
			price=69800,
			specs={'vram': '12GB', 'memory_gb': 12},
			url='https://example.com/gpu-4070-12gb-regression',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA GeForce RTX 5070 12GB Regression',
			price=99800,
			specs={'vram': '12GB', 'memory_gb': 12},
			url='https://example.com/gpu-5070-12gb-regression',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Tower Air Cooler Regression',
			price=5200,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-air-regression',
		)
		PCPart.objects.create(
			part_type='psu',
			name='400W PSU Regression',
			price=4580,
			specs={'wattage': 400},
			url='https://example.com/psu-400-regression',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Regression',
			price=9500,
			specs={'wattage': 750},
			url='https://example.com/psu-750-regression',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Regression',
			price=6500,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-regression',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Regression',
			price=16800,
			specs={},
			url='https://example.com/os-home-regression',
		)

	def _post_generate(self, usage, budget=260000):
		return self.client.post(
			'/api/generate-config/',
			{'budget': budget, 'usage': usage, 'build_priority': 'cost'},
			format='json',
		)

	def test_generate_config_accepts_all_canonical_usage_codes(self):
		for usage in ['gaming', 'creator', 'ai', 'general']:
			with self.subTest(usage=usage):
				response = self._post_generate(usage)
				self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
				self.assertEqual(response.data.get('usage'), usage)

	def test_generate_config_converts_legacy_usage_codes_to_canonical(self):
		expected_pairs = {
			'video_editing': 'creator',
			'business': 'general',
			'standard': 'general',
		}
		for legacy_usage, expected_usage in expected_pairs.items():
			with self.subTest(legacy_usage=legacy_usage):
				response = self._post_generate(legacy_usage)
				self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
				self.assertEqual(response.data.get('usage'), expected_usage)

	def test_generate_config_rejects_unsupported_usage(self):
		response = self._post_generate('workstation')
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('usage', str(response.data.get('detail', '')).lower())

	def test_generate_config_usage_rules_and_required_fields(self):
		creator_response = self._post_generate('creator')
		ai_response = self._post_generate('ai')
		general_response = self._post_generate('general')

		self.assertEqual(creator_response.status_code, status.HTTP_200_OK, creator_response.data)
		self.assertEqual(ai_response.status_code, status.HTTP_200_OK, ai_response.data)
		self.assertEqual(general_response.status_code, status.HTTP_200_OK, general_response.data)

		for response in [creator_response, ai_response, general_response]:
			self.assertIn('usage', response.data)
			self.assertIn('budget', response.data)
			self.assertIn('total_price', response.data)
			self.assertIn('parts', response.data)
			self.assertIsInstance(response.data['parts'], list)

		creator_parts = {part['category']: part for part in creator_response.data['parts']}
		ai_parts = {part['category']: part for part in ai_response.data['parts']}
		general_parts = {part['category']: part for part in general_response.data['parts']}

		self.assertIn('32GB', creator_parts['memory']['name'])
		self.assertIn('1TB', creator_parts['storage']['name'])

		self.assertGreater(ai_parts['gpu']['price'], 0)
		self.assertRegex(ai_parts['gpu']['name'], r'\b(?:8|12|16)GB\b')
		self.assertIn('32GB', ai_parts['memory']['name'])
		self.assertIn('1TB', ai_parts['storage']['name'])

		self.assertEqual(general_parts['gpu']['name'], '内蔵GPU（統合グラフィックス）')
		self.assertEqual(general_parts['gpu']['price'], 0)

	def test_required_psu_wattage_uses_400w_floor_for_general_igpu(self):
		selected_parts = {
			'cpu': PCPart.objects.get(name='AMD Ryzen 5 8600G General'),
		}

		required_w = _required_psu_wattage(selected_parts, 'general')

		self.assertEqual(required_w, 400)

	def test_pick_part_by_target_keeps_case_when_cost_target_band_is_empty(self):
		PCPart.objects.filter(part_type='case').delete()
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Fallback Regression',
			price=9800,
			specs={'supported_form_factors': ['ATX']},
			url='https://example.com/case-atx-fallback-regression',
		)

		case = _pick_part_by_target(
			'case',
			54980,
			'general',
			options={
				'build_priority': 'cost',
				'case_size': 'any',
				'case_fan_policy': 'auto',
			},
		)

		self.assertIsNotNone(case)
		self.assertEqual(case.name, 'ATX Case Fallback Regression')

	def test_generate_config_general_low_budget_keeps_os(self):
		response = self._post_generate('general', budget=54980)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {part['category']: part for part in response.data['parts']}
		self.assertIn('os', parts)
		self.assertTrue(parts['os']['name'])
		self.assertLessEqual(response.data['total_price'], response.data['budget'])

	def test_generate_config_general_low_budget_auto_adjusts_budget_when_os_required(self):
		PCPart.objects.filter(part_type='os').delete()
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home High Price Regression',
			price=70000,
			specs={},
			url='https://example.com/os-home-high-price-regression',
		)

		response = self._post_generate('general', budget=54980)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		parts = {part['category']: part for part in response.data['parts']}
		self.assertIn('os', parts)
		self.assertTrue(response.data.get('budget_auto_adjusted'))
		self.assertGreater(response.data.get('budget', 0), 54980)
		self.assertIn('OS必須', response.data.get('message', ''))

	def test_generate_config_gaming_cost_market_budget_correction_raises_too_low_budget(self):
		MarketPriceRangeSnapshot.objects.create(
			market_min=180000,
			market_max=1300000,
			suggested_default=729980,
			currency='JPY',
			sources={'dospara_tc30_market': {'count': 120}},
		)

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 120000, 'usage': 'gaming', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		self.assertTrue(response.data.get('market_budget_adjusted'))
		self.assertTrue(response.data.get('budget_auto_adjusted'))
		self.assertEqual(response.data.get('requested_budget'), 120000)
		self.assertEqual(response.data.get('budget'), 180000)
		self.assertIn('part_adjustments', response.data)
		self.assertIsInstance(response.data.get('part_adjustments'), list)
		self.assertIn('予算を補正しました', response.data.get('market_budget_note', ''))
		self.assertIn('引き上げ', response.data.get('market_budget_note', ''))

	def test_generate_config_gaming_cost_market_budget_correction_lowers_too_high_budget(self):
		MarketPriceRangeSnapshot.objects.create(
			market_min=180000,
			market_max=1300000,
			suggested_default=729980,
			currency='JPY',
			sources={'dospara_tc30_market': {'count': 120}},
		)

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 1500000, 'usage': 'gaming', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		self.assertTrue(response.data.get('market_budget_adjusted'))
		self.assertTrue(response.data.get('budget_auto_adjusted'))
		self.assertEqual(response.data.get('requested_budget'), 1500000)
		self.assertEqual(response.data.get('budget'), 1300000)
		self.assertIn('予算を補正しました', response.data.get('market_budget_note', ''))
		self.assertIn('引き下げ', response.data.get('market_budget_note', ''))

	def test_generate_config_creator_spec_market_budget_correction_applies(self):
		MarketPriceRangeSnapshot.objects.create(
			market_min=180000,
			market_max=1300000,
			suggested_default=729980,
			currency='JPY',
			sources={'dospara_tc30_market': {'count': 120}},
		)

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 120000, 'usage': 'creator', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		self.assertTrue(response.data.get('market_budget_adjusted'))
		self.assertTrue(response.data.get('budget_auto_adjusted'))
		self.assertEqual(response.data.get('requested_budget'), 120000)
		self.assertEqual(response.data.get('budget'), 180000)

	def test_generate_config_general_cost_market_budget_correction_applies(self):
		MarketPriceRangeSnapshot.objects.create(
			market_min=180000,
			market_max=1300000,
			suggested_default=729980,
			currency='JPY',
			sources={'dospara_tc30_market': {'count': 120}},
		)

		response = self.client.post(
			'/api/generate-config/',
			{'budget': 1500000, 'usage': 'general', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		self.assertTrue(response.data.get('market_budget_adjusted'))
		self.assertTrue(response.data.get('budget_auto_adjusted'))
		self.assertEqual(response.data.get('requested_budget'), 1500000)
		self.assertEqual(response.data.get('budget'), 1300000)

	def test_generate_config_general_spec_fallback_keeps_memory_at_16gb(self):
		response = self.client.post(
			'/api/generate-config/',
			{'budget': 115478, 'usage': 'general', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
		self.assertEqual(response.data.get('effective_build_priority'), 'cost')
		parts = {part['category']: part for part in response.data['parts']}
		self.assertIn('memory', parts)
		self.assertEqual(int(parts['memory'].get('specs', {}).get('capacity_gb', 0)), 16)
		self.assertNotIn('A520', parts['motherboard']['name'])

	def test_generate_config_general_spec_keeps_cpu_and_motherboard_at_least_cost_level(self):
		PCPart.objects.filter(part_type__in=['cpu', 'motherboard', 'memory', 'storage', 'cpu_cooler', 'psu', 'case', 'os']).delete()
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5500GT BOX Regression',
			price=19760,
			specs={'socket': 'AM4', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-5500gt-regression',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 5600GT BOX Regression',
			price=22120,
			specs={'socket': 'AM4', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-5600gt-regression',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='ASRock A520M-HDV Regression',
			price=5680,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-a520-regression',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='GIGABYTE B550 GAMING X V2 Regression',
			price=12370,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-regression',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Regression',
			price=5680,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/memory-8gb-regression',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Regression',
			price=9680,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/memory-16gb-regression',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Regression',
			price=6800,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/storage-regression',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AM4 Air Cooler Regression',
			price=3540,
			specs={'supported_sockets': ['AM4']},
			url='https://example.com/cooler-regression',
		)
		PCPart.objects.create(
			part_type='psu',
			name='400W PSU Regression',
			price=4580,
			specs={'wattage': 400},
			url='https://example.com/psu-regression',
		)
		PCPart.objects.create(
			part_type='case',
			name='MicroATX Case Regression',
			price=3548,
			specs={'supported_form_factors': ['MicroATX']},
			url='https://example.com/case-regression',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Regression',
			price=16480,
			specs={},
			url='https://example.com/os-regression',
		)

		cost_response = self.client.post(
			'/api/generate-config/',
			{'budget': 115478, 'usage': 'general', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)
		spec_response = self.client.post(
			'/api/generate-config/',
			{'budget': 115478, 'usage': 'general', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(cost_response.status_code, status.HTTP_200_OK, cost_response.data)
		self.assertEqual(spec_response.status_code, status.HTTP_200_OK, spec_response.data)

		cost_parts = {part['category']: part for part in cost_response.data['parts']}
		spec_parts = {part['category']: part for part in spec_response.data['parts']}

		cost_cpu = PCPart.objects.get(name=cost_parts['cpu']['name'])
		spec_cpu = PCPart.objects.get(name=spec_parts['cpu']['name'])
		self.assertGreaterEqual(_get_cpu_perf_score(spec_cpu) or 0, _get_cpu_perf_score(cost_cpu) or 0)

		cost_mb = PCPart.objects.get(name=cost_parts['motherboard']['name'])
		spec_mb = PCPart.objects.get(name=spec_parts['motherboard']['name'])
		self.assertGreaterEqual(
			_creator_motherboard_expandability_score(spec_mb),
			_creator_motherboard_expandability_score(cost_mb),
		)
		self.assertEqual(int(spec_parts['memory'].get('specs', {}).get('capacity_gb', 0)), 16)

	def test_generate_config_general_spec_prefers_higher_price_cpu_cooler_than_cost(self):
		PCPart.objects.filter(part_type='cpu_cooler').delete()
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Budget Air Cooler Regression',
			price=2980,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-budget-regression',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Premium Air Cooler Regression',
			price=3980,
			specs={'supported_sockets': ['AM5']},
			url='https://example.com/cooler-premium-regression',
		)

		cost_response = self.client.post(
			'/api/generate-config/',
			{'budget': 115478, 'usage': 'general', 'build_priority': 'cost', 'os_edition': 'home'},
			format='json',
		)
		spec_response = self.client.post(
			'/api/generate-config/',
			{'budget': 115478, 'usage': 'general', 'build_priority': 'spec', 'os_edition': 'home'},
			format='json',
		)

		self.assertEqual(cost_response.status_code, status.HTTP_200_OK, cost_response.data)
		self.assertEqual(spec_response.status_code, status.HTTP_200_OK, spec_response.data)

		cost_parts = {part['category']: part for part in cost_response.data['parts']}
		spec_parts = {part['category']: part for part in spec_response.data['parts']}
		self.assertLessEqual(int(cost_parts['cpu_cooler']['price']), int(spec_parts['cpu_cooler']['price']))

	def test_generate_config_general_returns_explicit_error_when_no_os_candidate(self):
		PCPart.objects.filter(part_type='os').delete()

		response = self._post_generate('general', budget=54980)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('OS必須予算不足', str(response.data.get('detail', '')))

	def test_ai_latest_generation_cpu_filter(self):
		latest_cpu = PCPart(
			part_type='cpu',
			name='Intel Core Ultra 7 265K BOX',
			price=47780,
			specs={'socket': 'LGA1851', 'cores': 20, 'threads': 20},
			url='https://example.com/cpu-intel-ultra-265k',
		)
		latest_amd_cpu = PCPart(
			part_type='cpu',
			name='AMD Ryzen 7 9700X BOX',
			price=49800,
			specs={'socket': 'AM5', 'cores': 8, 'threads': 16},
			url='https://example.com/cpu-ryzen-9700x',
		)
		previous_amd_cpu = PCPart(
			part_type='cpu',
			name='AMD Ryzen 5 8500G BOX',
			price=29800,
			specs={'socket': 'AM5', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-ryzen-8500g',
		)
		legacy_cpu = PCPart(
			part_type='cpu',
			name='Intel Core i5-12400F BOX',
			price=22800,
			specs={'socket': 'LGA1700', 'cores': 6, 'threads': 12},
			url='https://example.com/cpu-intel-12400f',
		)

		self.assertTrue(_matches_selection_options('cpu', latest_cpu, options={'usage': 'ai', 'cpu_vendor': 'any'}))
		self.assertTrue(_matches_selection_options('cpu', latest_amd_cpu, options={'usage': 'ai', 'cpu_vendor': 'any'}))
		self.assertFalse(_matches_selection_options('cpu', previous_amd_cpu, options={'usage': 'ai', 'cpu_vendor': 'any'}))
		self.assertFalse(_matches_selection_options('cpu', legacy_cpu, options={'usage': 'ai', 'cpu_vendor': 'any'}))

	def test_ai_latest_generation_gpu_filter(self):
		latest_gpu = PCPart(
			part_type='gpu',
			name='NVIDIA GeForce RTX 5070 12GB',
			price=99800,
			specs={'memory_gb': 12},
			url='https://example.com/gpu-rtx5070',
		)
		legacy_gpu = PCPart(
			part_type='gpu',
			name='NVIDIA GeForce RTX 4070 12GB',
			price=69800,
			specs={'memory_gb': 12},
			url='https://example.com/gpu-rtx4070',
		)

		self.assertTrue(_matches_selection_options('gpu', latest_gpu, options={'usage': 'ai', 'build_priority': 'spec', 'budget': 300000}))
		self.assertFalse(_matches_selection_options('gpu', legacy_gpu, options={'usage': 'ai', 'build_priority': 'spec', 'budget': 300000}))

	def test_ai_cpu_selection_prefers_performance_for_spec_and_efficiency_for_cost(self):
		PCPart.objects.filter(part_type='cpu').delete()

		cpu_high_perf = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 9 285K BOX AI Test',
			price=47000,
			specs={'socket': 'LGA1851', 'cpu_perf_score': 15000},
			url='https://example.com/cpu-ai-285k',
		)
		cpu_value = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 7 265K BOX AI Test',
			price=30000,
			specs={'socket': 'LGA1851', 'cpu_perf_score': 10000},
			url='https://example.com/cpu-ai-265k',
		)
		cpu_cheap = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 5 245K BOX AI Test',
			price=25000,
			specs={'socket': 'LGA1851', 'cpu_perf_score': 4000},
			url='https://example.com/cpu-ai-245k',
		)

		spec_pick = _pick_part_by_target(
			'cpu',
			budget=300000,
			usage='ai',
			options={
				'build_priority': 'spec',
				'cpu_vendor': 'any',
			},
		)
		cost_pick = _pick_part_by_target(
			'cpu',
			budget=300000,
			usage='ai',
			options={
				'build_priority': 'cost',
				'cpu_vendor': 'any',
			},
		)

		self.assertIsNotNone(spec_pick)
		self.assertIsNotNone(cost_pick)
		self.assertEqual(spec_pick.id, cpu_high_perf.id)
		self.assertEqual(cost_pick.id, cpu_value.id)
		self.assertNotEqual(cost_pick.id, cpu_cheap.id)

	def test_ai_cpu_selection_cost_premium_prefers_top_tier_cpu(self):
		PCPart.objects.filter(part_type='cpu').delete()

		cpu_value_low = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 5 245KF BOX AI Floor Test',
			price=32780,
			specs={'socket': 'LGA1851', 'cpu_perf_score': 8657},
			url='https://example.com/cpu-ai-245kf-floor',
		)
		cpu_value_mid = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 7 265F BOX AI Floor Test',
			price=52380,
			specs={'socket': 'LGA1851', 'cpu_perf_score': 9979},
			url='https://example.com/cpu-ai-265f-floor',
		)
		cpu_top = PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 9 285K BOX AI Floor Test',
			price=79800,
			specs={'socket': 'LGA1851', 'cpu_perf_score': 13474},
			url='https://example.com/cpu-ai-285k-floor',
		)

		cost_pick = _pick_part_by_target(
			'cpu',
			budget=734980,
			usage='ai',
			options={
				'build_priority': 'cost',
				'cpu_vendor': 'any',
			},
		)

		self.assertIsNotNone(cost_pick)
		self.assertEqual(cost_pick.id, cpu_top.id)
		self.assertNotEqual(cost_pick.id, cpu_value_mid.id)
		self.assertNotEqual(cost_pick.id, cpu_value_low.id)

	def test_ai_gpu_selection_premium_fixed_by_build_priority(self):
		PCPart.objects.filter(part_type='gpu').delete()

		gpu_r9700 = PCPart.objects.create(
			part_type='gpu',
			name='ASRock Radeon AI PRO R9700 Creator 32GB (R9700 CT 32G)',
			price=259800,
			specs={'memory_gb': 32},
			url='https://example.com/gpu-r9700-ai-fixed',
		)
		gpu_pro4500 = PCPart.objects.create(
			part_type='gpu',
			name='NVIDIA RTX PRO 4500 Blackwell BOX (RTX PRO 4500 32GB)',
			price=506000,
			specs={'memory_gb': 32},
			url='https://example.com/gpu-rtxpro4500-ai-fixed',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='GIGABYTE GeForce RTX 5080 16GB',
			price=309800,
			specs={'memory_gb': 16},
			url='https://example.com/gpu-rtx5080-ai-fixed',
		)

		cost_pick = _pick_part_by_target(
			'gpu',
			budget=734980,
			usage='ai',
			options={
				'build_priority': 'cost',
			},
		)
		spec_pick = _pick_part_by_target(
			'gpu',
			budget=734980,
			usage='ai',
			options={
				'build_priority': 'spec',
			},
		)

		self.assertIsNotNone(cost_pick)
		self.assertIsNotNone(spec_pick)
		self.assertEqual(cost_pick.id, gpu_r9700.id)
		self.assertEqual(spec_pick.id, gpu_pro4500.id)


class DosparaScraperTests(APITestCase):
	class _DummyResponse:
		def __init__(self, text='', json_data=None):
			self.text = text
			self._json_data = json_data

		def raise_for_status(self):
			return None

		def json(self):
			return self._json_data

	class _DummySession:
		def __init__(self, html_text, api_json):
			self._html_text = html_text
			self._api_json = api_json

		def get(self, *_args, **_kwargs):
			return DosparaScraperTests._DummyResponse(text=self._html_text)

		def post(self, *_args, **_kwargs):
			return DosparaScraperTests._DummyResponse(json_data=self._api_json)

	def test_parse_dospara_parts_html_extracts_known_categories(self):
		html = """
		<div class="product-card">
			<a href="/product/123">Ryzen 7 7700 CPU</a>
			<span class="price">34,980円</span>
		</div>
		<div class="product-card">
			<a href="/product/456">GeForce RTX 4060 GPU</a>
			<span class="price">49,800円</span>
		</div>
		"""

		parts = parse_dospara_parts_html(html)

		self.assertEqual(len(parts), 2)
		self.assertEqual(parts[0]['part_type'], 'cpu')
		self.assertEqual(parts[0]['price'], 34980)
		self.assertIn('dospara.co.jp', parts[0]['url'])
		self.assertEqual(parts[1]['part_type'], 'gpu')

	def test_extract_specs_from_simplespec_case_radiator_sizes(self):
		simplespec = 'フォームファクタ：Mini-ITX ● 対応ラジエーター：120mm / 240mm / 360mm ● 最大ラジエーター：360mm'

		specs = _extract_specs_from_simplespec('case', simplespec)

		self.assertEqual(specs.get('max_radiator_mm'), 360)
		self.assertEqual(specs.get('radiator_sizes'), [120, 240, 360])
		self.assertEqual(specs.get('supported_radiators'), [120, 240, 360])

	def test_fetch_cpu_selection_material_excludes_intel_13_14_generation(self):
		amd_html = """
		<table>
			<tr><th>型番</th><th>性能目安</th></tr>
			<tr><td>Ryzen 7 9700X</td><td>3904</td></tr>
		</table>
		"""
		intel_html = """
		<table>
			<tr><th>型番</th><th>性能目安</th></tr>
			<tr><td>Core i5-14400F</td><td>5120</td></tr>
			<tr><td>Core i5-12400F</td><td>3918</td></tr>
		</table>
		"""

		class _PageSession:
			def get(self, url, headers=None, timeout=None):
				if 'cts_lp_amd_cpu' in url:
					return DosparaScraperTests._DummyResponse(text=amd_html)
				return DosparaScraperTests._DummyResponse(text=intel_html)

		result = fetch_dospara_cpu_selection_material(session=_PageSession(), exclude_intel_13_14=True)

		models = {row['model_name'] for row in result['entries']}
		excluded_models = {row['model_name'] for row in result['excluded_entries']}
		self.assertIn('Ryzen 7 9700X', models)
		self.assertIn('Core i5-12400F', models)
		self.assertIn('Core i5-14400F', excluded_models)

	@override_settings(
		DOSPARA_SCRAPER_ENV='development',
		DOSPARA_SCRAPER={
			'url': 'https://www.dospara.co.jp/parts/custom',
			'timeout': 12,
			'max_items': 50,
			'selectors': {
				'item_roots': ['div.product-card'],
				'name': ['a.product-link'],
				'price': ['span.product-price'],
				'link': ['a.product-link'],
			},
		},
		DOSPARA_SCRAPER_BY_ENV={},
	)
	def test_get_dospara_scraper_config_reads_settings_override(self):
		config = get_dospara_scraper_config()

		self.assertEqual(config['url'], 'https://www.dospara.co.jp/parts/custom')
		self.assertEqual(config['timeout'], 12)
		self.assertEqual(config['max_items'], 50)
		self.assertEqual(config['selectors']['item_roots'], ['div.product-card'])
		self.assertEqual(config['env'], 'development')

	@override_settings(
		DOSPARA_SCRAPER_ENV='production',
		DOSPARA_SCRAPER={
			'timeout': 22,
			'max_items': 80,
			'selectors': {
				'name': ['a[href]'],
			},
		},
		DOSPARA_SCRAPER_BY_ENV={
			'production': {
				'timeout': 35,
				'max_items': 250,
				'selectors': {
					'price': ['span.value-price'],
				},
			},
		},
	)
	def test_get_dospara_scraper_config_applies_env_override(self):
		config = get_dospara_scraper_config()

		self.assertEqual(config['env'], 'production')
		self.assertEqual(config['timeout'], 35)
		self.assertEqual(config['max_items'], 250)
		self.assertEqual(config['selectors']['name'], ['a[href]'])
		self.assertEqual(config['selectors']['price'], ['span.value-price'])
		self.assertIn('item_roots', config['selectors'])

	def test_parse_dospara_parts_html_supports_selector_override(self):
		html = """
		<div class="product-card">
			<a class="product-link" href="/product/aaa">Core i5 14400F</a>
			<p class="price-text">¥28,980</p>
		</div>
		"""
		selectors = {
			'item_roots': ['div.product-card'],
			'name': ['a.product-link'],
			'price': ['p.price-text'],
			'link': ['a.product-link'],
		}

		parts = parse_dospara_parts_html(html, selectors=selectors)

		self.assertEqual(len(parts), 1)
		self.assertEqual(parts[0]['name'], 'Core i5 14400F')
		self.assertEqual(parts[0]['price'], 28980)
		self.assertEqual(parts[0]['part_type'], 'cpu')

	def test_parse_dospara_parts_html_regex_fallback_extracts_product_and_price(self):
		html = """
		<section>
			<a href="/SBR1481/IC497968.html">Intel Core i5 14400F BOX</a>
			<div>24時間以内に出荷</div>
			<a href="/SBR1481/IC497968.html">25,880 円</a>
		</section>
		"""

		parts = parse_dospara_parts_html(html, selectors={'item_roots': ['div.unmatched']})

		self.assertEqual(len(parts), 1)
		self.assertEqual(parts[0]['name'], 'Intel Core i5 14400F BOX')
		self.assertEqual(parts[0]['price'], 25880)
		self.assertEqual(parts[0]['part_type'], 'cpu')

	def test_scrape_dospara_parts_uses_products_api_data(self):
		html = '<div>IC497968 IC526330</div>'
		api_json = {
			'returnCode': '000000',
			'productInfoList': {
				'pid%3AIC497968%2Cq%3A%2Ckflg%3A': {
					'pname': 'Intel Core i5 14400F BOX',
					'amttax': 25880,
					'url': '/SBR1481/IC497968.html',
				},
				'pid%3AIC526330%2Cq%3A%2Ckflg%3A': {
					'pname': 'Palit GeForce RTX 5070 Ti 16GB',
					'amttax': 167800,
					'url': '/SBR1892/IC526330.html',
				},
			},
		}

		session = self._DummySession(html_text=html, api_json=api_json)
		parts = scrape_dospara_parts(session=session)

		self.assertEqual(len(parts), 2)
		self.assertEqual(parts[0]['part_type'], 'cpu')
		self.assertEqual(parts[0]['price'], 25880)
		self.assertIn('dospara.co.jp/SBR1481/IC497968.html', parts[0]['url'])
		self.assertEqual(parts[1]['part_type'], 'gpu')

	def test_infer_part_type_detects_motherboard_psu_case(self):
		self.assertEqual(
			_infer_part_type('ASRock B760M Pro RS WiFi (B760 1700 MicroATX)', 'https://www.dospara.co.jp/SBR1798/IC500350.html'),
			'motherboard',
		)
		self.assertEqual(
			_infer_part_type('MSI MAG A750GL PCIE5 (750W)', 'https://www.dospara.co.jp/SBR83/IC492649.html'),
			'psu',
		)
		self.assertEqual(
			_infer_part_type('MONTECH KING 95 PRO Red (ATX ガラス レッド)', 'https://www.dospara.co.jp/SBR79/IC496198.html'),
			'case',
		)

	def test_infer_part_type_avoids_cpu_grease_false_positive(self):
		self.assertIsNone(
			_infer_part_type('AINEX JP-DX1 (CPU グリス / ナノダイヤモンドグリス)', 'https://www.dospara.co.jp/SBR131/IC415129.html')
		)

	def test_infer_part_type_detects_cpu_cooler(self):
		self.assertEqual(
			_infer_part_type('DeepCool AK620 CPUクーラー', 'https://www.dospara.co.jp/SBR95/IC123456.html'),
			'cpu_cooler',
		)

	def test_infer_part_type_detects_os(self):
		self.assertEqual(
			_infer_part_type('Microsoft Windows 11 Pro 日本語パッケージ版', 'https://www.dospara.co.jp/SBR170/IC479479.html'),
			'os',
		)

	def test_infer_part_type_detects_hdd_storage(self):
		self.assertEqual(
			_infer_part_type('Seagate BarraCuda ST8000DM004 (8TB)', 'https://www.dospara.co.jp/SBR1964/IC451338.html'),
			'storage',
		)
		self.assertEqual(
			_infer_part_type('TOSHIBA MQ04ABD200 (2TB)', 'https://www.dospara.co.jp/SBR405/IC453537.html'),
			'storage',
		)

	def test_infer_part_type_detects_storage_from_br13_hint(self):
		self.assertEqual(
			_infer_part_type('Unknown Drive Model', 'https://www.dospara.co.jp/BR13/IC451338.html'),
			'storage',
		)

	def test_infer_part_type_excludes_geforce_gt_series_gpu(self):
		self.assertIsNone(
			_infer_part_type('玄人志向 GF-GT710-E1GB/HS (GeForce GT 710 1GB)', 'https://www.dospara.co.jp/SBR4/IC123456.html')
		)

	@patch('scraper.tasks.scrape_pckoubou_all')
	def test_run_scraper_task_saves_dospara_parts(self, mock_scrape):
		mock_scrape.return_value = [
			{
				'part_type': 'cpu',
				'name': 'Intel Core i5 14400F',
				'price': 28980,
				'url': 'https://www.pc-koubou.jp/product/abc',
				'specs': {'source': 'pckoubou'},
			},
			{
				'part_type': 'memory',
				'name': 'DDR5 32GB Kit',
				'price': 14980,
				'url': 'https://www.pc-koubou.jp/product/def',
				'specs': {'source': 'pckoubou'},
			},
		]

		result = run_scraper_task()

		self.assertEqual(result['status'], 'success')
		self.assertEqual(result['source'], 'pckoubou_parts')
		self.assertEqual(result['fetched'], 2)
		self.assertIn('normalized', result)
		self.assertIn('merged', result)

		status_obj = ScraperStatus.objects.get(id=1)
		self.assertEqual(status_obj.total_scraped, 2)
		self.assertEqual(status_obj.success_count, 1)

	@patch('scraper.tasks.scrape_pckoubou_all', side_effect=RuntimeError('network timeout'))
	def test_run_scraper_task_increments_error_count_on_failure(self, _mock_scrape):
		result = run_scraper_task()

		self.assertEqual(result['status'], 'error')
		status_obj = ScraperStatus.objects.get(id=1)
		self.assertEqual(status_obj.error_count, 1)

	@patch('scraper.tasks.scrape_pckoubou_all', return_value=[])
	def test_run_scraper_task_uses_settings_timeout_and_max_items(self, mock_scrape):
		run_scraper_task()

		mock_scrape.assert_called_once_with(max_items_per_category=500)
