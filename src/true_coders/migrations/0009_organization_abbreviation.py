# -*- coding: utf-8 -*-
# Generated by Django 1.11.10 on 2018-02-04 18:42


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('true_coders', '0008_remove_team_slug'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='abbreviation',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]