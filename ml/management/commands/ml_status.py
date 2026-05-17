"""
CLI snapshot of model health — handy on a remote shell.

    python manage.py ml_status
"""

from django.core.management.base import BaseCommand

from menu.models import RestaurantSettings
from ml.models import (
    AnomalyEvent, BasketRule, DemandForecast, MenuClass, ModelRun,
    ReorderSuggestion, WeatherObservation,
)


class Command(BaseCommand):
    help = 'Print the latest ModelRun per trainer + row counts.'

    def handle(self, *args, **opts):
        names = ['forecast', 'reorder', 'anomaly', 'basket', 'menu_class']
        self.stdout.write(self.style.NOTICE('ML status'))
        self.stdout.write('─' * 60)
        for name in names:
            run = ModelRun.objects.filter(model_name=name).first()
            if run is None:
                self.stdout.write(f'{name:12} never run')
                continue
            tag = {
                'ok': self.style.SUCCESS,
                'failed': self.style.ERROR,
                'skipped': self.style.WARNING,
                'running': self.style.NOTICE,
            }.get(run.status, self.style.NOTICE)
            self.stdout.write(
                f'{name:12} {tag(run.status):8}  '
                f'rows {run.rows_used}/{run.rows_written}  '
                f'{run.started_at:%Y-%m-%d %H:%M}'
            )
            if run.error:
                self.stdout.write(f'             ↳ {run.error[:120]}')
        self.stdout.write('─' * 60)
        self.stdout.write(f'DemandForecast rows:     {DemandForecast.objects.count()}')
        self.stdout.write(f'ReorderSuggestion open:  {ReorderSuggestion.objects.filter(status="open").count()}')
        self.stdout.write(f'AnomalyEvent open:       {AnomalyEvent.objects.filter(dismissed=False).count()}')
        self.stdout.write(f'BasketRule:              {BasketRule.objects.count()}')
        self.stdout.write(f'MenuClass:               {MenuClass.objects.count()}')

        # ── Weather coverage ──
        self.stdout.write('─' * 60)
        s = RestaurantSettings.load()
        if s.latitude is None or s.longitude is None:
            self.stdout.write(self.style.WARNING(
                'Weather:                 disabled (set lat/lon in Restaurant Settings)'
            ))
        else:
            actuals = WeatherObservation.objects.filter(source='actual').count()
            forecasts = WeatherObservation.objects.filter(source='forecast').count()
            self.stdout.write(
                f'Weather coverage:        {actuals} actual + {forecasts} forecast day(s) '
                f'@ ({s.latitude}, {s.longitude})'
            )
