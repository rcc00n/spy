from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.research.models import FacebookComment, FacebookPost, FacebookReview, FacebookDiscoverySource, ResearchJob
from apps.research.services.facebook_store import enqueue_url, persist_snapshot


class WorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('reader', is_staff=True)
        self.client.force_login(self.user)
        self.url = reverse('research:facebook_monitor')
        self.job = ResearchJob.objects.create(query='PCL', status='completed')
        self.post = enqueue_url(self.job, 'https://www.facebook.com/PCLconstruction/posts/123/').post
        work = self.job.facebook_threads.get()
        persist_snapshot(work.pk, {'url':self.post.url, 'public_verified':True, 'text':'An arena announcement', 'comments':[
            {'id':'1','url':self.post.url+'?comment_id=1','text':'PCL did the construction','mentions_pcl':True},
            {'id':'2','url':self.post.url+'?comment_id=2','text':'A general comment','mentions_pcl':False}]})
        self.post.refresh_from_db()
        other = enqueue_url(self.job, 'https://www.facebook.com/reel/456/')
        persist_snapshot(other.pk, {'url':other.post.url,'public_verified':True,'text':'An unrelated hockey score','comments':[]})
        self.other = other.post
        self.private = enqueue_url(self.job, 'https://www.facebook.com/reel/789/').post

    def test_default_feed_matches_post_or_comment_and_excludes_unverified_evidence(self):
        response = self.client.get(self.url, secure=True)
        self.assertContains(response, 'PCL did the construction')
        self.assertNotContains(response, 'An unrelated hockey score')
        self.assertEqual([p.pk for p in response.context['page_obj']], [self.post.pk])
        all_posts = self.client.get(self.url, {'scope':'all'}, secure=True)
        self.assertEqual(all_posts.context['page_obj'].paginator.count, 2)
        self.assertNotContains(all_posts, self.private.url)

    def test_totals_count_unique_corpus_not_scan_observations_or_current_filter(self):
        second = ResearchJob.objects.create(query='Again')
        duplicate = enqueue_url(second, self.post.url)
        duplicate.comments.set(self.post.comments.all())
        response = self.client.get(self.url, {'q':'nothing matches'}, secure=True)
        self.assertEqual(response.context['posts_read'], 2)
        self.assertEqual(response.context['posts_found'], 3)
        self.assertEqual(response.context['comments_read'], 2)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_search_finds_comments_and_date_filter_uses_first_discovery(self):
        response = self.client.get(self.url, {'q':'general comment'}, secure=True)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.post.first_seen_at = timezone.now()-timedelta(days=20); self.post.save()
        response = self.client.get(self.url, {'period':'7'}, secure=True)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        response = self.client.get(self.url, {'period':'invalid','scope':'invalid'}, secure=True)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_personal_bookmarks_are_idempotent_and_read_filter_can_be_reversed(self):
        url = reverse('research:facebook_review', args=[self.post.pk])
        for _ in range(2):
            response = self.client.post(url, {'action':'save'}, HTTP_ACCEPT='application/json', secure=True)
            self.assertTrue(response.json()['saved'])
        self.assertEqual(FacebookReview.objects.count(), 1)
        self.assertEqual(self.client.get(self.url, {'scope':'saved'}, secure=True).context['page_obj'].paginator.count, 1)
        self.client.post(url, {'action':'read'}, secure=True)
        self.assertEqual(self.client.get(self.url, {'unread':'1'}, secure=True).context['page_obj'].paginator.count, 0)
        self.client.post(url, {'action':'unread'}, secure=True)
        self.assertEqual(self.client.get(self.url, {'unread':'1'}, secure=True).context['page_obj'].paginator.count, 1)
        second = get_user_model().objects.create_user('other-reader', is_staff=True)
        self.client.force_login(second)
        self.assertEqual(self.client.get(self.url, {'scope':'saved'}, secure=True).context['page_obj'].paginator.count, 0)
        self.assertEqual(self.client.get(self.url, {'unread':'1'}, secure=True).context['page_obj'].paginator.count, 1)

    def test_reading_get_never_changes_state_and_comments_are_escaped(self):
        comment = self.post.comments.first(); comment.text='<script>alert("x")</script>'; comment.save()
        response = self.client.get(reverse('research:facebook_discussion',args=[self.post.pk]), {'panel':'1','comments':'all'}, secure=True)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>')
        self.assertEqual(FacebookReview.objects.count(), 0)
        self.assertContains(response, 'A general comment')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get(reverse('research:facebook_discussion',args=[self.private.pk]),secure=True).status_code,404)

    def test_pcl_comment_filter_and_pagination_keep_discussion_route(self):
        for i in range(40):
            FacebookComment.objects.create(post=self.post,facebook_id=str(i+20),url=self.post.url+'?comment_id='+str(i+20),text='PCL '+str(i),mentions_pcl=True)
        url = reverse('research:facebook_discussion',args=[self.post.pk])
        response = self.client.get(url, {'panel':'1'}, secure=True)
        self.assertEqual(len(response.context['comments_page']), 30)
        self.assertNotContains(response, 'A general comment')
        self.assertContains(response, url+'?comments=pcl&amp;page=2&amp;panel=1')

    def test_review_requires_staff_post_csrf_and_rejects_external_redirects(self):
        url = reverse('research:facebook_review',args=[self.post.pk])
        self.assertEqual(self.client.get(url,secure=True).status_code,405)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(url,{'action':'save'},secure=True).status_code,403)
        response = self.client.post(url,{'action':'save','next':'https://evil.example/'},secure=True)
        self.assertRedirects(response,reverse('research:facebook_discussion',args=[self.post.pk]),fetch_redirect_response=False)
        self.user.is_staff=False; self.user.save()
        for endpoint in [self.url,reverse('research:facebook_discussion',args=[self.post.pk]),reverse('research:facebook_settings')]:
            self.assertEqual(self.client.get(endpoint,secure=True).status_code,403)
        self.assertEqual(self.client.post(url,{'action':'save'},secure=True).status_code,403)

    def test_source_switch_only_changes_on_valid_post(self):
        source=FacebookDiscoverySource.objects.create(name='PCL',kind='search',target='PCL')
        url=reverse('research:facebook_source_toggle',args=[source.pk])
        self.assertEqual(self.client.get(url,secure=True).status_code,405)
        self.assertEqual(self.client.post(url,{'action':'wrong'},secure=True).status_code,400)
        self.client.post(url,{'action':'disable'},secure=True)
        source.refresh_from_db(); self.assertFalse(source.enabled)
        self.client.post(url,{'action':'enable'},secure=True)
        source.refresh_from_db(); self.assertTrue(source.enabled)
        self.assertEqual(self.post.comments.count(),2)

    def test_staff_lands_in_findings_while_legacy_dashboard_stays_accessible(self):
        self.assertRedirects(self.client.get(reverse('dashboard:index'),secure=True),self.url,fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('dashboard:index'),{'legacy':'1'},secure=True).status_code,200)


from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import sync_playwright, expect
