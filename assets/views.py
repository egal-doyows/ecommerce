import io
import os
from decimal import Decimal

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from menu.models import RestaurantSettings
from .models import Asset, AssetCategory


from core.permissions import is_manager as _has_access, is_overall_manager as _is_overall


# ── List ───────────────────────────────────────────────────────────

@login_required(login_url='my-login')
def asset_list(request):
    if not _has_access(request.user):
        return redirect('admin-dashboard')

    branch = getattr(request, 'branch', None)
    qs = Asset.objects.select_related('category', 'branch')
    if branch:
        qs = qs.filter(branch=branch)

    # Filters
    cat_id = request.GET.get('category')
    condition = request.GET.get('condition')
    search = request.GET.get('q', '')

    if cat_id:
        qs = qs.filter(category_id=cat_id)
    if condition:
        qs = qs.filter(condition=condition)
    if search:
        qs = qs.filter(name__icontains=search)

    categories = AssetCategory.objects.all()
    total_value = sum(a.total_value for a in qs)

    return render(request, 'assets/asset_list.html', {
        'assets': qs,
        'categories': categories,
        'selected_category': cat_id or '',
        'selected_condition': condition or '',
        'search': search,
        'condition_choices': Asset.CONDITION_CHOICES,
        'total_value': total_value,
        'total_count': qs.count(),
        'currency': RestaurantSettings.load().currency_symbol,
    })


# ── Create / Edit ─────────────────────────────────────────────────

@login_required(login_url='my-login')
def asset_create(request):
    if not _has_access(request.user):
        return redirect('admin-dashboard')

    categories = AssetCategory.objects.all()
    branch = getattr(request, 'branch', None)

    if request.method == 'POST':
        asset = Asset(
            branch=branch,
            name=request.POST.get('name', '').strip(),
            description=request.POST.get('description', '').strip(),
            serial_number=request.POST.get('serial_number', '').strip(),
            location=request.POST.get('location', '').strip(),
            condition=request.POST.get('condition', 'good'),
            notes=request.POST.get('notes', '').strip(),
            created_by=request.user,
        )
        cat_id = request.POST.get('category')
        if cat_id:
            asset.category_id = int(cat_id)
        try:
            asset.quantity = int(request.POST.get('quantity', 1))
        except (ValueError, TypeError):
            messages.error(request, 'Invalid quantity value.')
            return render(request, 'assets/asset_form.html', {
                'categories': categories, 'condition_choices': Asset.CONDITION_CHOICES, 'title': 'Add Asset',
            })
        try:
            asset.purchase_price = Decimal(request.POST.get('purchase_price', '0'))
        except Exception:
            messages.error(request, 'Invalid purchase price.')
            return render(request, 'assets/asset_form.html', {
                'categories': categories, 'condition_choices': Asset.CONDITION_CHOICES, 'title': 'Add Asset',
            })
        date_str = request.POST.get('purchase_date', '').strip()
        if date_str:
            asset.purchase_date = date_str

        if request.FILES.get('image'):
            asset.image = request.FILES['image']

        asset.save()
        messages.success(request, f'Asset "{asset.name}" created.')
        return redirect('asset-list')

    return render(request, 'assets/asset_form.html', {
        'categories': categories,
        'condition_choices': Asset.CONDITION_CHOICES,
        'title': 'Add Asset',
    })


@login_required(login_url='my-login')
def asset_edit(request, pk):
    if not _has_access(request.user):
        return redirect('admin-dashboard')

    asset = get_object_or_404(Asset, pk=pk)
    categories = AssetCategory.objects.all()

    if request.method == 'POST':
        asset.name = request.POST.get('name', '').strip()
        asset.description = request.POST.get('description', '').strip()
        asset.serial_number = request.POST.get('serial_number', '').strip()
        asset.location = request.POST.get('location', '').strip()
        asset.condition = request.POST.get('condition', 'good')
        asset.notes = request.POST.get('notes', '').strip()
        cat_id = request.POST.get('category')
        asset.category_id = int(cat_id) if cat_id else None
        try:
            asset.quantity = int(request.POST.get('quantity', 1))
        except (ValueError, TypeError):
            messages.error(request, 'Invalid quantity value.')
            return render(request, 'assets/asset_form.html', {
                'asset': asset, 'categories': categories,
                'condition_choices': Asset.CONDITION_CHOICES, 'title': 'Edit Asset',
            })
        try:
            asset.purchase_price = Decimal(request.POST.get('purchase_price', '0'))
        except Exception:
            messages.error(request, 'Invalid purchase price.')
            return render(request, 'assets/asset_form.html', {
                'asset': asset, 'categories': categories,
                'condition_choices': Asset.CONDITION_CHOICES, 'title': 'Edit Asset',
            })
        date_str = request.POST.get('purchase_date', '').strip()
        if date_str:
            asset.purchase_date = date_str

        if request.FILES.get('image'):
            asset.image = request.FILES['image']

        asset.save()
        messages.success(request, f'Asset "{asset.name}" updated.')
        return redirect('asset-list')

    return render(request, 'assets/asset_form.html', {
        'asset': asset,
        'categories': categories,
        'condition_choices': Asset.CONDITION_CHOICES,
        'title': 'Edit Asset',
    })


# ── Delete ─────────────────────────────────────────────────────────

@login_required(login_url='my-login')
def asset_delete(request, pk):
    if not _has_access(request.user):
        return redirect('admin-dashboard')

    asset = get_object_or_404(Asset, pk=pk)
    if request.method == 'POST':
        name = asset.name
        asset.delete()
        messages.success(request, f'Asset "{name}" deleted.')
    return redirect('asset-list')


# ── Category management ────────────────────────────────────────────

