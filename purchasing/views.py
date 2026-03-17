import json
from datetime import datetime
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction

from .models import PurchaseOrder, PurchaseOrderItem
from .forms import PurchaseOrderForm, PurchaseOrderItemForm
from supplier.models import Supplier, SupplierTransaction


def _is_manager_or_supervisor(user):
    """Manager, Supervisor, or Superuser."""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name__in=['Manager', 'Supervisor']).exists()
    )


def _is_manager(user):
    """Manager or Superuser only (not Supervisor)."""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name='Manager').exists()
    )


def staff_required(view_func):
    """Allows Manager, Supervisor, and Superuser."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager_or_supervisor(request.user):
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def manager_required(view_func):
    """Manager and Superuser only — Supervisors cannot access."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager(request.user):
            messages.error(request, 'Only managers can perform this action.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def _can_edit_po(user, po):
    """Managers/Superuser can edit any draft/pending PO; supervisors only their own drafts."""
    if _is_manager(user):
        return po.status in ('draft', 'pending')
    # Supervisor — only their own draft POs
    return po.status == 'draft' and po.created_by == user


def superuser_only(view_func):
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, 'Only the administrator can perform this action.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


# ── Purchase Order List ──────────────────────────────────────────────

@staff_required
def po_list(request):
    orders = PurchaseOrder.objects.select_related('supplier', 'created_by').order_by('-order_date', '-pk')

    status_filter = request.GET.get('status')
    if status_filter in ('draft', 'pending', 'approved', 'received', 'cancelled'):
        orders = orders.filter(status=status_filter)
    else:
        status_filter = None

    # Date filtering
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    if date_from:
        try:
            orders = orders.filter(order_date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            date_from = ''
    if date_to:
        try:
            orders = orders.filter(order_date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            date_to = ''

    paginator = Paginator(orders, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'purchasing/po_list.html', {
        'orders': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
    })


# ── Create Purchase Order ────────────────────────────────────────────

@staff_required
def po_create(request):
    if request.method == 'POST':
        form = PurchaseOrderForm(request.POST)
        if form.is_valid():
            po = form.save(commit=False)
            po.created_by = request.user
            po.save()
            messages.success(request, f'{po.po_number} created.')
            return redirect('po-detail', pk=po.pk)
    else:
        form = PurchaseOrderForm()
    return render(request, 'purchasing/po_form.html', {
        'form': form, 'title': 'New Purchase Order',
    })


# ── Purchase Order Detail ────────────────────────────────────────────

@staff_required
def po_detail(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related('supplier', 'created_by', 'approved_by'), pk=pk)
    items = po.items.select_related('inventory_item').all()

    from menu.models import RestaurantSettings, InventoryItem
    symbol = RestaurantSettings.load().currency_symbol

    # For the inline add-item panel on editable POs
    inventory_items = []
    suppliers = []
    can_edit = _can_edit_po(request.user, po)
    if can_edit:
        existing_inv_ids = set(items.values_list('inventory_item_id', flat=True))

        for inv in InventoryItem.objects.all().order_by('name'):
            if inv.pk in existing_inv_ids:
                continue
            inventory_items.append({
                'pk': inv.pk,
                'name': inv.name,
                'unit': inv.get_unit_display(),
                'stock': float(inv.stock_quantity),
                'threshold': float(inv.low_stock_threshold),
                'low': inv.is_low_stock,
                'price': float(inv.buying_price),
            })

        from supplier.models import Supplier
        suppliers = Supplier.objects.filter(is_active=True).order_by('name')

    return render(request, 'purchasing/po_detail.html', {
        'po': po,
        'items': items,
        'currency_symbol': symbol,
        'inventory_json': json.dumps(inventory_items),
        'suppliers': suppliers,
        'can_approve': _is_manager(request.user),
        'can_edit': can_edit,
    })


# ── Edit Purchase Order ──────────────────────────────────────────────

@staff_required
def po_edit(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not _can_edit_po(request.user, po):
        messages.error(request, 'You do not have permission to edit this order.')
        return redirect('po-detail', pk=po.pk)

    if request.method == 'POST':
        form = PurchaseOrderForm(request.POST, instance=po)
        if form.is_valid():
            form.save()
            messages.success(request, f'{po.po_number} updated.')
            return redirect('po-detail', pk=po.pk)
    else:
        form = PurchaseOrderForm(instance=po)
    return render(request, 'purchasing/po_form.html', {
        'form': form, 'title': f'Edit {po.po_number}', 'po': po,
    })


# ── Add Item to PO ──────────────────────────────────────────────────

@staff_required
def po_add_item(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not _can_edit_po(request.user, po):
        messages.error(request, 'You do not have permission to edit this order.')
        return redirect('po-detail', pk=po.pk)

    if request.method == 'POST':
        from menu.models import InventoryItem
        inv_id = request.POST.get('inventory_item')
        quantity = request.POST.get('quantity', '1')
        unit_price = request.POST.get('unit_price', '0')

        try:
            inv = InventoryItem.objects.get(pk=inv_id)
            quantity = Decimal(quantity).quantize(Decimal('0.01'))
            unit_price = Decimal(unit_price).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid item or values.')
            return redirect('po-detail', pk=po.pk)

        if quantity <= 0:
            messages.error(request, 'Quantity must be greater than zero.')
            return redirect('po-detail', pk=po.pk)

        # Prevent duplicate items on the same PO
        if po.items.filter(inventory_item=inv).exists():
            messages.error(request, f'{inv.name} is already on this order.')
            return redirect('po-detail', pk=po.pk)

        PurchaseOrderItem.objects.create(
            purchase_order=po,
            inventory_item=inv,
            quantity=quantity,
            unit_price=unit_price,
        )
        messages.success(request, f'{inv.name} added.')

    return redirect('po-detail', pk=po.pk)


# ── Update Item on PO ──────────────────────────────────────────────

@staff_required
def po_update_item(request, pk, item_pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not _can_edit_po(request.user, po):
        messages.error(request, 'You do not have permission to edit this order.')
        return redirect('po-detail', pk=po.pk)

    item = get_object_or_404(PurchaseOrderItem, pk=item_pk, purchase_order=po)

    if request.method == 'POST':
        try:
            quantity = Decimal(request.POST.get('quantity', '1')).quantize(Decimal('0.01'))
            unit_price = Decimal(request.POST.get('unit_price', '0')).quantize(Decimal('0.01'))
        except Exception:
            messages.error(request, 'Invalid values.')
            return redirect('po-detail', pk=po.pk)

        if quantity <= 0:
            messages.error(request, 'Quantity must be greater than zero.')
            return redirect('po-detail', pk=po.pk)

        item.quantity = quantity
        item.unit_price = unit_price
        item.save()
        messages.success(request, f'{item.inventory_item.name} updated.')

    return redirect('po-detail', pk=po.pk)


# ── Change Supplier on PO ─────────────────────────────────────────

@staff_required
def po_change_supplier(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not _can_edit_po(request.user, po):
        messages.error(request, 'You do not have permission to edit this order.')
        return redirect('po-detail', pk=po.pk)

    if request.method == 'POST':
        from supplier.models import Supplier
        supplier_id = request.POST.get('supplier')
        try:
            supplier = Supplier.objects.get(pk=supplier_id, is_active=True)
        except Supplier.DoesNotExist:
            messages.error(request, 'Invalid supplier.')
            return redirect('po-detail', pk=po.pk)

        po.supplier = supplier
        po.save()
        messages.success(request, f'Supplier changed to {supplier.name}.')

    return redirect('po-detail', pk=po.pk)


# ── Remove Item from PO ─────────────────────────────────────────────

@staff_required
def po_remove_item(request, pk, item_pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not _can_edit_po(request.user, po):
        messages.error(request, 'You do not have permission to edit this order.')
        return redirect('po-detail', pk=po.pk)

    item = get_object_or_404(PurchaseOrderItem, pk=item_pk, purchase_order=po)
    if request.method == 'POST':
        item.delete()
        messages.success(request, 'Item removed.')
    return redirect('po-detail', pk=po.pk)


# ── Submit Purchase Order for Approval ─────────────────────────────

@staff_required
def po_submit(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not _can_edit_po(request.user, po):
        messages.error(request, 'You do not have permission to submit this order.')
        return redirect('po-detail', pk=po.pk)
    if po.status != 'draft':
        messages.error(request, 'Only draft orders can be submitted.')
        return redirect('po-detail', pk=po.pk)
    if po.items.count() == 0:
        messages.error(request, 'Cannot submit an empty purchase order.')
        return redirect('po-detail', pk=po.pk)

    if request.method == 'POST':
        po.status = 'pending'
        po.save()
        messages.success(request, f'{po.po_number} submitted for approval.')
    return redirect('po-detail', pk=po.pk)


# ── Approve Purchase Order ───────────────────────────────────────────

@manager_required
def po_approve(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.status not in ('draft', 'pending'):
        messages.error(request, 'Only draft or pending orders can be approved.')
        return redirect('po-detail', pk=po.pk)

    if po.items.count() == 0:
        messages.error(request, 'Cannot approve an empty purchase order.')
        return redirect('po-detail', pk=po.pk)

    if request.method == 'POST':
        po.status = 'approved'
        po.approved_by = request.user
        po.save()
        messages.success(request, f'{po.po_number} approved.')
    return redirect('po-detail', pk=po.pk)


# ── Receive Goods ────────────────────────────────────────────────────

@manager_required
def po_receive(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.status != 'approved':
        messages.error(request, 'Only approved orders can be received.')
        return redirect('po-detail', pk=po.pk)

    items = po.items.select_related('inventory_item').all()

    if request.method == 'POST':
        from django.utils import timezone as tz

        with transaction.atomic():
            total_received_value = Decimal('0')

            for item in items:
                received_qty = request.POST.get(f'received_{item.pk}', '0')
                try:
                    received_qty = Decimal(received_qty).quantize(Decimal('0.01'))
                except Exception:
                    received_qty = Decimal('0')

                if received_qty < 0:
                    received_qty = Decimal('0')
                if received_qty > item.quantity:
                    received_qty = item.quantity

                item.received_quantity = received_qty
                item.save()

                # Update inventory stock
                if received_qty > 0:
                    inv = item.inventory_item
                    inv.stock_quantity += received_qty
                    # Update buying price to latest
                    if item.unit_price > 0:
                        inv.buying_price = item.unit_price
                    inv.save()
                    total_received_value += received_qty * item.unit_price

            po.status = 'received'
            po.received_date = tz.now()
            po.save()

            # Create supplier invoice (debit transaction)
            if total_received_value > 0:
                SupplierTransaction.objects.create(
                    supplier=po.supplier,
                    transaction_type='debit',
                    amount=total_received_value,
                    description=f'Goods received — {po.po_number}',
                    reference=po.po_number,
                    created_by=request.user,
                )

        from menu.models import RestaurantSettings
        symbol = RestaurantSettings.load().currency_symbol
        messages.success(
            request,
            f'{po.po_number} received. Invoice of {symbol} {total_received_value:,.2f} '
            f'recorded for {po.supplier.name}.',
        )
        return redirect('po-detail', pk=po.pk)

    from menu.models import RestaurantSettings
    symbol = RestaurantSettings.load().currency_symbol

    return render(request, 'purchasing/po_receive.html', {
        'po': po,
        'items': items,
        'currency_symbol': symbol,
    })


# ── Cancel Purchase Order ────────────────────────────────────────────

@manager_required
def po_cancel(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if po.status in ('received', 'cancelled'):
        messages.error(request, 'This order cannot be cancelled.')
        return redirect('po-detail', pk=po.pk)

    if request.method == 'POST':
        po.status = 'cancelled'
        po.save()
        messages.success(request, f'{po.po_number} cancelled.')
    return redirect('po-detail', pk=po.pk)


# ── Download Purchase Order PDF ─────────────────────────────────────

@staff_required
def po_pdf(request, pk):
    import io
    import os
    import datetime
    from django.conf import settings as django_settings
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
        Image, HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    ACCENT = colors.HexColor('#1e3a5f')
    ACCENT_LIGHT = colors.HexColor('#2563eb')
    GREY = colors.HexColor('#6b7280')
    LIGHT_BG = colors.HexColor('#f1f5f9')
    BORDER = colors.HexColor('#e2e8f0')
    WHITE = colors.white

    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier', 'created_by', 'approved_by'),
        pk=pk,
    )
    items = po.items.select_related('inventory_item').all()

    from menu.models import RestaurantSettings
    rs = RestaurantSettings.load()
    symbol = rs.currency_symbol

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )
    page_w = A4[0] - 36 * mm  # usable width

    styles = getSampleStyleSheet()
    s_company = ParagraphStyle('Company', fontName='Helvetica-Bold', fontSize=16, textColor=ACCENT)
    s_tagline = ParagraphStyle('Tagline', fontName='Helvetica', fontSize=8, textColor=GREY, spaceBefore=1)
    s_contact = ParagraphStyle('Contact', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11)
    s_po_title = ParagraphStyle('POTitle', fontName='Helvetica-Bold', fontSize=18, textColor=ACCENT, alignment=TA_RIGHT, leading=22)
    s_po_sub = ParagraphStyle('POSub', fontName='Helvetica', fontSize=9, textColor=GREY, alignment=TA_RIGHT, leading=13)
    s_label = ParagraphStyle('Label', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11)
    s_value = ParagraphStyle('Value', fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#1f2937'), leading=12)
    s_section = ParagraphStyle('Section', fontName='Helvetica-Bold', fontSize=10, textColor=ACCENT, spaceBefore=4, spaceAfter=2)
    s_notes = ParagraphStyle('Notes', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11)
    s_footer = ParagraphStyle('Footer', fontName='Helvetica', fontSize=7, textColor=GREY, alignment=TA_CENTER)

    elements = []

    # ── HEADER ───────────────────────────────────────────────────────
    logo_path = None
    if rs.logo:
        candidate = os.path.join(django_settings.MEDIA_ROOT, rs.logo.name)
        if os.path.isfile(candidate):
            logo_path = candidate

    # Build left side: logo + company name + tagline + contact
    left_logo = Image(logo_path, width=32 * mm, height=32 * mm, kind='proportional') if logo_path else ''
    company_text = Paragraph(rs.name, s_company)
    tagline_text = Paragraph(rs.tagline, s_tagline) if rs.tagline else Paragraph('', s_tagline)

    contact_parts = []
    if rs.phone:
        contact_parts.append(rs.phone)
    if rs.website:
        contact_parts.append(rs.website)
    contact_text = Paragraph('<br/>'.join(contact_parts), s_contact) if contact_parts else Paragraph('', s_contact)

    # Nested table for logo | company info
    if logo_path:
        left_table = Table(
            [
                [left_logo, company_text],
                ['', tagline_text],
                ['', contact_text],
            ],
            colWidths=[36 * mm, page_w * 0.55 - 36 * mm],
        )
        left_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('SPAN', (0, 0), (0, -1)),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
    else:
        left_table = Table(
            [[company_text], [tagline_text], [contact_text]],
            colWidths=[page_w * 0.55],
        )
        left_table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))

    # Right side: PO title block
    status_label = po.get_status_display().upper()
    right_table = Table(
        [
            [Paragraph('PURCHASE ORDER', s_po_title)],
            [Paragraph(po.po_number, ParagraphStyle(
                'PONum', fontName='Helvetica-Bold', fontSize=12, textColor=ACCENT_LIGHT,
                alignment=TA_RIGHT, leading=16,
            ))],
            [Spacer(1, 2 * mm)],
            [Paragraph(f'Date: {po.order_date.strftime("%d %b %Y")}', s_po_sub)],
            [Paragraph(f'Status: {status_label}', s_po_sub)],
        ],
        colWidths=[page_w * 0.45],
        rowHeights=[26, 18, 6, 14, 14],
    )
    right_table.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    header_table = Table(
        [[left_table, right_table]],
        colWidths=[page_w * 0.55, page_w * 0.45],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_table)

    # Accent line
    elements.append(Spacer(1, 4 * mm))
    elements.append(HRFlowable(width='100%', thickness=2, color=ACCENT_LIGHT, spaceAfter=5 * mm))

    # ── SUPPLIER & ORDER INFO ────────────────────────────────────────
    # Build supplier rows
    sup_rows = [
        [Paragraph('SUPPLIER', s_label)],
        [Paragraph(po.supplier.name, s_value)],
    ]
    if hasattr(po.supplier, 'phone') and po.supplier.phone:
        sup_rows.append([Paragraph(po.supplier.phone, s_notes)])
    if hasattr(po.supplier, 'email') and po.supplier.email:
        sup_rows.append([Paragraph(po.supplier.email, s_notes)])

    sup_table = Table(sup_rows, colWidths=[page_w * 0.5 - 24])
    sup_table.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (0, 0), 4),
    ]))

    # Build order detail rows
    detail_rows = [
        [Paragraph('ORDER DETAILS', s_label)],
    ]
    info_pairs = [
        ('Created by', po.created_by.username if po.created_by else '—'),
    ]
    if po.approved_by:
        info_pairs.append(('Approved by', po.approved_by.username))
    if po.expected_date:
        info_pairs.append(('Expected', po.expected_date.strftime('%d %b %Y')))
    if po.received_date:
        info_pairs.append(('Received', po.received_date.strftime('%d %b %Y, %H:%M')))
    for label, val in info_pairs:
        detail_rows.append([Paragraph(
            f'<font color="#6b7280">{label}:</font>  {val}', s_value,
        )])

    detail_table = Table(detail_rows, colWidths=[page_w * 0.5 - 24])
    detail_table.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (0, 0), 4),
    ]))

    info_box = Table(
        [[sup_table, detail_table]],
        colWidths=[page_w * 0.5, page_w * 0.5],
    )
    info_box.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BG),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('LINEAFTER', (0, 0), (0, -1), 0.5, BORDER),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    elements.append(info_box)

    # Notes
    if po.notes:
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph(f'<b>Notes:</b> {po.notes}', s_notes))

    elements.append(Spacer(1, 5 * mm))

    # ── ITEMS TABLE ──────────────────────────────────────────────────
    elements.append(Paragraph('ORDER ITEMS', s_section))
    elements.append(Spacer(1, 2 * mm))

    header = ['#', 'Item', 'Unit', 'Qty', f'Unit Price ({symbol})', f'Total ({symbol})']
    if po.status == 'received':
        header.append('Received')

    table_data = [header]
    for idx, item in enumerate(items, 1):
        row = [
            str(idx),
            Paragraph(item.inventory_item.name, ParagraphStyle('ItemName', fontName='Helvetica', fontSize=9, leading=11)),
            item.inventory_item.get_unit_display(),
            f'{item.quantity:,.2f}',
            f'{item.unit_price:,.2f}',
            f'{item.line_total:,.2f}',
        ]
        if po.status == 'received':
            row.append(f'{item.received_quantity:,.2f}')
        table_data.append(row)

    # Subtotal / Total rows
    empty_cols = 4
    table_data.append(['', '', '', '', Paragraph('<b>Subtotal</b>', ParagraphStyle('', fontSize=9, alignment=TA_RIGHT)),
                        f'{symbol} {po.total:,.2f}'] + ([''] if po.status == 'received' else []))
    table_data.append(['', '', '', '', Paragraph('<b>TOTAL</b>', ParagraphStyle('', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
                        Paragraph(f'<b>{symbol} {po.total:,.2f}</b>', ParagraphStyle('', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT))]
                       + ([''] if po.status == 'received' else []))

    col_widths = [22, None, 42, 42, 72, 78]
    if po.status == 'received':
        col_widths.append(52)
    # Calculate auto-width for Item column
    fixed = sum(w for w in col_widths if w is not None)
    col_widths[1] = page_w - fixed

    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), ACCENT),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
        ('TOPPADDING', (0, 0), (-1, 0), 7),
        ('LEFTPADDING', (0, 0), (-1, 0), 6),
        ('RIGHTPADDING', (0, 0), (-1, 0), 6),
        # Body rows
        ('FONTNAME', (0, 1), (-1, -3), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -3), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -3), 5),
        ('TOPPADDING', (0, 1), (-1, -3), 5),
        ('LEFTPADDING', (0, 1), (-1, -1), 6),
        ('RIGHTPADDING', (0, 1), (-1, -1), 6),
        # Alternating rows
        ('ROWBACKGROUNDS', (0, 1), (-1, -3), [WHITE, LIGHT_BG]),
        # Grid lines on body
        ('LINEBELOW', (0, 0), (-1, -3), 0.3, BORDER),
        # Alignment
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),   # #
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),    # Qty onwards
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # Subtotal row
        ('FONTSIZE', (0, -2), (-1, -2), 9),
        ('TOPPADDING', (0, -2), (-1, -2), 6),
        ('LINEABOVE', (4, -2), (-1, -2), 0.5, BORDER),
        # Total row
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 10),
        ('TOPPADDING', (0, -1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
        ('LINEABOVE', (4, -1), (-1, -1), 1.2, ACCENT),
        ('LINEBELOW', (4, -1), (-1, -1), 0.4, ACCENT),
    ]))
    elements.append(items_table)

    # ── SIGNATURE LINE ───────────────────────────────────────────────
    elements.append(Spacer(1, 20 * mm))
    sig_table = Table(
        [
            [Paragraph('Prepared by', s_label), '', Paragraph('Authorized by', s_label)],
            ['_' * 35, '', '_' * 35],
            [Paragraph(po.created_by.username if po.created_by else '', s_notes), '',
             Paragraph(po.approved_by.username if po.approved_by else '', s_notes)],
        ],
        colWidths=[page_w * 0.4, page_w * 0.2, page_w * 0.4],
    )
    sig_table.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('FONTSIZE', (0, 1), (-1, 1), 8),
        ('TEXTCOLOR', (0, 1), (-1, 1), BORDER),
    ]))
    elements.append(sig_table)

    # ── FOOTER ───────────────────────────────────────────────────────
    elements.append(Spacer(1, 8 * mm))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=3 * mm))
    elements.append(Paragraph(
        f'{rs.name}  |  {po.po_number}  |  Generated {datetime.date.today().strftime("%d %b %Y")}',
        s_footer,
    ))

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{po.po_number}.pdf"'
    return response


