from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("configuration", "0002_alter_systemconfig_poll_interval_s"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemconfig",
            name="maintenance_message",
            field=models.CharField(blank=True, default="", max_length=300, verbose_name="aviso de mantenimiento"),
        ),
    ]
