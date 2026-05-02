import json
from pathlib import Path

from django.core.management.base import BaseCommand

from scraper.dospara_scraper import fetch_dospara_cpu_selection_material


class Command(BaseCommand):
    help = "Fetch CPU selection materials from Dospara AMD/Intel comparison pages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=20,
            help="HTTP timeout in seconds for each CPU comparison page.",
        )
        parser.add_argument(
            "--include-intel-13-14",
            action="store_true",
            help="Include Intel 13th/14th generation models (default is excluded).",
        )
        parser.add_argument(
            "--output",
            type=str,
            default="",
            help="Optional output JSON file path.",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        exclude_intel_13_14 = not options["include_intel_13_14"]
        result = fetch_dospara_cpu_selection_material(
            timeout=timeout,
            exclude_intel_13_14=exclude_intel_13_14,
        )

        if options["output"]:
            output_path = Path(options["output"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"CPU selection material saved: {output_path}"))
            self.stdout.write(self.style.SUCCESS(f"entries={result['entry_count']} excluded={result['excluded_count']}"))
            return

        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
