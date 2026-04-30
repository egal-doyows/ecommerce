from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def clear_old_payroll(apps, schema_editor):
    """Delete all old per-employee payroll records before restructuring."""
    Payroll = apps.get_model('staff_compensation', 'Payroll')
    Payroll.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('staff_compensation', '0010_add_payroll_model'),
        ('branches', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Step 1: Clear old data
        migrations.RunPython(clear_old_payroll, migrations.RunPython.noop),

        # Step 2: Remove old unique_together
        migrations.AlterUniqueTogether(
            name='payroll',
            unique_together=set(),
        ),

        # Step 3: Remove old fields
        migrations.RemoveField(model_name='payroll', name='advance_deductions'),
        migrations.RemoveField(model_name='payroll', name='approved_at'),
        migrations.RemoveField(model_name='payroll', name='approved_by'),
        migrations.RemoveField(model_name='payroll', name='basic_salary'),
        migrations.RemoveField(model_name='payroll', name='commission'),
        migrations.RemoveField(model_name='payroll', name='employee'),
        migrations.RemoveField(model_name='payroll', name='gross_pay'),
        migrations.RemoveField(model_name='payroll', name='net_pay'),
        migrations.RemoveField(model_name='payroll', name='other_deductions'),
        migrations.RemoveField(model_name='payroll', name='status'),

        # Step 4: Add new fields
        migrations.AddField(
            model_name='payroll',
            name='employee_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='payroll',
            name='generated_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='generated_payrolls', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='payroll',
            name='total_advances',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='payroll',
            name='total_basic',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='payroll',
            name='total_commission',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='payroll',
            name='total_gross',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='payroll',
            name='total_net',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),

        # Step 5: Alter existing fields
        migrations.AlterField(
            model_name='payroll',
            name='branch',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='payrolls', to='branches.branch',
            ),
        ),
        migrations.AlterField(
            model_name='payroll',
            name='month_label',
            field=models.CharField(blank=True, help_text="e.g. 'March 2026'", max_length=30),
        ),

        # Step 6: New unique_together
        migrations.AlterUniqueTogether(
            name='payroll',
            unique_together={('branch', 'month', 'year')},
        ),

        # Step 7: Update Meta
        migrations.AlterModelOptions(
            name='payroll',
            options={'ordering': ['-year', '-month'], 'verbose_name': 'Payroll', 'verbose_name_plural': 'Payroll Records'},
        ),

        # Step 8: Create PayrollLine
        migrations.CreateModel(
            name='PayrollLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('basic_salary', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('commission', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('gross_pay', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('advance_deductions', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('other_deductions', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('net_pay', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('branch', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to='branches.branch',
                )),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payroll_lines', to=settings.AUTH_USER_MODEL,
                )),
                ('payroll', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='lines', to='staff_compensation.payroll',
                )),
            ],
            options={
                'ordering': ['employee__username'],
                'unique_together': {('payroll', 'employee')},
            },
        ),
    ]
