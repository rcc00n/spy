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


def pcl_filter():
    return Q(mentions_pcl=True) | Q(pcl_in_comments=True)


def readable_text(text):
    # Presentation cleanup only. Keep the original saved evidence untouched.
    lines = text.strip().splitlines()
    chrome = {'·', '.', 'Follow', 'Following', 'Подписаться', 'Вы подписаны'}
    lines = [line for index, line in enumerate(lines) if not (index < 5 and line.strip() in chrome)]
    result = '\n'.join(lines)
    return re.sub(r'\s*(?:See less|See more|Показать меньше|Показать больше)\s*$', '', result).strip()


def decorate_posts(posts):
    sources = list(FacebookDiscoverySource.objects.exclude(kind='search'))
    for post in posts:
        post.source_name = 'Facebook'
        post.source_initial = 'f'
        path = urlsplit(post.url).path.strip('/').split('/')
        post.content_kind = 'Reel' if path[0] == 'reel' else 'Video' if 'videos' in path else 'Post'
        for source in sources:
            if post.url.startswith(source.target):
                post.source_name = source.name
                post.source_initial = source.name[:1].upper()
                if source.kind == 'group':
                    post.content_kind = 'Group'
                break
        else:
            if len(path) > 2 and path[1] == 'posts':
                post.source_name = path[0]
                post.source_initial = path[0][:1].upper()
        post.excerpt = readable_text(post.text) or 'Post text is unavailable. Saved comments may still be available.'
        post.has_gap = post.latest_work_status in ('partial', 'failed', 'blocked')
    return posts
