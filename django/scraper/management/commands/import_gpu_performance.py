from django.core.management.base import BaseCommand

from scraper.tasks import import_gpu_performance_scores_task


class Command(BaseCommand):
    help = "Import GPU performance data from Dospara into snapshot/entry tables and update GPU specs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=20,
            help="HTTP timeout in seconds for the Dospara performance page.",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        result = import_gpu_performance_scores_task(timeout=timeout)
        self.stdout.write(self.style.SUCCESS(f"GPU performance import completed: {result}"))
