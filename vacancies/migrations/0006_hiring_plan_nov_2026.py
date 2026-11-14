"""The hiring plan as of November 2026, replacing the one seeded in 0002.

Every public vacancy still open is marked Closed rather than deleted: a
closed position stays on record and can be reopened from the Vacancies popup
in one click, whereas a deleted one is gone. Confidential vacancies are a
separate list and are left alone.

The sheet's budget column is deliberately not carried over - the dashboard
does not show salary budgets. It has no reporting-manager column either, so
that is blank except where the sheet named one.
"""
from django.db import migrations

NOTE = 'Hiring plan, November 2026.'

ROWS = [
    # function, department, title, grade, location, state, type, education, experience
    ('Factory', 'Manufacturing', 'Plant Head', 'C1', 'Roorkee', 'Uttarakhand', 'Replacement',
     'B.E./B.Mech/MBA/M.Tech/Post Graduate in Food', '15-25 Yrs'),
    ('HO', 'F&A', 'AM/DM - FR', 'M2', 'Delhi', 'Delhi', 'New', 'CA qualified', '2-5 Yrs'),
    ('Sales', 'SALES (GT)', 'ASE', 'O4', 'Shimla', 'Himachal', 'Replacement', 'Graduate', '2-6 Yrs'),
    ('Sales', 'SALES (GT)', 'ASE', 'O4', 'Bhatinda', 'Punjab', 'New', 'Graduate', '2-6 Yrs'),
    ('Sales', 'SALES (GT)', 'ASE', 'O4', 'Jhansi', 'UP Central', 'New', 'Graduate', '2-6 Yrs'),
    ('HO', 'Marketing', 'AM - Digital Marketing', 'M1', 'Delhi', 'Delhi', 'New', 'Graduate', '2-6 Yrs'),
    ('Factory', 'Engineering', 'Fitter', 'W4', 'Roorkee', 'Uttarakhand', 'New',
     'ITI/Diploma in relevant field', '3-15 Yrs'),
    ('Factory', 'Quality', 'Executive - Quality Assurance', 'O4', 'Roorkee', 'Uttarakhand', 'Replacement',
     'B.Sc/M.Sc/B.Tech - Chemistry/Food/Biotech/Microbiology', '1-3 Yrs'),
    ('Factory', 'Production', 'Production Supervisor - Filling', 'O1', 'Roorkee', 'Uttarakhand', 'Replacement',
     'ITI/Diploma in relevant field', '2-7 Yrs'),
    ('Factory', 'Engineering', 'AM - Mechanical Maintenance', 'M1', 'Roorkee', 'Uttarakhand', 'New',
     'B.E/B.Tech/Diploma - Mechanical', '5-15 Yrs'),
    ('Factory', 'Quality', 'AM - Quality', 'M1', 'Roorkee', 'Uttarakhand', 'New',
     'B.Sc/M.Sc/B.Tech - Chemistry/Food/Biotech/Microbiology', '4-10 Yrs'),
    ('Factory', 'Production', 'AM Production - JAM Plant', 'M1', 'Roorkee', 'Uttarakhand', 'New',
     'Graduate/Diploma', '4-10 Yrs'),
    # The sheet has "Pradeep" under HQ Location and "New" under State - the
    # reporting manager in the wrong cells. A Head Office role, so Delhi.
    ('HO', 'Project Management', 'AM/DM - Civil Project', 'M1/M2', 'Delhi', 'Delhi', 'New',
     'B.E/B.Tech - Civil', '7-15 Yrs', 'Pradeep'),
    ('Sales', 'Institutional Sales', 'AM/DM', 'M1', 'Delhi', 'Delhi', 'New', 'MBA', '7-15 Yrs'),
    ('Sales', 'SALES (GT)', 'ASM', 'M1', 'Kanpur', 'Uttar Pradesh', 'Replacement', 'Graduate', '7-20 Yrs'),
    ('Sales', 'SALES (GT)', 'ASM/TSM', 'M1', 'Shimla/Solan', 'Himachal', 'Replacement', 'Graduate', '7-20 Yrs'),
    ('Sales', 'SALES (GT)', 'ASE', 'O4', 'Kanpur', 'Uttar Pradesh', 'New', 'Graduate', '4-12 Yrs'),
    ('HO', 'F&A', 'AM - IA', 'M1', 'Delhi', 'Delhi', 'Replacement', 'Commerce Graduate', '5-15 Yrs'),
    ('Factory', 'Production', 'AM Production - Filling', 'M1', 'Roorkee', 'Uttarakhand', 'Replacement',
     'Graduate/Diploma in relevant field', '5-15 Yrs'),
    ('Factory', 'Engineering', 'Electrician', 'W4', 'Roorkee', 'Uttarakhand', 'New',
     'ITI/Diploma in relevant field', '2-12 Yrs'),
]


def apply_plan(apps, schema_editor):
    Vacancy = apps.get_model('vacancies', 'Vacancy')
    Vacancy.objects.filter(confidential=False, status='Active').update(status='Closed')
    Vacancy.objects.bulk_create([
        Vacancy(function=r[0], department=r[1], title=r[2], grade=r[3], location=r[4], state=r[5],
                type=r[6], education=r[7], experience=r[8],
                reporting_manager=r[9] if len(r) > 9 else '',
                status='Active', confidential=False,
                moderation_status='approved', review_note=NOTE)
        for r in ROWS])


def remove_plan(apps, schema_editor):
    """Takes the November rows away again. The positions it closed are not
    reopened: which of them were still open before is not recorded."""
    Vacancy = apps.get_model('vacancies', 'Vacancy')
    Vacancy.objects.filter(review_note=NOTE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vacancies', '0005_vacancy_confidential'),
    ]

    operations = [migrations.RunPython(apply_plan, remove_plan)]
