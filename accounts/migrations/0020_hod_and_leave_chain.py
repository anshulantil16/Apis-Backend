from django.db import migrations, models


class Migration(migrations.Migration):
    """The HOD, the leave recommender and the leave approver.

    Pocket HRMS support named the columns on 28-10-2026: HOD, LEAVERE and
    LEAVEAPP. HOD was already being asked for and came back empty for
    everybody -- which on this API is what an unfilled column AND a wrong
    field name both look like, since an unrecognised name is dropped
    silently with a 200. Having the names confirmed is what tells the two
    apart.

    Each arrives the way the reporting manager does, one string holding
    "(HO0235)Pankaj Tripathi", so each is stored split: the code joins to
    that person's record, the name is what a screen shows.

    New columns only -- nothing existing is read or changed, and they stay
    empty until HR fills them in upstream.

    The DEFAULT_AUTO_FIELD id changes Django also offers here are left out
    deliberately, as in 0016-0019.
    """

    dependencies = [
        ('accounts', '0019_email_optional_for_people_hrms_has_no_address_for'),
    ]

    operations = [
        migrations.AddField(model_name='portaluser', name='hod_code',
                            field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name='portaluser', name='hod_name',
                            field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='portaluser', name='leave_recommender_code',
                            field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name='portaluser', name='leave_recommender_name',
                            field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='portaluser', name='leave_approver_code',
                            field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name='portaluser', name='leave_approver_name',
                            field=models.CharField(blank=True, max_length=200)),
    ]
