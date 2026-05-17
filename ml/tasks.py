"""
Celery tasks for nightly retraining.

Each task is a thin wrapper around a trainer module so beat scheduling and
testability stay simple. Trainers handle their own ModelRun rows and errors.
"""

import os

from celery import shared_task

from ml import digest as digest_mod
from ml import weather as weather_mod
from ml.trainers import anomaly, basket, forecast, menu_class, reorder


@shared_task(name='ml.fetch_weather')
def fetch_weather():
    """Nightly weather refresh — runs before the forecast trainer."""
    return weather_mod.refresh_weather()


@shared_task(name='ml.train_forecast')
def train_forecast():
    forecast.train()


@shared_task(name='ml.compute_reorder')
def compute_reorder():
    reorder.train()


@shared_task(name='ml.train_anomaly')
def train_anomaly():
    anomaly.train()


@shared_task(name='ml.train_basket')
def train_basket():
    basket.train()


@shared_task(name='ml.compute_menu_class')
def compute_menu_class():
    menu_class.train()


@shared_task(name='ml.daily_digest')
def daily_digest():
    """Email a daily ML summary to managers. No-op if nothing actionable."""
    base_url = os.environ.get('SITE_BASE_URL', '')
    return digest_mod.send_daily_digest(base_url=base_url or None)


@shared_task(name='ml.cleanup_model_runs')
def cleanup_model_runs(keep_days=90):
    """Trim ModelRun history past `keep_days`."""
    from datetime import timedelta

    from django.utils import timezone

    from ml.models import ModelRun

    cutoff = timezone.now() - timedelta(days=keep_days)
    ModelRun.objects.filter(started_at__lt=cutoff).delete()
