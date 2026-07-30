"""Read models for the operator workspace; collection data remains unmodified."""
from urllib.parse import urlsplit
import re

from django.db.models import Count, Exists, OuterRef, Q, Subquery

from apps.research.models import (
    FacebookComment, FacebookDiscoverySource, FacebookPost, FacebookReview, FacebookThreadWork,
)


def evidence_posts(user):
    comments = FacebookComment.objects.filter(post_id=OuterRef('pk'))
    reviews = FacebookReview.objects.filter(post_id=OuterRef('pk'), user=user)
    latest_work = FacebookThreadWork.objects.filter(post_id=OuterRef('pk')).order_by('-updated_at')
    return FacebookPost.objects.filter(public_verified=True).annotate(
        comment_count=Count('comments', distinct=True),
        pcl_in_comments=Exists(comments.filter(mentions_pcl=True)),
        pcl_excerpt=Subquery(comments.filter(mentions_pcl=True).order_by('-first_seen_at', '-pk').values('text')[:1]),
        is_saved=Exists(reviews.filter(saved=True)),
        is_reviewed=Exists(reviews.filter(reviewed_at__isnull=False)),
        latest_coverage=Subquery(latest_work.values('coverage')[:1]),
        latest_work_status=Subquery(latest_work.values('status')[:1]),
    )
