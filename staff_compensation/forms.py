from django import forms
from .models import StaffCompensation, StaffBankDetails, PaymentRecord


class CompensationForm(forms.ModelForm):
    """Form for setting compensation type during user creation."""

    class Meta:
        model = StaffCompensation
        fields = [
            'compensation_type', 'commission_scope',
            'commission_rate_regular', 'commission_rate_premium',
            'salary_amount', 'payment_frequency',
        ]
        widgets = {
            'compensation_type': forms.RadioSelect(attrs={
                'class': 'comp-radio',
            }),
            'commission_scope': forms.RadioSelect(attrs={
                'class': 'scope-radio',
            }),
            'commission_rate_regular': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 10',
                'min': '0',
                'max': '100',
                'step': '0.01',
            }),
            'commission_rate_premium': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 15',
                'min': '0',
                'max': '100',
                'step': '0.01',
            }),
            'salary_amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 30000',
                'min': '0',
                'step': '0.01',
            }),
            'payment_frequency': forms.Select(attrs={
                'class': 'form-control',
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        comp_type = cleaned_data.get('compensation_type')

        if comp_type == 'commission':
            scope = cleaned_data.get('commission_scope', 'both')
            rate_reg = cleaned_data.get('commission_rate_regular') or 0
            rate_prem = cleaned_data.get('commission_rate_premium') or 0

            if scope in ('regular', 'both') and rate_reg <= 0:
                self.add_error('commission_rate_regular', 'Rate is required for regular items.')
            if scope in ('premium', 'both') and rate_prem <= 0:
                self.add_error('commission_rate_premium', 'Rate is required for premium items.')

            if rate_reg > 100:
                self.add_error('commission_rate_regular', 'Cannot exceed 100%.')
            if rate_prem > 100:
                self.add_error('commission_rate_premium', 'Cannot exceed 100%.')

            # Clear salary fields
            cleaned_data['salary_amount'] = 0

            # Clear rates for non-applicable scopes
            if scope == 'regular':
                cleaned_data['commission_rate_premium'] = 0
            elif scope == 'premium':
                cleaned_data['commission_rate_regular'] = 0

        elif comp_type == 'salary':
            amount = cleaned_data.get('salary_amount')
            if not amount or amount <= 0:
                self.add_error('salary_amount', 'Salary amount is required and must be greater than 0.')
            # Clear commission fields
            cleaned_data['commission_rate_regular'] = 0
            cleaned_data['commission_rate_premium'] = 0

        return cleaned_data


class StaffBankDetailsForm(forms.ModelForm):
    class Meta:
        model = StaffBankDetails
        fields = ['bank_name', 'account_name', 'account_number', 'branch']
        widgets = {
            'bank_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Equity Bank'}),
            'account_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Account holder name'}),
            'account_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Account number'}),
            'branch': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Branch (optional)'}),
        }


class PaymentDisbursementForm(forms.Form):
    """Form shown when clicking 'Pay' on a pending payment record."""
    from administration.models import Account

    account = forms.ModelChoiceField(
        queryset=None,  # set in __init__
        widget=forms.RadioSelect(attrs={'class': 'pay-account-radio'}),
        label='Pay From Account',
        empty_label=None,
    )
    amount = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'min': '1',
            'placeholder': 'Amount to pay',
        }),
        label='Amount to Pay',
    )
    advance_recovery = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'min': '0',
            'placeholder': '0.00',
        }),
        label='Recover from advance',
        help_text='Withheld from this payment to repay an outstanding salary advance.',
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Payment notes (optional)',
        }),
        label='Notes',
    )

    def __init__(self, *args, **kwargs):
        self.remaining_amount = kwargs.pop('remaining_amount', 0)
        self.advance_outstanding = kwargs.pop('advance_outstanding', 0)
        super().__init__(*args, **kwargs)
        from administration.models import Account
        self.fields['account'].queryset = Account.objects.filter(is_active=True, account_type='cash')
        self.fields['amount'].widget.attrs['max'] = str(self.remaining_amount)
        self.fields['amount'].initial = self.remaining_amount
        self.fields['advance_recovery'].widget.attrs['max'] = str(self.advance_outstanding)
        if not self.advance_outstanding:
            # No outstanding advance — hide the recovery field entirely.
            self.fields.pop('advance_recovery')

    def _currency_symbol(self):
        from menu.models import RestaurantSettings
        return RestaurantSettings.load().currency_symbol

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount > self.remaining_amount:
            s = self._currency_symbol()
            raise forms.ValidationError(
                f'Cannot exceed remaining balance of {s} {self.remaining_amount:,.2f}.'
            )
        return amount

    def clean(self):
        from decimal import Decimal
        cleaned_data = super().clean()
        account = cleaned_data.get('account')
        amount = cleaned_data.get('amount')
        recovery = cleaned_data.get('advance_recovery') or Decimal('0')
        s = self._currency_symbol()

        if recovery:
            if recovery > self.advance_outstanding:
                self.add_error(
                    'advance_recovery',
                    f'Cannot exceed the outstanding advance of {s} {self.advance_outstanding:,.2f}.',
                )
            if amount and recovery > amount:
                self.add_error(
                    'advance_recovery',
                    'Recovery cannot exceed the amount being paid.',
                )

        # The cash that actually leaves the account is amount minus the recovery
        # (the advance cash already left when it was disbursed).
        if account and amount:
            cash_out = amount - recovery
            if account.balance < cash_out:
                self.add_error(
                    'account',
                    f'Insufficient balance. {account.name} has {s} {account.balance:,.2f} '
                    f'but the cash to pay is {s} {cash_out:,.2f}.',
                )
        return cleaned_data
