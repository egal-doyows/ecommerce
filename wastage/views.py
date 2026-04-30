import io
from datetime import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as tz

from menu.models import InventoryItem, RestaurantSettings

from .models import WasteLog, WasteItem


from core.permissions import admin_required as staff_required


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@staff_required
def waste_list(request):
    qs = WasteLog.objects.filter(branch=request.branch).select_related('logged_by', 'branch').prefetch_related('items')

    # Filters
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    reason_filter = request.GET.get('reason', '')
    search = request.GET.get('q', '')

    if date_from:
        try:
            qs = qs.filter(date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to:
        try:
            qs = qs.filter(date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError:
            pass
    if reason_filter:
        qs = qs.filter(reason=reason_filter)
    if search:
        qs = qs.filter(
            models.Q(notes__icontains=search)
            | models.Q(items__inventory_item__name__icontains=search)
        ).distinct()

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    restaurant = RestaurantSettings.load()

    return render(request, 'wastage/waste_list.html', {
        'logs': page_obj,
        'page_obj': page_obj,
        'date_from': date_from,
        'date_to': date_to,
        'reason_filter': reason_filter,
        'search': search,
        'reason_choices': WasteLog.REASON_CHOICES,
        'restaurant': restaurant,
        'is_overall': False,
        'branches': [],
        'branch_filter': '',
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@staff_required
def waste_create(request):
    from branches.utils import resolve_branch
    items = InventoryItem.objects.filter(stock_quantity__gt=0, branch=request.branch).order_by('name')
    all_items = InventoryItem.objects.filter(branch=request.branch).order_by('name')
    restaurant = RestaurantSettings.load()

    if request.method == 'POST':
        reason = request.POST.get('reason', '')
        date_str = request.POST.get('date', '')
        notes = request.POST.get('notes', '')

        if not reason:
            messages.error(request, 'Please select a reason for waste.')
            return redirect('wastage-create')

        from core.utils import parse_date
        waste_date = parse_date(date_str)
        if waste_date is None:
            messages.error(request, 'Please enter a valid date.')
            return redirect('wastage-create')

        # Collect items from POST
        waste_items = []
        for key in request.POST:
            if key.startswith('item_') and not key.startswith('item_qty_') and not key.startswith('item_notes_'):
                idx = key.replace('item_', '')
                item_pk = request.POST.get(key)
                qty = request.POST.get(f'item_qty_{idx}', '0')
                item_notes = request.POST.get(f'item_notes_{idx}', '')

                if not item_pk:
                    continue

                try:
                    qty = Decimal(qty)
                except Exception:
                    qty = Decimal('0')

                if qty <= 0:
                    continue

                try:
                    inv_item = InventoryItem.objects.get(pk=item_pk)
                except InventoryItem.DoesNotExist:
                    continue

                waste_items.append({
                    'inventory_item': inv_item,
                    'quantity': qty,
                    'unit_cost': inv_item.buying_price,
                    'notes': item_notes,
                })

        if not waste_items:
            messages.error(request, 'Please add at least one item with a valid quantity.')
            return redirect('wastage-create')

        # Create the waste log (atomic to keep stock and records consistent)
        from django.db import transaction
        with transaction.atomic():
            log = WasteLog.objects.create(
                reason=reason,
                date=waste_date,
                notes=notes,
                logged_by=request.user,
                branch=resolve_branch(request),
            )

            for wi in waste_items:
                WasteItem.objects.create(
                    waste_log=log,
                    inventory_item=wi['inventory_item'],
                    quantity=wi['quantity'],
                    unit_cost=wi['unit_cost'],
                    notes=wi['notes'],
                )
                # Deduct stock
                wi['inventory_item'].deduct(wi['quantity'])

        messages.success(request, f'Waste log {log.waste_number} recorded successfully.')
        return redirect('wastage-detail', pk=log.pk)

    return render(request, 'wastage/waste_create.html', {
        'items': items,
        'all_items': all_items,
        'reason_choices': WasteLog.REASON_CHOICES,
        'today': tz.now().date().isoformat(),
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@staff_required
def waste_detail(request, pk):
    is_overall = request.user.is_superuser or request.user.groups.filter(name='Overall Manager').exists()
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    log = get_object_or_404(
        WasteLog.objects.select_related('logged_by'),
        **filter_kwargs,
    )
    items = log.items.select_related('inventory_item').all()
    restaurant = RestaurantSettings.load()

    return render(request, 'wastage/waste_detail.html', {
        'log': log,
        'items': items,
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
    })


# ---------------------------------------------------------------------------
# Summary / Analytics
# ---------------------------------------------------------------------------

@staff_required
def waste_summary(request):
    restaurant = RestaurantSettings.load()

    # Date range defaults to current month
    today = tz.now().date()
    date_from = request.GET.get('date_from', today.replace(day=1).isoformat())
    date_to = request.GET.get('date_to', today.isoformat())

    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d').date()
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        d_to = today

    logs = WasteLog.objects.filter(date__gte=d_from, date__lte=d_to, branch=request.branch)

    # Total cost
    waste_items = WasteItem.objects.filter(
        waste_log__date__gte=d_from,
        waste_log__date__lte=d_to,
        waste_log__branch=request.branch,
    ).select_related('inventory_item', 'waste_log')

    total_cost = sum(wi.cost for wi in waste_items)
    total_events = logs.count()
    total_items = waste_items.count()

    # By reason
    by_reason = []
    for code, label in WasteLog.REASON_CHOICES:
        reason_items = waste_items.filter(waste_log__reason=code)
        cost = sum(wi.cost for wi in reason_items)
        count = reason_items.count()
        if count > 0:
            by_reason.append({
                'reason': label,
                'code': code,
                'count': count,
                'cost': cost,
                'pct': round(cost / total_cost * 100, 1) if total_cost else 0,
            })
    by_reason.sort(key=lambda x: x['cost'], reverse=True)

    # Top wasted items
    item_totals = {}
    for wi in waste_items:
        key = wi.inventory_item.pk
        if key not in item_totals:
            item_totals[key] = {
                'name': wi.inventory_item.name,
                'unit': wi.inventory_item.get_unit_display(),
                'total_qty': Decimal('0'),
                'total_cost': Decimal('0'),
                'count': 0,
            }
        item_totals[key]['total_qty'] += wi.quantity
        item_totals[key]['total_cost'] += wi.cost
        item_totals[key]['count'] += 1

    top_items = sorted(item_totals.values(), key=lambda x: x['total_cost'], reverse=True)[:10]

    return render(request, 'wastage/waste_summary.html', {
        'restaurant': restaurant,
        'currency_symbol': restaurant.currency_symbol,
        'date_from': date_from,
        'date_to': date_to,
        'total_cost': total_cost,
        'total_events': total_events,
        'total_items': total_items,
        'by_reason': by_reason,
        'top_items': top_items,
    })


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

@staff_required
def waste_pdf(request, pk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    is_overall = request.user.is_superuser or request.user.groups.filter(name='Overall Manager').exists()
    filter_kwargs = {'pk': pk}
    if not is_overall:
        filter_kwargs['branch'] = request.branch
    log = get_object_or_404(
        WasteLog.objects.select_related('logged_by'),
        **filter_kwargs,
    )
    items = log.items.select_related('inventory_item').all()
    restaurant = RestaurantSettings.load()
    cs = restaurant.currency_symbol

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('DocTitle', parent=styles['Heading1'],
                              fontSize=18, textColor=colors.HexColor('#dc3545'),
                              spaceAfter=4))
    styles.add(ParagraphStyle('SubInfo', parent=styles['Normal'],
                              fontSize=9, textColor=colors.grey))

    elements = []

    # Header
    elements.append(Paragraph(restaurant.name, styles['Heading2']))
    elements.append(Paragraph('WASTE REPORT', styles['DocTitle']))
    elements.append(Spacer(1, 4 * mm))

    # Meta info
    meta = [
        ['Waste Ref:', log.waste_number, 'Date:', log.date.strftime('%d %b %Y')],
        ['Reason:', log.get_reason_display(), 'Logged by:', log.logged_by.username if log.logged_by else '—'],
        ['Recorded at:', log.created_at.strftime('%d %b %Y, %H:%M'), '', ''],
    ]
    meta_table = Table(meta, colWidths=[25 * mm, 55 * mm, 25 * mm, 55 * mm])
    meta_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.grey),
        ('TEXTCOLOR', (2, 0), (2, -1), colors.grey),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)

    if log.notes:
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph(f'<b>Notes:</b> {log.notes}', styles['Normal']))

    elements.append(Spacer(1, 6 * mm))

    # Items table
    data = [['Item', 'Unit', 'Quantity', f'Unit Cost ({cs})', f'Total Cost ({cs})', 'Notes']]
    for item in items:
        data.append([
            item.inventory_item.name,
            item.inventory_item.get_unit_display(),
            f'{item.quantity:,.2f}',
            f'{item.unit_cost:,.2f}',
            f'{item.cost:,.2f}',
            item.notes or '—',
        ])
    # Total row
    data.append(['', '', '', 'Total', f'{log.total_cost:,.2f}', ''])

    col_widths = [45 * mm, 20 * mm, 22 * mm, 25 * mm, 25 * mm, 33 * mm]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dc3545')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        # Alignment
        ('ALIGN', (2, 0), (4, -1), 'RIGHT'),
        # Grid
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#dc3545')),
        ('LINEBELOW', (0, -2), (-1, -2), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#fff5f5')]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        # Total row
        ('FONTNAME', (3, -1), (4, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (3, -1), (4, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#dc3545')),
    ]))
    elements.append(t)

    elements.append(Spacer(1, 10 * mm))

    # Signature lines
    sig_data = [
        ['Logged by', 'Verified by'],
        ['', ''],
        ['_________________________', '_________________________'],
        [log.logged_by.username if log.logged_by else '________________', '________________'],
    ]
    sig_table = Table(sig_data, colWidths=[80 * mm, 80 * mm])
    sig_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.grey),
        ('TOPPADDING', (0, 2), (-1, 2), 20),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(sig_table)

    # Footer
    elements.append(Spacer(1, 8 * mm))
    elements.append(Paragraph(
        f'Generated on {tz.now().strftime("%d %b %Y, %H:%M")} — {restaurant.name}',
        styles['SubInfo'],
    ))

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{log.waste_number}.pdf"'
    return response
