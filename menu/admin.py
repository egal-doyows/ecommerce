from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    RestaurantSettings, Category, InventoryItem, MenuItem, Recipe, Table,
    Order, OrderItem, Shift, AccompanimentGroup, AccompanimentOption,
)


@admin.register(RestaurantSettings)
class RestaurantSettingsAdmin(ModelAdmin):
    list_display = ('name', 'tagline', 'phone', 'email')

    fieldsets = (
        ('Branding', {
            'fields': ('name', 'tagline', 'logo'),
        }),
        ('Contact', {
            'description': (
                'Public contact details shown on the website (Contact page, footer). '
                'Anything left blank is simply hidden — no broken UI.'
            ),
            'fields': (
                'phone', 'whatsapp_number', 'email', 'website', 'address',
                'tax_number', 'mpesa_till_number', 'map_embed_url', 'directions_url',
            ),
        }),
        ('Social', {
            'fields': ('facebook_url', 'instagram_url', 'twitter_url'),
        }),
        ('Operational', {
            'fields': ('currency', 'default_markup_percent'),
        }),
        ('Location', {
            'description': (
                'Decimal degrees, e.g. -1.28333 / 36.81667 for Nairobi CBD. '
                'Needed for weather-aware demand forecasting — leave blank to disable.'
            ),
            'fields': ('latitude', 'longitude'),
        }),
        ('Thermal printing (QZ Tray)', {
            'description': (
                'Certificate that lets the Windows registers print receipts '
                'silently (no "Allow" popup per print).'
            ),
            'fields': ('qz_certificate_download',),
        }),
    )

    readonly_fields = ('qz_certificate_download',)

    def has_add_permission(self, request):
        # Only allow one instance
        return not RestaurantSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    # ── QZ Tray certificate download ────────────────────────────────────────
    def get_urls(self):
        custom = [
            path(
                'qz-certificate/download/',
                self.admin_site.admin_view(self.download_qz_certificate),
                name='menu_restaurantsettings_qz_cert',
            ),
        ]
        return custom + super().get_urls()

    def download_qz_certificate(self, request):
        """Stream the public QZ Tray certificate as a file download."""
        from . import qz_signing
        cert = qz_signing.get_certificate()
        if not cert:
            self.message_user(
                request,
                'QZ Tray certificate is not provisioned on this server yet. '
                'Generate the key pair first (see deployment/qz/README.md).',
                level=messages.WARNING,
            )
            return redirect('admin:menu_restaurantsettings_changelist')
        resp = HttpResponse(cert, content_type='text/plain')
        resp['Content-Disposition'] = 'attachment; filename="digital-certificate.txt"'
        return resp

    @admin.display(description='Digital certificate')
    def qz_certificate_download(self, obj=None):
        from . import qz_signing
        url = reverse('admin:menu_restaurantsettings_qz_cert')
        if qz_signing.signing_available():
            return format_html(
                '<a class="button" href="{}" download>Download digital-certificate.txt</a>'
                '<p style="margin-top:8px;color:#15803d;font-weight:600;">'
                '✓ Signing is active on the server.</p>'
                '<p style="color:#6b7280;font-size:12px;margin-top:4px;">'
                'On each Windows register, paste this file’s contents into '
                '<code>C:\\Program Files\\QZ Tray\\demo\\ssl\\override.crt</code>, '
                'then restart QZ Tray. After that the print popup is gone.</p>',
                url,
            )
        return format_html(
            '<p style="color:#b91c1c;font-weight:600;">Not provisioned yet.</p>'
            '<p style="color:#6b7280;font-size:12px;margin-top:4px;">'
            'Generate the key pair on the server (see '
            '<code>deployment/qz/README.md</code>); this becomes a download link '
            'once the certificate and private key are in place.</p>'
        )


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ('name', 'slug', 'icon')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)   # required so MenuItemAdmin can autocomplete on category


@admin.register(InventoryItem)
class InventoryItemAdmin(ModelAdmin):
    list_display = ('name', 'unit', 'stock_quantity', 'buying_price', 'low_stock_threshold', 'stock_status')
    list_filter = ('unit',)
    search_fields = ('name',)
    list_editable = ('stock_quantity', 'buying_price')

    def stock_status(self, obj):
        if obj.stock_quantity <= 0:
            return 'OUT OF STOCK'
        if obj.is_low_stock:
            return 'LOW'
        return 'OK'
    stock_status.short_description = 'Status'


class RecipeInline(TabularInline):
    model = Recipe
    extra = 1
    fields = ('inventory_item', 'quantity_required')
    autocomplete_fields = ('inventory_item',)


