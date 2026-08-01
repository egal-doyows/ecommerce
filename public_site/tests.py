from django.test import TestCase
from django.urls import reverse

from menu.models import Category, MenuItem


class PublicSiteTests(TestCase):
    def test_menu_excludes_unavailable_items(self):
        category = Category.objects.create(name='Test Coffee', slug='test-coffee')
        MenuItem.objects.create(
            category=category, title='Test Espresso', slug='test-espresso', price='250', is_available=True,
        )
        MenuItem.objects.create(
            category=category, title='Test Hidden', slug='test-hidden', price='250', is_available=False,
        )

        response = self.client.get(reverse('public-menu'))

        self.assertContains(response, 'Test Espresso')
        self.assertNotContains(response, 'Test Hidden')

    def test_robots_disallows_back_office(self):
        response = self.client.get(reverse('public-robots-txt'))

        self.assertContains(response, 'Disallow: /restpos/')
        self.assertContains(response, 'Sitemap: http://testserver/sitemap.xml')
