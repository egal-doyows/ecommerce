"""DRF serializers for the POS API."""

from rest_framework import serializers

from menu.models import Category, MenuItem, Table, Order, OrderItem, Shift


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'icon']


class MenuItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'title', 'slug', 'description', 'price',
            'category', 'category_name', 'image', 'item_tier',
            'preparation_time', 'is_available',
        ]


class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_title = serializers.CharField(source='menu_item.title', read_only=True)
    subtotal = serializers.DecimalField(
        source='get_subtotal', max_digits=10, decimal_places=2, read_only=True,
    )

    class Meta:
        model = OrderItem
        fields = [
            'id', 'menu_item', 'menu_item_title',
            'quantity', 'unit_price', 'subtotal', 'notes',
        ]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    waiter_name = serializers.CharField(source='waiter.username', read_only=True)
    table_number = serializers.IntegerField(source='table.number', read_only=True, default=None)
    total = serializers.DecimalField(
        source='get_total', max_digits=10, decimal_places=2, read_only=True,
    )
    item_count = serializers.IntegerField(source='get_item_count', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'table', 'table_number', 'waiter', 'waiter_name',
            'status', 'payment_method', 'total', 'item_count',
            'tax_rate', 'tax_amount', 'tax_type',
            'created_at', 'updated_at', 'notes', 'items',
        ]
        read_only_fields = ['status', 'created_at', 'updated_at']


class OrderCreateSerializer(serializers.Serializer):
    """Serializer for creating orders via API."""
    table_id = serializers.IntegerField()
    items = serializers.ListField(
        child=serializers.DictField(), min_length=1,
    )
    notes = serializers.CharField(required=False, default='', allow_blank=True)
    attendant_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_items(self, value):
        for item in value:
            if 'id' not in item:
                raise serializers.ValidationError('Each item must have an "id" field.')
            if 'qty' not in item:
                item['qty'] = 1
            if not isinstance(item['qty'], int) or item['qty'] < 1:
                raise serializers.ValidationError('Item qty must be a positive integer.')
        return value


class OrderStatusUpdateSerializer(serializers.Serializer):
    """Serializer for updating order status."""
    status = serializers.ChoiceField(choices=Order.STATUS_CHOICES)
    payment_method = serializers.ChoiceField(
        choices=Order.PAYMENT_CHOICES, required=False, allow_blank=True,
    )
    mpesa_code = serializers.CharField(required=False, default='', allow_blank=True)
    debtor_id = serializers.IntegerField(required=False, allow_null=True)


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = ['id', 'number', 'capacity', 'status']


class ShiftSerializer(serializers.ModelSerializer):
    waiter_name = serializers.CharField(source='waiter.username', read_only=True)
    duration = serializers.CharField(source='get_duration', read_only=True)
    order_count = serializers.IntegerField(source='get_order_count', read_only=True)
    total_sales = serializers.DecimalField(
        source='get_total_sales', max_digits=10, decimal_places=2, read_only=True,
    )

    class Meta:
        model = Shift
        fields = [
            'id', 'waiter', 'waiter_name', 'started_at', 'ended_at',
            'is_active', 'starting_cash', 'duration',
            'order_count', 'total_sales', 'notes',
        ]
