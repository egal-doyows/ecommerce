from django.contrib import admin
from .models import RestaurantSettings, Category, MenuItem, Table, Order, OrderItem, Shift


@admin.register(RestaurantSettings)
class RestaurantSettingsAdmin(admin.ModelAdmin):
    list_display = ('name', 'tagline', 'phone')

    def has_add_permission(self, request):
        # Only allow one instance
        return not RestaurantSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'icon')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'price', 'is_available', 'preparation_time')
    list_filter = ('category', 'is_available')
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}
    list_editable = ('price', 'is_available')


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
        return f"Ksh {obj.get_total():,.2f}"
    get_total.short_description = 'Total'


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
