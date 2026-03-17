import random
import string

from django.db import models
from django.contrib.auth.models import User


class WaiterCode(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='waiter_code')
    code = models.CharField(max_length=6, unique=True)
    is_active = models.BooleanField(default=True)
    photo = models.ImageField(upload_to='waiters/', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Login Code'
        verbose_name_plural = 'Login Codes'

    def __str__(self):
        return f"{self.user.username} - {self.code}"

    @staticmethod
    def generate_code():
        """Generate a unique 6-digit code."""
        while True:
            code = ''.join(random.choices(string.digits, k=6))
            if not WaiterCode.objects.filter(code=code).exists():
                return code
