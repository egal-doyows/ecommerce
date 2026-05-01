from django.shortcuts import render

from .utils import manager_required


@manager_required
def reports_index(request):
    """Landing page for the reports module."""
    return render(request, 'reports/index.html')
