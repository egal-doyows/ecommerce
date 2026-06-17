import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models, transaction
from django.utils import timezone as tz

from purchasing.models import PurchaseOrder
from supplier.models import SupplierTransaction
from .models import GoodsReceipt, GoodsReceiptItem


def _is_manager(user):
    """Manager or Superuser only (not Supervisor)."""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name='Manager').exists()
    )


def _is_supervisor(user):
    return user.is_authenticated and user.groups.filter(name='Supervisor').exists()


def _can_receive_po(user, po):
    """Managers/superusers receive any PO; supervisors receive their own."""
    return _is_manager(user) or (_is_supervisor(user) and po.created_by_id == user.id)


# ── Goods-receipt correction window ──────────────────────────────────
#
# A fresh receipt can be cleanly corrected (undone + re-received) for a bounded
# window after it was recorded — long enough to catch a same-shift data-entry
# error, short enough that the stock ledger and supplier invoices settle. The
# window length is configurable in Restaurant Settings. Once it lapses, only a
# manager can still Reverse the receipt (unbounded escape hatch).

def _edit_window_hours():
    from menu.cache import get_restaurant_settings
    return int(getattr(get_restaurant_settings(), 'receipt_edit_window_hours', 24) or 0)


def _edit_deadline(receipt):
    return receipt.created_at + timedelta(hours=_edit_window_hours())


def _within_edit_window(receipt):
    return tz.now() <= _edit_deadline(receipt)


def _can_correct_receipt(user, receipt):
    """Who may correct a receipt: a manager, or the supervisor who recorded it.
    Time-bounding is checked separately via `_within_edit_window`."""
    return _is_manager(user) or (
        _is_supervisor(user) and receipt.received_by_id == user.id
    )


def _reverse_receipt(receipt, invoice, po):
    """Undo a goods receipt in one atomic block: decrement the stock it added,
    roll the PO back to draft, and delete the auto-generated supplier invoice
    and the GRN itself. Shared by Reverse (manager, unbounded) and Correct
    (bounded). The caller owns permission checks, the invoice-payment guard,
    and audit logging."""
    from django.db.models import F
    from menu.models import InventoryItem
    with transaction.atomic():
        # Lock the GRN row and bail if a concurrent reverse/correct already
        # removed it, so the stock decrement can't run twice.
        locked = GoodsReceipt.objects.select_for_update().filter(pk=receipt.pk).first()
        if locked is None:
            return False
        for gi in receipt.items.select_related('po_item'):
            InventoryItem.objects.filter(pk=gi.po_item.inventory_item_id).update(
                stock_quantity=F('stock_quantity') - gi.received_quantity,
            )
            # PurchaseOrderItem.received_quantity is derived from the GRN rows,
            # so deleting this receipt below rolls it back automatically.
        if po.status == 'received':
            po.status = 'draft'
            po.received_date = None
            po.save(update_fields=['status', 'received_date'])
        if invoice:
            invoice.delete()
        receipt.delete()
    return True


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


def staff_required(view_func):
    """Managers, supervisors, and superusers can access. Per-PO ownership is
    enforced inside each view via `_can_receive_po`, so supervisors are scoped
    to the purchase orders they created."""
    @login_required(login_url='my-login')
    def wrapper(request, *args, **kwargs):
        if not (_is_manager(request.user) or _is_supervisor(request.user)):
            messages.error(request, 'Only managers and supervisors can access goods receiving.')
            return redirect('admin-dashboard')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


# ── Receipt List ─────────────────────────────────────────────────────

@staff_required
def receipt_list(request):
    receipts = GoodsReceipt.objects.select_related(
        'purchase_order__supplier', 'received_by',
    ).order_by('-received_date', '-pk')

    # Supervisors are scoped to receiving for the POs they created.
    own_only = not _is_manager(request.user)
    if own_only:
        receipts = receipts.filter(purchase_order__created_by=request.user)

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

    # POs ready to receive: open (not fully received / cancelled) and have items.
    approved_pos = PurchaseOrder.objects.filter(
        status__in=['draft', 'approved'],
    ).exclude(items__isnull=True).distinct().select_related('supplier').order_by('-order_date')
    if own_only:
        approved_pos = approved_pos.filter(created_by=request.user)

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