@admin.register(MenuItem)
class MenuItemAdmin(ModelAdmin):
    list_display = ('title', 'category', 'price', 'item_tier', 'is_available', 'is_featured', 'preparation_time', 'stock_type', 'stock_info')
    list_filter = ('category', 'is_available', 'is_featured', 'item_tier')
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ('price', 'is_available', 'is_featured', 'item_tier')
    autocomplete_fields = ('category', 'inventory_item')
    filter_horizontal = ('accompaniment_groups',)
    inlines = [RecipeInline]
    fieldsets = (
        (None, {
            'fields': ('category', 'title', 'slug', 'description', 'price', 'image', 'item_tier', 'is_available', 'preparation_time'),
        }),
        ('Accompaniments', {
            'description': (
                "Choice groups offered with this item (e.g. 'Choose a side'). "
                'Create groups and their options under Accompaniment groups / options first.'
            ),
            'fields': ('accompaniment_groups',),
        }),
        ('Public site', {
            'description': (
                'Tick <strong>Is featured</strong> to surface this item in the '
                '"Signature brews" section on the public landing page. '
                'Up to 3 featured items are shown.'
            ),
            'fields': ('is_featured',),
        }),
        ('Inventory', {
            'description': (
                'For <strong>direct-sale items</strong> (soda, water): link an inventory item below. '
                'For <strong>prepared items</strong> (juice, burger): leave this blank and add ingredients in the Recipe section.'
            ),
            'fields': ('inventory_item',),
        }),
    )

    def stock_type(self, obj):
        if obj.is_direct_sale:
            return 'Direct sale'
        if obj.recipe_items.exists():
            return 'Prepared'
        return 'No stock tracking'
    stock_type.short_description = 'Type'

    def stock_info(self, obj):
        if obj.is_direct_sale:
            inv = obj.inventory_item
            return f"{inv.stock_quantity} {inv.get_unit_display()}"
        count = obj.recipe_items.count()
        if count:
            return f"{count} ingredient{'s' if count != 1 else ''}"
        return '—'
    stock_info.short_description = 'Stock'


@admin.register(Table)
class TableAdmin(ModelAdmin):
    list_display = ('number', 'capacity', 'status')
    list_editable = ('status',)
    list_filter = ('status',)


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('get_subtotal',)

    def get_subtotal(self, obj):
        return obj.get_subtotal()
    get_subtotal.short_description = 'Subtotal'


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = ('id', 'table', 'waiter', 'status', 'payment_method', 'mpesa_code', 'get_total', 'created_at')
    list_filter = ('status', 'payment_method', 'waiter', 'created_at')
    inlines = [OrderItemInline]
    readonly_fields = ('created_at', 'updated_at')

    def get_total(self, obj):
        symbol = RestaurantSettings.load().currency_symbol
        return f"{symbol} {obj.get_total():,.2f}"
    get_total.short_description = 'Total'

    def delete_queryset(self, request, queryset):
        tables = set(order.table for order in queryset if order.table)
        queryset.delete()
        for table in tables:
            if not table.orders.filter(status='active').exists():
                table.status = 'available'
                table.save()

    def delete_model(self, request, obj):
        obj.delete()


class AccompanimentOptionInline(TabularInline):
    model = AccompanimentOption
    extra = 1
    fields = ('label', 'price_delta', 'is_available', 'inventory_item', 'inventory_quantity')
    autocomplete_fields = ('inventory_item',)


@admin.register(AccompanimentGroup)
class AccompanimentGroupAdmin(ModelAdmin):
    list_display = ('name', 'is_required', 'option_count')
    list_filter = ('is_required',)
    search_fields = ('name',)
    inlines = [AccompanimentOptionInline]

    def option_count(self, obj):
        return obj.options.count()
    option_count.short_description = 'Options'


@admin.register(AccompanimentOption)
class AccompanimentOptionAdmin(ModelAdmin):
    list_display = ('label', 'group', 'price_delta', 'is_available', 'stock_type')
    list_filter = ('group', 'is_available')
    search_fields = ('label', 'group__name')
    list_editable = ('price_delta', 'is_available')
    autocomplete_fields = ('group', 'inventory_item')
    inlines = [RecipeInline]
    fieldsets = (
        (None, {
            'fields': ('group', 'label', 'price_delta', 'is_available'),
        }),
        ('Inventory', {
            'description': (
                'For a <strong>direct-stock side</strong> (e.g. a bottled extra): link an inventory item '
                'and set how many of its units are consumed per pick (e.g. 0.02 for 20 g of cheese). '
                'For a <strong>prepared side</strong> (fries, rice): leave the item blank and add ingredients in the Recipe section.'
            ),
            'fields': ('inventory_item', 'inventory_quantity'),
        }),
    )

    def stock_type(self, obj):
        if obj.is_direct_sale:
            return 'Direct sale'
        if obj.recipe_items.exists():
            return 'Prepared'
        return 'No stock tracking'
    stock_type.short_description = 'Type'


@admin.register(Shift)
class ShiftAdmin(ModelAdmin):
    list_display = ('id', 'waiter', 'started_at', 'ended_at', 'is_active', 'get_duration', 'get_order_count')
    list_filter = ('is_active', 'waiter', 'started_at')
    readonly_fields = ('started_at',)

    def get_duration(self, obj):
        return obj.get_duration()
    get_duration.short_description = 'Duration'

    def get_order_count(self, obj):
        return obj.get_order_count()
    get_order_count.short_description = 'Orders'
