from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from menu.models import InventoryItem
from purchasing.models import PurchaseOrder, PurchaseOrderItem
from supplier.models import Supplier, SupplierTransaction


class ReceivingFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='boss', email='boss@example.com', password='pw',
        )
        self.supplier = Supplier.objects.create(name='Acme Foods')
        self.inv = InventoryItem.objects.create(
            name='Tomato', unit='kg',
            stock_quantity=Decimal('0'), buying_price=Decimal('50'),
        )
        self.po = PurchaseOrder.objects.create(
            supplier=self.supplier,
            status='approved', created_by=self.user,
        )
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, inventory_item=self.inv,
            quantity=Decimal('10'), unit_price=Decimal('50'),
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_po_receive_redirects_to_receipt_create(self):
        """The legacy po-receive URL must 301 to receipt-create — anything else
        risks running the deprecated double-invoicing flow."""
        resp = self.client.get(
            reverse('po-receive', args=[self.po.pk]),
            follow=False,
        )
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(
            resp['Location'],
            reverse('receipt-create', kwargs={'po_pk': self.po.pk}),
        )

    def test_receiving_a_po_does_not_double_post_supplier_invoice(self):
        """Posting through the canonical receiving flow must create exactly one
        SupplierTransaction. C4 regression: the old purchasing.po_receive view
        also created one, doubling the invoice if both were called."""
        resp = self.client.post(
            reverse('receipt-create', kwargs={'po_pk': self.po.pk}),
            data={f'received_{self.po_item.pk}': '10'},
        )
        self.assertIn(resp.status_code, (302, 200))

        txns = SupplierTransaction.objects.filter(supplier=self.supplier)
        self.assertEqual(
            txns.count(), 1,
            f'Expected exactly one supplier invoice; found {txns.count()}.',
        )
        self.assertEqual(txns.first().transaction_type, 'debit')
        self.assertEqual(txns.first().amount, Decimal('500.00'))
