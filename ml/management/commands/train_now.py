"""
On-demand trainer entrypoint. Useful for first-time bootstrap, debugging,
or running outside the Celery scheduler.

    python manage.py train_now               # all models
    python manage.py train_now forecast      # just one
    python manage.py train_now forecast reorder
"""

from django.core.management.base import BaseCommand

from ml.trainers import anomaly, basket, forecast, menu_class, reorder


TRAINERS = {
    'forecast': forecast.train,
    'reorder': reorder.train,
    'anomaly': anomaly.train,
    'basket': basket.train,
    'menu_class': menu_class.train,
}


class Command(BaseCommand):
    help = 'Run ML trainers synchronously (defaults to all).'

    def add_arguments(self, parser):
        parser.add_argument(
            'names', nargs='*',
            help=f'Trainer names to run. Choices: {", ".join(TRAINERS)}. Default: all.',
        )

    def handle(self, *args, **opts):
        names = opts['names'] or list(TRAINERS)
        unknown = [n for n in names if n not in TRAINERS]
        if unknown:
            self.stderr.write(self.style.ERROR(f'Unknown trainer(s): {unknown}'))
            return
        for name in names:
            self.stdout.write(self.style.NOTICE(f'→ training {name}...'))
            try:
                TRAINERS[name]()
                self.stdout.write(self.style.SUCCESS(f'  {name} done'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  {name} failed: {e}'))
