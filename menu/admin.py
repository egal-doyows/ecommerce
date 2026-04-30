from django.contrib import admin
from .models import RestaurantSettings, Station, Category, InventoryItem, MenuItem, Recipe, Table, Order, OrderItem, Shift, StationRequest


@admin.register(RestaurantSettings)
class RestaurantSettingsAdmin(admin.ModelAdmin):
    list_display = ('name', 'tagline', 'phone')

    def has_add_permission(self, request):
        # Only allow one instance
        return not RestaurantSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'icon', 'station')
    list_filter = ('station',)
    list_editable = ('station',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
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


class RecipeInline(admin.TabularInline):
    model = Recipe
    extra = 1
    autocomplete_fields = ('inventory_item',)


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'price', 'item_tier', 'is_available', 'preparation_time', 'stock_type', 'stock_info')
    list_filter = ('category', 'is_available', 'item_tier')
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ('price', 'is_available', 'item_tier')
    autocomplete_fields = ('inventory_item',)
    inlines = [RecipeInline]
    fieldsets = (
        (None, {
            'fields': ('category', 'title', 'slug', 'description', 'price', 'image', 'item_tier', 'is_available', 'preparation_time'),
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
class TableAdmin(admin.ModelAdmin):
    list_display = ('number', 'capacity', 'status')
    list_editable = ('status',)
    list_filter = ('status',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('get_subtotal',)

    def get_subtotal(self, obj):
        return obj.get_subtotal()
    get_subtotal.short_description = 'Subtotal'


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
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


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('id', 'waiter', 'started_at', 'ended_at', 'is_active', 'get_duration', 'get_order_count')
    list_filter = ('is_active', 'waiter', 'started_at')
    readonly_fields = ('started_at',)

    def get_duration(self, obj):
        return obj.get_duration()
    get_duration.short_description = 'Duration'

    def get_order_count(self, obj):
        return obj.get_order_count()
    get_order_count.short_description = 'Orders'


@admin.register(StationRequest)
class StationRequestAdmin(admin.ModelAdmin):
    list_display = ('order_item', 'request_type', 'status', 'requested_by', 'created_at')
    list_filter = ('request_type', 'status')
    readonly_fields = ('created_at',)
