"""Tests for the reopen-closed-shift-for-correction feature.

Covers: manager reopen/re-close, auto-backdating of corrected orders and their
ledger entries to the shift's date, refunding/reopening paid orders with
balanced accounting, and the guards that keep backdating out of normal
operation.
"""
import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from menu.models import (
    Category, MenuItem, InventoryItem, Order, OrderItem, Shift,
)


def _past():
    """A datetime several days before today (the shift's business day)."""
    return timezone.now() - timezone.timedelta(days=3)


class ShiftCorrectionBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)
        # Plain user = cash-handling server (manual shift, not auto-shift).
        cls.server = User.objects.create_user('server', password='pw')

        cls.cat = Category.objects.create(name='Drinks', slug='drinks')
        cls.inv = InventoryItem.objects.create(
            name='Coke', unit='bottle',
            stock_quantity=Decimal('100'), buying_price=Decimal('50'),
        )
        cls.item = MenuItem.objects.create(
            category=cls.cat, title='Coke', slug='coke',
            price=Decimal('150'), inventory_item=cls.inv,
        )

    def _closed_shift(self, waiter=None, started=None):
        waiter = waiter or self.server
        shift = Shift.objects.create(
            waiter=waiter, starting_cash=Decimal('1000'), is_active=False,
            counted_cash=Decimal('1000'), ended_at=timezone.now(),
        )
        # started_at is auto_now_add; force it to a past business day.
        Shift.objects.filter(pk=shift.pk).update(started_at=started or _past())
        shift.refresh_from_db()
        return shift

    def _reopen(self, shift):
        shift.is_active = True
        shift.reopened_at = timezone.now()
        shift.reopened_by = self.manager
        shift.save()
        return shift

    def _order(self, shift, status='active', payment_method='', debtor=None):
        order = Order.objects.create(
            waiter=self.server, shift=shift, status=status,
            payment_method=payment_method, debtor=debtor,
        )
        OrderItem.objects.create(
            order=order, menu_item=self.item, quantity=2,
            unit_price=Decimal('150'), unit_cost=Decimal('50'),
        )
        return order


class ReopenRecloseTests(ShiftCorrectionBase):
    def test_manager_reopen_sets_flags(self):
        shift = self._closed_shift()
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('admin-shift-reopen', args=[shift.id]))
        self.assertEqual(resp.status_code, 302)
        shift.refresh_from_db()
        self.assertTrue(shift.is_active)
        self.assertIsNotNone(shift.reopened_at)
        self.assertEqual(shift.reopened_by, self.manager)
        self.assertTrue(shift.in_correction)

    def test_server_cannot_reopen(self):
        shift = self._closed_shift()
        self.client.force_login(self.server)
        resp = self.client.post(reverse('admin-shift-reopen', args=[shift.id]))
        self.assertEqual(resp.status_code, 302)  # bounced by manager_only
        shift.refresh_from_db()
        self.assertFalse(shift.is_active)

    def test_reopen_blocked_when_waiter_has_active_shift(self):
        shift = self._closed_shift()
        Shift.objects.create(waiter=self.server, is_active=True)  # current shift
        self.client.force_login(self.manager)
        self.client.post(reverse('admin-shift-reopen', args=[shift.id]))
        shift.refresh_from_db()
        self.assertFalse(shift.is_active)  # stayed closed

    def test_reopen_idempotent(self):
        shift = self._reopen(self._closed_shift())
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('admin-shift-reopen', args=[shift.id]))
        self.assertEqual(resp.status_code, 302)  # warning, no error

    def test_reclose_clears_flags_and_refreshes_ended_at(self):
        shift = self._reopen(self._closed_shift())
        self.client.force_login(self.manager)
        self.client.post(reverse('admin-shift-reclose', args=[shift.id]))
        shift.refresh_from_db()
        self.assertFalse(shift.is_active)
        self.assertIsNone(shift.reopened_at)
        self.assertIsNone(shift.reopened_by)
        self.assertFalse(shift.in_correction)

    def test_reclose_blocked_with_open_order(self):
        shift = self._reopen(self._closed_shift())
        self._order(shift, status='active')
        self.client.force_login(self.manager)
        self.client.post(reverse('admin-shift-reclose', args=[shift.id]))
        shift.refresh_from_db()
        self.assertTrue(shift.is_active)  # still open — blocked


