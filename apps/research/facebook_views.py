"""Focused, staff-only evidence workspace and reading controls."""
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.research.forms import FacebookMonitorForm
from apps.research.models import (
    FacebookComment, FacebookDiscoverySource, FacebookMonitor, FacebookPost, FacebookReview,
)
from apps.research.services.facebook_monitor import ACTIVE, configure_monitor
from apps.research.services.facebook_workspace import decorate_posts, evidence_posts, pcl_filter


def operator_view(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden('Operator account required.')
        return view(request, *args, **kwargs)
    return wrapper


def monitor_context():
    monitor = FacebookMonitor.objects.filter(pk=1).first()
    active = monitor.jobs.filter(status__in=ACTIVE).first() if monitor else None
    last_post = FacebookPost.objects.filter(public_verified=True).order_by('-last_seen_at').first()
    return {'monitor': monitor, 'active_cycle': active, 'last_observed_at': last_post.last_seen_at if last_post else None,
            'posts_read': FacebookPost.objects.filter(public_verified=True).exclude(text='').count(),
            'posts_found': FacebookPost.objects.count(),
            'comments_read': FacebookComment.objects.count(),
            'active_sources': FacebookDiscoverySource.objects.filter(enabled=True).count()}


@operator_view
def workspace(request):
    # Preserve previously bookmarked POST controls without showing settings in the inbox.
    if request.method == 'POST':
        return settings(request)
    posts = evidence_posts(request.user)
    scope = request.GET.get('scope', 'pcl')
    if scope not in ('pcl', 'all', 'saved'):
        scope = 'pcl'
    query = request.GET.get('q', '').strip()[:200]
    period = request.GET.get('period', 'all')
    if period not in ('all', '1', '7', '30'):
        period = 'all'
    unread = request.GET.get('unread') == '1'
    totals = {'pcl': posts.filter(pcl_filter()).count(), 'all': posts.count(), 'saved': posts.filter(is_saved=True).count()}
    if scope == 'pcl':
        posts = posts.filter(pcl_filter())
    elif scope == 'saved':
        posts = posts.filter(is_saved=True)
    if query:
        matching_comments = FacebookComment.objects.filter(post_id=OuterRef('pk'), text__icontains=query)
        posts = posts.annotate(matches_comment=Exists(matching_comments)).filter(Q(text__icontains=query) | Q(matches_comment=True))
    if period != 'all':
        posts = posts.filter(first_seen_at__gte=timezone.now()-timedelta(days=int(period)))
    if unread:
        posts = posts.filter(is_reviewed=False)
    ordering = ('-pcl_in_comments', '-first_seen_at', '-pk') if scope == 'pcl' else ('-first_seen_at', '-pk')
    page = Paginator(posts.order_by(*ordering), 20).get_page(request.GET.get('page'))
    decorate_posts(page.object_list)
    params = request.GET.copy(); params.pop('page', None)
    tabs = []
    for value, label in [('pcl', 'PCL mentions'), ('all', 'All posts'), ('saved', 'Saved')]:
        tab_params = params.copy(); tab_params['scope'] = value
        tabs.append({'value': value, 'label': label, 'count': totals[value], 'url': '?' + tab_params.urlencode()})
    return render(request, 'research/facebook_workspace.html', {
        **monitor_context(), 'fb_nav': 'findings', 'page_obj': page,
        'scope': scope, 'query': query, 'period': period, 'unread': unread, 'tabs': tabs,
        'pagination_query': params.urlencode(),
    })


@operator_view
def discussion(request, pk):
    post = get_object_or_404(evidence_posts(request.user), pk=pk)
    decorate_posts([post])
    comments = post.comments.order_by('-first_seen_at', '-pk')
    pcl_count = comments.filter(mentions_pcl=True).count()
    comment_scope = request.GET.get('comments', 'pcl' if pcl_count else 'all')
    if comment_scope not in ('pcl', 'all'):
        comment_scope = 'all'
    if comment_scope == 'pcl':
        comments = comments.filter(mentions_pcl=True)
    page = Paginator(comments, 30).get_page(request.GET.get('page'))
    fragment = request.GET.get('panel') == '1'
    response = render(request, 'research/facebook_discussion_content.html' if fragment else 'research/facebook_discussion.html', {
        'fb_nav': 'findings', 'post': post, 'comments_page': page,
        'pcl_count': pcl_count, 'comment_scope': comment_scope, 'fragment': fragment,
    })
    response['Cache-Control'] = 'private, no-store'
    return response


@operator_view
@require_POST
def review(request, pk):
    post = get_object_or_404(FacebookPost, pk=pk, public_verified=True)
    action = request.POST.get('action')
    if action not in ('save', 'unsave', 'read', 'unread'):
        return HttpResponseBadRequest('Unknown action')
    with transaction.atomic():
        state, _ = FacebookReview.objects.get_or_create(user=request.user, post=post)
        state = FacebookReview.objects.select_for_update().get(pk=state.pk)
        if action in ('save', 'unsave'):
            state.saved = action == 'save'
        else:
            state.reviewed_at = timezone.now() if action == 'read' else None
        state.save()
    if request.headers.get('Accept') == 'application/json':
        return JsonResponse({'post_id': post.pk, 'saved': state.saved, 'reviewed': bool(state.reviewed_at)})
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse('research:facebook_discussion', args=[post.pk])
    return redirect(next_url or reverse('research:facebook_discussion', args=[post.pk]))


@operator_view
def settings(request):
    monitor, _ = FacebookMonitor.objects.get_or_create(pk=1)
    form = FacebookMonitorForm(instance=monitor)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save':
            form = FacebookMonitorForm(request.POST, instance=monitor)
            if form.is_valid():
                configure_monitor('save', form.cleaned_data)
                messages.success(request, 'Settings saved. The current cycle will finish with its existing configuration.')
                return redirect('research:facebook_settings')
        elif action in ('pause', 'resume'):
            configure_monitor(action)
            messages.success(request, 'Collection paused. The current post may finish saving.' if action == 'pause' else 'Collection enabled.')
            return redirect('research:facebook_settings')
        else:
            return HttpResponseBadRequest('Unknown action')
    return render(request, 'research/facebook_settings.html', {
        **monitor_context(), 'monitor': monitor, 'fb_nav': 'settings', 'form': form,
        'source_count': FacebookDiscoverySource.objects.filter(enabled=True).count(),
        'recent_cycles': monitor.jobs.order_by('-pk')[:10],
    })


@operator_view
