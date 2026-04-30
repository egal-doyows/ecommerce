from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from account.models import WaiterCode


@override_settings(
    # django-ratelimit reads the default cache; isolate the test cache so
    # counters don't leak between tests / between test and dev.
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    RATELIMIT_USE_CACHE='default',
)
class WaiterLoginLockoutTests(TestCase):
    """F12 regression: brute-force protection on the 6-digit waiter code.

    The code namespace is small (1M possibilities). Without rate limiting,
    a single IP can walk the full space in minutes. The view stacks two
    @ratelimit windows; this test verifies the 5/m window kicks in.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='waiter1', password='unused-real-password',
        )
        self.waiter_code = WaiterCode.objects.create(
            user=self.user, code='123456',
        )
        self.client = Client()
        self.url = reverse('waiter-login')
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_waiter_login_locks_after_5_failed_attempts(self):
        # First 5 wrong codes — each gets a normal 200 with the "Invalid code"
        # form error. Rate limit not yet tripped.
        for i in range(5):
            resp = self.client.post(self.url, {'code': '999999'})
            self.assertEqual(
                resp.status_code, 200,
                f'Attempt {i + 1} should return 200, got {resp.status_code}',
            )
            self.assertNotIn(b'Forbidden', resp.content)

        # 6th attempt — even with the *correct* code — is blocked by
        # django-ratelimit. block=True returns Django's 403 Forbidden.
        resp = self.client.post(self.url, {'code': self.waiter_code.code})
        self.assertEqual(
            resp.status_code, 403,
            f'6th attempt within 60s should be 403 (rate-limited), got {resp.status_code}',
        )
