import secrets
import string

from django.db import models
from django.contrib.auth.models import User


class WaiterCode(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='waiter_code')
    code = models.CharField(max_length=8, unique=True)
    is_active = models.BooleanField(default=True)
    photo = models.ImageField(upload_to='waiters/', blank=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    CODE_LENGTH = 6
    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 15

    class Meta:
        verbose_name = 'Login Code'
        verbose_name_plural = 'Login Codes'

    def __str__(self):
        return f"{self.user.username} - {self.code}"

    @staticmethod
    def generate_code():
        """Generate a unique cryptographically secure code."""
        while True:
            code = ''.join(secrets.choice(string.digits) for _ in range(WaiterCode.CODE_LENGTH))
            if not WaiterCode.objects.filter(code=code).exists():
                return code

    def record_failed_attempt(self):
        from django.utils import timezone
        from datetime import timedelta
        self.failed_attempts += 1
        if self.failed_attempts >= self.MAX_FAILED_ATTEMPTS:
            self.locked_until = timezone.now() + timedelta(minutes=self.LOCKOUT_DURATION_MINUTES)
        self.save(update_fields=['failed_attempts', 'locked_until'])

    def reset_failed_attempts(self):
        if self.failed_attempts > 0:
            self.failed_attempts = 0
            self.locked_until = None
            self.save(update_fields=['failed_attempts', 'locked_until'])

    def is_locked(self):
        from django.utils import timezone
        if self.locked_until and self.locked_until > timezone.now():
            return True
        if self.locked_until:
            self.reset_failed_attempts()
        return False
