from django.conf import settings
from django.db import models


class Branch(models.Model):
    name = models.CharField(
        max_length=150,
        help_text='Location name (e.g. Karen, Lavington). The restaurant name is prepended automatically.',
    )
    code = models.SlugField(max_length=30, unique=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='managed_branches',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'branches'

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        from menu.models import RestaurantSettings
        restaurant = RestaurantSettings.load()
        return f'{restaurant.name} - {self.name}'


class UserBranch(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='branch_assignments',
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name='staff',
    )
    is_primary = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'branch')
        ordering = ['-is_primary', 'branch__name']

    def __str__(self):
        return f'{self.user.username} @ {self.branch.name}'
