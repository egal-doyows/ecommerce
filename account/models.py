import random
import string

from django.db import models
from django.contrib.auth.models import User


class WaiterCode(models.Model):
    CODE_LENGTH = 6

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='waiter_code')
    code = models.CharField(max_length=CODE_LENGTH, unique=True)
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
        """Generate a unique numeric code of CODE_LENGTH digits."""
        while True:
            code = ''.join(random.choices(string.digits, k=WaiterCode.CODE_LENGTH))
            if not WaiterCode.objects.filter(code=code).exists():
                return code
