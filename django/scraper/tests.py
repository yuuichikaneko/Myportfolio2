from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch
from django.test import override_settings

from .models import Configuration, PCPart, ScraperStatus
from .dospara_scraper import (
	get_dospara_scraper_config,
	parse_dospara_parts_html,
	scrape_dospara_parts,
	_infer_part_type,
	_extract_specs_from_simplespec,
)
from .tasks import run_scraper_task
from .views import _enforce_memory_speed_floor, _infer_memory_speed_mhz, _infer_storage_capacity_gb, _prefer_higher_gaming_cost_x3d_cpu, _rebalance_gaming_cost_cpu_to_storage


class ScraperApiTests(APITestCase):
	def setUp(self):
		self.cpu = PCPart.objects.create(
			part_type='cpu',
			name='Ryzen 5 7600',
			price=32000,
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
		ScraperStatus.objects.create(
			total_scraped=2,
			success_count=2,
			error_count=0,
			cache_enabled=True,
			cache_ttl_seconds=1800,
		)

	def test_generate_config_viewset_action_returns_configuration(self):
		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['usage'], 'gaming')
		self.assertEqual(response.data['budget'], 120000)
		self.assertIsNotNone(response.data['configuration_id'])
		self.assertEqual(response.data['total_price'], 80000)
		self.assertEqual(len(response.data['parts']), 2)

		configuration = Configuration.objects.get(id=response.data['configuration_id'])
		self.assertEqual(configuration.budget, 120000)
		self.assertEqual(configuration.usage, 'gaming')
		self.assertEqual(configuration.total_price, 80000)
		self.assertEqual(configuration.cpu, self.cpu)
		self.assertEqual(configuration.gpu, self.gpu)

	def test_generate_config_viewset_action_rejects_invalid_budget(self):
		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 0, 'usage': 'gaming'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('budget', response.data['detail'])

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

		gaming_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 220000, 'usage': 'gaming'},
			format='json',
		)
		general_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 220000, 'usage': 'standard'},
			format='json',
		)

		gaming_gpu = [p for p in gaming_response.data['parts'] if p['category'] == 'gpu'][0]
		standard_gpu = [p for p in general_response.data['parts'] if p['category'] == 'gpu'][0]

		self.assertEqual(gaming_response.status_code, status.HTTP_200_OK)
		self.assertEqual(general_response.status_code, status.HTTP_200_OK)
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
			{'budget': 90000, 'usage': 'gaming'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertLessEqual(response.data['total_price'], 90000)

	def test_generate_config_includes_os_when_available(self):
		PCPart.objects.create(
			part_type='os',
			name='Microsoft Windows 11 HOME 日本語パッケージ版',
			price=16480,
			specs={'edition': 'Home'},
			url='https://example.com/windows-home',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		os_parts = [p for p in response.data['parts'] if p['category'] == 'os']
		self.assertEqual(len(os_parts), 1)
		self.assertIn('Windows 11', os_parts[0]['name'])
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
		self.assertIn('B650 Board', part_names)
		self.assertIn('DDR5 16GB', part_names)

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
			name='Intel Core Ultra 7 265KF BOX',
			price=45980,
			specs={'socket': 'LGA1851', 'tdp_w': 125},
			url='https://example.com/cpu-265kf',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='ID-COOLING FX360-PRO 360mm AIO',
			price=8990,
			specs={'supported_sockets': ['LGA1851']},
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
			name='B860 ATX Board',
			price=18480,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
			url='https://example.com/mb-b860-atx',
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

	def test_generate_config_ignores_unsuitable_cpu_accessory(self):
		PCPart.objects.create(
			part_type='cpu',
			name='AINEX CPU グリス',
			price=1200,
			specs={},
			url='https://example.com/cpu-grease',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 120000, 'usage': 'gaming'},
			format='json',
		)

		cpu_part = [p for p in response.data['parts'] if p['category'] == 'cpu'][0]
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertNotIn('グリス', cpu_part['name'])

	def test_generate_config_includes_cpu_cooler_when_available(self):
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='DeepCool AK620',
			price=9980,
			specs={},
			url='https://example.com/cooler-ak620',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 140000, 'usage': 'gaming'},
			format='json',
		)

		cooler_part = [p for p in response.data['parts'] if p['category'] == 'cpu_cooler']
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(cooler_part), 1)
		self.assertEqual(cooler_part[0]['name'], 'DeepCool AK620')

	def test_generate_config_respects_cooler_type_selection(self):
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Noctua NH-D15 空冷クーラー',
			price=9980,
			specs={},
			url='https://example.com/cooler-air',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='DeepCool AK400 空冷クーラー',
			price=7980,
			specs={},
			url='https://example.com/cooler-air-deepcool',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Corsair iCUE H150i ELITE LCD 水冷',
			price=16800,
			specs={},
			url='https://example.com/cooler-liquid',
		)

		air_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'gaming', 'cooler_type': 'air'},
			format='json',
		)
		liquid_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'gaming', 'cooler_type': 'liquid'},
			format='json',
		)

		air_cooler = [p for p in air_response.data['parts'] if p['category'] == 'cpu_cooler'][0]
		liquid_cooler = [p for p in liquid_response.data['parts'] if p['category'] == 'cpu_cooler'][0]

		self.assertEqual(air_response.status_code, status.HTTP_200_OK)
		self.assertEqual(liquid_response.status_code, status.HTTP_200_OK)
		self.assertNotIn('Noctua', air_cooler['name'])
		self.assertIn('空冷', air_cooler['name'])
		self.assertIn('水冷', liquid_cooler['name'])

	def test_generate_config_excludes_noctua_cpu_cooler(self):
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='noctua NH-D15 G2 chromax.black (NH-D15-G2-CH-BK)',
			price=25980,
			specs={},
			url='https://example.com/noctua-nh-d15-g2',
		)
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
			part_type='cpu_cooler',
			name='AIO Liquid Cooler 240mm High Performance 水冷',
			price=12000,
			specs={},
			url='https://example.com/cooler-240',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='AIO Liquid Cooler 360mm High Performance 水冷',
			price=18000,
			specs={},
			url='https://example.com/cooler-360',
		)
		PCPart.objects.create(
			part_type='case',
			name='Compact Mini-ITX Case',
			price=9000,
			specs={},
			url='https://example.com/case-mini',
		)
		PCPart.objects.create(
			part_type='case',
			name='Full Tower E-ATX Case',
			price=18000,
			specs={},
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
			part_type='cpu',
			name='Intel Core i7 14700F',
			price=42000,
			specs={},
			url='https://example.com/cpu-intel',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7700',
			price=41000,
			specs={},
			url='https://example.com/cpu-amd',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 7800X3D',
			price=52000,
			specs={},
			url='https://example.com/cpu-amd-x3d',
		)

		intel_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'gaming', 'cpu_vendor': 'intel'},
			format='json',
		)
		amd_response = self.client.post(
			'/api/configurations/generate/',
			{'budget': 180000, 'usage': 'gaming', 'cpu_vendor': 'amd'},
			format='json',
		)

		intel_cpu = [p for p in intel_response.data['parts'] if p['category'] == 'cpu'][0]
		amd_cpu = [p for p in amd_response.data['parts'] if p['category'] == 'cpu'][0]

		self.assertEqual(intel_response.status_code, status.HTTP_200_OK)
		self.assertEqual(amd_response.status_code, status.HTTP_200_OK)
		self.assertIn('intel', intel_cpu['name'].lower())
		self.assertIn('x3d', amd_cpu['name'].lower())
		self.assertEqual(intel_response.data['cpu_vendor'], 'intel')
		self.assertEqual(amd_response.data['cpu_vendor'], 'amd')

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
		self.assertEqual(parts['cpu']['name'], 'Ryzen 7 7700 Custom Weight')
		self.assertIn(parts['gpu']['name'], {'RTX 4060', 'RTX 4060 Custom Weight'})
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

		cost_response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cpu_vendor': 'intel',
				'build_priority': 'cost',
			},
			format='json',
		)
		spec_response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cpu_vendor': 'intel',
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

	def test_generate_config_gaming_cost_prefers_16gb_even_under_220k(self):
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i5 14400F Under220k',
			price=32000,
			specs={'socket': 'LGA1700'},
			url='https://example.com/cpu-intel-14400f-under220k',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='B760 DDR4 Board Under220k',
			price=14000,
			specs={'socket': 'LGA1700', 'memory_type': 'DDR4', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-b760-ddr4-under220k',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 8GB Under220k',
			price=3000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 8},
			url='https://example.com/mem-ddr4-8-under220k',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Under220k',
			price=5000,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-16-under220k',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 1TB Under220k',
			price=12000,
			specs={'interface': 'NVMe', 'capacity_gb': 1000},
			url='https://example.com/ssd-under220k',
		)
		PCPart.objects.create(
			part_type='psu',
			name='750W PSU Under220k',
			price=9000,
			specs={'wattage': 750},
			url='https://example.com/psu-under220k',
		)
		PCPart.objects.create(
			part_type='case',
			name='ATX Case Under220k',
			price=9000,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-under220k',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 165000,
				'usage': 'gaming',
				'cpu_vendor': 'intel',
				'build_priority': 'cost',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('DDR4', parts['memory']['name'])
		self.assertIn('16GB', parts['memory']['name'])

	def test_generate_config_gaming_cost_falls_back_to_8gb_when_16gb_unavailable(self):
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core i5 14400F Fallback',
			price=32000,
			specs={'socket': 'LGA1700'},
			url='https://example.com/cpu-intel-14400f-fallback',
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

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 220000,
				'usage': 'gaming',
				'cpu_vendor': 'intel',
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
				'budget': 180000,
				'usage': 'gaming',
				'build_priority': 'spec',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertEqual(parts['memory']['name'], 'DDR4 64GB Premium')
		self.assertLessEqual(response.data['total_price'], 180000)
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
				'budget': 170000,
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

	def test_generate_config_gaming_spec_prefers_16gb_when_target_pool_lacks_16gb(self):
		PCPart.objects.create(
			part_type='cpu',
			name='Intel Core Ultra 5 225F Spec16GB',
			price=24850,
			specs={'socket': 'LGA1851'},
			url='https://example.com/cpu-ultra5-225f-spec16gb',
		)
		PCPart.objects.create(
			part_type='gpu',
			name='ASUS RTX 5060 Ti 8GB Spec16GB',
			price=88686,
			specs={'vram': '8GB'},
			url='https://example.com/gpu-rtx5060ti-spec16gb',
		)
		PCPart.objects.create(
			part_type='motherboard',
			name='H810M K Spec16GB',
			price=7980,
			specs={'socket': 'LGA1851', 'memory_type': 'DDR5', 'form_factor': 'MicroATX'},
			url='https://example.com/mb-h810m-k-spec16gb',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 8GB Spec16GB',
			price=13580,
			specs={'memory_type': 'DDR5', 'capacity_gb': 8},
			url='https://example.com/mem-ddr5-8-spec16gb',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR5 16GB Spec16GB',
			price=15580,
			specs={'memory_type': 'DDR5', 'capacity_gb': 16},
			url='https://example.com/mem-ddr5-16-spec16gb',
		)
		PCPart.objects.create(
			part_type='storage',
			name='NVMe 512GB Spec16GB',
			price=13979,
			specs={'interface': 'NVMe', 'capacity_gb': 512},
			url='https://example.com/ssd-512-spec16gb',
		)
		PCPart.objects.create(
			part_type='psu',
			name='650W PSU Spec16GB',
			price=6480,
			specs={'wattage': 650},
			url='https://example.com/psu-650-spec16gb',
		)
		PCPart.objects.create(
			part_type='case',
			name='Mid Case Spec16GB',
			price=3580,
			specs={'supported_form_factors': ['MicroATX', 'ATX']},
			url='https://example.com/case-mid-spec16gb',
		)
		PCPart.objects.create(
			part_type='cpu_cooler',
			name='Air Cooler Spec16GB',
			price=3780,
			specs={'cooler_type': 'air'},
			url='https://example.com/cooler-air-spec16gb',
		)
		PCPart.objects.create(
			part_type='os',
			name='Windows 11 Home Spec16GB',
			price=17352,
			url='https://example.com/os-spec16gb',
		)

		response = self.client.post(
			'/api/configurations/generate/',
			{
				'budget': 181478,
				'usage': 'gaming',
				'build_priority': 'spec',
				'cpu_vendor': 'any',
				'cooler_type': 'air',
				'case_size': 'mid',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('16GB', parts['memory']['name'])

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
				'budget': 170000,
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
				'budget': 170000,
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
				'budget': 170000,
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

		self.assertEqual(upgraded['cpu'].id, better_cpu.id)
		self.assertEqual(upgraded['memory'].id, cheaper_memory.id)
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
				'budget': 574980,
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
		self.assertIn('X870 AM5', parts['motherboard']['name'])
		self.assertNotIn('X870E', parts['motherboard']['name'])
		self.assertEqual(parts['gpu']['name'], 'Palit GeForce RTX 5070 Ti OC 16GB')

	def test_generate_config_gaming_spec_upgrades_to_liquid_cooler_when_surplus(self):
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
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		parts = {p['category']: p for p in response.data['parts']}
		self.assertIn('水冷', parts['cpu_cooler']['name'])

	def test_generate_config_gaming_spec_upgrades_case_for_cooling_when_surplus(self):
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
		self.assertEqual(response.data['total_parts_in_db'], 2)
		self.assertEqual(response.data['cached_categories'], ['cpu', 'gpu'])

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
		self.assertEqual(list_response.data['results'][0]['id'], generate_response.data['configuration_id'])
		self.assertEqual(list_response.data['results'][0]['cpu_data']['name'], 'Ryzen 5 7600')

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
		"""gaming+cost で予算余剰がある場合、非X3DからX3Dへ昇格できることを確認"""
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 5 3400G BOX Surplus Case',
			price=10500,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-3400g-surplus',
		)
		PCPart.objects.create(
			part_type='cpu',
			name='AMD Ryzen 7 9800X3D BOX Surplus Case',
			price=64799,
			specs={'socket': 'AM4'},
			url='https://example.com/cpu-9800x3d-surplus',
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
			name='B550 DDR4 Board Surplus Case',
			price=12000,
			specs={'socket': 'AM4', 'memory_type': 'DDR4', 'form_factor': 'ATX'},
			url='https://example.com/mb-b550-surplus',
		)
		PCPart.objects.create(
			part_type='memory',
			name='DDR4 16GB Affordable Surplus Case',
			price=9800,
			specs={'memory_type': 'DDR4', 'capacity_gb': 16},
			url='https://example.com/mem-ddr4-surplus',
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
		self.assertIn('9800X3D', parts['cpu']['name'])
		self.assertLessEqual(response.data['total_price'], 169980)


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

	@patch('scraper.tasks.scrape_dospara_category_parts', return_value=[])
	@patch('scraper.tasks.scrape_dospara_parts')
	def test_run_scraper_task_saves_dospara_parts(self, mock_scrape, _mock_category):
		mock_scrape.return_value = [
			{
				'part_type': 'cpu',
				'name': 'Intel Core i5 14400F',
				'price': 28980,
				'url': 'https://www.dospara.co.jp/product/abc',
				'specs': {'source': 'dospara'},
			},
			{
				'part_type': 'memory',
				'name': 'DDR5 32GB Kit',
				'price': 14980,
				'url': 'https://www.dospara.co.jp/product/def',
				'specs': {'source': 'dospara'},
			},
		]

		result = run_scraper_task()

		self.assertEqual(result['status'], 'success')
		self.assertEqual(result['source'], 'dospara_parts')
		self.assertEqual(result['fetched'], 2)
		self.assertIn('normalized', result)
		self.assertIn('merged', result)
		self.assertEqual(PCPart.objects.filter(url__contains='dospara.co.jp').count(), 2)

		status_obj = ScraperStatus.objects.get(id=1)
		self.assertEqual(status_obj.total_scraped, 2)
		self.assertEqual(status_obj.success_count, 1)

	@patch('scraper.tasks.scrape_dospara_category_parts', return_value=[])
	@patch('scraper.tasks.scrape_dospara_parts', side_effect=RuntimeError('network timeout'))
	def test_run_scraper_task_increments_error_count_on_failure(self, _mock_scrape, _mock_category):
		result = run_scraper_task()

		self.assertEqual(result['status'], 'error')
		status_obj = ScraperStatus.objects.get(id=1)
		self.assertEqual(status_obj.error_count, 1)

	@patch('scraper.tasks.scrape_dospara_category_parts', return_value=[])
	@patch('scraper.tasks.get_dospara_scraper_config')
	@patch('scraper.tasks.scrape_dospara_parts')
	def test_run_scraper_task_uses_settings_timeout_and_max_items(self, mock_scrape, mock_config, _mock_category):
		mock_config.return_value = {
			'url': 'https://www.dospara.co.jp/parts',
			'timeout': 9,
			'max_items': 33,
			'headers': {},
			'selectors': {},
		}
		mock_scrape.return_value = []

		run_scraper_task()

		mock_scrape.assert_called_once_with(timeout=9, max_items=33)