@staff_required
def receipt_create(request, po_pk):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier'),
        pk=po_pk,
    )

    if not _can_receive_po(request.user, po):
        messages.error(request, 'You can only receive goods on purchase orders you created.')
        return redirect('po-detail', pk=po.pk)

    if po.status not in ('draft', 'approved', 'received'):
        messages.error(request, 'This purchase order can no longer be received.')
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

    from menu.cache import get_restaurant_settings
    symbol = get_restaurant_settings().currency_symbol

    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()

        from django.db.models import F
        from menu.models import InventoryItem

        with transaction.atomic():
            # Lock the PO so concurrent or double-submitted receipts serialise.
            # Each one then recomputes `remaining` below against the GRN rows the
            # other has already committed, instead of a stale GET-time value —
            # this is what prevents over-receiving past the ordered quantity.
            po = (
                PurchaseOrder.objects
                .select_for_update()
                .select_related('supplier')
                .get(pk=po.pk)
            )

            # Idempotency: this token is minted once per form load (GET) and
            # travels with the POST. A double-submit, browser retry, or replayed
            # request carries the same token — under the PO lock above we check
            # for an existing receipt and return it instead of recording stock
            # and an invoice twice. The unique constraint on the column is the
            # hard backstop if this check is ever bypassed.
            idem_key = request.POST.get('idempotency_key', '').strip() or None
            if idem_key:
                existing = GoodsReceipt.objects.filter(idempotency_key=idem_key).first()
                if existing:
                    messages.info(
                        request,
                        f'{existing.grn_number} was already recorded for {po.po_number}.',
                    )
                    return redirect('receipt-detail', pk=existing.pk)

            receipt = GoodsReceipt.objects.create(
                purchase_order=po,
                received_by=request.user,
                received_date=tz.now().date(),
                notes=notes,
                idempotency_key=idem_key,
            )

            total_received_value = Decimal('0')
            any_received = False
            received_lines = []

            for item in po.items.select_related('inventory_item'):
                # "Already received" is recomputed from the GRN audit trail
                # under the PO lock — the single source of truth.
                prev_received = GoodsReceiptItem.objects.filter(
                    po_item=item,
                ).aggregate(total=models.Sum('received_quantity'))['total'] or Decimal('0')
                remaining = max(item.quantity - prev_received, Decimal('0'))

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
                received_lines.append(f'{item.inventory_item.name}×{received_qty}')

                # Update inventory stock atomically. F() avoids the lost-update
                # race; PurchaseOrderItem.received_quantity is now derived from
                # the GRN rows above, so there is nothing to write back there.
                if received_qty > 0:
                    any_received = True
                    stock_update = {'stock_quantity': F('stock_quantity') + received_qty}
                    if item.unit_price > 0:
                        stock_update['buying_price'] = item.unit_price
                    InventoryItem.objects.filter(
                        pk=item.inventory_item_id,
                    ).update(**stock_update)
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

        # ── Strong audit trail ──────────────────────────────────────────
        # Supervisors can create AND receive their own POs (no separate
        # approver), so every receipt is recorded as an explicit business
        # event. A self-receive — the user who created the PO also receiving
        # the goods, i.e. no separation of duties — is additionally flagged at
        # WARNING so it stands out in the audit log and any monitoring.
        import logging
        self_receive = (po.created_by_id == request.user.id)
        client_ip = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', '')
        audit = logging.getLogger('audit')
        audit.info(
            "Goods received: grn=%s po=%s supplier=%s received_by=%s "
            "self_receive=%s status=%s value=%s items=[%s] ip=%s",
            receipt.grn_number, po.po_number, po.supplier.name,
            request.user.username, self_receive,
            'fully' if fully_received else 'partial',
            total_received_value, '; '.join(received_lines), client_ip,
        )
        if self_receive:
            audit.warning(
                "SELF-RECEIVE (no separation of duties): %s received goods on "
                "%s which they created — grn=%s value=%s ip=%s",
                request.user.username, po.po_number, receipt.grn_number,
                total_received_value, client_ip,
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
        'idempotency_key': secrets.token_hex(16),
    })


# ── Receipt Detail ──────────────────────────────────────────────────

@staff_required
def receipt_detail(request, pk):
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related(
            'purchase_order__supplier', 'received_by',
        ),
        pk=pk,
    )
    if not _can_receive_po(request.user, receipt.purchase_order):
        messages.error(request, 'You can only view receipts for purchase orders you created.')
        return redirect('receipt-list')
    items = receipt.items.select_related('po_item__inventory_item').all()

    from menu.cache import get_restaurant_settings
    symbol = get_restaurant_settings().currency_symbol

    can_correct = (
        _can_correct_receipt(request.user, receipt)
        and _within_edit_window(receipt)
    )

    return render(request, 'receiving/receipt_detail.html', {
        'receipt': receipt,
        'items': items,
        'currency_symbol': symbol,
        'can_reverse': _is_manager(request.user),
        'can_correct': can_correct,
        'edit_deadline': _edit_deadline(receipt),
        'edit_window_hours': _edit_window_hours(),
    })


# ── PO Receiving Summary ────────────────────────────────────────────

