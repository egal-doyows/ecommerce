from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from menu.models import InventoryItem

from .models import WasteLog


class WasteCreateTests(TestCase):
    def setUp(self):
        managers, _ = Group.objects.get_or_create(name='Manager')
        self.user = User.objects.create_user('manager')
        self.user.groups.add(managers)
        self.inventory = InventoryItem.objects.create(
            name='Milk', unit='litre', stock_quantity=Decimal('10'),
            buying_price=Decimal('80'),
        )
        self.client.force_login(self.user)

    def test_create_deducts_stock_and_repeated_key_is_idempotent(self):
        payload = {
            'reason': 'spoiled', 'date': '2026-08-01', 'idempotency_key': 'waste-1',
            'item_0': str(self.inventory.pk), 'item_qty_0': '2',
        }
        self.client.post(reverse('waste-create'), payload)
        self.client.post(reverse('waste-create'), payload)

        self.inventory.refresh_from_db()
        self.assertEqual(WasteLog.objects.count(), 1)
        self.assertEqual(self.inventory.stock_quantity, Decimal('8'))
