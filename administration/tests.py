"""Tests for back-office CRUD parity additions:
Job Openings, Account edit, Shift edit, and Goods-receipt reversal.
"""
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from careers.models import JobOpening
from menu.models import Shift, InventoryItem
from administration.models import Account


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mgr_group, _ = Group.objects.get_or_create(name='Manager')
        cls.manager = User.objects.create_user('bmgr', password='pw')
        cls.manager.groups.add(cls.mgr_group)
        cls.cashier = User.objects.create_user('bcash', password='pw')


class JobOpeningCRUDTests(_Base):
    def test_manager_full_crud(self):
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('admin-job-create'), {
            'title': 'Barista', 'employment_type': 'full_time', 'location': 'Kilimani',
            'summary': 's', 'description': 'd', 'requirements': 'r',
            'how_to_apply': 'email', 'is_open': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        job = JobOpening.objects.get(title='Barista')
        self.assertTrue(job.slug)  # auto-generated
        self.client.post(reverse('admin-job-edit', args=[job.pk]), {
            'title': 'Senior Barista', 'employment_type': 'full_time', 'location': '',
            'summary': '', 'description': 'd', 'requirements': '', 'how_to_apply': 'email',
        })
        job.refresh_from_db()
        self.assertEqual(job.title, 'Senior Barista')
        self.assertFalse(job.is_open)  # checkbox unchecked
        self.client.post(reverse('admin-job-delete', args=[job.pk]))
        self.assertFalse(JobOpening.objects.filter(pk=job.pk).exists())

    def test_non_manager_cannot_create(self):
        self.client.force_login(self.cashier)
        resp = self.client.post(reverse('admin-job-create'), {
            'title': 'X', 'employment_type': 'full_time', 'description': 'd', 'how_to_apply': 'e',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(JobOpening.objects.filter(title='X').exists())


class AccountEditTests(_Base):
    def test_manager_edits_account(self):
        acct = Account.get_by_type('cash')
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('admin-account-edit-form', args=[acct.pk]), {
            'name': 'Main Till', 'description': 'front counter', 'is_active': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        acct.refresh_from_db()
        self.assertEqual(acct.name, 'Main Till')


class ShiftEditTests(_Base):
    def test_manager_corrects_starting_cash(self):
        shift = Shift.objects.create(waiter=self.cashier, starting_cash=Decimal('100'))
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('admin-shift-edit', args=[shift.id]), {
            'starting_cash': '250.00', 'notes': 'corrected float',
        })
        self.assertEqual(resp.status_code, 302)
        shift.refresh_from_db()
        self.assertEqual(shift.starting_cash, Decimal('250.00'))


class ReceiptReversalTests(_Base):
    def _setup_receipt(self, paid=False):
        from supplier.models import Supplier, SupplierTransaction
        from purchasing.models import PurchaseOrder, PurchaseOrderItem
        from receiving.models import GoodsReceipt, GoodsReceiptItem

        supplier = Supplier.objects.create(name='ACME Foods')
        inv = InventoryItem.objects.create(
            name='Flour', unit='kg', stock_quantity=Decimal('10'), buying_price=Decimal('50'),
        )
        po = PurchaseOrder.objects.create(supplier=supplier, status='received', created_by=self.manager)
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po, inventory_item=inv,
            quantity=Decimal('10'), unit_price=Decimal('50'), received_quantity=Decimal('10'),
        )
        receipt = GoodsReceipt.objects.create(purchase_order=po, received_by=self.manager)
        GoodsReceiptItem.objects.create(receipt=receipt, po_item=po_item, received_quantity=Decimal('10'))
        invoice = SupplierTransaction.objects.create(
            supplier=supplier, transaction_type='debit', amount=Decimal('500'),
            amount_paid=Decimal('250') if paid else Decimal('0'),
            description='Goods received', reference=receipt.grn_number,
        )
        return po, po_item, inv, receipt, invoice

    def test_reversal_rolls_back_stock_po_and_invoice(self):
        from supplier.models import SupplierTransaction
        po, po_item, inv, receipt, invoice = self._setup_receipt()
        self.client.force_login(self.manager)
        resp = self.client.post(reverse('receipt-reverse', args=[receipt.pk]))
        self.assertEqual(resp.status_code, 302)
        inv.refresh_from_db(); po_item.refresh_from_db(); po.refresh_from_db()
        self.assertEqual(inv.stock_quantity, Decimal('0'))
        self.assertEqual(po_item.received_quantity, Decimal('0'))
        self.assertEqual(po.status, 'approved')
        self.assertFalse(SupplierTransaction.objects.filter(pk=invoice.pk).exists())

    def test_reversal_blocked_when_invoice_paid(self):
        from receiving.models import GoodsReceipt
        po, po_item, inv, receipt, invoice = self._setup_receipt(paid=True)
        self.client.force_login(self.manager)
        self.client.post(reverse('receipt-reverse', args=[receipt.pk]))
        self.assertTrue(GoodsReceipt.objects.filter(pk=receipt.pk).exists())
        inv.refresh_from_db()
        self.assertEqual(inv.stock_quantity, Decimal('10'))
