"""Tests for day-of-week-aware forecasting.

Covers the seasonal-naive baseline (which must preserve weekday shape where
the old flat MA-7 erased it), the weekday-aware global fallback, and the
forward-looking 'Demand by Day' view.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from menu.models import Category, MenuItem, InventoryItem, Order, OrderItem
from ml.models import DemandForecast
from ml.trainers.forecast import _seasonal_naive_baseline


class SeasonalNaiveBaselineTests(TestCase):
    def _spiky_series(self, weeks=6):
        """Daily series where Saturdays (weekday 5) sell 10x a weekday."""
        series = []
        start = date(2026, 1, 5)  # a Monday
        for i in range(weeks * 7):
            d = start + timedelta(days=i)
            series.append((d, 10.0 if d.weekday() == 5 else 1.0))
        return series

    def test_preserves_weekday_shape(self):
        series = self._spiky_series()
        start = series[-1][0] + timedelta(days=1)  # next day after series
        preds = _seasonal_naive_baseline(series, 14, start)
        # Map predictions back to weekdays and check Saturday >> others.
        sat = [p for i, p in enumerate(preds) if (start + timedelta(days=i)).weekday() == 5]
        non_sat = [p for i, p in enumerate(preds) if (start + timedelta(days=i)).weekday() != 5]
        self.assertTrue(sat, 'expected at least one Saturday in the horizon')
        self.assertAlmostEqual(min(sat), 10.0, places=5)
        self.assertAlmostEqual(max(non_sat), 1.0, places=5)
        # A flat MA-7 would have predicted ~2.3 for every day — assert we're not flat.
        self.assertGreater(max(preds) - min(preds), 5.0)

    def test_empty_series(self):
        self.assertEqual(_seasonal_naive_baseline([], 5, date(2026, 1, 1)), [0.0] * 5)

    def test_weekday_with_no_history_falls_back_to_mean(self):
        # Only Mondays present; other weekdays should use the recent overall mean.
        series = [(date(2026, 1, 5) + timedelta(days=7 * i), 4.0) for i in range(5)]
        start = date(2026, 2, 10)  # a Tuesday
        preds = _seasonal_naive_baseline(series, 3, start)
        self.assertTrue(all(p == 4.0 for p in preds))  # overall mean is 4.0


class ForecastBaselineFallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cat = Category.objects.create(name='F', slug='f')
        cls.inv = InventoryItem.objects.create(
            name='Beans', unit='kg', stock_quantity=Decimal('999'), buying_price=Decimal('10'),
        )
        cls.item = MenuItem.objects.create(
            category=cls.cat, title='Plate', slug='plate',
            price=Decimal('100'), inventory_item=cls.inv,
        )

    def test_fallback_is_weekday_aware(self):
        from ml import fallbacks
        today = timezone.localdate()
        # Create paid orders only on Saturdays over the last 8 weeks.
        for i in range(1, 57):
            d = today - timedelta(days=i)
            if d.weekday() != 5:
                continue
            o = Order.objects.create(status='paid', waiter=None)
            Order.objects.filter(pk=o.pk).update(
                created_at=timezone.make_aware(
                    timezone.datetime(d.year, d.month, d.day, 12, 0)
                )
            )
            OrderItem.objects.create(
                order=o, menu_item=self.item, quantity=8,
                unit_price=Decimal('100'), unit_cost=Decimal('10'),
            )
        rows = fallbacks.forecast_baseline(14)
        self.assertTrue(rows, 'expected baseline rows for the item')
        by_wd = {}
        for r in rows:
            by_wd.setdefault(r['date'].weekday(), r['qty_p50'])
        # Saturday forecast should dominate; other days near zero.
        self.assertGreater(by_wd.get(5, 0), 0)
        others = [v for wd, v in by_wd.items() if wd != 5]
        self.assertTrue(all(v < by_wd[5] for v in others))


class WeekdayForecastViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('fmgr', password='pw')
        cls.manager.groups.add(cls.manager_group)
        cls.server = User.objects.create_user('fsrv', password='pw')

        cls.cat = Category.objects.create(name='G', slug='g')
        cls.hot = MenuItem.objects.create(category=cls.cat, title='Pizza', slug='pizza', price=Decimal('500'))
        cls.cold = MenuItem.objects.create(category=cls.cat, title='Soup', slug='soup', price=Decimal('200'))

        today = timezone.localdate()
        # 14-day forward forecast. Pizza spikes on Saturdays; Soup flat.
        for d_ahead in range(1, 15):
            d = today + timedelta(days=d_ahead)
            DemandForecast.objects.create(
                menu_item=cls.hot, date=d, hour=None,
                qty_p50=20.0 if d.weekday() == 5 else 3.0, qty_p90=30.0, source='ml',
            )
            DemandForecast.objects.create(
                menu_item=cls.cold, date=d, hour=None,
                qty_p50=5.0, qty_p90=7.0, source='ml',
            )

    def test_requires_supervisor_or_manager(self):
        self.client.force_login(self.server)
        resp = self.client.get(reverse('ml-weekday-forecast'))
        self.assertEqual(resp.status_code, 302)

    def test_busiest_day_is_saturday_and_pizza_peaks_saturday(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('ml-weekday-forecast'))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        # Busiest upcoming day must be a Saturday (pizza spike dominates).
        self.assertEqual(ctx['busiest']['date'].weekday(), 5)
        self.assertNotEqual(ctx['slowest']['date'].weekday(), 5)
        # Weekday ranking: Saturday on top.
        self.assertEqual(ctx['weekday_ranked'][0]['name'], 'Saturday')
        # Per-item: Pizza peaks Saturday, Soup is flat (peak is whatever, but present).
        peaks = {it['item'].title: it['peak_name'] for it in ctx['items']}
        self.assertEqual(peaks['Pizza'], 'Saturday')
        self.assertIn('Soup', peaks)
