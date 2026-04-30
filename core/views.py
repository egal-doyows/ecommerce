"""Health check and utility views."""

from django.http import JsonResponse
from django.db import connection


def health_check(request):
    """Health check endpoint for monitoring and load balancers."""
    status = {'status': 'ok'}

    # Check database connectivity
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        status['database'] = 'ok'
    except Exception as e:
        status['database'] = 'error'
        status['status'] = 'degraded'

    http_status = 200 if status['status'] == 'ok' else 503
    return JsonResponse(status, status=http_status)
