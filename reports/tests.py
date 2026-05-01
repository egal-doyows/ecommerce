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


class StockOnHandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)

    def test_empty_renders(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-stock-on-hand'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_value'], Decimal('0'))

    def test_total_value_equals_sum_of_stock_times_cost(self):
        from menu.models import InventoryItem
        InventoryItem.objects.create(
            name='A', unit='kg', stock_quantity=Decimal('10'), buying_price=Decimal('5'),
        )
        InventoryItem.objects.create(
            name='B', unit='piece', stock_quantity=Decimal('3'), buying_price=Decimal('20'),
        )
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-stock-on-hand'))
        self.assertEqual(resp.context['total_value'], Decimal('110'))  # 10×5 + 3×20

    def test_changing_buying_price_changes_total(self):
        from menu.models import InventoryItem
        item = InventoryItem.objects.create(
            name='A', unit='kg', stock_quantity=Decimal('10'), buying_price=Decimal('5'),
        )
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-stock-on-hand'))
        self.assertEqual(resp.context['total_value'], Decimal('50'))

        item.buying_price = Decimal('8')
        item.save()
        resp = self.client.get(reverse('reports-stock-on-hand'))
        self.assertEqual(resp.context['total_value'], Decimal('80'))

    def test_csv_has_counted_column(self):
        from menu.models import InventoryItem
        InventoryItem.objects.create(
            name='A', unit='kg', stock_quantity=Decimal('10'), buying_price=Decimal('5'),
        )
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-stock-on-hand'), {'format': 'csv'})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('counted', body.split('\n')[0])

    def test_low_stock_only_filter(self):
        from menu.models import InventoryItem
        InventoryItem.objects.create(
            name='High', unit='kg', stock_quantity=Decimal('100'),
            buying_price=Decimal('5'), low_stock_threshold=Decimal('10'),
        )
        InventoryItem.objects.create(
            name='Low', unit='kg', stock_quantity=Decimal('1'),
            buying_price=Decimal('5'), low_stock_threshold=Decimal('10'),
        )
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-stock-on-hand'), {'low_stock': '1'})
        names = [r['name'] for r in resp.context['rows']]
        self.assertEqual(names, ['Low'])


class AgedReceivablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)

    def _make_invoice(self, debtor, amount, days_old, paid=Decimal('0')):
        from debtor.models import DebtorTransaction
        return DebtorTransaction.objects.create(
            debtor=debtor,
            transaction_type='debit',
            amount=Decimal(str(amount)),
            amount_paid=paid,
            description='inv',
            date=timezone.localdate() - timedelta(days=days_old),
        )

    def test_empty_renders(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-aged-receivables'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['totals']['total'], Decimal('0'))

    def test_buckets_split_at_30_60_90(self):
        """Acceptance: invoice 60 days old falls into 31-60; 61 days into 61-90."""
        from debtor.models import Debtor
        d = Debtor.objects.create(name='Acme')
        self._make_invoice(d, 100, days_old=15)   # 0-30
        self._make_invoice(d, 200, days_old=60)   # 31-60
        self._make_invoice(d, 400, days_old=61)   # 61-90
        self._make_invoice(d, 800, days_old=120)  # 90+

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-aged-receivables'))
        row = resp.context['rows'][0]
        self.assertEqual(row['b0_30'], Decimal('100'))
        self.assertEqual(row['b31_60'], Decimal('200'))
        self.assertEqual(row['b61_90'], Decimal('400'))
        self.assertEqual(row['b90_plus'], Decimal('800'))
        self.assertEqual(row['total'], Decimal('1500'))

    def test_buckets_sum_to_row_total(self):
        from debtor.models import Debtor
        d = Debtor.objects.create(name='X')
        self._make_invoice(d, 100, days_old=10)
        self._make_invoice(d, 50, days_old=45)
        self._make_invoice(d, 25, days_old=200)

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-aged-receivables'))
        row = resp.context['rows'][0]
        self.assertEqual(
            row['b0_30'] + row['b31_60'] + row['b61_90'] + row['b90_plus'],
            row['total'],
        )

    def test_paid_invoices_excluded(self):
        from debtor.models import Debtor
        d = Debtor.objects.create(name='Paid Up')
        self._make_invoice(d, 500, days_old=15, paid=Decimal('500'))  # fully paid

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-aged-receivables'))
        self.assertEqual(resp.context['rows'], [])

    def test_partial_payment_only_outstanding_aged(self):
        from debtor.models import Debtor
        d = Debtor.objects.create(name='Partial')
        # 1000 invoice, 700 paid, 300 outstanding, 45 days old → 31-60 bucket
        self._make_invoice(d, 1000, days_old=45, paid=Decimal('700'))

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-aged-receivables'))
        row = resp.context['rows'][0]
        self.assertEqual(row['b31_60'], Decimal('300'))
        self.assertEqual(row['total'], Decimal('300'))


class AuditTrailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)
        cls.superuser = User.objects.create_superuser('boss', 'b@x.com', 'pw')

    def test_manager_cannot_access(self):
        """Audit trail is owner-only — even managers are blocked."""
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-audit-trail'))
        self.assertEqual(resp.status_code, 302)

    def test_superuser_can_access(self):
        self.client.force_login(self.superuser)
        resp = self.client.get(reverse('reports-audit-trail'))
        self.assertEqual(resp.status_code, 200)

    def test_empty_state_renders(self):
        """A date range with no entries shows the empty state."""
        self.client.force_login(self.superuser)
        resp = self.client.get(reverse('reports-audit-trail'), {
            'preset': 'custom', 'start': '2020-01-01', 'end': '2020-01-01',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No audit log entries')

    def test_five_known_actions_produce_five_entries(self):
        """Acceptance: take five known actions, assert exactly five new audit log entries."""
        from auditlog.models import LogEntry
        from debtor.models import Debtor
        before = LogEntry.objects.count()

        d1 = Debtor.objects.create(name='One')        # 1
        d2 = Debtor.objects.create(name='Two')        # 2
        d1.name = 'One updated'
        d1.save()                                     # 3
        d2.name = 'Two updated'
        d2.save()                                     # 4
        d1.delete()                                   # 5

        self.assertEqual(LogEntry.objects.count() - before, 5)

        self.client.force_login(self.superuser)
        resp = self.client.get(reverse('reports-audit-trail'))
        self.assertEqual(resp.status_code, 200)
        # Page list should show all 5 (under the 50/page paginator).
        self.assertGreaterEqual(len(resp.context['entries']), 5)

    def test_action_filter_narrows_results(self):
        from debtor.models import Debtor
        d = Debtor.objects.create(name='X')   # create
        d.name = 'Y'
        d.save()                              # update
        d.delete()                            # delete

        self.client.force_login(self.superuser)
        resp = self.client.get(reverse('reports-audit-trail'), {'action': 'delete'})
        actions = {e['action'] for e in resp.context['entries']}
        self.assertEqual(actions, {'delete'})


class ZReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)
        cls.cashier = User.objects.create_user('cashier', password='pw')
        cls.other = User.objects.create_user('other', password='pw')

    def _make_shift(self, waiter, starting_cash=Decimal('1000')):
        from menu.models import Shift
        return Shift.objects.create(waiter=waiter, starting_cash=starting_cash)

    def _make_paid_order(self, shift, total, payment_method='cash'):
        """Create a paid order with one OrderItem totalling `total`."""
        from menu.models import Category, MenuItem, Order, OrderItem
        cat, _ = Category.objects.get_or_create(name='Test', slug='test')
        mi, _ = MenuItem.objects.get_or_create(
            category=cat, title='Item', slug='item', defaults={'price': total},
        )
        order = Order.objects.create(
            shift=shift, waiter=shift.waiter,
            status='paid', payment_method=payment_method,
        )
        OrderItem.objects.create(
            order=order, menu_item=mi, quantity=1,
            unit_price=Decimal(str(total)),
        )
        return order

    def test_list_renders_for_manager(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-z-report'))
        self.assertEqual(resp.status_code, 200)

    def test_cashier_only_sees_own_shifts_in_list(self):
        self._make_shift(self.cashier)
        self._make_shift(self.other)
        self.client.force_login(self.cashier)
        resp = self.client.get(reverse('reports-z-report'))
        shift_waiters = {s.waiter_id for s in resp.context['shifts']}
        self.assertEqual(shift_waiters, {self.cashier.id})

    def test_cashier_cannot_view_others_shift_detail(self):
        shift = self._make_shift(self.other)
        self.client.force_login(self.cashier)
        resp = self.client.get(reverse('reports-z-report-detail', args=[shift.id]))
        self.assertEqual(resp.status_code, 302)

    def test_empty_shift_renders(self):
        shift = self._make_shift(self.cashier)
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-z-report-detail', args=[shift.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['gross_sales'], Decimal('0'))
        self.assertEqual(resp.context['txn_count'], 0)

    def test_mixed_payments_reconcile(self):
        """Sales/payment-method/expected-cash match the data."""
        shift = self._make_shift(self.cashier, starting_cash=Decimal('500'))
        self._make_paid_order(shift, Decimal('200'), 'cash')
        self._make_paid_order(shift, Decimal('300'), 'cash')
        self._make_paid_order(shift, Decimal('150'), 'mpesa')

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-z-report-detail', args=[shift.id]))
        ctx = resp.context
        self.assertEqual(ctx['gross_sales'], Decimal('650'))
        self.assertEqual(ctx['txn_count'], 3)
        # Cash payment row
        cash_row = next(r for r in ctx['pm_breakdown'] if r['method'] == 'Cash')
        self.assertEqual(cash_row['count'], 2)
        self.assertEqual(cash_row['amount'], Decimal('500'))
        # Expected cash = 500 (opening) + 500 (cash sales) - 0 (refunds)
        self.assertEqual(ctx['expected_cash'], Decimal('1000'))

    def test_variance_calculation(self):
        """Counted - Expected; over is positive, short is negative."""
        from menu.models import Shift
        shift = self._make_shift(self.cashier, starting_cash=Decimal('1000'))
        self._make_paid_order(shift, Decimal('500'), 'cash')
        # Expected = 1500. Cashier counts 1450 → KES 50 short.
        shift.counted_cash = Decimal('1450')
        shift.save()

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-z-report-detail', args=[shift.id]))
        self.assertEqual(resp.context['variance'], Decimal('-50'))

    def test_void_refund_discount_comp_classification(self):
        """Cancelled-unpaid → void; cancelled-paid → refund; comp → comp; discounted → discount."""
        from menu.models import Category, MenuItem, Order, OrderItem
        shift = self._make_shift(self.cashier)
        cat, _ = Category.objects.get_or_create(name='T', slug='t')
        mi, _ = MenuItem.objects.get_or_create(
            category=cat, title='I', slug='i', defaults={'price': Decimal('100')},
        )

        def add(status, **kwargs):
            o = Order.objects.create(shift=shift, waiter=self.cashier, status=status, **kwargs)
            OrderItem.objects.create(order=o, menu_item=mi, quantity=1, unit_price=Decimal('100'))
            return o

        add('cancelled')                                         # void
        add('cancelled', payment_method='cash')                  # refund
        add('paid', payment_method='cash', is_comp=True)         # comp
        add('paid', payment_method='cash', discount_amount=Decimal('20'))  # discount

        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-z-report-detail', args=[shift.id]))
        self.assertEqual(resp.context['voids']['count'], 1)
        self.assertEqual(resp.context['refunds']['count'], 1)
        self.assertEqual(resp.context['comps']['count'], 1)
        self.assertEqual(resp.context['discounts']['count'], 1)
