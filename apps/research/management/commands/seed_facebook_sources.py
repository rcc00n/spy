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
