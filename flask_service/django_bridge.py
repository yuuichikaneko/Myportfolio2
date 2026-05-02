import os
import sys
from pathlib import Path

import django

_DJANGO_READY = False


def bootstrap_django() -> None:
    global _DJANGO_READY
    if _DJANGO_READY:
        return

    project_root = Path(__file__).resolve().parents[1]
    django_root = project_root / "django"

    if str(django_root) not in sys.path:
        sys.path.insert(0, str(django_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myportfolio_django.settings")
    django.setup()
    _DJANGO_READY = True
