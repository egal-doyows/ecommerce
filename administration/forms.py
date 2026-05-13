from django import forms
from django.contrib.auth.models import User, Group
from django.contrib.auth.forms import UserCreationForm
from django.utils.text import slugify

from menu.models import (
    Category, MenuItem, InventoryItem, Recipe, Table, RestaurantSettings,
)
from account.models import WaiterCode
from staff_compensation.models import StaffCompensation


# ── Shared widget attrs ──────────────────────────────────────────────
_input = {'class': 'adm-input'}
_select = {'class': 'adm-input'}
_textarea = {'class': 'adm-input', 'rows': 3}
_file = {'class': 'adm-input'}


# ── Staff ─────────────────────────────────────────────────────────────

class StaffCreateForm(UserCreationForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs=_input))
    role = forms.ModelChoiceField(
        queryset=Group.objects.exclude(name='Attendant'),
        required=False,
        widget=forms.Select(attrs=_select),
        help_text='Assign a role. Leave blank only for commission-only staff.',
    )
    is_commission_only = forms.BooleanField(
        required=False,
        label='Commission-only staff (no login)',
        help_text=(
            'For staff who exist only to receive commission attribution '
            'and never log in. No password or role required.'
        ),
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs=_input),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update(_input)
        self.fields['password2'].widget.attrs.update(_input)
        # Real requirement is enforced in clean() — commission-only staff
        # skip both the password and the role.
        self.fields['password1'].required = False
        self.fields['password2'].required = False

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('This email is already in use.')
        return email

    def clean(self):
        cleaned_data = super().clean()
        is_co = cleaned_data.get('is_commission_only')
        role = cleaned_data.get('role')
        if is_co:
            # Drop password validation errors — they don't apply.
            self.errors.pop('password1', None)
            self.errors.pop('password2', None)
        else:
            if not role:
                self.add_error('role', 'Select a role.')
            if not cleaned_data.get('password1'):
                self.add_error('password1', 'This field is required.')
            if not cleaned_data.get('password2'):
                self.add_error('password2', 'This field is required.')
        return cleaned_data

    def save(self, commit=True):
        is_co = self.cleaned_data.get('is_commission_only')
        role = self.cleaned_data.get('role')
        user = super().save(commit=False)
        if is_co:
            user.set_unusable_password()
        if commit:
            user.save()
            if role:
                user.groups.set([role])
        return user


class StaffUpdateForm(forms.ModelForm):
    is_active = forms.BooleanField(required=False)
    role = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        required=False,
        widget=forms.Select(attrs=_select),
        help_text='Change the staff member\'s role',
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs=_input),
            'email': forms.EmailInput(attrs=_input),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        # Pre-select current group
        if self.instance and self.instance.pk:
            current_group = self.instance.groups.first()
            if current_group:
                self.fields['role'].initial = current_group

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            role = self.cleaned_data.get('role')
            if role:
                user.groups.set([role])
        return user


class WaiterCodeForm(forms.ModelForm):
    class Meta:
        model = WaiterCode
        fields = ['code', 'is_active']
        widgets = {
            'code': forms.TextInput(attrs={**_input, 'maxlength': 6, 'pattern': '[0-9]{6}'}),
        }


# ── Menu ──────────────────────────────────────────────────────────────

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'icon']
        widgets = {
            'name': forms.TextInput(attrs=_input),
            'icon': forms.TextInput(attrs={**_input, 'placeholder': 'fa-coffee'}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.slug:
            instance.slug = slugify(instance.name)
        if commit:
            instance.save()
        return instance


class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = [
            'category', 'title', 'description', 'price', 'image',
            'item_tier', 'is_available', 'preparation_time', 'inventory_item',
        ]
        widgets = {
            'category': forms.Select(attrs=_select),
            'title': forms.TextInput(attrs=_input),
            'description': forms.Textarea(attrs=_textarea),
            'price': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
            'image': forms.ClearableFileInput(attrs=_file),
            'item_tier': forms.Select(attrs=_select),
            'is_available': forms.CheckboxInput(),
            'preparation_time': forms.NumberInput(attrs={**_input, 'min': '0'}),
            'inventory_item': forms.Select(attrs=_select),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.slug:
            instance.slug = slugify(instance.title)
        # Ensure unique slug
        base_slug = instance.slug
        counter = 1
        while MenuItem.objects.filter(slug=instance.slug).exclude(pk=instance.pk).exists():
            instance.slug = f"{base_slug}-{counter}"
            counter += 1
        if commit:
            instance.save()
        return instance


class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ['inventory_item', 'quantity_required']
        widgets = {
            'inventory_item': forms.Select(attrs=_select),
            'quantity_required': forms.NumberInput(attrs={**_input, 'step': '0.001', 'min': '0'}),
        }


# ── Inventory ─────────────────────────────────────────────────────────

class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ['name', 'unit', 'stock_quantity', 'buying_price', 'low_stock_threshold', 'preferred_supplier']
        widgets = {
            'name': forms.TextInput(attrs=_input),
            'unit': forms.Select(attrs=_select),
            'stock_quantity': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
            'buying_price': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
            'low_stock_threshold': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
            'preferred_supplier': forms.Select(attrs=_select),
        }


class StockUpdateForm(forms.Form):
    """Quick stock quantity update."""
    quantity = forms.DecimalField(
        max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
    )


# ── Tables ────────────────────────────────────────────────────────────

class TableForm(forms.ModelForm):
    class Meta:
        model = Table
        fields = ['number', 'capacity', 'status']
        widgets = {
            'number': forms.TextInput(attrs={**_input, 'maxlength': '10'}),
            'capacity': forms.NumberInput(attrs={**_input, 'min': '1'}),
            'status': forms.Select(attrs=_select),
        }


# ── Restaurant Settings ──────────────────────────────────────────────

class RestaurantSettingsForm(forms.ModelForm):
    class Meta:
        model = RestaurantSettings
        fields = ['name', 'tagline', 'phone', 'website', 'logo', 'currency', 'default_markup_percent']
        widgets = {
            'name': forms.TextInput(attrs=_input),
            'tagline': forms.TextInput(attrs=_input),
            'phone': forms.TextInput(attrs=_input),
            'website': forms.TextInput(attrs=_input),
            'logo': forms.ClearableFileInput(attrs=_file),
            'currency': forms.Select(attrs=_select),
            'default_markup_percent': forms.NumberInput(attrs={**_input, 'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from menu.currencies import CURRENCY_CHOICES
        self.fields['currency'].widget = forms.Select(attrs=_select, choices=CURRENCY_CHOICES)
