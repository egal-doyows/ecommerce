from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .models import CustomerFeedback


class FeedbackTests(TestCase):
    def test_customer_can_submit_feedback(self):
        response = self.client.post(reverse('feedback-submit'), {
            'rating': '5',
            'visit_type': 'dine_in',
            'liked_most': 'Warm service',
            'improvement': '',
            'name': '',
            'contact': '',
        })
        self.assertRedirects(response, reverse('feedback-thanks'), fetch_redirect_response=False)
        self.assertEqual(CustomerFeedback.objects.count(), 1)
        self.assertEqual(CustomerFeedback.objects.get().rating, 5)

    def test_rating_is_required(self):
        response = self.client.post(reverse('feedback-submit'), {'liked_most': 'Coffee'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required')
        self.assertFalse(CustomerFeedback.objects.exists())

    def test_dashboard_requires_manager(self):
        response = self.client.get(reverse('feedback-dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_manager_can_view_and_update_feedback(self):
        group = Group.objects.create(name='Manager')
        user = User.objects.create_user('manager', password='pw')
        user.groups.add(group)
        item = CustomerFeedback.objects.create(rating=2, improvement='Faster service')
        self.client.force_login(user)
        response = self.client.get(reverse('feedback-dashboard'))
        self.assertContains(response, 'Faster service')
        self.client.post(reverse('feedback-status', args=[item.pk]), {'status': 'resolved'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'resolved')
