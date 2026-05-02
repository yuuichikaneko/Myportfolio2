from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from scraper.models import (
    CPUCoolerDetail,
    CPUDetail,
    CPUSelectionEntry,
    CaseDetail,
    GPUDetail,
    GPUPerformanceEntry,
    MemoryDetail,
    MotherboardDetail,
    OSDetail,
    PCPart,
    PSUDetail,
    StorageDetail,
)


def _query_rows(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _query_count(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        row = cursor.fetchone()
    return int(row[0] if row else 0)


class Command(BaseCommand):
    help = 'Run integrity checks for duplicate keys, orphan detail rows, and unresolved reference mappings.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20, help='Limit printed duplicate rows.')
        parser.add_argument(
            '--fail-on-issues',
            action='store_true',
            help='Exit with non-zero status when any integrity issue is found.',
        )

    def handle(self, *args, **options):
        limit = max(1, int(options['limit']))
        fail_on_issues = bool(options['fail_on_issues'])
        issue_count = 0

        pcpart_table = PCPart._meta.db_table

        duplicate_part_rows = _query_rows(
            f'''
            SELECT part_type, name, COUNT(*) AS cnt
            FROM {pcpart_table}
            GROUP BY part_type, name
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC, part_type, name
            LIMIT %s
            ''',
            [limit],
        )
        if duplicate_part_rows:
            issue_count += len(duplicate_part_rows)
            self.stdout.write(self.style.WARNING('[dup] PCPart(part_type, name) duplicates detected'))
            for row in duplicate_part_rows:
                self.stdout.write(f"  part_type={row['part_type']} name={row['name']} cnt={row['cnt']}")
        else:
            self.stdout.write(self.style.SUCCESS('[ok] PCPart(part_type, name) duplicates: none'))

        duplicate_code_rows = _query_rows(
            f'''
            SELECT dospara_code, COUNT(*) AS cnt
            FROM {pcpart_table}
            WHERE dospara_code IS NOT NULL AND dospara_code <> ''
            GROUP BY dospara_code
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC, dospara_code
            LIMIT %s
            ''',
            [limit],
        )
        if duplicate_code_rows:
            issue_count += len(duplicate_code_rows)
            self.stdout.write(self.style.WARNING('[dup] PCPart.dospara_code duplicates detected'))
            for row in duplicate_code_rows:
                self.stdout.write(f"  dospara_code={row['dospara_code']} cnt={row['cnt']}")
        else:
            self.stdout.write(self.style.SUCCESS('[ok] PCPart.dospara_code duplicates: none'))

        detail_models = (
            CPUDetail,
            GPUDetail,
            MotherboardDetail,
            MemoryDetail,
            StorageDetail,
            OSDetail,
            PSUDetail,
            CaseDetail,
            CPUCoolerDetail,
        )
        for model in detail_models:
            table = model._meta.db_table
            orphan_count = _query_count(
                f'''
                SELECT COUNT(*)
                FROM {table} d
                LEFT JOIN {pcpart_table} p ON p.id = d.part_id
                WHERE p.id IS NULL
                '''
            )
            if orphan_count > 0:
                issue_count += orphan_count
                self.stdout.write(self.style.WARNING(f'[orphan] {table}: {orphan_count}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'[ok] {table} orphan rows: none'))

        unresolved_ref_checks = [
            ('scraper_cpudetail', 'socket', 'socket_ref_id'),
            ('scraper_cpudetail', 'memory_type', 'memory_type_ref_id'),
            ('scraper_gpudetail', 'interface', 'interface_ref_id'),
            ('scraper_motherboarddetail', 'socket', 'socket_ref_id'),
            ('scraper_motherboarddetail', 'memory_type', 'memory_type_ref_id'),
            ('scraper_motherboarddetail', 'form_factor', 'form_factor_ref_id'),
            ('scraper_memorydetail', 'memory_type', 'memory_type_ref_id'),
            ('scraper_memorydetail', 'form_factor', 'form_factor_ref_id'),
            ('scraper_storagedetail', 'interface', 'interface_ref_id'),
            ('scraper_storagedetail', 'form_factor', 'form_factor_ref_id'),
            ('scraper_osdetail', 'os_family', 'os_family_ref_id'),
            ('scraper_osdetail', 'os_edition', 'os_edition_ref_id'),
            ('scraper_osdetail', 'license_type', 'license_type_ref_id'),
            ('scraper_psudetail', 'efficiency_grade', 'efficiency_grade_ref_id'),
            ('scraper_psudetail', 'form_factor', 'form_factor_ref_id'),
            ('scraper_casedetail', 'form_factor', 'form_factor_ref_id'),
            ('scraper_cpucoolerdetail', 'socket', 'socket_ref_id'),
            ('scraper_cpucoolerdetail', 'form_factor', 'form_factor_ref_id'),
        ]

        for table, text_col, ref_col in unresolved_ref_checks:
            unresolved = _query_count(
                f'''
                SELECT COUNT(*)
                FROM {table}
                WHERE COALESCE({text_col}, '') <> ''
                  AND {ref_col} IS NULL
                '''
            )
            if unresolved > 0:
                issue_count += unresolved
                self.stdout.write(self.style.WARNING(f'[unresolved_ref] {table}.{text_col}: {unresolved}'))

        gpu_entry_table = GPUPerformanceEntry._meta.db_table
        gpu_snapshot_dup = _query_rows(
            f'''
            SELECT snapshot_id, model_key, COALESCE(vram_gb, -1) AS vram_norm, is_laptop, COUNT(*) AS cnt
            FROM {gpu_entry_table}
            GROUP BY snapshot_id, model_key, COALESCE(vram_gb, -1), is_laptop
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC, snapshot_id
            LIMIT %s
            ''',
            [limit],
        )
        if gpu_snapshot_dup:
            issue_count += len(gpu_snapshot_dup)
            self.stdout.write(self.style.WARNING('[dup] GPU snapshot entries duplicates detected'))

        cpu_entry_table = CPUSelectionEntry._meta.db_table
        cpu_snapshot_dup = _query_rows(
            f'''
            SELECT snapshot_id, model_name, COUNT(*) AS cnt
            FROM {cpu_entry_table}
            GROUP BY snapshot_id, model_name
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC, snapshot_id
            LIMIT %s
            ''',
            [limit],
        )
        if cpu_snapshot_dup:
            issue_count += len(cpu_snapshot_dup)
            self.stdout.write(self.style.WARNING('[dup] CPU snapshot entries duplicates detected'))

        if issue_count == 0:
            self.stdout.write(self.style.SUCCESS('Integrity check passed: no issues found.'))
            return

        summary = f'Integrity check found {issue_count} issue rows.'
        self.stdout.write(self.style.WARNING(summary))
        if fail_on_issues:
            raise CommandError(summary)
