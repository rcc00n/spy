"""Transactional checkpoints and an idempotent corpus independent of task lifetime."""
import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.research.models import (
    FacebookComment, FacebookCommentRevision, FacebookPost, FacebookThreadWork,
    ResearchEvent, ResearchSource,
)
from apps.research.services.facebook_browser import canonical_post_url

LIMITS = {'comment_limit_reached', 'expansion_limit_reached', 'time_budget_reached', 'visible_comments_only', 'collecting', 'comments_not_observed', 'comment_links_not_matched'}


def enqueue_url(job, url, queries=()):
    url = canonical_post_url(url)
    if not url:
        return None
    post, _ = FacebookPost.objects.get_or_create(
        url_hash=hashlib.sha256(url.encode()).hexdigest(), defaults={'url': url})
    work, _ = FacebookThreadWork.objects.get_or_create(job=job, post=post)
    combined = list(dict.fromkeys(work.queries + list(queries)))
