import csv
import os
import re
import sys
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
sys.path.insert(0, str(Path(__file__).parent))
django.setup()

from scraper.dospara_scraper import INTEL_13_14_GEN_PATTERN
from scraper.models import PCPart
from scraper.views import _get_cpu_perf_score


MIN_PERF_SCORE = 3000


def _vendor_of(name: str) -> str:
    text = (name or "").lower()
    if "ryzen" in text or "amd" in text:
        return "amd"
    if "intel" in text or "core" in text or "celeron" in text or "pentium" in text:
        return "intel"
    return "unknown"


def _is_excluded_intel_13_14(name: str) -> bool:
    normalized = re.sub(r"\s+", " ", (name or "")).upper()
    return bool(INTEL_13_14_GEN_PATTERN.search(normalized))


def _collect_candidates():
    rows = []
    parts = (
        PCPart.objects.filter(part_type="cpu", is_active=True)
        .exclude(url__isnull=True)
        .exclude(url="")
        .order_by("price", "id")
    )

    for part in parts:
        vendor = _vendor_of(part.name)
        if vendor == "intel" and _is_excluded_intel_13_14(part.name):
            continue

        perf_score = _get_cpu_perf_score(part)
        if perf_score is None or int(perf_score) < MIN_PERF_SCORE:
            continue

        price = int(part.price or 0)
        if price <= 0:
            continue

        value_score = float(perf_score) / float(price)
        rows.append(
            {
                "part_id": int(part.id),
                "vendor": vendor,
                "name": part.name,
                "price": price,
                "perf_score": int(perf_score),
                "value_score": value_score,
                "url": part.url,
            }
        )

    rows.sort(key=lambda r: (-r["value_score"], -r["perf_score"], r["price"], r["name"]))
    return rows


def _write_csv(path: Path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["順位", "ID", "ベンダー", "CPU", "価格", "性能目安", "コスパ値", "URL"])
        for i, row in enumerate(rows, 1):
            w.writerow(
                [
                    i,
                    row["part_id"],
                    row["vendor"],
                    row["name"],
                    row["price"],
                    row["perf_score"],
                    f"{row['value_score']:.6f}",
                    row["url"],
                ]
            )


def generate_and_save_rankings():
    rows = _collect_candidates()

    base = Path(__file__).resolve().parents[1]
    all_csv = base / "ゲーミングCPU総合ランキング_DB基準_性能3000以上_URLあり.csv"
    amd_csv = base / "ゲーミングCPU_AMD_DB基準_性能3000以上_URLあり.csv"
    intel_csv = base / "ゲーミングCPU_Intel_DB基準_性能3000以上_URLあり.csv"

    amd_rows = [r for r in rows if r["vendor"] == "amd"]
    intel_rows = [r for r in rows if r["vendor"] == "intel"]

    _write_csv(all_csv, rows)
    _write_csv(amd_csv, amd_rows)
    _write_csv(intel_csv, intel_rows)

    print(
        {
            "status": "success",
            "condition": {
                "min_perf_score": MIN_PERF_SCORE,
                "requires_url": True,
                "exclude_intel_13_14": True,
            },
            "total": len(rows),
            "amd": len(amd_rows),
            "intel": len(intel_rows),
            "output": {
                "all": str(all_csv),
                "amd": str(amd_csv),
                "intel": str(intel_csv),
            },
        }
    )


if __name__ == "__main__":
    generate_and_save_rankings()