# ── Quick PO from Low Stock ──────────────────────────────────────────

@staff_required
def po_from_low_stock(request):
    """Create a PO pre-filled with low-stock items grouped by preferred supplier."""
    from menu.models import InventoryItem

    low_stock = [i for i in InventoryItem.objects.all() if i.is_low_stock and i.preferred_supplier]

    if not low_stock:
        messages.info(request, 'No low-stock items with preferred suppliers found.')
        return redirect('po-list')

    # Group by supplier
    suppliers = {}
    for item in low_stock:
        sid = item.preferred_supplier_id
        if sid not in suppliers:
            suppliers[sid] = {
                'supplier': item.preferred_supplier,
                'items': [],
            }
        suppliers[sid]['items'].append(item)

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier_id')
        if not supplier_id:
            messages.error(request, 'Select a supplier.')
            return redirect('po-from-low-stock')

        supplier_data = suppliers.get(int(supplier_id))
        if not supplier_data:
            messages.error(request, 'Invalid supplier.')
            return redirect('po-from-low-stock')

        with transaction.atomic():
            po = PurchaseOrder.objects.create(
                supplier=supplier_data['supplier'],
                created_by=request.user,
                notes='Auto-generated from low stock items',
            )
            for item in supplier_data['items']:
                reorder_qty = item.low_stock_threshold * 2 - item.stock_quantity
                if reorder_qty < 1:
                    reorder_qty = item.low_stock_threshold
                PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    inventory_item=item,
                    quantity=reorder_qty.quantize(Decimal('0.01')),
                    unit_price=item.buying_price,
                )

        messages.success(request, f'{po.po_number} created with {po.item_count} items for {po.supplier.name}.')
        return redirect('po-detail', pk=po.pk)

    return render(request, 'purchasing/po_low_stock.html', {
        'suppliers': suppliers,
    })
