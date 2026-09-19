from django.db import models


class TimestampedModel(models.Model):
    """
    When a row was created and last saved.

    Abstract: Django creates no table for it and copies the two fields into
    the table of every model that inherits from it. A concrete base would get
    its own table instead, and every query would need a JOIN to reach them.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
