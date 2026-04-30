"""
Tests for critical order and stock business logic.
"""

from decimal import Decimal

from django.test import TestCase, TransactionTestCase
from django.contrib.auth.models import User, Group

from .models import (
    Category, MenuItem, InventoryItem, Recipe, Table,
    Order, OrderItem, Shift, _InsufficientStock,
)
from .services import (
    place_order, update_order_status,
    InvalidTransition, validate_transition,
)


class OrderStateMachineTest(TestCase):
    """Test order status transition validation."""

    def test_active_to_paid_allowed(self):
        validate_transition('active', 'paid')

    def test_active_to_cancelled_allowed(self):
        validate_transition('active', 'cancelled')

    def test_paid_to_active_rejected(self):
        with self.assertRaises(InvalidTransition):
            validate_transition('paid', 'active')

    def test_paid_to_cancelled_rejected(self):
        with self.assertRaises(InvalidTransition):
            validate_transition('paid', 'cancelled')

    def test_cancelled_to_active_rejected(self):
        with self.assertRaises(InvalidTransition):
            validate_transition('cancelled', 'active')

    def test_cancelled_to_paid_rejected(self):
        with self.assertRaises(InvalidTransition):
            validate_transition('cancelled', 'paid')


class StockDeductionTest(TransactionTestCase):
    """Test atomic stock deduction on order placement."""

    def setUp(self):
        self.user = User.objects.create_user('waiter', password='pass')
        self.category = Category.objects.create(name='Drinks', slug='drinks')
        self.table = Table.objects.create(number=1, capacity=4)
        self.shift = Shift.objects.create(waiter=self.user, starting_cash=0)

    def test_direct_sale_deducts_stock(self):
        inv = InventoryItem.objects.create(name='Soda', stock_quantity=10)
        item = MenuItem.objects.create(
            title='Soda', slug='soda', price=100,
            category=self.category, inventory_item=inv,
        )

        order = place_order(
            cart_items=[{'product': item, 'qty': 3, 'price': Decimal('100')}],
            table=self.table, waiter=self.user, shift=self.shift,
        )

        inv.refresh_from_db()
        self.assertEqual(inv.stock_quantity, 7)
        self.assertEqual(order.status, 'active')
        self.assertEqual(order.items.count(), 1)

    def test_recipe_deducts_ingredients(self):
        mango = InventoryItem.objects.create(name='Mango', stock_quantity=10, unit='piece')
        sugar = InventoryItem.objects.create(name='Sugar', stock_quantity=5, unit='kg')
        juice = MenuItem.objects.create(
            title='Mango Juice', slug='mango-juice', price=200,
            category=self.category,
        )
        Recipe.objects.create(menu_item=juice, inventory_item=mango, quantity_required=Decimal('1'))
        Recipe.objects.create(menu_item=juice, inventory_item=sugar, quantity_required=Decimal('0.05'))

        place_order(
            cart_items=[{'product': juice, 'qty': 2, 'price': Decimal('200')}],
            table=self.table, waiter=self.user, shift=self.shift,
        )

        mango.refresh_from_db()
        sugar.refresh_from_db()
        self.assertEqual(mango.stock_quantity, 8)
        self.assertEqual(sugar.stock_quantity, Decimal('4.90'))

    def test_order_rolls_back_when_stock_insufficient(self):
        """F11 regression: ordering more than is in stock must raise
        _InsufficientStock from the service layer, leave inventory untouched,
        and create zero Order rows."""
        inv = InventoryItem.objects.create(name='Lemon', stock_quantity=1)
        item = MenuItem.objects.create(
            title='Lemon', slug='lemon', price=50,
            category=self.category, inventory_item=inv,
        )

        with self.assertRaises(_InsufficientStock) as ctx:
            place_order(
                cart_items=[{'product': item, 'qty': 5, 'price': Decimal('50')}],
                table=self.table, waiter=self.user, shift=self.shift,
            )

        self.assertIn('Lemon', str(ctx.exception))
        inv.refresh_from_db()
        self.assertEqual(inv.stock_quantity, 1)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    def test_insufficient_stock_rolls_back_entirely(self):
        """If any item fails stock deduction, the entire order rolls back."""
        inv1 = InventoryItem.objects.create(name='Item1', stock_quantity=5)
        inv2 = InventoryItem.objects.create(name='Item2', stock_quantity=1)
        item1 = MenuItem.objects.create(
            title='Item1', slug='item1', price=100,
            category=self.category, inventory_item=inv1,
        )
        item2 = MenuItem.objects.create(
            title='Item2', slug='item2', price=200,
            category=self.category, inventory_item=inv2,
        )

        with self.assertRaises(Exception):
            place_order(
                cart_items=[
                    {'product': item1, 'qty': 2, 'price': Decimal('100')},
                    {'product': item2, 'qty': 5, 'price': Decimal('200')},  # insufficient
                ],
                table=self.table, waiter=self.user, shift=self.shift,
            )

        # Stock should be unchanged due to atomic rollback
        inv1.refresh_from_db()
        inv2.refresh_from_db()
        self.assertEqual(inv1.stock_quantity, 5)
        self.assertEqual(inv2.stock_quantity, 1)
        self.assertEqual(Order.objects.count(), 0)

    def test_table_marked_occupied_after_order(self):
        item = MenuItem.objects.create(
            title='Tea', slug='tea', price=50, category=self.category,
        )

        place_order(
            cart_items=[{'product': item, 'qty': 1, 'price': Decimal('50')}],
            table=self.table, waiter=self.user, shift=self.shift,
        )

        self.table.refresh_from_db()
        self.assertEqual(self.table.status, 'occupied')


