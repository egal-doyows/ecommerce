from django.db import migrations


SAMPLES = [
    {
        "group": "Choose a side",
        "is_required": True,
        "attach_to": "Grilled Chicken",
        "options": [
            ("French Fries", "0.00"),
            ("Mashed Potatoes", "0.00"),
            ("Side Salad", "0.00"),
            ("Coleslaw", "0.00"),
        ],
    },
    {
        "group": "Pick your sauce",
        "is_required": True,
        "attach_to": "Chicken Wings",
        "options": [
            ("BBQ", "0.00"),
            ("Garlic Aioli", "0.00"),
            ("Peri-Peri", "0.00"),
            ("Plain", "0.00"),
        ],
    },
    {
        "group": "Burger upgrades",
        "is_required": False,
        "attach_to": "Classic Burger",
        "options": [
            ("Extra Cheese", "50.00"),
            ("Bacon", "100.00"),
            ("Avocado", "75.00"),
        ],
    },
    {
        "group": "Steak doneness",
        "is_required": True,
        "attach_to": "Beef Steak",
        "options": [
            ("Rare", "0.00"),
            ("Medium-rare", "0.00"),
            ("Medium", "0.00"),
            ("Well-done", "0.00"),
        ],
    },
    {
        "group": "Ice cream flavour",
        "is_required": True,
        "attach_to": "Ice Cream",
        "options": [
            ("Vanilla", "0.00"),
            ("Chocolate", "0.00"),
            ("Strawberry", "0.00"),
        ],
    },
]


def seed_accompaniments(apps, schema_editor):
    MenuItem = apps.get_model("menu", "MenuItem")
    AccompanimentGroup = apps.get_model("menu", "AccompanimentGroup")
    AccompanimentOption = apps.get_model("menu", "AccompanimentOption")

    for sample in SAMPLES:
        group, _ = AccompanimentGroup.objects.get_or_create(
            name=sample["group"],
            defaults={"is_required": sample["is_required"]},
        )
        for label, price_delta in sample["options"]:
            AccompanimentOption.objects.get_or_create(
                group=group,
                label=label,
                defaults={"price_delta": price_delta},
            )
        item = MenuItem.objects.filter(title=sample["attach_to"]).first()
        if item is not None:
            item.accompaniment_groups.add(group)


def unseed_accompaniments(apps, schema_editor):
    AccompanimentGroup = apps.get_model("menu", "AccompanimentGroup")
    AccompanimentGroup.objects.filter(
        name__in=[s["group"] for s in SAMPLES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("menu", "0036_accompanimentgroup_orderitemoption_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_accompaniments, unseed_accompaniments),
    ]
