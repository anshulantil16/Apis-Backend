"""python manage.py marketwatch_snapshot

Fetches and saves today's prices for every product Market Watch compares
(sources.PRODUCTS), so every day has a report even on a day nobody opens
Market Watch. Run it once a day from the server's scheduler.
"""
from django.core.management.base import BaseCommand

from marketwatch.sources import PRODUCTS
from marketwatch.views import _fetch_live


class Command(BaseCommand):
    help = "Fetch and save today's Market Watch prices, every product."

    def handle(self, *args, **opts):
        for product, spec in PRODUCTS.items():
            live = _fetch_live(product)
            self.stdout.write(f"{spec['label']}: saved {len(live['listings'])} listings at {live['fetched_at']}.")
