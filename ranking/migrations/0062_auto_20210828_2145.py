# Generated by Django 3.1.12 on 2021-08-28 21:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ranking', '0061_auto_20210828_2132'),
    ]

    operations = [
        migrations.AlterField(
            model_name='module',
            name='long_contest_delay',
            field=models.DurationField(blank=True, default='02:00:00', null=True),
        ),
        migrations.AlterField(
            model_name='module',
            name='long_contest_idle',
            field=models.DurationField(blank=True, default='06:00:00', null=True),
        ),
    ]