@staff_required
def po_receiving_summary(request, po_pk):
    """Show all receipts for a specific PO."""
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier'),
        pk=po_pk,
    )
    if not _can_receive_po(request.user, po):
        messages.error(request, 'You can only view receiving for purchase orders you created.')
        return redirect('receipt-list')
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

    from menu.cache import get_restaurant_settings
    symbol = get_restaurant_settings().currency_symbol

    return render(request, 'receiving/po_summary.html', {
        'po': po,
        'receipts': receipts,
        'items_data': items_data,
        'currency_symbol': symbol,
    })


# ── GRN PDF ─────────────────────────────────────────────────────────

@staff_required
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
    if not _can_receive_po(request.user, receipt.purchase_order):
        messages.error(request, 'You can only view receipts for purchase orders you created.')
        return redirect('receipt-list')
    items = receipt.items.select_related('po_item__inventory_item').all()
    po = receipt.purchase_order

    from menu.cache import get_restaurant_settings
    rs = get_restaurant_settings()
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


@manager_required
def receipt_reverse(request, pk):
    """Reverse a goods receipt: decrement the stock it added, roll back the
    PO's received quantities/status, and remove the auto-generated supplier
    invoice. The safe alternative to deleting a GRN in Django admin (which
    leaves stock and the supplier ledger overstated).

    Blocked if the supplier invoice has any payment recorded against it —
    settle/unwind that in the supplier module first.
    """
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related('purchase_order'), pk=pk,
    )
    invoice = SupplierTransaction.objects.filter(
        reference=receipt.grn_number, transaction_type='debit',
    ).first()

    if invoice and invoice.amount_paid and invoice.amount_paid > 0:
        messages.error(
            request,
            f'{receipt.grn_number} has a supplier payment recorded against it. '
            'Unwind that in the supplier ledger before reversing this receipt.',
        )
        return redirect('receipt-detail', pk=receipt.pk)

    if request.method != 'POST':
        return render(request, 'receiving/receipt_reverse_confirm.html', {
            'receipt': receipt, 'invoice': invoice,
        })

    grn = receipt.grn_number
    po = receipt.purchase_order
    if not _reverse_receipt(receipt, invoice, po):
        messages.info(request, f'{grn} was already reversed.')
        return redirect('receipt-list')

    import logging
    logging.getLogger('audit').info(
        "Goods receipt reversed: grn=%s po=%s by=%s",
        grn, po.po_number, request.user.username,
    )
    messages.success(request, f'{grn} reversed — stock and the {po.po_number} invoice were rolled back.')
    return redirect('receipt-list')


@staff_required
def receipt_correct(request, pk):
    """Cleanly correct a fresh goods receipt that was entered with an error.

    Within the configurable edit window, the person who received the goods (or a
    manager) can undo the receipt — reversing its stock and supplier invoice and
    reopening the PO — and is taken straight back to the receive form to re-enter
    the correct quantities. This is the bounded, supervisor-accessible sibling of
    the manager-only, unbounded Reverse.
    """
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related('purchase_order'), pk=pk,
    )

    if not _can_correct_receipt(request.user, receipt):
        messages.error(request, 'You can only correct goods receipts you recorded.')
        return redirect('receipt-detail', pk=receipt.pk)

    if not _within_edit_window(receipt):
        hrs = _edit_window_hours()
        messages.error(
            request,
            f'The {hrs}-hour correction window for {receipt.grn_number} has '
            'passed. Ask a manager to reverse it instead.',
        )
        return redirect('receipt-detail', pk=receipt.pk)

    invoice = SupplierTransaction.objects.filter(
        reference=receipt.grn_number, transaction_type='debit',
    ).first()

    if invoice and invoice.amount_paid and invoice.amount_paid > 0:
        messages.error(
            request,
            f'{receipt.grn_number} has a supplier payment recorded against it. '
            'Unwind that in the supplier ledger before correcting this receipt.',
        )
        return redirect('receipt-detail', pk=receipt.pk)

    if request.method != 'POST':
        return render(request, 'receiving/receipt_correct_confirm.html', {
            'receipt': receipt,
            'invoice': invoice,
            'edit_deadline': _edit_deadline(receipt),
            'edit_window_hours': _edit_window_hours(),
        })

    grn = receipt.grn_number
    po = receipt.purchase_order
    if not _reverse_receipt(receipt, invoice, po):
        messages.info(request, f'{grn} was already undone — re-enter quantities for {po.po_number}.')
        return redirect('receipt-create', po_pk=po.pk)

    import logging
    logging.getLogger('audit').info(
        "Goods receipt corrected (reversed for re-entry): grn=%s po=%s by=%s",
        grn, po.po_number, request.user.username,
    )
    messages.info(
        request,
        f'{grn} was undone for correction — re-enter the correct quantities '
        f'for {po.po_number} below.',
    )
    return redirect('receipt-create', po_pk=po.pk)
