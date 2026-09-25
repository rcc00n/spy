from django.core.management.base import BaseCommand
from apps.research.models import FacebookDiscoverySource

PAGES = [
    ('PCL Construction','PCLconstruction'), ('Edmonton Oilers','Oilers.NHL'),
    ('Rogers Place','RogersPlace'), ('Global Edmonton','GlobalEdmonton'),
    ('Edmonton Journal','edmontonjournal'),
]
GROUPS = [
    ('Edmonton Oilers Super Fans','1037777950035874'),
    ('Edmonton Oilers fans','1536541873596993'),
    ('Everything Oilers','886607998976287'),
]
QUERIES = ['PCL Oilers','PCL Construction Edmonton','PCL Constructors Rogers Place',
           'Oilers Formenton','Oilers Bowman','Oilers sponsors boycott',
           'Rogers Place boycott','Rogers Place construction']


class Command(BaseCommand):
    help = 'Add the PCL/Oilers discovery watchlist without changing existing source settings or starting jobs.'

    def handle(self, **options):
        created=0
        for name,slug in PAGES:
            _,new=FacebookDiscoverySource.objects.get_or_create(kind='page',target=f'https://www.facebook.com/{slug}/',
                defaults={'name':name,'notes':'Page opened and checked on 2026-09-24. Direct feed discovery; individual posts must pass public visibility checks.'})
            created+=new
        for name,ident in GROUPS:
            _,new=FacebookDiscoverySource.objects.get_or_create(kind='group',target=f'https://www.facebook.com/groups/{ident}/',
                defaults={'name':name,'notes':'Public group header observed on 2026-09-24. Rechecked before each discovery and post read. No joining required.'})
            created+=new
        for phrase in QUERIES:
            _,new=FacebookDiscoverySource.objects.get_or_create(kind='search',target=phrase,
                defaults={'name':phrase,'notes':'Discovery phrase, not an allegation or a relevance guarantee. Search can return unrelated posts.'})
            created+=new
        self.stdout.write(f'Added {created} sources. Existing settings unchanged; no scans started.')
