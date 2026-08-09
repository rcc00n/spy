from django.urls import path

from . import views, facebook_views


app_name = "research"

urlpatterns = [
    path("facebook/settings/", facebook_views.settings, name="facebook_settings"),
    path("facebook/posts/<int:pk>/", facebook_views.discussion, name="facebook_discussion"),
    path("facebook/posts/<int:pk>/review/", facebook_views.review, name="facebook_review"),
    path("facebook/sources/<int:pk>/toggle/", facebook_views.toggle_source, name="facebook_source_toggle"),
    path("facebook/monitor/", facebook_views.workspace, name="facebook_monitor"),
    path("facebook/sources/", views.facebook_sources, name="facebook_sources"),
    path("facebook/sources/new/", views.facebook_source_edit, name="facebook_source_create"),
    path("facebook/sources/<int:pk>/", views.facebook_source_edit, name="facebook_source_edit"),
    path("facebook/new/", views.facebook_scan_create, name="facebook_scan_create"),
    path("", views.jobs, name="jobs"),
    path("new/", views.job_create, name="job_create"),
    path("<int:pk>/", views.job_detail, name="job_detail"),
    path("<int:pk>/status/", views.job_status, name="job_status"),
    path("<int:pk>/retry/", views.job_retry, name="job_retry"),
]

