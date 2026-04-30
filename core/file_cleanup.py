"""
Automatic cleanup of old files when a model's FileField/ImageField is
updated or the instance is deleted.

Usage (in any app's apps.py):

    from core.file_cleanup import register_file_cleanup

    class MyAppConfig(AppConfig):
        def ready(self):
            from .models import MyModel
            register_file_cleanup(MyModel, fields=['image', 'photo'])

That's it — old files are deleted from disk automatically.
"""

import os
import logging

from django.db.models.signals import pre_save, post_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _delete_file(field_file):
    """Safely delete a file from storage if it exists."""
    if not field_file:
        return
    try:
        storage = field_file.storage
        name = field_file.name
        if name and storage.exists(name):
            storage.delete(name)
            logger.debug("Deleted orphaned file: %s", name)
    except Exception:
        logger.warning("Failed to delete file: %s", field_file.name, exc_info=True)


def register_file_cleanup(model, fields):
    """
    Register pre_save and post_delete signals to clean up old files
    for the given model and field names.

    Args:
        model: Django model class
        fields: list of field names (str) that are FileField/ImageField
    """
    uid_prefix = f'{model._meta.label_lower}_file_cleanup'

    def on_pre_save(sender, instance, **kwargs):
        """Delete old file when the field value changes on an existing instance."""
        if not instance.pk:
            return
        try:
            old_instance = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            return
        for field_name in fields:
            old_file = getattr(old_instance, field_name, None)
            new_file = getattr(instance, field_name, None)
            if not old_file:
                continue
            # Compare file names — if changed (or cleared), delete old
            old_name = old_file.name if old_file else ''
            new_name = new_file.name if new_file else ''
            if old_name and old_name != new_name:
                _delete_file(old_file)

    def on_post_delete(sender, instance, **kwargs):
        """Delete files when the instance is deleted."""
        for field_name in fields:
            field_file = getattr(instance, field_name, None)
            if field_file:
                _delete_file(field_file)

    pre_save.connect(
        on_pre_save, sender=model,
        dispatch_uid=f'{uid_prefix}_pre_save',
    )
    post_delete.connect(
        on_post_delete, sender=model,
        dispatch_uid=f'{uid_prefix}_post_delete',
    )
