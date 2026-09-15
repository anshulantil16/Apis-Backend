"""Seed the noticeboard with what the dashboard already showed.

These two lists lived in the frontend as hard-coded arrays. Moving them into
the database only helps if the move is invisible to the company, so the
circular is transcribed here verbatim: the same ten zones in the same order,
the same thirteen days each, the same names and National/State marks as the
signed 2026 list (public/Policies/Holiday List- 2026 revised.pdf).

Announcements are the exception. The two rows in the frontend were openly
labelled sample data — placeholder text about a helpdesk maintenance window
that has long passed. Seeding those would put fiction on the dashboard under
the company's name, so the card starts empty and says so until HR writes a
real notice.
"""
from django.db import migrations


ZONES = [
    ('north', 'Delhi (NCR) & North Zone', 0),
    ('uttarakhand', 'Plant / Uttarakhand', 1),
    ('maharashtra', 'Maharashtra / Goa', 2),
    ('tamilnadu', 'Tamil Nadu', 3),
    ('south', 'Andhra Pradesh / Telangana / Karnataka', 4),
    ('kerala', 'Kerala', 5),
    ('jharkhand', 'Jharkhand', 6),
    ('bihar', 'Bihar', 7),
    ('eastzone', 'West Bengal / Assam', 8),
    ('westzone', 'Gujarat / Rajasthan', 9),
]

# zone key -> [(date, name, type), ...]
HOLIDAYS = {
    'north': [
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-08-28', 'Rakshabandhan', 'State'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-20', 'Dussehra / Vijayadasami', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-09', 'Govardhan Pooja', 'State'),
        ('2026-11-10', 'Diwali Holiday', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'uttarakhand': [
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-11', 'Shivratri', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-08-28', 'Rakshabandhan', 'State'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-20', 'Dussehra', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-09', 'Govardhan Pooja', 'State'),
        ('2026-11-10', 'Diwali Holiday', 'State'),
    ],
    'maharashtra': [
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-09-14', 'Ganesh Chaturthi', 'State'),
        ('2026-09-25', 'Anant Chaturdashi', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-20', 'Dussehra / Vijayadasami', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-09', 'Govardhan Pooja', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'tamilnadu': [
        ('2026-01-14', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-15', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-16', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-04-14', 'Tamil New Year', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-09-14', 'Ganesh Chaturthi', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-20', 'Ayutha Puja / Dussehra', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-09', 'Govardhan Pooja', 'State'),
    ],
    'south': [
        ('2026-01-14', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-15', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-19', 'Ugadi / Cheti Chand', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-09-14', 'Ganesh Chaturthi', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-21', 'Vijayadasami', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'kerala': [
        ('2026-01-15', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-04-15', 'Bihu', 'State'),
        ('2026-05-01', 'Labour / May Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-08-26', 'Onam', 'State'),
        ('2026-09-14', 'Ganesh Chaturthi', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-21', 'Vijayadasami', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'jharkhand': [
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-05-27', 'Eid-ul-Zuha (Bakrid)', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-19', 'Ashtami', 'State'),
        ('2026-10-20', 'Dussehra', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-09', 'Govardhan Pooja', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'bihar': [
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-03-21', 'Eid-ul-Fitr (Ramzan)', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-05-27', 'Eid-ul-Zuha (Bakrid)', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-08-26', 'Milad-un-Nabi', 'State'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-19', 'Ashtami', 'State'),
        ('2026-10-20', 'Dussehra', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'eastzone': [
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-04-15', 'Bengali New Year Day', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-17', 'Saptami', 'State'),
        ('2026-10-19', 'Navami', 'State'),
        ('2026-10-20', 'Dussehra', 'State'),
        ('2026-10-21', 'Durga Idol Immersion Day', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-11', 'Bhai Dooj', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
    'westzone': [
        ('2026-01-14', 'Makar Sankranti / Pongal', 'State'),
        ('2026-01-26', 'Republic Day', 'National'),
        ('2026-03-04', 'Holi', 'State'),
        ('2026-05-01', 'Buddha Purnima / Labour Day', 'State'),
        ('2026-08-15', 'Independence Day', 'National'),
        ('2026-08-28', 'Rakshabandhan', 'State'),
        ('2026-09-04', 'Janmashtami', 'State'),
        ('2026-10-02', 'Mahatma Gandhi Jayanti', 'National'),
        ('2026-10-20', 'Dussehra / Vijayadasami', 'State'),
        ('2026-11-08', 'Deepawali', 'State'),
        ('2026-11-09', 'Govardhan Pooja', 'State'),
        ('2026-11-10', 'Diwali Holiday', 'State'),
        ('2026-12-25', 'Christmas', 'National'),
    ],
}


def seed(apps, schema_editor):
    Zone = apps.get_model('noticeboard', 'HolidayZone')
    Holiday = apps.get_model('noticeboard', 'Holiday')

    for key, label, order in ZONES:
        zone, _ = Zone.objects.get_or_create(
            key=key, defaults={'label': label, 'sort_order': order})
        for date, name, kind in HOLIDAYS.get(key, []):
            Holiday.objects.get_or_create(
                zone=zone, date=date, name=name, defaults={'type': kind})


def unseed(apps, schema_editor):
    apps.get_model('noticeboard', 'HolidayZone').objects.filter(
        key__in=[z[0] for z in ZONES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0001_initial'),
    ]

    operations = [migrations.RunPython(seed, unseed)]
