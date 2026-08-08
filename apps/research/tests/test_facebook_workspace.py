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

