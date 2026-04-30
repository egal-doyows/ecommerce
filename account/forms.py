from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django import forms
from django.forms.widgets import PasswordInput, TextInput

from account.models import WaiterCode


def _validate_digit_code(code):
    """Shared validator for waiter login codes."""
    if not code.isdigit():
        raise forms.ValidationError('Code must contain only digits.')
    return code


COMPENSATION_TYPE_CHOICES = [
    ('commission', 'Commission'),
    ('salary', 'Salary'),
]

COMMISSION_SCOPE_CHOICES = [
    ('regular', 'Regular Items Only'),
    ('premium', 'Premium Items Only'),
    ('both', 'Both Regular & Premium'),
]

PAYMENT_FREQUENCY_CHOICES = [
    ('weekly', 'Weekly'),
    ('biweekly', 'Bi-Weekly'),
    ('monthly', 'Monthly'),
]


class CreateUserForm(UserCreationForm):
    compensation_type = forms.ChoiceField(
        choices=COMPENSATION_TYPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'comp-radio'}),
        label='Compensation Type',
    )
    commission_scope = forms.ChoiceField(
        choices=COMMISSION_SCOPE_CHOICES,
        required=False,
        widget=forms.RadioSelect(attrs={'class': 'scope-radio'}),
        label='Commission Scope',
    )
    commission_rate_regular = forms.DecimalField(
        required=False, min_value=0, max_value=100,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. 10',
            'step': '0.01',
        }),
        label='Regular Items Rate (%)',
    )
    commission_rate_premium = forms.DecimalField(
        required=False, min_value=0, max_value=100,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. 15',
            'step': '0.01',
        }),
        label='Premium Items Rate (%)',
    )
    salary_amount = forms.DecimalField(
        required=False, min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. 30000',
            'step': '0.01',
        }),
        label='Salary Amount',
    )
    payment_frequency = forms.ChoiceField(
        choices=PAYMENT_FREQUENCY_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='Payment Frequency',
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('This email is already in use.')
        if len(email) >= 350:
            raise forms.ValidationError('Your email is too long.')
        return email

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
        elif comp_type == 'salary':
            amount = cleaned_data.get('salary_amount')
            if not amount or amount <= 0:
                self.add_error('salary_amount', 'Salary amount is required.')
        return cleaned_data


class LoginForm(AuthenticationForm):
    username = forms.CharField(widget=TextInput())
    password = forms.CharField(widget=PasswordInput())


class WaiterLoginForm(forms.Form):
    code = forms.CharField(
        max_length=WaiterCode.CODE_LENGTH,
        min_length=WaiterCode.CODE_LENGTH,
        widget=forms.TextInput(attrs={
            'placeholder': f'Enter {WaiterCode.CODE_LENGTH}-digit code',
            'class': 'form-control form-control-lg text-center',
            'inputmode': 'numeric',
            'pattern': f'[0-9]{{{WaiterCode.CODE_LENGTH}}}',
            'autofocus': 'autofocus',
        }),
        label='Login Code',
    )

    def clean_code(self):
        return _validate_digit_code(self.cleaned_data.get('code'))


class WaiterProfileForm(forms.Form):
    photo = forms.ImageField(required=False, widget=forms.FileInput(attrs={
        'class': 'form-control',
        'accept': 'image/*',
    }))
    code = forms.CharField(
        max_length=WaiterCode.CODE_LENGTH,
        min_length=WaiterCode.CODE_LENGTH,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg text-center',
            'inputmode': 'numeric',
            'pattern': f'[0-9]{{{WaiterCode.CODE_LENGTH}}}',
            'style': 'letter-spacing:8px; font-size:24px; font-weight:700;',
        }),
        label='Login Code',
    )

    def clean_code(self):
        return _validate_digit_code(self.cleaned_data.get('code'))


class UserUpdateForm(forms.ModelForm):
    password = None

    class Meta:
        model = User
        fields = ['username', 'email']
        exclude = ['password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('This email is linked to an existing account.')
        if len(email) >= 350:
            raise forms.ValidationError('Your email is too long.')
        return email