class BackdatingTests(ShiftCorrectionBase):
    def test_api_order_created_backdated_to_shift(self):
        shift = self._reopen(self._closed_shift())
        self.client.force_login(self.server)
        resp = self.client.post(
            reverse('api-place-order'),
            data=json.dumps({
                'order_type': 'takeaway',
                'items': [{'id': self.item.id, 'qty': 1}],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(pk=resp.json()['order_id'])
        self.assertEqual(order.created_at.date(), shift.started_at.date())

    def test_backdate_is_noop_on_normal_shift(self):
        shift = Shift.objects.create(waiter=self.server, is_active=True)
        self.client.force_login(self.server)
        resp = self.client.post(
            reverse('api-place-order'),
            data=json.dumps({
                'order_type': 'takeaway',
                'items': [{'id': self.item.id, 'qty': 1}],
            }),
            content_type='application/json',
        )
        order = Order.objects.get(pk=resp.json()['order_id'])
        self.assertEqual(order.created_at.date(), timezone.localdate())

    def test_paid_cash_order_ledger_backdated(self):
        from administration.models import Account
        shift = self._reopen(self._closed_shift())
        order = self._order(shift, status='active')
        self.client.force_login(self.server)
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'paid', 'payment_method': 'cash'},
        )
        cash = Account.get_by_type('cash')
        txn = cash.transactions.get(reference_type='order', reference_id=order.id)
        self.assertEqual(txn.transaction_type, 'credit')
        self.assertEqual(txn.created_at.date(), shift.started_at.date())


class RefundTests(ShiftCorrectionBase):
    def test_refund_paid_cash_order_balances_and_restores_stock(self):
        from administration.models import Account
        shift = self._reopen(self._closed_shift())
        order = self._order(shift, status='active')
        self.client.force_login(self.server)
        # Pay it (cash credit), then refund (cancel) it.
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'paid', 'payment_method': 'cash'},
        )
        self.inv.refresh_from_db()
        stock_after_sale = self.inv.stock_quantity
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'cancelled'},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(order.payment_method, 'cash')  # kept → classed as refund
        # Stock restored.
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.stock_quantity, stock_after_sale + Decimal('2'))
        # Ledger nets to zero for this order (credit + reversing debit).
        cash = Account.get_by_type('cash')
        txns = cash.transactions.filter(reference_id=order.id)
        self.assertEqual(txns.count(), 2)
        net = sum(
            (t.amount if t.transaction_type == 'credit' else -t.amount)
            for t in txns
        )
        self.assertEqual(net, Decimal('0'))
        for t in txns:
            self.assertEqual(t.created_at.date(), shift.started_at.date())

    def test_reopen_to_active_then_repay(self):
        from administration.models import Account
        shift = self._reopen(self._closed_shift())
        order = self._order(shift, status='active')
        self.client.force_login(self.server)
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'paid', 'payment_method': 'cash'},
        )
        # Reopen the paid order for correction.
        self.client.post(reverse('order-reopen', args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.status, 'active')
        self.assertEqual(order.payment_method, '')
        # Re-pay.
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'paid', 'payment_method': 'cash'},
        )
        cash = Account.get_by_type('cash')
        net = sum(
            (t.amount if t.transaction_type == 'credit' else -t.amount)
            for t in cash.transactions.filter(reference_id=order.id)
        )
        # original credit - reversing debit + new credit = one order total.
        self.assertEqual(net, order.get_total())

    def test_credit_refund_removes_invoice(self):
        from debtor.models import Debtor, DebtorTransaction
        debtor = Debtor.objects.create(name='Acme')
        shift = self._reopen(self._closed_shift())
        order = self._order(shift, status='active')
        self.client.force_login(self.server)
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'paid', 'payment_method': 'credit', 'debtor_id': debtor.id},
        )
        self.assertTrue(
            DebtorTransaction.objects.filter(reference=str(order.id)).exists()
        )
        # Refund the credit order.
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'cancelled'},
        )
        self.assertFalse(
            DebtorTransaction.objects.filter(reference=str(order.id)).exists()
        )

    def test_partially_settled_credit_blocked(self):
        from debtor.models import Debtor, DebtorTransaction
        debtor = Debtor.objects.create(name='Acme')
        shift = self._reopen(self._closed_shift())
        order = self._order(shift, status='paid', payment_method='credit', debtor=debtor)
        inv = DebtorTransaction.objects.create(
            debtor=debtor, transaction_type='debit', amount=order.get_total(),
            amount_paid=Decimal('100'), description='inv', reference=str(order.id),
        )
        self.client.force_login(self.server)
        self.client.post(reverse('order-reopen', args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')  # blocked — still paid
        self.assertTrue(DebtorTransaction.objects.filter(pk=inv.pk).exists())


class CorrectionGuardTests(ShiftCorrectionBase):
    def test_clock_out_blocked_in_correction(self):
        shift = self._reopen(self._closed_shift())
        self.client.force_login(self.server)
        self.client.post(reverse('shift-clock-out'))
        shift.refresh_from_db()
        self.assertTrue(shift.is_active)  # not clocked out

    def test_audit_entries_written_for_reopen(self):
        from auditlog.models import LogEntry
        shift = self._closed_shift()
        self.client.force_login(self.manager)
        self.client.post(reverse('admin-shift-reopen', args=[shift.id]))
        self.client.post(reverse('admin-shift-reclose', args=[shift.id]))
        entries = LogEntry.objects.filter(
            object_pk=str(shift.id),
            content_type__model='shift',
        )
        self.assertGreaterEqual(entries.count(), 1)


# ── Audit-remediation batch: totals, offline idempotency, double-pay guard ──

class OrderTotalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cat = Category.objects.create(name='T', slug='t')
        cls.item = MenuItem.objects.create(
            category=cls.cat, title='Plate', slug='plate', price=Decimal('100'),
        )

    def _order(self, **kw):
        o = Order.objects.create(status='active', **kw)
        OrderItem.objects.create(
            order=o, menu_item=self.item, quantity=3,
            unit_price=Decimal('100'), unit_cost=Decimal('20'),
        )
        return o

    def test_plain_total(self):
        self.assertEqual(self._order().get_total(), Decimal('300'))

    def test_comp_is_zero(self):
        self.assertEqual(self._order(is_comp=True).get_total(), Decimal('0'))

    def test_discount_subtracted(self):
        o = self._order(discount_amount=Decimal('50'))
        self.assertEqual(o.get_total(), Decimal('250'))

    def test_discount_cannot_go_negative(self):
        o = self._order(discount_amount=Decimal('999'))
        self.assertEqual(o.get_total(), Decimal('0'))


class OfflineSyncIdempotencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.server = User.objects.create_user('osrv', password='pw')
        cls.cat = Category.objects.create(name='O', slug='o')
        cls.inv = InventoryItem.objects.create(
            name='Stuff', unit='ea', stock_quantity=Decimal('1000'), buying_price=Decimal('5'),
        )
        cls.item = MenuItem.objects.create(
            category=cls.cat, title='Thing', slug='thing',
            price=Decimal('100'), inventory_item=cls.inv,
        )

    def setUp(self):
        Shift.objects.create(waiter=self.server, is_active=True)
        self.client.force_login(self.server)

    def _place(self, offline_id):
        return self.client.post(
            reverse('api-place-order'),
            data=json.dumps({
                'order_type': 'takeaway', 'offline_id': offline_id,
                'items': [{'id': self.item.id, 'qty': 1}],
            }),
            content_type='application/json',
        )

    def test_replayed_offline_id_does_not_duplicate(self):
        r1 = self._place('abc-123')
        r2 = self._place('abc-123')  # replay (lost response)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()['order_id'], r2.json()['order_id'])
        self.assertTrue(r2.json().get('duplicate'))
        self.assertEqual(Order.objects.filter(offline_id='abc-123').count(), 1)

    def test_double_pay_is_idempotent(self):
        from administration.models import Account
        shift = Shift.objects.filter(waiter=self.server, is_active=True).first()
        order = Order.objects.create(status='active', waiter=self.server, shift=shift)
        OrderItem.objects.create(
            order=order, menu_item=self.item, quantity=2,
            unit_price=Decimal('100'), unit_cost=Decimal('5'),
        )
        url = reverse('api-update-order-status', args=[order.id])
        body = json.dumps({'status': 'paid', 'payment_method': 'cash'})
        r1 = self.client.post(url, data=body, content_type='application/json')
        r2 = self.client.post(url, data=body, content_type='application/json')  # replay
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r2.json().get('idempotent'))
        cash = Account.get_by_type('cash')
        # Exactly one credit for this order, not two.
        self.assertEqual(
            cash.transactions.filter(reference_type='order', reference_id=order.id).count(), 1,
        )


class SplitPaymentTests(ShiftCorrectionBase):
    """Split tender: one order settled across several modes that must sum to
    the total. See menu.views._apply_split_payment / Order.payment_breakdown."""

    def setUp(self):
        self.shift = Shift.objects.create(waiter=self.server, is_active=True)
        self.client.force_login(self.server)

    def _pay_split(self, order, methods, amounts, codes=None):
        data = {
            'status': 'paid',
            'payment_method': 'split',
            'split_method': methods,
            'split_amount': amounts,
        }
        if codes is not None:
            data['split_mpesa_code'] = codes
        return self.client.post(
            reverse('order-update-status', args=[order.id]), data,
        )

    def test_split_pays_in_full_and_posts_per_mode_ledger(self):
        from administration.models import Account
        order = self._order(self.shift, status='active')  # total 300
        self._pay_split(order, ['cash', 'mpesa'], ['200', '100'], ['', 'AB12'])

        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.payment_method, 'split')
        self.assertEqual(order.payments.count(), 2)

        bd = order.payment_breakdown()
        self.assertEqual(bd['cash'], Decimal('200.00'))
        self.assertEqual(bd['mpesa'], Decimal('100.00'))

        cash = Account.get_by_type('cash')
        mpesa = Account.get_by_type('mpesa')
        cash_txn = cash.transactions.get(reference_type='order', reference_id=order.id)
        mpesa_txn = mpesa.transactions.get(reference_type='order', reference_id=order.id)
        self.assertEqual(cash_txn.transaction_type, 'credit')
        self.assertEqual(cash_txn.amount, Decimal('200.00'))
        self.assertEqual(mpesa_txn.amount, Decimal('100.00'))
        # M-Pesa code captured on the line.
        self.assertEqual(order.payments.get(payment_method='mpesa').mpesa_code, 'AB12')

    def test_split_must_sum_to_total(self):
        from administration.models import Account
        order = self._order(self.shift, status='active')  # total 300
        resp = self._pay_split(order, ['cash', 'card'], ['200', '50'])  # = 250 ≠ 300
        self.assertEqual(resp.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, 'active')          # not settled
        self.assertEqual(order.payments.count(), 0)        # no rows created
        self.assertFalse(
            Account.get_by_type('cash').transactions
            .filter(reference_id=order.id).exists()
        )

    def test_split_refund_reverses_each_mode(self):
        from administration.models import Account
        shift = self._reopen(self._closed_shift())
        order = self._order(shift, status='active')        # total 300
        self._pay_split(order, ['cash', 'mpesa'], ['200', '100'], ['', 'AB12'])
        # Refund (cancel a paid order while its shift is under correction).
        self.client.post(
            reverse('order-update-status', args=[order.id]),
            {'status': 'cancelled'},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        for acct_type in ('cash', 'mpesa'):
            txns = Account.get_by_type(acct_type).transactions.filter(reference_id=order.id)
            self.assertEqual(txns.count(), 2)              # credit + reversing debit
            net = sum(
                (t.amount if t.transaction_type == 'credit' else -t.amount)
                for t in txns
            )
            self.assertEqual(net, Decimal('0'))
