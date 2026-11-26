from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("configuration", "0005_systemconfig_temp_alert"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemconfig",
            name="excluded_aircraft",
            field=models.JSONField(blank=True, default=list, verbose_name="aeronaves que no generan alerta"),
        ),
    ]
