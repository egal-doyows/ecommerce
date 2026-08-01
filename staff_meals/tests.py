from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from menu.models import InventoryItem

from .models import StaffMealItem, StaffMealLog


class StaffMealItemTests(TestCase):
    def test_line_requires_exactly_one_source(self):
        log = StaffMealLog.objects.create(meal_type='lunch')
        item = StaffMealItem(
            staff_meal_log=log, quantity=Decimal('1'), unit_cost=Decimal('50'),
        )

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_raw_inventory_line_calculates_cost(self):
        inventory = InventoryItem.objects.create(
            name='Rice', unit='kg', stock_quantity=Decimal('5'), buying_price=Decimal('100'),
        )
        line = StaffMealItem.objects.create(
            staff_meal_log=StaffMealLog.objects.create(meal_type='lunch'),
            inventory_item=inventory, quantity=Decimal('2'), unit_cost=Decimal('100'),
        )

        self.assertEqual(line.cost, Decimal('200'))
        self.assertEqual(line.item_name, 'Rice')