@login_required(login_url='my-login')
def category_list(request):
    if not _has_access(request.user):
        return redirect('admin-dashboard')
    categories = AssetCategory.objects.all()
    return render(request, 'assets/category_list.html', {'categories': categories})


@login_required(login_url='my-login')
def category_create(request):
    if not _has_access(request.user):
        return redirect('admin-dashboard')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        icon = request.POST.get('icon', '').strip()
        if name:
            AssetCategory.objects.create(name=name, icon=icon)
            messages.success(request, f'Category "{name}" created.')
        return redirect('asset-category-list')
    return render(request, 'assets/category_form.html', {'title': 'Add Category'})


@login_required(login_url='my-login')
def category_edit(request, pk):
    if not _has_access(request.user):
        return redirect('admin-dashboard')
    cat = get_object_or_404(AssetCategory, pk=pk)
    if request.method == 'POST':
        cat.name = request.POST.get('name', '').strip()
        cat.icon = request.POST.get('icon', '').strip()
        cat.save()
        messages.success(request, f'Category "{cat.name}" updated.')
        return redirect('asset-category-list')
    return render(request, 'assets/category_form.html', {
        'category': cat,
        'title': 'Edit Category',
    })


# ── Asset register PDF ─────────────────────────────────────────────

@login_required(login_url='my-login')
def asset_register_pdf(request):
    if not _has_access(request.user):
        return redirect('admin-dashboard')

    branch = getattr(request, 'branch', None)
    qs = Asset.objects.select_related('category', 'branch').order_by('category__name', 'name')
    if branch:
        qs = qs.filter(branch=branch)

    restaurant = RestaurantSettings.load()
    currency = restaurant.currency_symbol

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        topMargin=15*mm, bottomMargin=12*mm,
        leftMargin=12*mm, rightMargin=12*mm,
    )
    styles = getSampleStyleSheet()
    elements = []

    # ── Header with logo ──
    if restaurant.logo:
        logo_path = os.path.join(django_settings.MEDIA_ROOT, str(restaurant.logo))
        if os.path.exists(logo_path):
            elements.append(Image(logo_path, width=45, height=45, hAlign='CENTER'))
            elements.append(Spacer(1, 2*mm))

    brand_style = ParagraphStyle(
        'Brand', parent=styles['Title'],
        fontSize=16, alignment=TA_CENTER, spaceAfter=1,
    )
    elements.append(Paragraph(restaurant.name, brand_style))

    if restaurant.tagline:
        elements.append(Paragraph(
            restaurant.tagline,
            ParagraphStyle('Tag', parent=styles['Normal'], fontSize=8, alignment=TA_CENTER, textColor=colors.HexColor('#6b7280')),
        ))
    if restaurant.phone:
        elements.append(Paragraph(
            f'Tel: {restaurant.phone}',
            ParagraphStyle('Ph', parent=styles['Normal'], fontSize=7, alignment=TA_CENTER, textColor=colors.HexColor('#9ca3af')),
        ))

    elements.append(Spacer(1, 5*mm))
    elements.append(Paragraph(
        'Asset Register',
        ParagraphStyle('Title2', parent=styles['Heading2'], fontSize=13, alignment=TA_CENTER, spaceAfter=2),
    ))

    date_str = timezone.localdate().strftime('%d %B %Y')
    time_str = timezone.localtime().strftime('%H:%M')
    branch_name = branch.name if branch else 'All Branches'
    elements.append(Paragraph(
        f'{branch_name} &mdash; Generated {date_str} at {time_str}',
        ParagraphStyle('Sub', parent=styles['Normal'], fontSize=9, alignment=TA_CENTER, textColor=colors.HexColor('#6b7280')),
    ))
    elements.append(Spacer(1, 7*mm))

    # ── Table ──
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=7, leading=9)
    header_data = ['', '#', 'Asset Name', 'Category', 'Serial #', 'Qty', 'Condition', 'Location', f'Value ({currency})', 'Notes']

    data = [header_data]
    total_value = Decimal('0')

    for i, asset in enumerate(qs, 1):
        # Image thumbnail
        img_cell = ''
        if asset.image:
            img_path = os.path.join(django_settings.MEDIA_ROOT, str(asset.image))
            if os.path.exists(img_path):
                try:
                    img_cell = Image(img_path, width=22, height=22)
                except Exception:
                    img_cell = ''

        total_value += asset.total_value
        data.append([
            img_cell,
            str(i),
            Paragraph(asset.name, cell_style),
            asset.category.name if asset.category else '—',
            asset.serial_number or '—',
            str(asset.quantity),
            asset.get_condition_display(),
            asset.location or '—',
            f'{asset.total_value:,.2f}',
            Paragraph(asset.notes[:60] if asset.notes else '', cell_style),
        ])

    # Totals row
    data.append(['', '', '', '', '', '', '', 'Total', f'{total_value:,.2f}', ''])

    col_widths = [28, 22, 120, 70, 65, 30, 55, 70, 65, 120]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1d2e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('ALIGN', (0, 0), (1, -1), 'CENTER'),
        ('ALIGN', (5, 0), (5, -1), 'CENTER'),
        ('ALIGN', (8, 0), (8, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dee2e6')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('ROWHEIGHT', (0, 1), (-1, -2), 28),
        # Totals row
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f3f4f6')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 8),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#1a1d2e')),
    ]))
    elements.append(t)

    elements.append(Spacer(1, 10*mm))
    elements.append(Paragraph(
        'Verified by: _____________________ &nbsp;&nbsp; Signature: _____________________ &nbsp;&nbsp; Date: ____________',
        styles['Normal'],
    ))

    doc.build(elements)
    buf.seek(0)

    response = HttpResponse(buf, content_type='application/pdf')
    filename = f'asset_register_{timezone.localdate().isoformat()}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
