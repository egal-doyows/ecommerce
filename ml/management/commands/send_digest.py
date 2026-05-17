"""
Send the ML daily digest email on demand.

Useful for previewing what managers will receive, or for re-sending after
fixing a recipient list. Honours --dry-run to print the rendered email
instead of sending it.

    python manage.py send_digest                 # actually emails managers
    python manage.py send_digest --dry-run       # prints text + recipient count
    python manage.py send_digest --base-url=https://beanandbite.co.ke
"""

import os

from django.core.management.base import BaseCommand

from ml.digest import _recipients, build_digest_context, send_daily_digest


class Command(BaseCommand):
    help = 'Send the ML daily digest email (or preview with --dry-run).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Render the digest to stdout without sending.',
        )
        parser.add_argument(
            '--base-url', default=os.environ.get('SITE_BASE_URL', ''),
            help='Absolute URL prefix for links in the email.',
        )

    def handle(self, *args, **opts):
        base_url = opts['base_url'] or None
        if opts['dry_run']:
            ctx = build_digest_context(base_url=base_url)
            recipients = _recipients()
            self.stdout.write(self.style.NOTICE('--- DRY RUN ---'))
            self.stdout.write(f'Would send to {len(recipients)} recipient(s): '
                              f'{", ".join(u.email for u in recipients) or "(none)"}')
            self.stdout.write(f'has_any: {ctx["has_any"]}')
            self.stdout.write(f'prep rows: {len(ctx["prep_rows"])}')
            self.stdout.write(f'reorders: {len(ctx["reorder_rows"])} '
                              f'(of {ctx["reorder_total"]} open)')
            self.stdout.write(f'exceptions: {len(ctx["exception_rows"])} '
                              f'(of {ctx["exception_total"]} in window)')
            self.stdout.write(f'top upsell: {ctx["top_upsell"] or "—"}')
            if not ctx['has_any']:
                self.stdout.write(self.style.WARNING(
                    'Nothing actionable today → real run would send 0 emails.'
                ))
            return
        sent = send_daily_digest(base_url=base_url)
        self.stdout.write(self.style.SUCCESS(f'Sent to {sent} recipient(s).'))
