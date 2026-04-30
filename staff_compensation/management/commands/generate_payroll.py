from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Generate monthly payroll records for all salaried employees. Run on the 25th of each month.'

    def add_arguments(self, parser):
        parser.add_argument('--month', type=int, help='Month number (1-12). Defaults to current month.')
        parser.add_argument('--year', type=int, help='Year. Defaults to current year.')

    def handle(self, *args, **options):
        from staff_compensation.models import generate_payroll

        now = timezone.localdate()
        month = options['month'] or now.month
        year = options['year'] or now.year

        self.stdout.write(f'Generating payroll for {month}/{year}...')

        payroll = generate_payroll(year, month)

        if payroll:
            self.stdout.write(self.style.SUCCESS(
                f'{payroll.month_label} payroll generated — {payroll.employee_count} staff, net {payroll.total_net}.'
            ))
        else:
            self.stdout.write(self.style.WARNING(f'No new payroll to generate for {month}/{year}. Already exists or no salaried staff.'))
