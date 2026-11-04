from datetime import timedelta
from django.contrib import messages
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from administration.views import manager_required
from menu.cache import get_restaurant_settings

from .forms import CustomerFeedbackForm
from .models import CustomerFeedback


@never_cache
def submit_feedback(request):
    if request.method == 'POST':
        form = CustomerFeedbackForm(request.POST)
        if form.is_valid():
            form.save()
            request.session['feedback_submitted'] = True
            return redirect('feedback-thanks')
    else:
        form = CustomerFeedbackForm()
    return render(request, 'feedback/submit.html', {
        'form': form,
        'settings': get_restaurant_settings(),
    })


@never_cache
def feedback_thanks(request):
    if not request.session.pop('feedback_submitted', False):
        return redirect('feedback-submit')
    return render(request, 'feedback/thanks.html', {'settings': get_restaurant_settings()})


@manager_required
def feedback_dashboard(request):
    status = request.GET.get('status', '')
    rating = request.GET.get('rating', '')
    feedback = CustomerFeedback.objects.all()
    if status in dict(CustomerFeedback.STATUS_CHOICES):
        feedback = feedback.filter(status=status)
    if rating in {'1', '2', '3', '4', '5'}:
        feedback = feedback.filter(rating=int(rating))

    last_30_days = CustomerFeedback.objects.filter(created_at__gte=timezone.now() - timedelta(days=30))
    stats = last_30_days.aggregate(total=Count('id'), average=Avg('rating'))
    stats['average'] = round(stats['average'] or 0, 1)
    stats['positive'] = last_30_days.filter(rating__gte=4).count()
    stats['needs_attention'] = CustomerFeedback.objects.filter(status='new', rating__lte=2).count()

    return render(request, 'feedback/dashboard.html', {
        'feedback_list': feedback,
        'stats': stats,
        'selected_status': status,
        'selected_rating': rating,
        'feedback_url': request.build_absolute_uri(reverse('feedback-submit')),
    })


@manager_required
@require_POST
def update_feedback_status(request, pk):
    item = get_object_or_404(CustomerFeedback, pk=pk)
    status = request.POST.get('status')
    if status in dict(CustomerFeedback.STATUS_CHOICES):
        item.status = status
        item.staff_notes = request.POST.get('staff_notes', '').strip()
        item.save(update_fields=['status', 'staff_notes'])
        messages.success(request, 'Feedback updated.')
    return redirect(request.POST.get('next') or 'feedback-dashboard')

