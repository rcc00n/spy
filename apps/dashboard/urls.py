from django.urls import path

from . import views


app_name = "dashboard"

urlpatterns = [
    path("settings/facebook-login/session-access/", views.facebook_session_access, name="facebook_session_access"),
    path("", views.index, name="index"),
    path("run-check-now/", views.run_check_now, name="run_check_now"),
    path("accounts/", views.accounts, name="accounts"),
    path("accounts/add/", views.account_create, name="account_create"),
    path("accounts/<int:pk>/edit/", views.account_edit, name="account_edit"),
    path(
        "accounts/<int:pk>/deactivate/",
        views.account_deactivate,
        name="account_deactivate",
    ),
    path("keywords/", views.keywords, name="keywords"),
    path("keywords/add/", views.keyword_create, name="keyword_create"),
    path("keywords/<int:pk>/edit/", views.keyword_edit, name="keyword_edit"),
    path(
        "keywords/<int:pk>/deactivate/",
        views.keyword_deactivate,
        name="keyword_deactivate",
    ),
    path("matches/", views.matches, name="matches"),
    path("check-runs/", views.check_runs, name="check_runs"),
    path("check-runs/<int:pk>/", views.check_run_detail, name="check_run_detail"),
    path(
        "settings/facebook-login/",
        views.facebook_login_settings,
        name="facebook_login",
    ),
    path(
        "settings/facebook-login/session-request/",
        views.facebook_session_request_create,
        name="facebook_session_request_create",
    ),
]
