import io
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Sum
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from menu.models import InventoryItem
from .models import StockMovement, StockAdjustment, StockAdjustmentLine


from core.permissions import has_full_access, is_manager, is_overall_manager


def _has_stock_access(user):
    """Owners, Overall Managers, and Branch Managers can view stock data."""
    return is_manager(user)


# ── Movement log ───────────────────────────────────────────────────

@login_required(login_url='my-login')
def movement_list(request):
    if not _has_stock_access(request.user):
        return redirect('admin-dashboard')

    branch = getattr(request, 'branch', None)
    qs = StockMovement.objects.select_related('inventory_item', 'created_by')
    if branch:
        qs = qs.filter(branch=branch)

    # Filters
    item_id = request.GET.get('item')
    movement_type = request.GET.get('type')
    if item_id:
        qs = qs.filter(inventory_item_id=item_id)
    if movement_type:
        qs = qs.filter(movement_type=movement_type)

    movements = qs[:200]
    items = InventoryItem.objects.all().order_by('name')
    if branch:
        items = items.filter(branch=branch)

    return render(request, 'stocks/movement_list.html', {
        'movements': movements,
        'items': items,
        'selected_item': item_id or '',
        'selected_type': movement_type or '',
        'type_choices': StockMovement.TYPE_CHOICES,
    })


# ── Stock adjustments (Owner only) ────────────────────────────────

@login_required(login_url='my-login')
def adjustment_list(request):
    if not _has_stock_access(request.user):
        return redirect('admin-dashboard')

    branch = getattr(request, 'branch', None)
    qs = StockAdjustment.objects.select_related('created_by')
    if branch:
        qs = qs.filter(branch=branch)

    adjustments = qs[:100]
    return render(request, 'stocks/adjustment_list.html', {
        'adjustments': adjustments,
        'is_owner': has_full_access(request.user),
    })


@login_required(login_url='my-login')
def adjustment_create(request):
    if not has_full_access(request.user):
        from django.contrib import messages
        messages.error(request, 'Only owners can make stock adjustments.')
        return redirect('stock-adjustment-list')

    branch = getattr(request, 'branch', None)
    items = InventoryItem.objects.all().order_by('name')
    if branch:
        items = items.filter(branch=branch)

    if request.method == 'POST':
        reason = request.POST.get('reason', 'correction')
        notes = request.POST.get('notes', '').strip()

        item_ids = request.POST.getlist('item_id')
        new_qtys = request.POST.getlist('new_qty')

        if not item_ids:
            from django.contrib import messages
            messages.error(request, 'No items to adjust.')
            return redirect('stock-adjustment-create')

        with transaction.atomic():
            adj = StockAdjustment.objects.create(
                branch=branch,
                reason=reason,
                notes=notes,
                created_by=request.user,
            )
            for item_id, new_qty_str in zip(item_ids, new_qtys):
                if not new_qty_str.strip():
                    continue
                try:
                    new_qty = Decimal(new_qty_str)
                except (InvalidOperation, ValueError):
                    continue

                inv = InventoryItem.objects.select_for_update().get(pk=item_id)
                old_qty = inv.stock_quantity
                if new_qty == old_qty:
                    continue

                diff = new_qty - old_qty
                inv.stock_quantity = new_qty
                inv.save(update_fields=['stock_quantity'])

                StockAdjustmentLine.objects.create(
                    adjustment=adj,
                    inventory_item=inv,
                    old_quantity=old_qty,
                    new_quantity=new_qty,
                )
                StockMovement.objects.create(
                    inventory_item=inv,
                    branch=branch,
                    movement_type='adjustment',
                    quantity=diff,
                    balance_after=new_qty,
                    reference=f'Adjustment #{adj.pk} — {adj.get_reason_display()}',
                    notes=notes,
                    created_by=request.user,
                )

            # Delete empty adjustments
            if not adj.lines.exists():
                adj.delete()
                from django.contrib import messages
                messages.info(request, 'No changes were made.')
                return redirect('stock-adjustment-list')

        from django.contrib import messages
        messages.success(request, f'Stock adjustment #{adj.pk} saved.')
        return redirect('stock-adjustment-detail', pk=adj.pk)

    return render(request, 'stocks/adjustment_form.html', {
        'items': items,
        'reason_choices': StockAdjustment.REASON_CHOICES,
    })


