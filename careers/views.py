from django.shortcuts import render

from menu.models import RestaurantSettings

from .models import JobOpening


def careers_list(request):
    openings = JobOpening.objects.filter(is_open=True)
    return render(request, 'careers/list.html', {
        'settings': RestaurantSettings.objects.first(),
        'openings': openings,
    })
