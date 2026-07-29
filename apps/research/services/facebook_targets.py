"""Strict source URLs and public-group header verification."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

RESERVED = {'me','friends','login','logout','checkpoint','search','watch','reel','reels',
            'marketplace','settings','home','notifications','gaming','stories','messages',
            'ads','help','policies','share','privacy','privacycenter','groups','pages','events'}
GROUP_PUBLIC = re.compile(r'^(?:Public group|Общедоступная группа|Открытая группа)(?:\s*[·•]|\s*$)', re.I | re.M)
GROUP_PRIVATE = re.compile(r'^(?:Private group|Закрытая группа|Частная группа)(?:\s*[·•]|\s*$)', re.I | re.M)


def canonical_source_url(value, kind=None):