@login_required(login_url='my-login')
def adjustment_detail(request, pk):
    if not _has_stock_access(request.user):
        return redirect('admin-dashboard')

    adj = get_object_or_404(
        StockAdjustment.objects.select_related('created_by'),
        pk=pk,
    )
    lines = adj.lines.select_related('inventory_item').all()
    return render(request, 'stocks/adjustment_detail.html', {
        'adjustment': adj,
        'lines': lines,
    })


# ── Stock-taking sheet PDF ─────────────────────────────────────────

@login_required(login_url='my-login')
def stocktake_pdf(request):
    if not _has_stock_access(request.user):
        return redirect('admin-dashboard')

    branch = getattr(request, 'branch', None)
    items = InventoryItem.objects.all().order_by('name')
    if branch:
        items = items.filter(branch=branch)

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from django.conf import settings as django_settings
    import os

    from menu.models import RestaurantSettings
    restaurant = RestaurantSettings.load()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    elements = []

    # Logo
    if restaurant.logo:
        logo_path = os.path.join(django_settings.MEDIA_ROOT, str(restaurant.logo))
        if os.path.exists(logo_path):
            elements.append(Image(logo_path, width=50, height=50, hAlign='CENTER'))
            elements.append(Spacer(1, 3*mm))

    # Restaurant name
    brand_style = ParagraphStyle(
        'Brand', parent=styles['Title'],
        fontSize=18, alignment=TA_CENTER, spaceAfter=2,
    )
    elements.append(Paragraph(restaurant.name, brand_style))

    if restaurant.tagline:
        tagline_style = ParagraphStyle(
            'Tagline', parent=styles['Normal'],
            fontSize=9, alignment=TA_CENTER, textColor=colors.HexColor('#6b7280'),
        )
        elements.append(Paragraph(restaurant.tagline, tagline_style))

    if restaurant.phone:
        phone_style = ParagraphStyle(
            'Phone', parent=styles['Normal'],
            fontSize=8, alignment=TA_CENTER, textColor=colors.HexColor('#9ca3af'),
        )
        elements.append(Paragraph(f'Tel: {restaurant.phone}', phone_style))

    elements.append(Spacer(1, 6*mm))

    # Title
    title_style = ParagraphStyle(
        'SheetTitle', parent=styles['Heading2'],
        fontSize=14, alignment=TA_CENTER, spaceAfter=4,
    )
    elements.append(Paragraph('Stock-Taking Sheet', title_style))

    date_str = timezone.localdate().strftime('%d %B %Y')
    time_str = timezone.localtime().strftime('%H:%M')
    branch_name = branch.name if branch else 'All Branches'
    sub_style = ParagraphStyle(
        'Sub', parent=styles['Normal'],
        fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor('#6b7280'),
    )
    elements.append(Paragraph(f'{branch_name} &mdash; {date_str} at {time_str}', sub_style))
    elements.append(Spacer(1, 8*mm))

    # Table header
    data = [['#', 'Item Name', 'Unit', 'System Qty', 'Actual Qty', 'Variance', 'Notes']]

    for i, item in enumerate(items, 1):
        data.append([
            str(i),
            item.name,
            item.get_unit_display(),
            f'{item.stock_quantity:.2f}',
            '',  # blank for manual entry
            '',  # blank
            '',  # blank
        ])

    col_widths = [25, 140, 55, 65, 65, 55, 100]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1d2e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (3, 0), (5, -1), 'CENTER'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        # Row height for manual writing
        ('ROWHEIGHT', (0, 1), (-1, -1), 24),
    ]))
    elements.append(t)

    elements.append(Spacer(1, 12*mm))
    elements.append(Paragraph('Counted by: _____________________ &nbsp;&nbsp; Signature: _____________________ &nbsp;&nbsp; Date: ____________', styles['Normal']))

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf, content_type='application/pdf')
    filename = f'stocktake_{timezone.localdate().isoformat()}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
