import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import django


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
sys.path.insert(0, str(BASE_DIR))
django.setup()

from scraper.tasks import run_scraper_task  # noqa: E402


stop_requested = False


def _on_signal(_signum, _frame):
    global stop_requested
    stop_requested = True


def _configure_logging(log_level: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "auto_run_scrapers.log"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def _run_once(run_no: int) -> bool:
    logging.info("run_started run_no=%s", run_no)
    started = time.time()

    try:
        result = run_scraper_task()
        elapsed = round(time.time() - started, 2)
        logging.info(
            "run_completed run_no=%s elapsed_sec=%s result=%s",
            run_no,
            elapsed,
            json.dumps(result, ensure_ascii=False),
        )
        return True
    except Exception:
        elapsed = round(time.time() - started, 2)
        logging.exception("run_failed run_no=%s elapsed_sec=%s", run_no, elapsed)
        return False


def _sleep_with_interrupt(total_seconds: int) -> None:
    slept = 0
    while slept < total_seconds and not stop_requested:
        time.sleep(1)
        slept += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scraper task automatically at intervals.")
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=0,
        help="Interval between runs in minutes. 0 means run once and exit.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Maximum number of runs. 0 means unlimited (only when interval > 0).",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue next cycle even if a run fails.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (DEBUG, INFO, WARNING, ERROR).",
    )

    args = parser.parse_args()
    _configure_logging(args.log_level)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    logging.info(
        "auto_runner_started at=%s interval_minutes=%s max_runs=%s continue_on_error=%s",
        datetime.now().isoformat(timespec="seconds"),
        args.interval_minutes,
        args.max_runs,
        args.continue_on_error,
    )

    run_no = 0
    success_count = 0
    failed_count = 0

    while not stop_requested:
        run_no += 1
        ok = _run_once(run_no)
        if ok:
            success_count += 1
        else:
            failed_count += 1
            if not args.continue_on_error:
                logging.error("stopping_due_to_error run_no=%s", run_no)
                break

        if args.interval_minutes <= 0:
            break

        if args.max_runs > 0 and run_no >= args.max_runs:
            logging.info("reached_max_runs max_runs=%s", args.max_runs)
            break

        sleep_seconds = args.interval_minutes * 60
        logging.info("sleeping seconds=%s", sleep_seconds)
        _sleep_with_interrupt(sleep_seconds)

    logging.info(
        "auto_runner_stopped total_runs=%s success=%s failed=%s stop_requested=%s",
        run_no,
        success_count,
        failed_count,
        stop_requested,
    )

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
