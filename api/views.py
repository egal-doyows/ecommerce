"""DRF API views for the POS system."""
import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

logger = logging.getLogger(__name__)

from menu.models import Category, MenuItem, Table, Order, Shift
from menu.views import _must_select_attendant, _is_marketing, _is_supervisor, _is_auto_shift_user, _ensure_shift
from menu.services import place_order as service_place_order, update_order_status as service_update_order_status, InvalidTransition

from .serializers import (
    CategorySerializer, MenuItemSerializer, TableSerializer,
    OrderSerializer, OrderCreateSerializer, OrderStatusUpdateSerializer,
    ShiftSerializer,
)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = None


class MenuItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MenuItemSerializer
    pagination_class = None

    def get_queryset(self):
        return MenuItem.objects.filter(is_available=True).select_related('category')


class TableViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TableSerializer
    pagination_class = None

    def get_queryset(self):
        branch = getattr(self.request, 'branch', None)
        return Table.objects.for_branch(branch)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer

    def get_queryset(self):
        branch = getattr(self.request, 'branch', None)
        if branch is None:
            raise PermissionDenied('No branch context available.')
        qs = Order.objects.for_branch(branch).exclude(status='cancelled')
        if not (self.request.user.is_superuser or _is_supervisor(self.request.user)):
            qs = qs.filter(Q(waiter=self.request.user) | Q(created_by=self.request.user))
        return qs.select_related('table', 'waiter').prefetch_related('items__menu_item')

    def create(self, request, *args, **kwargs):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        table = get_object_or_404(Table, id=serializer.validated_data['table_id'])

        if _is_auto_shift_user(request.user):
            _ensure_shift(request.user, branch=getattr(request, 'branch', None))

        active_shift = Shift.objects.filter(
            waiter=request.user, is_active=True,
            branch=getattr(request, 'branch', None),
        ).first()
        if not active_shift:
            return Response({'error': 'No active shift'}, status=status.HTTP_400_BAD_REQUEST)

        order_waiter = request.user
        order_created_by = None
        if _must_select_attendant(request.user):
            attendant_id = serializer.validated_data.get('attendant_id')
            if not attendant_id:
                return Response({'error': 'Select an attendant'}, status=status.HTTP_400_BAD_REQUEST)
            order_waiter = get_object_or_404(User, id=attendant_id, groups__name='Attendant', is_active=True)
            if _is_marketing(request.user):
                order_created_by = request.user

        cart_items = []
        for item_data in serializer.validated_data['items']:
            menu_item = get_object_or_404(MenuItem, id=item_data['id'])
            cart_items.append({
                'product': menu_item,
                'qty': item_data.get('qty', 1),
                'price': menu_item.price,
            })

        try:
            order = service_place_order(
                cart_items=cart_items,
                table=table,
                waiter=order_waiter,
                created_by=order_created_by,
                shift=active_shift,
                notes=serializer.validated_data.get('notes', ''),
                branch=getattr(request, 'branch', None),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Order creation failed: %s", str(e), exc_info=True)
            return Response({'error': 'Order could not be placed. Please try again.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='update-status')
    def update_status(self, request, pk=None):
        order = self.get_object()
        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        debtor = None
        if serializer.validated_data.get('debtor_id'):
            from debtor.models import Debtor
            debtor = get_object_or_404(Debtor, pk=serializer.validated_data['debtor_id'], is_active=True)

        try:
            service_update_order_status(
                order,
                serializer.validated_data['status'],
                payment_method=serializer.validated_data.get('payment_method', ''),
                mpesa_code=serializer.validated_data.get('mpesa_code', ''),
                debtor=debtor,
                user=request.user,
            )
        except InvalidTransition as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(order).data)


class ShiftViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ShiftSerializer

    def get_queryset(self):
        branch = getattr(self.request, 'branch', None)
        return Shift.objects.for_branch(branch).filter(
            waiter=self.request.user,
        ).select_related('waiter')
