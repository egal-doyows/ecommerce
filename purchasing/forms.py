from django import forms
from .models import PurchaseOrder, PurchaseOrderItem
from supplier.models import Supplier
from menu.models import InventoryItem

_input = {'class': 'adm-input'}
_select = {'class': 'adm-input'}
_textarea = {'class': 'adm-input', 'rows': 2}


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ['supplier', 'order_date', 'expected_date', 'notes']
        widgets = {
            'supplier': forms.Select(attrs=_select),
            'order_date': forms.DateInput(attrs={**_input, 'type': 'date'}),
            'expected_date': forms.DateInput(attrs={**_input, 'type': 'date'}),
            'notes': forms.Textarea(attrs=_textarea),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.filter(is_active=True)
        self.fields['expected_date'].required = False


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ['inventory_item', 'quantity', 'unit_price']
        widgets = {
            'inventory_item': forms.Select(attrs=_select),
            'quantity': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0.01'}),
            'unit_price': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['inventory_item'].queryset = InventoryItem.objects.all().order_by('name')
