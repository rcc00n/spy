import time

from django.test import SimpleTestCase
from playwright.sync_api import sync_playwright

from apps.research.services.facebook_browser import collect_thread


class DiscussionDomTests(SimpleTestCase):
    def test_removing_hidden_dialog_does_not_break_public_thread_or_mix_background(self):
        url = 'https://www.facebook.com/example/posts/123/'
        html = '''<html><body>
