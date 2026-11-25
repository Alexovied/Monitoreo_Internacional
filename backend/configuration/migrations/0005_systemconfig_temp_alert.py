import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("configuration", "0004_systemconfig_guest_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemconfig",
            name="temp_alert_enabled",
            field=models.BooleanField(default=False, verbose_name="aviso de temperatura"),
        ),
        migrations.AddField(
            model_name="systemconfig",
            name="temp_alert_threshold_c",
            field=models.FloatField(
                default=75,
                validators=[
                    django.core.validators.MinValueValidator(40),
                    django.core.validators.MaxValueValidator(95),
                ],
                verbose_name="temperatura límite (°C)",
            ),
        ),
    ]
