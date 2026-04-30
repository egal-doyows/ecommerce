from django import forms
from .models import TaxConfiguration


class TaxConfigurationForm(forms.ModelForm):
    class Meta:
        model = TaxConfiguration
        fields = ['is_enabled', 'tax_name', 'tax_rate', 'tax_type', 'tax_number']
        widgets = {
            'is_enabled': forms.CheckboxInput(attrs={'class': 'adm-checkbox'}),
            'tax_name': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'e.g. VAT, GST'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'adm-input', 'step': '0.01', 'min': '0', 'max': '100'}),
            'tax_type': forms.Select(attrs={'class': 'adm-input'}),
            'tax_number': forms.TextInput(attrs={'class': 'adm-input', 'placeholder': 'Tax registration number'}),
        }
