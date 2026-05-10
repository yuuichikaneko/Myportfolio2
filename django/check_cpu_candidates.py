import django
import os
import sys
import io

sys.path.insert(0, 'f:\\Python\\Myportfolio2\\django')
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
django.setup()

from scraper.models import PCPart
from scraper.views import (
    _get_cached_parts_by_type,
    _is_am5_cpu,
    _is_cpu_vendor_match,
    _is_general_cost_legacy_cpu,
    _is_part_suitable,
    _pick_general_cost_cpu_candidate,
    _pick_part_by_target,
)
P = PCPart

print("=== Core Ultra CPUs in DB ===")
cpus = PCPart.objects.filter(part_type='cpu').filter(name__icontains='Ultra').values('name', 'price', 'specs').order_by('price')
for cpu in cpus:
    socket = cpu['specs'].get('socket', 'N/A') if isinstance(cpu['specs'], dict) else 'N/A'
    print(f"{cpu['name'][:65]:<65} | {cpu['price']:>8}円 | Socket: {socket}")

print("\n=== Target price filter (general cost middle, budget=224980, cpu_weight=0.20) ===")
target = int(224980 * 0.20)
print(f"Target price: ¥{target}")

print("\n=== reversed(part_pool) のcpu候補シミュレーション ===")
print("(アップグレード時に better_candidates[0] として選ばれる候補)")
ryzen_7600x_price = 38980

# reversed(part_pool)でアップグレード候補 (Ryzen 5 7600X より高い & affordable_max 以下)
# surplus 例: 10,000円
surplus = 10000
affordable_max = ryzen_7600x_price + surplus
better_candidates_sim = list(reversed([
    c for c in P.objects.filter(part_type='cpu').order_by('price')
    if c.price > ryzen_7600x_price and c.price <= affordable_max
]))
print(f"Ryzen 5 7600X (¥{ryzen_7600x_price}) から surplus=¥{surplus} でアップグレード候補:")
for cpu in better_candidates_sim:
    print(f"  {cpu.name[:55]:<55} | ¥{cpu.price:>8}")
if better_candidates_sim:
    print(f"→ better_candidates[0] (現在の実装): {better_candidates_sim[0].name} (¥{better_candidates_sim[0].price})")

print("\n=== コスト重視なら選ばれるべきCPU ===")
if better_candidates_sim:
    cost_sorted = sorted(better_candidates_sim, key=lambda p: (
        0 if _is_general_cost_legacy_cpu(p) else 1,
        0 if not _is_am5_cpu(p) else 1,
        p.price
    ))
    print(f"→ _pick_general_cost_cpu_candidate: {cost_sorted[0].name} (¥{cost_sorted[0].price})")

print("\n=== 実際の initial CPU pick (general + cost, 224980円) ===")
options = {
    'build_priority': 'cost',
    'cooler_type': 'air',
    'radiator_size': '240',
    'cooling_profile': 'performance',
    'case_size': 'mid',
    'case_fan_policy': 'auto',
    'os_edition': 'home',
}
base_parts = _get_cached_parts_by_type('cpu', options=options)
candidates = [p for p in base_parts if _is_part_suitable('cpu', p)]
candidates = [
    p for p in candidates
    if not _is_cpu_vendor_match(p, 'intel') or ('core i' in f'{p.name} {p.url}'.lower() or 'core ultra' in f'{p.name} {p.url}'.lower() or 'pentium' in f'{p.name} {p.url}'.lower() or 'celeron' in f'{p.name} {p.url}'.lower())
]
target_price = int(224980 * 0.20)
within_target = [p for p in candidates if p.price <= target_price]

print(f"within_target count: {len(within_target)} / target_price: ¥{target_price}")
for cpu in within_target[:15]:
    print(
        f"  {cpu.name[:55]:<55} | ¥{cpu.price:>8} | "
        f"intel={_is_cpu_vendor_match(cpu, 'intel')} am5={_is_am5_cpu(cpu)} legacy={_is_general_cost_legacy_cpu(cpu)}"
    )

preferred = _pick_general_cost_cpu_candidate(within_target)
actual = _pick_part_by_target('cpu', 224980, 'general', options=options)
if preferred:
    print(f"preferred by _pick_general_cost_cpu_candidate: {preferred.name} (¥{preferred.price})")
if actual:
    print(f"actual _pick_part_by_target result: {actual.name} (¥{actual.price})")
