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

    def test_received_quantity_is_derived_from_grn_rows(self):
        """PurchaseOrderItem.received_quantity is no longer a stored column —
        it sums the goods-receipt rows, so two partial receipts add up and
        stock is incremented exactly once per receipt."""
        for qty in ('4', '6'):
            self.client.post(
                reverse('receipt-create', kwargs={'po_pk': self.po.pk}),
                data={f'received_{self.po_item.pk}': qty},
            )
        self.po_item.refresh_from_db()
        self.inv.refresh_from_db()
        self.assertEqual(self.po_item.received_quantity, Decimal('10.00'))
        self.assertEqual(self.inv.stock_quantity, Decimal('10.00'))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'received')

    def test_cannot_over_receive_past_ordered_quantity(self):
        """Receiving more than remains is capped — stock and the invoice only
        ever reflect the ordered quantity, never more."""
        self.client.post(
            reverse('receipt-create', kwargs={'po_pk': self.po.pk}),
            data={f'received_{self.po_item.pk}': '8'},
        )
        # Try to receive 8 more on a PO with only 2 remaining.
        self.client.post(
            reverse('receipt-create', kwargs={'po_pk': self.po.pk}),
            data={f'received_{self.po_item.pk}': '8'},
        )
        self.po_item.refresh_from_db()
        self.inv.refresh_from_db()
        self.assertEqual(self.po_item.received_quantity, Decimal('10.00'))
        self.assertEqual(self.inv.stock_quantity, Decimal('10.00'))
        total_invoiced = sum(
            t.amount for t in SupplierTransaction.objects.filter(supplier=self.supplier)
        )
        self.assertEqual(total_invoiced, Decimal('500.00'))


class SupervisorPurchasingTests(TestCase):
    """Supervisors create, self-approve, and receive their own POs without a
    separate approver — and cannot act on POs they did not create."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.sup_group = Group.objects.create(name='Supervisor')
        self.supplier = Supplier.objects.create(name='Acme Foods')
        self.inv = InventoryItem.objects.create(
            name='Onion', unit='kg',
            stock_quantity=Decimal('0'), buying_price=Decimal('20'),
        )

    def _make_supervisor(self, username):
        u = User.objects.create_user(username=username, password='pw')
        u.groups.add(self.sup_group)
        return u

    def _po_with_item(self, owner):
        po = PurchaseOrder.objects.create(
            supplier=self.supplier, status='draft', created_by=owner,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po, inventory_item=self.inv,
            quantity=Decimal('5'), unit_price=Decimal('20'),
        )
        return po

    def test_supervisor_receives_own_draft_po_directly(self):
        """No approval step — a supervisor receives goods straight off their
        own draft PO."""
        sup = self._make_supervisor('sup1')
        po = self._po_with_item(sup)  # status='draft'
        c = Client(); c.force_login(sup)

        item = po.items.first()
        c.post(
            reverse('receipt-create', kwargs={'po_pk': po.pk}),
            data={f'received_{item.pk}': '5'},
        )
        po.refresh_from_db(); self.inv.refresh_from_db()
        self.assertEqual(po.status, 'received')
        self.assertEqual(self.inv.stock_quantity, Decimal('5.00'))

    def test_supervisor_cannot_receive_others_po(self):
        owner = self._make_supervisor('owner')
        other = self._make_supervisor('other')
        po = self._po_with_item(owner)
        c = Client(); c.force_login(other)

        item = po.items.first()
        c.post(
            reverse('receipt-create', kwargs={'po_pk': po.pk}),
            data={f'received_{item.pk}': '5'},
        )
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.stock_quantity, Decimal('0'))

    def test_self_receive_is_audit_logged_and_flagged(self):
        from receiving.models import GoodsReceipt
        sup = self._make_supervisor('sup_audit')
        po = self._po_with_item(sup)
        c = Client(); c.force_login(sup)
        item = po.items.first()

        with self.assertLogs('audit', level='INFO') as cm:
            c.post(
                reverse('receipt-create', kwargs={'po_pk': po.pk}),
                data={f'received_{item.pk}': '5'},
            )
        out = '\n'.join(cm.output)
        self.assertIn('self_receive=True', out)      # the event line
        self.assertIn('SELF-RECEIVE', out)            # the WARNING flag

        grn = GoodsReceipt.objects.get(purchase_order=po)
        self.assertTrue(grn.is_self_received)

    def test_manager_receiving_others_po_is_not_self_receive(self):
        from django.contrib.auth.models import User
        from receiving.models import GoodsReceipt
        owner = self._make_supervisor('sup_owner2')
        po = self._po_with_item(owner)
        mgr = User.objects.create_superuser('boss2', 'b@e.com', 'pw')
        c = Client(); c.force_login(mgr)
        item = po.items.first()

        with self.assertLogs('audit', level='INFO') as cm:
            c.post(
                reverse('receipt-create', kwargs={'po_pk': po.pk}),
                data={f'received_{item.pk}': '5'},
            )
        out = '\n'.join(cm.output)
        self.assertIn('self_receive=False', out)
        self.assertNotIn('SELF-RECEIVE', out)

        grn = GoodsReceipt.objects.get(purchase_order=po)
        self.assertFalse(grn.is_self_received)
