from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from administration.models import Account, Transaction as AccountTransaction
from debtor.models import Debtor, DebtorPaymentAllocation, DebtorTransaction


class DebtorPaymentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pw')
        self.debtor = Debtor.objects.create(name='Acme')
        self.invoice = DebtorTransaction.objects.create(
            debtor=self.debtor, transaction_type='debit', amount=Decimal('500'),
            description='Invoice',
        )
        self.client.force_login(self.admin)

    def test_general_partial_card_payment_credits_bank(self):
        response = self.client.post(
            reverse('debtor-receive-payment', args=[self.debtor.id]),
            {
                'payment_amount': '125.00', 'payment_method': 'card',
                'payment_reference': 'AUTH42',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.remaining, Decimal('375.00'))
        payment = DebtorTransaction.objects.get(transaction_type='credit')
        self.assertEqual(payment.payment_method, 'card')
        self.assertEqual(payment.payment_reference, 'AUTH42')
        self.assertEqual(Account.get_by_type('bank').transactions.get().amount, Decimal('125.00'))

    def test_reverse_payment_reopens_invoice_and_reverses_account(self):
        self.client.post(
            reverse('debtor-receive-payment', args=[self.debtor.id]),
            {'payment_amount': '100.00', 'payment_method': 'cash'},
        )
        payment = DebtorTransaction.objects.get(transaction_type='credit')
        self.client.post(reverse('debtor-payment-reverse', args=[payment.id]))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('0'))
        self.assertFalse(DebtorTransaction.objects.filter(pk=payment.id).exists())
        self.assertFalse(DebtorPaymentAllocation.objects.exists())
        entries = AccountTransaction.objects.filter(account=Account.get_by_type('cash'))
        self.assertEqual(entries.count(), 2)
        self.assertEqual(
            sum(e.amount if e.transaction_type == 'credit' else -e.amount for e in entries),
            Decimal('0'),
        )

    def test_overpayment_is_rejected(self):
        self.client.post(
            reverse('debtor-receive-payment', args=[self.debtor.id]),
            {'payment_amount': '500.01', 'payment_method': 'cash'},
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('0'))
        self.assertFalse(AccountTransaction.objects.exists())
