from django.db import migrations


def seed_default_branch(apps, schema_editor):
    Branch = apps.get_model('branches', 'Branch')
    UserBranch = apps.get_model('branches', 'UserBranch')
    User = apps.get_model('auth', 'User')

    # Try to use the restaurant name
    try:
        RestaurantSettings = apps.get_model('menu', 'RestaurantSettings')
        settings = RestaurantSettings.objects.first()
        name = settings.name if settings else 'Main Branch'
    except Exception:
        name = 'Main Branch'

    branch, _ = Branch.objects.get_or_create(
        code='main',
        defaults={'name': name, 'is_active': True},
    )

    # Assign all existing users to this branch
    for user in User.objects.all():
        UserBranch.objects.get_or_create(
            user=user, branch=branch,
            defaults={'is_primary': True},
        )

    # Assign all existing data to this branch
    # menu models
    for model_name in ['InventoryItem', 'Table', 'Shift', 'Order']:
        try:
            Model = apps.get_model('menu', model_name)
            Model.objects.filter(branch__isnull=True).update(branch=branch)
        except Exception:
            pass

    # administration models
    for model_name in ['Account', 'Transaction']:
        try:
            Model = apps.get_model('administration', model_name)
            Model.objects.filter(branch__isnull=True).update(branch=branch)
        except Exception:
            pass

    # Other app models
    model_map = {
        'waste': ['WasteLog'],
        'expenses': ['Expense'],
        'purchasing': ['PurchaseOrder'],
        'receiving': ['GoodsReceipt'],
        'debtor': ['Debtor'],
        'supplier': ['SupplierTransaction'],
        'staff_compensation': ['PaymentRecord'],
        'hr': ['Employee'],
    }
    for app_label, models in model_map.items():
        for model_name in models:
            try:
                Model = apps.get_model(app_label, model_name)
                Model.objects.filter(branch__isnull=True).update(branch=branch)
            except Exception:
                pass


def reverse_seed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('branches', '0001_initial'),
        ('menu', '0016_inventoryitem_branch_order_branch_shift_branch_and_more'),
        ('administration', '0002_account_branch_transaction_branch_and_more'),
        ('waste', '0002_wastelog_branch'),
        ('expenses', '0004_expense_branch'),
        ('purchasing', '0004_purchaseorder_branch'),
        ('receiving', '0002_goodsreceipt_branch'),
        ('debtor', '0003_debtor_branch'),
        ('supplier', '0003_suppliertransaction_branch'),
        ('staff_compensation', '0007_paymentrecord_branch'),
        ('hr', '0004_employee_branch'),
    ]

    operations = [
        migrations.RunPython(seed_default_branch, reverse_seed),
    ]
