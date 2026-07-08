from django.contrib import admin

from apps.research.models import ResearchEvent, ResearchJob, ResearchSource


class ResearchEventInline(admin.TabularInline):
    model = ResearchEvent
    extra = 0
    readonly_fields = ["level", "message", "payload", "created_at"]
    can_delete = False


class ResearchSourceInline(admin.TabularInline):
    model = ResearchSource
    extra = 0
    readonly_fields = ["source_type", "title", "url", "excerpt", "payload", "created_at"]
    can_delete = False


@admin.register(ResearchJob)
class ResearchJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "display_title",
        "status",
        "depth",
        "include_social",
        "requested_by",
        "created_at",
        "completed_at",
    ]
    list_filter = ["status", "depth", "include_social", "created_at"]
    search_fields = ["title", "query", "external_job_id", "task_id"]
    readonly_fields = [
        "monitor",
        "monitor_lane",
        "task_id",
        "external_job_id",
        "plan",
        "report_markdown",
        "error_message",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ]
    inlines = [ResearchEventInline, ResearchSourceInline]


@admin.register(ResearchEvent)
class ResearchEventAdmin(admin.ModelAdmin):
    list_display = ["job", "level", "message", "created_at"]
    list_filter = ["level", "created_at"]
    search_fields = ["message", "job__title", "job__query"]


@admin.register(ResearchSource)
class ResearchSourceAdmin(admin.ModelAdmin):
    list_display = ["job", "source_type", "title", "url", "created_at"]
    list_filter = ["source_type", "created_at"]
    search_fields = ["title", "url", "excerpt", "job__title", "job__query"]



from apps.research.models import FacebookPost, FacebookComment, FacebookCommentRevision, FacebookThreadWork


class EvidenceAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CommentRevisionInline(admin.TabularInline):
    model = FacebookCommentRevision
    extra = 0
    readonly_fields = ['text', 'has_media', 'observed_at']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FacebookPost)
class FacebookPostAdmin(EvidenceAdmin):
    list_display = ['id', 'url', 'public_verified', 'mentions_pcl', 'last_seen_at']
    search_fields = ['url', 'text']
    list_filter = ['public_verified', 'mentions_pcl']


@admin.register(FacebookComment)
class FacebookCommentAdmin(EvidenceAdmin):
    list_display = ['facebook_id', 'post', 'mentions_pcl', 'first_seen_at', 'last_seen_at']
    search_fields = ['facebook_id', 'text', 'post__url']
    list_filter = ['mentions_pcl']
    list_select_related = ['post']
    inlines = [CommentRevisionInline]


@admin.register(FacebookThreadWork)
class FacebookThreadWorkAdmin(EvidenceAdmin):
    list_display = ['id', 'job', 'post', 'status', 'attempts', 'coverage', 'updated_at']
    list_filter = ['status', 'coverage']
