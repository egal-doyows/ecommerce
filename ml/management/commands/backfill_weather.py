"""
One-time backfill of historical weather from Open-Meteo's archive.

Pulls daily weather for the date range and upserts into WeatherObservation
so the forecast trainer has weather aligned with every past day of sales.

Usage:
    python manage.py backfill_weather --from 2024-01-01
    python manage.py backfill_weather --from 2024-01-01 --to 2024-12-31

Defaults: --to = yesterday.
"""

from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError

from ml.weather import WeatherUnavailable, fetch_historical


class Command(BaseCommand):
    help = "Backfill historical daily weather from Open-Meteo's archive."

    def add_arguments(self, parser):
        parser.add_argument(
            '--from', dest='from_date', required=True,
            help='Start date (inclusive), YYYY-MM-DD.',
        )
        parser.add_argument(
            '--to', dest='to_date', default=None,
            help='End date (inclusive), YYYY-MM-DD. Default: yesterday.',
        )

    def handle(self, *args, **opts):
        try:
            start = datetime.strptime(opts['from_date'], '%Y-%m-%d').date()
        except ValueError:
            raise CommandError('--from must be YYYY-MM-DD')

        end = date.today() - timedelta(days=1)
        if opts['to_date']:
            try:
                end = datetime.strptime(opts['to_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--to must be YYYY-MM-DD')

        if end < start:
            raise CommandError('--to is before --from')

        self.stdout.write(self.style.NOTICE(f'Backfilling weather {start} → {end}...'))
        try:
            n = fetch_historical(start, end)
        except WeatherUnavailable as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f'Upserted {n} day(s).'))
