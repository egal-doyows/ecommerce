from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class JobOpening(models.Model):
    EMPLOYMENT_CHOICES = [
        ('full_time', 'Full-time'),
        ('part_time', 'Part-time'),
        ('contract', 'Contract'),
        ('internship', 'Internship'),
        ('casual', 'Casual'),
    ]

    title = models.CharField(max_length=120, help_text='e.g. "Barista", "Sous Chef"')
    slug = models.SlugField(max_length=140, unique=True, blank=True)

    employment_type = models.CharField(
        max_length=20, choices=EMPLOYMENT_CHOICES, default='full_time',
    )
    location = models.CharField(
        max_length=120, blank=True,
        help_text='e.g. "Bean & Bite, Kilimani". Leave blank if obvious.',
    )

    summary = models.CharField(
        max_length=240, blank=True,
        help_text='One-line teaser shown on the careers page card.',
    )
    description = models.TextField(
        help_text='What the role is and a sense of the day-to-day.',
    )
    requirements = models.TextField(
        blank=True,
        help_text='Skills, experience, or attitude you want. One per line is fine.',
    )
    how_to_apply = models.TextField(
        help_text=(
            'Tell candidates exactly how to apply — e.g. '
            '"Email your CV to careers@beanandbite.co.ke" or '
            '"Drop your CV at the counter, ask for the manager".'
        ),
    )

    is_open = models.BooleanField(
        default=True,
        help_text='Untick to hide this opening from the public page without deleting it.',
    )
    posted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-posted_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:140] or 'opening'
            slug = base
            i = 2
            while JobOpening.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                suffix = f'-{i}'
                slug = f'{base[:140 - len(suffix)]}{suffix}'
                i += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('careers-list') + f'#job-{self.slug}'
