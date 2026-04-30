"""
Base models and managers for cross-cutting concerns:
- Soft deletion (SoftDeleteModel)
- Branch-scoped querysets (BranchScopedManager)
"""

from django.db import models
from django.utils import timezone


# ═══════════════════════════════════════════════════════════════════════
#  SOFT DELETE
# ═══════════════════════════════════════════════════════════════════════

class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that excludes soft-deleted objects by default."""

    def delete(self):
        return self.update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()

    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager):
    """Manager that returns only non-deleted objects by default."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).alive()

    def all_with_deleted(self):
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted(self):
        return SoftDeleteQuerySet(self.model, using=self._db).dead()


class SoftDeleteModel(models.Model):
    """
    Abstract model that provides soft-delete behaviour.

    Usage:
        class MyModel(SoftDeleteModel):
            name = models.CharField(max_length=100)

    - MyModel.objects.all() → excludes deleted
    - MyModel.objects.all_with_deleted() → includes deleted
    - MyModel.objects.deleted() → only deleted
    - instance.delete() → soft-deletes (sets is_deleted=True)
    - instance.hard_delete() → actual DB delete
    - instance.restore() → un-deletes
    """

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at'])


# ═══════════════════════════════════════════════════════════════════════
#  BRANCH-SCOPED MANAGER
# ═══════════════════════════════════════════════════════════════════════

class BranchScopedQuerySet(models.QuerySet):
    """QuerySet with helper to filter by branch."""

    def for_branch(self, branch):
        if branch is None:
            return self
        return self.filter(branch=branch)


class BranchScopedManager(models.Manager):
    """Manager that provides .for_branch(branch) shortcut."""

    def get_queryset(self):
        return BranchScopedQuerySet(self.model, using=self._db)

    def for_branch(self, branch):
        return self.get_queryset().for_branch(branch)


class BranchScopedSoftDeleteManager(models.Manager):
    """Combined soft-delete + branch-scoped manager."""

    def get_queryset(self):
        return BranchScopedQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def all_with_deleted(self):
        return BranchScopedQuerySet(self.model, using=self._db)

    def deleted(self):
        return BranchScopedQuerySet(self.model, using=self._db).filter(is_deleted=True)

    def for_branch(self, branch):
        return self.get_queryset().for_branch(branch)


# ═══════════════════════════════════════════════════════════════════════
#  FILE UPLOAD VALIDATORS
# ═══════════════════════════════════════════════════════════════════════

from django.core.exceptions import ValidationError


def validate_file_size(value):
    """Reject files larger than 10 MB."""
    max_size = 10 * 1024 * 1024  # 10 MB
    if value.size > max_size:
        raise ValidationError(f'File size must not exceed 10 MB. Got {value.size / (1024*1024):.1f} MB.')


ALLOWED_DOCUMENT_EXTENSIONS = [
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',
    '.jpg', '.jpeg', '.png', '.gif', '.webp',
    '.txt', '.csv',
]


def validate_file_extension(value):
    """Restrict uploaded files to safe document types."""
    import os
    ext = os.path.splitext(value.name)[1].lower()
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError(
            f'Unsupported file type: {ext}. '
            f'Allowed: {", ".join(ALLOWED_DOCUMENT_EXTENSIONS)}'
        )
