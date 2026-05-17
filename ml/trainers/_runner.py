"""
Shared run-and-record harness for trainers.

Every trainer follows the same shape: open a ModelRun row, do work, write
status + metric + error. The harness here centralises that so each trainer
only has to write the actual logic.
"""

import logging
import traceback
from contextlib import contextmanager

from django.utils import timezone

from ml.models import ModelRun


logger = logging.getLogger(__name__)


@contextmanager
def model_run(model_name):
    """Open a ModelRun row, yield it, close it on exit with success/failure."""
    run = ModelRun.objects.create(model_name=model_name, status='running')
    try:
        yield run
    except Exception as e:
        run.status = 'failed'
        run.error = f'{type(e).__name__}: {e}\n\n{traceback.format_exc()}'[:4000]
        run.finished_at = timezone.now()
        run.save()
        logger.exception('Trainer %s failed', model_name)
        raise
    else:
        if run.status == 'running':
            run.status = 'ok'
        run.finished_at = timezone.now()
        run.save()
        logger.info(
            'Trainer %s %s (used=%d, wrote=%d, %s=%s vs baseline %s)',
            model_name, run.status, run.rows_used, run.rows_written,
            run.metric_name or '-', run.metric_value, run.baseline_value,
        )
