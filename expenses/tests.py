from decimal import Decimal

from django.test import TestCase

from .models import Expense, ExpenseCategory


class ExpenseModelTests(TestCase):
    def test_category_total_and_expense_number(self):
        category = ExpenseCategory.objects.create(name='Test Utilities')
        first = Expense.objects.create(
            category=category, description='Electricity', amount=Decimal('1200.00'),
        )
        Expense.objects.create(
            category=category, description='Water', amount=Decimal('800.00'),
        )

        self.assertEqual(category.total_spent, Decimal('2000.00'))
        self.assertEqual(first.expense_number, f'EXP-{first.pk:04d}')
