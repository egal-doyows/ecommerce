"""Tests for the printed POS receipt rendered in menu/order-detail.html.

Covers the three integrity fixes:
  #1 Discounted / comped orders show Subtotal + Discount/Complimentary lines so
     the printed items reconcile to the printed TOTAL.
  #2 Active (unpaid) orders print a "Not Paid / Proforma" banner.
  #3 Cancelled (voided) orders print a "Void" banner.

The receipt block is always present in the response (it is merely display:none
on screen and revealed by @media print), so we can assert on the rendered HTML.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from menu.cache import REST_SETTINGS_KEY
from django.core.cache import cache

from menu.models import (
    Category, MenuItem, InventoryItem, Order, OrderItem, Shift,
)


class ReceiptRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
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

    def setUp(self):
        # Clear the cached RestaurantSettings singleton so currency/other
        # settings don't leak between tests.
        cache.delete(REST_SETTINGS_KEY)
        # order_detail is @shift_required; a manual-shift user needs an active one.
        Shift.objects.create(
            waiter=self.server, starting_cash=Decimal('1000'), is_active=True,
        )
        self.client.login(username='server', password='pw')

    def _order(self, *, status='active', payment_method='', quantity=2,
               unit_price='150', discount='0', is_comp=False):
        order = Order.objects.create(
            waiter=self.server, status=status, payment_method=payment_method,
            discount_amount=Decimal(discount), is_comp=is_comp,
        )
        OrderItem.objects.create(
            order=order, menu_item=self.item,
            quantity=quantity, unit_price=Decimal(unit_price),
        )
        return order

    def _get(self, order):
        resp = self.client.get(reverse('order-detail', args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    # --- #2 unpaid (proforma) ------------------------------------------------

    # The banner CSS class is always present in the <style> block, so these
    # assert on the banner's distinctive copy, which only the <div> emits.

    def test_active_order_shows_proforma_banner(self):
        html = self._get(self._order(status='active'))
        self.assertIn('Not Paid', html)
        self.assertIn('Proforma', html)

    # --- #3 voided -----------------------------------------------------------

    def test_cancelled_order_shows_void_banner(self):
        html = self._get(self._order(status='cancelled'))
        self.assertIn('Not a Valid Receipt', html)

    def test_paid_order_has_no_status_banner(self):
        html = self._get(self._order(status='paid', payment_method='cash'))
        self.assertNotIn('Proforma', html)
        self.assertNotIn('Not a Valid Receipt', html)

    # --- #1 discount / comp reconciliation -----------------------------------

    def test_paid_order_has_no_subtotal_or_discount_lines(self):
        # No discount, no comp -> only the TOTAL line, no Subtotal/Discount.
        html = self._get(self._order(status='paid', payment_method='cash'))
        self.assertNotIn('>Subtotal<', html)
        self.assertNotIn('>Discount<', html)
        self.assertNotIn('Complimentary', html)

    def test_discount_shows_subtotal_and_discount_lines(self):
        # 2 x 150 = 300 subtotal, 50 discount -> 250 total.
        order = self._order(status='paid', payment_method='cash', discount='50')
        self.assertEqual(order.get_subtotal(), Decimal('300'))
        self.assertEqual(order.get_total(), Decimal('250'))
        html = self._get(order)
        self.assertIn('>Subtotal<', html)
        self.assertIn('300.00', html)
        self.assertIn('>Discount<', html)
        self.assertIn('-', html)  # discount rendered as a negative line
        # The printed total reflects the discount, not the raw subtotal.
        self.assertIn('250.00', html)

    def test_comp_shows_complimentary_and_zero_total(self):
        order = self._order(status='paid', payment_method='cash', is_comp=True)
        self.assertEqual(order.get_subtotal(), Decimal('300'))
        self.assertEqual(order.get_total(), Decimal('0'))
        html = self._get(order)
        self.assertIn('>Subtotal<', html)
        self.assertIn('Complimentary', html)
        self.assertIn('TOTAL (COMP)', html)
        self.assertIn('0.00', html)
