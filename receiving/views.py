from datetime import datetime
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models, transaction
from django.utils import timezone as tz

from purchasing.models import PurchaseOrder, PurchaseOrderItem
from supplier.models import SupplierTransaction
from .models import GoodsReceipt, GoodsReceiptItem


def _is_manager(user):
    """Manager or Superuser only (not Supervisor)."""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name='Manager').exists()
    )


def manager_required(view_func):
    """Only managers and superusers can access receiving."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not _is_manager(request.user):
            messages.error(request, 'Only managers can access goods receiving.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


# ── Receipt List ─────────────────────────────────────────────────────

@manager_required
def receipt_list(request):
    receipts = GoodsReceipt.objects.select_related(
        'purchase_order__supplier', 'received_by',
    ).order_by('-received_date', '-pk')

    # Date filtering
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    if date_from:
        try:
            receipts = receipts.filter(received_date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            date_from = ''
    if date_to:
        try:
            receipts = receipts.filter(received_date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            date_to = ''

    # Supplier filtering
    supplier_filter = request.GET.get('supplier', '')
    if supplier_filter:
        receipts = receipts.filter(purchase_order__supplier_id=supplier_filter)

    # Search
    search = request.GET.get('q', '').strip()
    if search:
        receipts = receipts.filter(
            models.Q(purchase_order__supplier__name__icontains=search) |
            models.Q(notes__icontains=search)
        )

    paginator = Paginator(receipts, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Approved POs available for receiving
    approved_pos = PurchaseOrder.objects.filter(
        status='approved',
    ).select_related('supplier').order_by('-order_date')

    # Suppliers for filter dropdown
    from supplier.models import Supplier
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')

    return render(request, 'receiving/receipt_list.html', {
        'receipts': page_obj,
        'page_obj': page_obj,
        'date_from': date_from,
        'date_to': date_to,
        'supplier_filter': supplier_filter,
        'search': search,
        'approved_pos': approved_pos,
        'suppliers': suppliers,
    })


# ── Create Receipt (Receive Goods) ──────────────────────────────────

@manager_required
def receipt_create(request, po_pk):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier'),
        pk=po_pk,
    )

    if po.status not in ('approved', 'received'):
        messages.error(request, 'Only approved or partially received orders can be received.')
        return redirect('po-detail', pk=po.pk)

    items = po.items.select_related('inventory_item').all()

    # Calculate previously received quantities per item
    items_data = []
    for item in items:
        prev_received = GoodsReceiptItem.objects.filter(
            po_item=item,
        ).aggregate(total=models.Sum('received_quantity'))['total'] or Decimal('0')
        remaining = max(item.quantity - prev_received, Decimal('0'))
        items_data.append({
            'item': item,
            'prev_received': prev_received,
            'remaining': remaining,
        })

    all_fully_received = all(d['remaining'] == 0 for d in items_data)
    if all_fully_received:
        messages.info(request, f'All items on {po.po_number} have already been fully received.')
        return redirect('receipt-list')

    from menu.models import RestaurantSettings
    symbol = RestaurantSettings.load().currency_symbol

    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()

        with transaction.atomic():
            receipt = GoodsReceipt.objects.create(
                purchase_order=po,
                received_by=request.user,
                received_date=tz.now().date(),
                notes=notes,
            )

            total_received_value = Decimal('0')
            any_received = False

            for data in items_data:
                item = data['item']
                remaining = data['remaining']

                received_qty = request.POST.get(f'received_{item.pk}', '0')
                item_notes = request.POST.get(f'notes_{item.pk}', '').strip()

                try:
                    received_qty = Decimal(received_qty).quantize(Decimal('0.01'))
                except Exception:
                    received_qty = Decimal('0')

                if received_qty < 0:
                    received_qty = Decimal('0')
                if received_qty > remaining:
                    received_qty = remaining

                if received_qty == 0 and not item_notes:
                    continue

                GoodsReceiptItem.objects.create(
                    receipt=receipt,
                    po_item=item,
                    received_quantity=received_qty,
                    notes=item_notes,
                )

                # Update PO item received quantity
                item.received_quantity += received_qty
                item.save()

                # Update inventory stock
                if received_qty > 0:
                    any_received = True
                    inv = item.inventory_item
                    inv.stock_quantity += received_qty
                    if item.unit_price > 0:
                        inv.buying_price = item.unit_price
                    inv.save()
                    total_received_value += received_qty * item.unit_price

            if not any_received:
                receipt.delete()
                messages.error(request, 'No items were received. Please enter quantities.')
                return redirect('receipt-create', po_pk=po.pk)

            # Check if PO is fully received
            fully_received = True
            for item in po.items.all():
                total_item_received = GoodsReceiptItem.objects.filter(
                    po_item=item,
                ).aggregate(total=models.Sum('received_quantity'))['total'] or Decimal('0')
                if total_item_received < item.quantity:
                    fully_received = False
                    break

            if fully_received:
                po.status = 'received'
                po.received_date = tz.now()
                po.save()

            # Create supplier invoice (debit transaction)
            if total_received_value > 0:
                SupplierTransaction.objects.create(
                    supplier=po.supplier,
                    transaction_type='debit',
                    amount=total_received_value,
                    description=f'Goods received — {receipt.grn_number} ({po.po_number})',
                    reference=receipt.grn_number,
                    created_by=request.user,
                )

        status_msg = 'fully received' if fully_received else 'partially received'
        messages.success(
            request,
            f'{receipt.grn_number} created. {po.po_number} {status_msg}. '
            f'Invoice of {symbol} {total_received_value:,.2f} recorded for {po.supplier.name}.',
        )
        return redirect('receipt-detail', pk=receipt.pk)

    return render(request, 'receiving/receipt_create.html', {
        'po': po,
        'items_data': items_data,
        'currency_symbol': symbol,
    })


# ── Receipt Detail ──────────────────────────────────────────────────

@manager_required
def receipt_detail(request, pk):
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related(
            'purchase_order__supplier', 'received_by',
        ),
        pk=pk,
    )
    items = receipt.items.select_related('po_item__inventory_item').all()

    from menu.models import RestaurantSettings
    symbol = RestaurantSettings.load().currency_symbol

    return render(request, 'receiving/receipt_detail.html', {
        'receipt': receipt,
        'items': items,
        'currency_symbol': symbol,
    })


# ── PO Receiving Summary ────────────────────────────────────────────

@manager_required
def po_receiving_summary(request, po_pk):
    """Show all receipts for a specific PO."""
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier'),
        pk=po_pk,
    )
    receipts = po.receipts.select_related('received_by').order_by('-received_date')

    items = po.items.select_related('inventory_item').all()
    items_data = []
    for item in items:
        total_received = GoodsReceiptItem.objects.filter(
            po_item=item,
        ).aggregate(total=models.Sum('received_quantity'))['total'] or Decimal('0')
        items_data.append({
            'item': item,
            'total_received': total_received,
            'remaining': max(item.quantity - total_received, Decimal('0')),
            'fully_received': total_received >= item.quantity,
        })

    from menu.models import RestaurantSettings
    symbol = RestaurantSettings.load().currency_symbol

    return render(request, 'receiving/po_summary.html', {
        'po': po,
        'receipts': receipts,
        'items_data': items_data,
        'currency_symbol': symbol,
    })


# ── GRN PDF ─────────────────────────────────────────────────────────

@manager_required
def receipt_pdf(request, pk):
    """Generate a printable Goods Received Note PDF."""
    import io
    import os
    import datetime as dt
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
    GREEN = colors.HexColor('#059669')
    GREY = colors.HexColor('#6b7280')
    LIGHT_BG = colors.HexColor('#f1f5f9')
    BORDER = colors.HexColor('#e2e8f0')
    WHITE = colors.white
    WARNING = colors.HexColor('#d97706')
    DANGER = colors.HexColor('#dc2626')

    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related(
            'purchase_order__supplier', 'received_by',
        ),
        pk=pk,
    )
    items = receipt.items.select_related('po_item__inventory_item').all()
    po = receipt.purchase_order

    from menu.models import RestaurantSettings
    rs = RestaurantSettings.load()
    symbol = rs.currency_symbol

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )
    page_w = A4[0] - 36 * mm

    s_company = ParagraphStyle('Company', fontName='Helvetica-Bold', fontSize=16, textColor=ACCENT)
    s_tagline = ParagraphStyle('Tagline', fontName='Helvetica', fontSize=8, textColor=GREY, spaceBefore=1)
    s_contact = ParagraphStyle('Contact', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11)
    s_title = ParagraphStyle('GRNTitle', fontName='Helvetica-Bold', fontSize=18, textColor=GREEN, alignment=TA_RIGHT, leading=22)
    s_sub = ParagraphStyle('GRNSub', fontName='Helvetica', fontSize=9, textColor=GREY, alignment=TA_RIGHT, leading=13)
    s_label = ParagraphStyle('Label', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11)
    s_value = ParagraphStyle('Value', fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#1f2937'), leading=12)
    s_section = ParagraphStyle('Section', fontName='Helvetica-Bold', fontSize=10, textColor=ACCENT, spaceBefore=4, spaceAfter=2)
    s_notes = ParagraphStyle('Notes', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11)
    s_footer = ParagraphStyle('Footer', fontName='Helvetica', fontSize=7, textColor=GREY, alignment=TA_CENTER)

    elements = []

    # ── HEADER ────────────────────────────────────────────────────────
    logo_path = None
    if rs.logo:
        candidate = os.path.join(django_settings.MEDIA_ROOT, rs.logo.name)
        if os.path.isfile(candidate):
            logo_path = candidate

    company_text = Paragraph(rs.name, s_company)
    tagline_text = Paragraph(rs.tagline, s_tagline) if rs.tagline else Paragraph('', s_tagline)
    contact_parts = []
    if rs.phone:
        contact_parts.append(rs.phone)
    if rs.website:
        contact_parts.append(rs.website)
    contact_text = Paragraph('<br/>'.join(contact_parts), s_contact) if contact_parts else Paragraph('', s_contact)

    if logo_path:
        left_logo = Image(logo_path, width=32 * mm, height=32 * mm, kind='proportional')
        left_table = Table(
            [[left_logo, company_text], ['', tagline_text], ['', contact_text]],
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

    right_table = Table(
        [
            [Paragraph('GOODS RECEIVED NOTE', s_title)],
            [Paragraph(receipt.grn_number, ParagraphStyle(
                'GRNNum', fontName='Helvetica-Bold', fontSize=12, textColor=GREEN,
                alignment=TA_RIGHT, leading=16,
            ))],
            [Spacer(1, 2 * mm)],
            [Paragraph(f'Date: {receipt.received_date.strftime("%d %b %Y")}', s_sub)],
            [Paragraph(f'PO: {po.po_number}', s_sub)],
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

    elements.append(Spacer(1, 4 * mm))
    elements.append(HRFlowable(width='100%', thickness=2, color=GREEN, spaceAfter=5 * mm))

    # ── SUPPLIER & RECEIPT INFO ───────────────────────────────────────
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
    ]))

    detail_rows = [
        [Paragraph('RECEIPT DETAILS', s_label)],
    ]
    info_pairs = [
        ('Received by', receipt.received_by.username if receipt.received_by else '—'),
        ('Date & Time', receipt.created_at.strftime('%d %b %Y, %H:%M')),
        ('Purchase Order', po.po_number),
        ('PO Date', po.order_date.strftime('%d %b %Y')),
    ]
    if po.received_date:
        info_pairs.append(('PO Fully Received', po.received_date.strftime('%d %b %Y, %H:%M')))
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

    if receipt.notes:
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph(f'<b>Notes:</b> {receipt.notes}', s_notes))

    elements.append(Spacer(1, 5 * mm))

    # ── ITEMS TABLE ───────────────────────────────────────────────────
    elements.append(Paragraph('RECEIVED ITEMS', s_section))
    elements.append(Spacer(1, 2 * mm))

    table_header = ['#', 'Item', 'Unit', 'Ordered', 'Received', f'Unit Price ({symbol})', f'Value ({symbol})', 'Status']
    table_data = [table_header]

    for idx, item in enumerate(items, 1):
        ordered = item.po_item.quantity
        received = item.received_quantity
        if received >= ordered:
            status = 'Full'
        elif received > 0:
            status = 'Partial'
        else:
            status = 'None'

        row = [
            str(idx),
            Paragraph(item.po_item.inventory_item.name, ParagraphStyle('ItemName', fontName='Helvetica', fontSize=9, leading=11)),
            item.po_item.inventory_item.get_unit_display(),
            f'{ordered:,.2f}',
            f'{received:,.2f}',
            f'{item.po_item.unit_price:,.2f}',
            f'{item.received_value:,.2f}',
            status,
        ]
        table_data.append(row)

    # Total row
    table_data.append(['', '', '', '', '', '',
        Paragraph(f'<b>{symbol} {receipt.total_value:,.2f}</b>',
            ParagraphStyle('', fontName='Helvetica-Bold', fontSize=10, alignment=TA_RIGHT)),
        ''])

    col_widths = [22, None, 38, 48, 48, 68, 68, 44]
    fixed = sum(w for w in col_widths if w is not None)
    col_widths[1] = page_w - fixed

    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), GREEN),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
        ('TOPPADDING', (0, 0), (-1, 0), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        # Body
        ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -2), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -2), 5),
        ('TOPPADDING', (0, 1), (-1, -2), 5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [WHITE, LIGHT_BG]),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, BORDER),
        # Alignment
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # Total row
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 10),
        ('TOPPADDING', (0, -1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
        ('LINEABOVE', (5, -1), (6, -1), 1.2, GREEN),
    ]))
    elements.append(items_table)

    # ── DISCREPANCY NOTES ─────────────────────────────────────────────
    notes_items = [i for i in items if i.notes]
    if notes_items:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph('DISCREPANCY NOTES', s_section))
        elements.append(Spacer(1, 2 * mm))
        for item in notes_items:
            elements.append(Paragraph(
                f'<b>{item.po_item.inventory_item.name}:</b> {item.notes}', s_notes,
            ))
            elements.append(Spacer(1, 1.5 * mm))

    # ── SIGNATURE ─────────────────────────────────────────────────────
    elements.append(Spacer(1, 20 * mm))
    sig_table = Table(
        [
            [Paragraph('Received by', s_label), '', Paragraph('Authorized by', s_label)],
            ['_' * 35, '', '_' * 35],
            [Paragraph(receipt.received_by.username if receipt.received_by else '', s_notes), '', Paragraph('', s_notes)],
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

    # ── FOOTER ────────────────────────────────────────────────────────
    elements.append(Spacer(1, 8 * mm))
    elements.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=3 * mm))
    elements.append(Paragraph(
        f'{rs.name}  |  {receipt.grn_number}  |  {po.po_number}  |  Generated {dt.date.today().strftime("%d %b %Y")}',
        s_footer,
    ))

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{receipt.grn_number}.pdf"'
    return response
