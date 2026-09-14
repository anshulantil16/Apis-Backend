from django.db import migrations

# The real 23-role hiring-plan sheet, same data the frontend used to hold as
# a static VACANCY_LISTINGS array before this app existed — moved here so it
# survives a reload instead of resetting on every hard refresh.
SEED_ROWS = [
    {'function': 'Sales', 'department': 'SALES (AT)', 'title': 'KAE', 'grade': 'O4', 'location': 'Kochi', 'state': 'Kerala', 'reporting_manager': 'Gouri', 'type': 'Replacement', 'experience': '3-10 Yrs', 'education': 'Graduate'},
    {'function': 'Factory', 'department': 'Manufacturing', 'title': 'Plant Head', 'grade': 'C1', 'location': 'Roorkee', 'state': 'Uttarakhand', 'reporting_manager': 'MD', 'type': 'Replacement', 'experience': '15-25 Yrs', 'education': 'B.E/B.Tech/Masters'},
    {'function': 'Factory', 'department': 'Quality', 'title': 'Executive - Quality Assurance', 'grade': 'O4', 'location': 'Roorkee', 'state': 'Uttarakhand', 'reporting_manager': 'Sunil Tomar', 'type': 'Replacement', 'experience': '1-3 Yrs', 'education': 'B.Sc/M.Sc - Chemistry'},
    {'function': 'Sales', 'department': 'SALES (AT)', 'title': 'KAE ROTN', 'grade': 'O4', 'location': 'Coimbatore', 'state': 'Tamil Nadu', 'reporting_manager': 'Gouri', 'type': 'Replacement', 'experience': '3-10 Yrs', 'education': 'Graduate'},
    {'function': 'HO', 'department': 'Finance', 'title': 'DM', 'grade': 'M2', 'location': 'Delhi', 'state': 'Delhi', 'reporting_manager': 'Prateek Agarwal', 'type': 'New', 'experience': '2-5 Yrs', 'education': 'CA'},
    {'function': 'Factory', 'department': 'Production', 'title': 'Production Supervisor - Filling', 'grade': 'O1', 'location': 'Roorkee', 'state': 'Uttarakhand', 'reporting_manager': 'Rahul Dutt', 'type': 'New', 'experience': '2-7 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'AM - Project Execution', 'grade': 'O4', 'location': 'New Delhi', 'state': 'Delhi', 'reporting_manager': 'Arun Mishra', 'type': 'New', 'experience': '4-6 Yrs', 'education': 'MBA'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'ASE', 'grade': 'O4', 'location': 'Shimla', 'state': 'Himachal', 'reporting_manager': 'Mohinder', 'type': 'Replacement', 'experience': '2-6 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'ASE', 'grade': 'O4', 'location': 'Amritsar', 'state': 'Punjab', 'reporting_manager': 'Munish Kapoor', 'type': 'New', 'experience': '2-6 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'ASE', 'grade': 'O4', 'location': 'Ludhiana', 'state': 'Punjab', 'reporting_manager': 'Munish Kapoor', 'type': 'New', 'experience': '2-6 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'ASE', 'grade': 'O4', 'location': 'Bhatinda', 'state': 'Punjab', 'reporting_manager': 'Munish Kapoor', 'type': 'New', 'experience': '2-6 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'ASE', 'grade': 'O4', 'location': 'Jhansi', 'state': 'UP Central', 'reporting_manager': 'Sourabh', 'type': 'New', 'experience': '2-6 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'ASE', 'grade': 'O4', 'location': 'Agra', 'state': 'UP West', 'reporting_manager': 'Rajeev', 'type': 'Replacement', 'experience': '2-6 Yrs', 'education': 'Graduate'},
    {'function': 'HO', 'department': 'SCM', 'title': 'Executive - SCM', 'grade': 'O4', 'location': 'Delhi', 'state': 'Delhi', 'reporting_manager': 'Shri Prakash Chaubey', 'type': 'Replacement', 'experience': '1-7 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (AT)', 'title': 'KAE Delhi', 'grade': 'O4', 'location': 'New Delhi', 'state': 'Delhi', 'reporting_manager': 'Gouri', 'type': 'New', 'experience': '4-6 Yrs', 'education': 'Graduate'},
    {'function': 'HO', 'department': 'Digital Marketing', 'title': 'AM - Digital Marketing', 'grade': 'M1', 'location': 'Delhi', 'state': 'Delhi', 'reporting_manager': 'Nagesh Mishra', 'type': 'New', 'experience': '2-6 Yrs', 'education': 'MBA/Graduate'},
    {'function': 'HO', 'department': 'Engineering', 'title': 'AM/DM - Project Manager', 'grade': 'M1/M2', 'location': 'Gujarat / Delhi', 'state': 'Delhi', 'reporting_manager': 'Pradeep', 'type': 'New', 'experience': '4-10 Yrs', 'education': 'BE/B.Tech/M.Tech - Civil'},
    {'function': 'HO', 'department': 'Export', 'title': 'Dy. Manager', 'grade': 'M2', 'location': 'Delhi', 'state': 'Delhi', 'reporting_manager': 'Ershad Alam', 'type': 'New', 'experience': '4-10 Yrs', 'education': 'MBA - Operations / International Business'},
    {'function': 'HO', 'department': 'Export', 'title': 'Assistant Manager', 'grade': 'M2', 'location': 'Delhi', 'state': 'Delhi', 'reporting_manager': 'Ershad Alam', 'type': 'New', 'experience': '3-8 Yrs', 'education': 'MBA - Operations / International Business'},
    {'function': 'Sales', 'department': 'SALES (GT)', 'title': 'TSM', 'grade': 'M1', 'location': 'Kochi', 'state': 'Kerala', 'reporting_manager': 'Vasant D', 'type': 'Replacement', 'experience': '6-15 Yrs', 'education': 'Graduate'},
    {'function': 'Factory', 'department': 'Engineering', 'title': 'AM - Electrical Maintenance', 'grade': 'M1', 'location': 'Roorkee', 'state': 'Uttarakhand', 'reporting_manager': 'Sarvana', 'type': 'New', 'experience': '3-8 Yrs', 'education': 'ITI/Diploma - Electrical'},
    {'function': 'HO', 'department': 'Procurement', 'title': 'Sr. Executive', 'grade': 'O5', 'location': 'Delhi', 'state': 'Delhi', 'reporting_manager': 'Pradeep', 'type': 'Replacement', 'experience': '3-8 Yrs', 'education': 'Graduate'},
    {'function': 'Sales', 'department': 'SALES (AT)', 'title': 'KAE', 'grade': 'O4', 'location': 'Bangalore', 'state': 'Bangalore', 'reporting_manager': 'Gouri', 'type': 'Replacement', 'experience': '4-10 Yrs', 'education': 'Graduate'},
]


def seed(apps, schema_editor):
    Vacancy = apps.get_model('vacancies', 'Vacancy')
    # Idempotent: skip if the table's already populated, so this migration
    # can't duplicate rows if it's ever re-run against a live database.
    if Vacancy.objects.exists():
        return
    Vacancy.objects.bulk_create(Vacancy(status='Active', **row) for row in SEED_ROWS)


def unseed(apps, schema_editor):
    Vacancy = apps.get_model('vacancies', 'Vacancy')
    Vacancy.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vacancies', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
