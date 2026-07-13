from django.core.management.base import BaseCommand
from apps.research.models import ResearchJob, FacebookPost, FacebookComment
from apps.research.services.facebook_store import import_legacy_job, update_report


class Command(BaseCommand):
    help = 'Import existing browser reports into the persistent corpus without starting collection.'

    def handle(self, **options):
        for job in ResearchJob.objects.filter(plan__engine='facebook_browser'):
            import_legacy_job(job)
            update_report(job)
        self.stdout.write(f'Corpus: {FacebookPost.objects.count()} posts; {FacebookComment.objects.count()} comments. No jobs started.')
