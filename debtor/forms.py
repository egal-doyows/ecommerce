from django import forms
from .models import Debtor, DebtorTransaction


class DebtorForm(forms.ModelForm):
    class Meta:
        model = Debtor
        fields = ['name', 'contact_person', 'phone', 'email', 'address', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'Debtor name'}),
            'contact_person': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'Contact person'}),
            'phone': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'Phone number'}),
            'email': forms.EmailInput(attrs={'class': 'adm-input', 'placeholder': 'Email address'}),
            'address': forms.Textarea(attrs={'class': 'adm-input', 'rows': 2, 'placeholder': 'Address'}),
            'notes': forms.Textarea(attrs={'class': 'adm-input', 'rows': 2, 'placeholder': 'Notes (optional)'}),
        }


class DebtorTransactionForm(forms.ModelForm):
    class Meta:
        model = DebtorTransaction
        fields = ['amount', 'description', 'reference', 'date']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'adm-input', 'step': '0.01', 'min': '0.01', 'placeholder': 'Amount'}),
            'description': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'e.g. Catering order on credit'}),
            'reference': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'Invoice / receipt number (optional)'}),
            'date': forms.DateInput(attrs={'class': 'adm-input', 'type': 'date'}),
        }