class OrderStatusUpdateTest(TransactionTestCase):
    """Test order status transitions via service layer."""

    def setUp(self):
        self.user = User.objects.create_user('waiter', password='pass')
        self.category = Category.objects.create(name='Food', slug='food')
        self.table = Table.objects.create(number=1, capacity=4)
        self.shift = Shift.objects.create(waiter=self.user, starting_cash=0)
        self.item = MenuItem.objects.create(
            title='Burger', slug='burger', price=500, category=self.category,
        )

    def _create_order(self):
        return place_order(
            cart_items=[{'product': self.item, 'qty': 1, 'price': Decimal('500')}],
            table=self.table, waiter=self.user, shift=self.shift,
        )

    def test_pay_order(self):
        order = self._create_order()
        update_order_status(order, 'paid', payment_method='cash', user=self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.payment_method, 'cash')

    def test_cancel_order_restores_stock(self):
        inv = InventoryItem.objects.create(name='Bun', stock_quantity=10)
        item = MenuItem.objects.create(
            title='Sandwich', slug='sandwich', price=300,
            category=self.category, inventory_item=inv,
        )
        order = place_order(
            cart_items=[{'product': item, 'qty': 3, 'price': Decimal('300')}],
            table=self.table, waiter=self.user, shift=self.shift,
        )

        inv.refresh_from_db()
        self.assertEqual(inv.stock_quantity, 7)

        update_order_status(order, 'cancelled', user=self.user)

        inv.refresh_from_db()
        self.assertEqual(inv.stock_quantity, 10)

    def test_table_freed_on_payment(self):
        order = self._create_order()
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, 'occupied')

        update_order_status(order, 'paid', payment_method='cash', user=self.user)
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, 'available')

    def test_cannot_pay_cancelled_order(self):
        order = self._create_order()
        update_order_status(order, 'cancelled', user=self.user)
        with self.assertRaises(InvalidTransition):
            update_order_status(order, 'paid', payment_method='cash', user=self.user)

    def test_cannot_cancel_paid_order(self):
        order = self._create_order()
        update_order_status(order, 'paid', payment_method='cash', user=self.user)
        with self.assertRaises(InvalidTransition):
            update_order_status(order, 'cancelled', user=self.user)


class TaxCalculationTest(TestCase):
    """Test tax calculation at order time."""

    def setUp(self):
        self.user = User.objects.create_user('waiter', password='pass')
        self.category = Category.objects.create(name='Food', slug='food')
        self.table = Table.objects.create(number=1, capacity=4)
        self.shift = Shift.objects.create(waiter=self.user, starting_cash=0)

    def test_order_stores_tax_at_creation(self):
        from tax.models import TaxConfiguration
        tax = TaxConfiguration.load()
        tax.is_enabled = True
        tax.tax_rate = Decimal('16.00')
        tax.tax_type = 'exclusive'
        tax.save()

        item = MenuItem.objects.create(
            title='Steak', slug='steak', price=1000, category=self.category,
        )

        order = place_order(
            cart_items=[{'product': item, 'qty': 1, 'price': Decimal('1000')}],
            table=self.table, waiter=self.user, shift=self.shift,
        )

        self.assertEqual(order.tax_rate, Decimal('16.00'))
        self.assertEqual(order.tax_amount, Decimal('160.00'))
        self.assertEqual(order.tax_type, 'exclusive')
        self.assertEqual(order.get_total(), Decimal('1160.00'))
