"""Everything that was already on the dashboard stays on the dashboard.

The moderation column added in 0003 defaults to 'pending', which is right for
anything submitted from here on — but wrong for the rows that already exist.
The seeded hiring plan and anything HR added before this was built were live
and visible; leaving them at the default would silently empty the Vacancies
popup the moment this deploys.

They are marked approved with no reviewer, which is the honest record: nobody
reviewed them, they predate the gate.
"""
from django.db import migrations


def publish_existing(apps, schema_editor):
    Vacancy = apps.get_model('vacancies', 'Vacancy')
    Vacancy.objects.filter(moderation_status='pending').update(
        moderation_status='approved',
        review_note='Published before the approval step existed.')


def unpublish(apps, schema_editor):
    """Reversing puts them back to pending rather than guessing which rows
    this migration touched — the note above is the marker."""
    Vacancy = apps.get_model('vacancies', 'Vacancy')
    Vacancy.objects.filter(
        review_note='Published before the approval step existed.').update(
            moderation_status='pending', review_note='')


class Migration(migrations.Migration):

    dependencies = [
        ('vacancies', '0003_alter_vacancy_options_vacancy_moderation_status_and_more'),
    ]

    operations = [migrations.RunPython(publish_existing, unpublish)]
