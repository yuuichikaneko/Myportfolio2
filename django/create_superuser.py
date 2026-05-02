#!/usr/bin/env python
import argparse
import getpass
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myportfolio_django.settings')
django.setup()

from django.contrib.auth.models import User


def _resolve_value(cli_value: str | None, env_key: str, fallback: str | None = None) -> str | None:
	if cli_value:
		return cli_value
	env_value = os.environ.get(env_key)
	if env_value:
		return env_value
	return fallback


def main() -> int:
	parser = argparse.ArgumentParser(description='Create or update Django superuser without hardcoded credentials.')
	parser.add_argument('--username', default=None, help='Superuser username (or env: DJANGO_SUPERUSER_USERNAME).')
	parser.add_argument('--email', default=None, help='Superuser email (or env: DJANGO_SUPERUSER_EMAIL).')
	parser.add_argument('--password', default=None, help='Superuser password (or env: DJANGO_SUPERUSER_PASSWORD).')
	args = parser.parse_args()

	username = _resolve_value(args.username, 'DJANGO_SUPERUSER_USERNAME', 'admin')
	email = _resolve_value(args.email, 'DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
	password = _resolve_value(args.password, 'DJANGO_SUPERUSER_PASSWORD')

	if not password:
		password = getpass.getpass('Superuser password: ').strip()

	if not password:
		raise ValueError('Password is required. Use --password, DJANGO_SUPERUSER_PASSWORD, or interactive input.')

	user, _created = User.objects.get_or_create(username=username)
	user.email = email
	user.set_password(password)
	user.is_staff = True
	user.is_superuser = True
	user.save()

	print(f'✅ Superuser created/updated: {username}')
	print(f'   Email: {email}')
	print('   Password: [hidden]')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
