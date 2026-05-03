from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticPagesSitemap(Sitemap):
    """Public marketing pages — small, hand-curated set."""
    changefreq = 'weekly'
    protocol = 'https'

    def items(self):
        return [
            ('public-home',     1.0),
            ('public-menu',     0.9),
            ('public-contact',  0.7),
            ('careers-list',    0.6),
        ]

    def location(self, item):
        url_name, _ = item
        return reverse(url_name)

    def priority(self, item):
        return item[1]


sitemaps = {
    'pages': StaticPagesSitemap,
}
