from django.shortcuts import render

from menu.cache import get_restaurant_settings

from .models import JobOpening


def careers_list(request):
    openings = JobOpening.objects.filter(is_open=True)
    return render(request, 'careers/list.html', {
        'settings': get_restaurant_settings(),
        'openings': openings,
    })
