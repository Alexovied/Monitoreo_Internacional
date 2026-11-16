from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("configuration", "0003_systemconfig_maintenance_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemconfig",
            name="guest_access",
            field=models.BooleanField(default=False, verbose_name="acceso de visitantes"),
        ),
    ]
