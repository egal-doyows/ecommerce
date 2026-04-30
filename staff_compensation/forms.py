from django import forms
from django.contrib.auth.models import User
from .models import StaffCompensation, StaffBankDetails, PaymentRecord, AdvanceRequest


class CompensationForm(forms.ModelForm):
    """Form for setting compensation type during user creation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make all conditional fields optional at the form level;
        # clean() enforces requirements based on compensation_type.
        for f in ('commission_scope', 'commission_rate_regular', 'commission_rate_premium',
                  'salary_amount', 'payment_frequency'):
            if f in self.fields:
                self.fields[f].required = False

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

        needs_commission = comp_type in ('commission', 'both')
        needs_salary = comp_type in ('salary', 'both')

        if needs_commission:
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

            # Clear rates for non-applicable scopes
            if scope == 'regular':
                cleaned_data['commission_rate_premium'] = 0
            elif scope == 'premium':
                cleaned_data['commission_rate_regular'] = 0
        else:
            # Clear commission fields
            cleaned_data['commission_rate_regular'] = 0
            cleaned_data['commission_rate_premium'] = 0

        if needs_salary:
            amount = cleaned_data.get('salary_amount')
            if not amount or amount <= 0:
                self.add_error('salary_amount', 'Salary amount is required and must be greater than 0.')
        else:
            # Clear salary fields
            cleaned_data['salary_amount'] = 0

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
        super().__init__(*args, **kwargs)
        from administration.models import Account
        self.fields['account'].queryset = Account.objects.filter(is_active=True, account_type='cash')
        self.fields['amount'].widget.attrs['max'] = str(self.remaining_amount)
        self.fields['amount'].initial = self.remaining_amount

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
        cleaned_data = super().clean()
        account = cleaned_data.get('account')
        amount = cleaned_data.get('amount')
        if account and amount and account.balance < amount:
            s = self._currency_symbol()
            self.add_error(
                'account',
                f'Insufficient balance. {account.name} has {s} {account.balance:,.2f} '
                f'but payment is {s} {amount:,.2f}.',
            )
        return cleaned_data


_input = {'class': 'form-control'}


class AdvanceRequestForm(forms.ModelForm):
    """Form for requesting a salary advance."""

    class Meta:
        model = AdvanceRequest
        fields = ['amount', 'reason']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Amount requested',
                'min': '1',
                'step': '0.01',
            }),
            'reason': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Reason for the advance request',
            }),
        }

    def __init__(self, *args, salary_amount=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.salary_amount = salary_amount

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError('Amount must be greater than zero.')
        if self.salary_amount and amount > self.salary_amount:
            from menu.models import RestaurantSettings
            s = RestaurantSettings.load().currency_symbol
            raise forms.ValidationError(
                f'Cannot exceed salary of {s} {self.salary_amount:,.2f}.'
            )
        return amount


class ManagerAdvanceRequestForm(AdvanceRequestForm):
    """Form for Branch Managers submitting advance requests on behalf of attendants."""

    employee = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='Employee',
    )

    class Meta(AdvanceRequestForm.Meta):
        fields = ['employee', 'amount', 'reason']

    def __init__(self, *args, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(
            is_active=True,
            compensation__isnull=False,
            groups__name='Attendant',
        )
        if branch:
            from branches.models import UserBranch
            branch_ids = UserBranch.objects.filter(branch=branch).values_list('user_id', flat=True)
            qs = qs.filter(pk__in=branch_ids)
        self.fields['employee'].queryset = qs.distinct()


class AdvanceReviewForm(forms.Form):
    """Form for approving or rejecting an advance request."""
    action = forms.ChoiceField(choices=[('approved', 'Approve'), ('rejected', 'Reject')])
    review_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Notes (optional)',
        }),
    )
