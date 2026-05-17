"""
Menu engineering / Boston matrix.

This is statistical, not ML. We compute it the same way regardless of
data volume — the gate just determines whether we mark it 'baseline'
(under-confidence banner) or 'ml' (full confidence).
"""

from django.db import transaction
from django.utils import timezone

from ml import fallbacks, gates
from ml.models import MenuClass
from ml.trainers._runner import model_run


WINDOW_DAYS = 28


def train():
    with model_run('menu_class') as run:
        ready, info = gates.menu_class_ready()
        run.rows_used = info['orders_in_window']
        rows = fallbacks._menu_class_compute(WINDOW_DAYS)
        source = 'ml' if ready else 'baseline'
        _replace_window(rows, source)
        run.rows_written = len(rows)
        if not ready:
            run.status = 'skipped'
            run.metric_name = 'baseline_only'
            run.error = (
                f"Only {info['orders_in_window']}/{info['need']} orders in the "
                f"{WINDOW_DAYS}-day window. Showing baseline classification."
            )


@transaction.atomic
def _replace_window(rows, source):
    if not rows:
        return
    window_start = rows[0]['window_start']
    window_end = rows[0]['window_end']
    MenuClass.objects.filter(
        window_start=window_start, window_end=window_end,
    ).delete()
    objs = [
        MenuClass(
            menu_item_id=r['menu_item_id'],
            classification=r['classification'],
            window_start=r['window_start'],
            window_end=r['window_end'],
            units_sold=r['units_sold'],
            revenue=r['revenue'],
            margin=r['margin'],
            margin_pct=r['margin_pct'],
            popularity_pct=r['popularity_pct'],
            source=source,
        )
        for r in rows
    ]
    MenuClass.objects.bulk_create(objs, batch_size=200)
