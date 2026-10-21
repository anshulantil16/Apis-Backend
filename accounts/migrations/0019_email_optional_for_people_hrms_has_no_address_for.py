from django.db import migrations, models


class Migration(migrations.Migration):
    """Let a person exist in the directory with no email address.

    HRMS has no address for 53 of the people who work here, six of them the
    NA-coded staff. Until now they were skipped outright, so the portal's
    master data disagreed with the company's. They are imported instead;
    they cannot sign in, because a one-time code has nowhere to go, and the
    day HR fills the address in the next sync matches them on employee code
    and adds it.

    NULL, not '': MySQL allows any number of NULLs under a unique index but
    only one empty string, so '' would let exactly one person have no
    address and refuse every one after.

    The DEFAULT_AUTO_FIELD id changes Django also offers here are left out
    deliberately, as in 0016-0018 -- that drift is not being applied.
    """

    dependencies = [
        ('accounts', '0018_reporting_manager_name'),
    ]

    operations = [
        migrations.AlterField(
            model_name='portaluser',
            name='email',
            field=models.EmailField(blank=True, max_length=254, null=True, unique=True),
        ),
    ]
