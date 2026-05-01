from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class ReportsAccessTests(TestCase):
    """Confirm the manager_required gate works on the index."""

    @classmethod
    def setUpTestData(cls):
        cls.manager_group, _ = Group.objects.get_or_create(name='Manager')

        cls.manager = User.objects.create_user('manager', password='pw')
        cls.manager.groups.add(cls.manager_group)

        cls.cashier = User.objects.create_user('cashier', password='pw')
        cls.superuser = User.objects.create_superuser('boss', 'b@x.com', 'pw')

    def test_index_renders_for_manager(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 200)

    def test_index_renders_for_superuser(self):
        self.client.force_login(self.superuser)
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 200)

    def test_index_redirects_non_manager(self):
        self.client.force_login(self.cashier)
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 302)

    def test_index_redirects_anonymous(self):
        resp = self.client.get(reverse('reports-index'))
        self.assertEqual(resp.status_code, 302)


class ParseDateRangeTests(TestCase):
    """Period parsing — defaults, presets, and custom ranges."""

    def _make_request(self, **params):
        from django.test import RequestFactory
        return RequestFactory().get('/', params)

    def test_default_is_today(self):
        from django.utils import timezone
        from .utils import parse_date_range
        start, end, preset = parse_date_range(self._make_request())
        self.assertEqual(preset, 'today')
        self.assertEqual(start, timezone.localdate())
        self.assertEqual(end, timezone.localdate())

    def test_custom_swaps_inverted_range(self):
        from .utils import parse_date_range
        start, end, _ = parse_date_range(self._make_request(
            preset='custom', start='2026-05-10', end='2026-05-01',
        ))
        self.assertEqual(start.isoformat(), '2026-05-01')
        self.assertEqual(end.isoformat(), '2026-05-10')

    def test_month_spans_full_month(self):
        from .utils import parse_date_range
        start, end, _ = parse_date_range(self._make_request(preset='month'))
        self.assertEqual(start.day, 1)
        # End is the last day of the month → next day is day 1 of next month.
        from datetime import timedelta
        self.assertEqual((end + timedelta(days=1)).day, 1)
