"""Shared helpers for ReportLab PDF generation.

Centralised so every PDF surface uses the same logo loading rules and
sizing defaults. Purchasing/Receiving have their own elaborate headers
that pre-date this module; they keep their bespoke layouts. Newer PDFs
(expenses, waste, …) use `restaurant_logo_image()` for a small logo
above the document title.
"""
import os

from django.conf import settings as django_settings


def restaurant_logo_image(rs, width_mm=22, height_mm=22):
    """Return a ReportLab Image flowable for the configured logo, or None.

    `rs` is a `menu.models.RestaurantSettings` instance. If no logo is
    set or the file is missing on disk, returns None so the caller can
    fall back to a text-only header without raising.
    """
    if not getattr(rs, 'logo', None):
        return None

    candidate = os.path.join(django_settings.MEDIA_ROOT, rs.logo.name)
    if not os.path.isfile(candidate):
        return None

    # Local import keeps reportlab off the import path for views that
    # never render a PDF (admin pages, JSON APIs, etc.).
    from reportlab.platypus import Image
    from reportlab.lib.units import mm

    return Image(
        candidate,
        width=width_mm * mm,
        height=height_mm * mm,
        kind='proportional',
    )
