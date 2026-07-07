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
