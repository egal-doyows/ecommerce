from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class ReportsAccessTests(TestCase):
    """Confirm the manager_required gate works on the index."""

    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')

        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)

        cls.cashier = User.objects.create_user('cashier', password='pw')
        cls.superuser = User.objects.create_superuser('boss', 'b@x.com', 'pw')

    def test_index_renders_for_manager(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 200)

    def test_index_renders_for_superuser(self):
        self.client.force_login(self.superuser)
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 200)

    def test_index_redirects_non_manager(self):
        self.client.force_login(self.cashier)
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 302)

    def test_index_redirects_anonymous(self):
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 302)


class ParseDateRangeTests(TestCase):
    """Period parsing — defaults, presets, and custom ranges."""

    def _make_request(self, **params):
        from django.test import RequestFactory
        return RequestFactory().get('/', params)

    def test_default_is_today(self):
        from django.utils import timezone
        from .utils import parse_date_range
        start, end, preset = parse_date_range(self._make_request())
        self.assertEqual(preset, 'today')
        self.assertEqual(start, timezone.localdate())
        self.assertEqual(end, timezone.localdate())

    def test_custom_swaps_inverted_range(self):
        from .utils import parse_date_range
        start, end, _ = parse_date_range(self._make_request(
            preset='custom', start='2026-05-10', end='2026-05-01',
        ))
        self.assertEqual(start.isoformat(), '2026-05-01')
        self.assertEqual(end.isoformat(), '2026-05-10')

    def test_month_spans_full_month(self):
        from .utils import parse_date_range
        start, end, _ = parse_date_range(self._make_request(preset='month'))
        self.assertEqual(start.day, 1)
        # End is the last day of the month → next day is day 1 of next month.
        self.assertEqual((end + timedelta(days=1)).day, 1)


class ProfitLossTests(TestCase):
    """P&L renders, handles empty data, and uses frozen unit_cost (not live buying_price)."""

    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)

    def test_empty_period_renders_without_crashing(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-profit-loss'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No financial activity')

    def test_csv_export(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-profit-loss'), {'format': 'csv'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        self.assertIn('attachment', resp['Content-Disposition'])

    def test_cogs_uses_frozen_unit_cost_not_current_buying_price(self):
        """
        Regression: P&L must read OrderItem.unit_cost (snapshot at order time),
        not the current InventoryItem.buying_price. This is the whole point
        of the snapshot.
        """
        from menu.models import (
            Category, MenuItem, InventoryItem, Order, OrderItem,
        )
        cat = Category.objects.create(name='Drinks', slug='drinks')
        inv = InventoryItem.objects.create(
            name='Coke', unit='bottle',
            stock_quantity=Decimal('100'), buying_price=Decimal('50'),
        )
        mi = MenuItem.objects.create(
            category=cat, title='Coke', slug='coke',
            price=Decimal('150'), inventory_item=inv,
        )
        # Today's order, snapshot cost = 50
        order = Order.objects.create(status='paid', waiter=self.manager)
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=2,
            unit_price=Decimal('150'), unit_cost=Decimal('50'),
        )

        # Inventory cost surges *after* the order — must NOT affect historical P&L
        inv.buying_price = Decimal('999')
        inv.save()

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-profit-loss'))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context['current']
        self.assertEqual(ctx['revenue'], Decimal('300'))   # 2 × 150
        self.assertEqual(ctx['cogs'], Decimal('100'))      # 2 × 50, NOT 2 × 999
        self.assertEqual(ctx['gross_profit'], Decimal('200'))
