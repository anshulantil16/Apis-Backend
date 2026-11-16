"""Market Watch: what honey and dates sell for today, APIS against its competitors.

Every price here was read off a live listing at the moment in `fetched_at`.
Nothing is estimated, carried forward or filled in: a product that could not
be found on a given day simply has no row for that day. That is what makes
the history worth reading - a trend line drawn through guesses is a guess.
"""
from django.db import models


class PriceSnapshot(models.Model):
    """One product, as one store listed it, at one moment."""

    fetched_at = models.DateTimeField(db_index=True)
    day = models.DateField(db_index=True)          # IST calendar day, for daily trends
    source = models.CharField(max_length=40)        # 'Flipkart'
    # Which product this is a price for - 'honey' or 'dates' (see
    # sources.PRODUCTS). Rows from before there was more than one are honey.
    product = models.CharField(max_length=20, default='honey', db_index=True)
    brand = models.CharField(max_length=60, db_index=True)
    title = models.CharField(max_length=400)
    product_id = models.CharField(max_length=80)    # the store's own id
    pack_grams = models.PositiveIntegerField()     # the whole listing: 2 x 225 g is 450
    pack_label = models.CharField(max_length=40, blank=True)
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    url = models.URLField(max_length=600)
    image = models.URLField(max_length=600, blank=True)
    in_stock = models.BooleanField(default=True)

    class Meta:
        ordering = ['-fetched_at', 'brand']
        indexes = [models.Index(fields=['day', 'brand', 'pack_grams'])]

    def __str__(self):
        return f'{self.day} {self.brand} {self.pack_grams}g Rs.{self.price}'

    @property
    def per_100g(self):
        return round(float(self.price) * 100 / self.pack_grams, 2) if self.pack_grams else None
