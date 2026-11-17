from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('transit', '0002_busroute_busstop'),
    ]

    operations = [
        migrations.CreateModel(
            name='BusService',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('service_id', models.CharField(max_length=40, unique=True)),
                ('weekdays', models.CharField(help_text='Seven 0/1 flags, Monday first: runs on that day of the week?', max_length=7)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
            ],
        ),
        migrations.CreateModel(
            name='BusServiceException',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('added', models.BooleanField(help_text='True: runs on this date although its weekly pattern says no. False: does not run.')),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='exceptions', to='transit.busservice')),
            ],
        ),
        migrations.CreateModel(
            name='BusTimetable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('headsign', models.CharField(blank=True, help_text='Where the bus is heading', max_length=150)),
                ('variants', models.JSONField()),
                ('by_service', models.JSONField()),
                ('route', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='timetables', to='transit.busroute')),
                ('stop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='timetables', to='transit.busstop')),
            ],
        ),
        migrations.AddConstraint(
            model_name='bustimetable',
            constraint=models.UniqueConstraint(fields=('stop', 'route'), name='one_timetable_per_stop_and_route'),
        ),
    ]
