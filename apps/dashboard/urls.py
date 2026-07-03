from django.urls import path

from . import views


app_name = "dashboard"

urlpatterns = [
    path("settings/facebook-login/session-access/", views.facebook_session_access, name="facebook_session_access"),
    path("", views.index, name="index"),
    path("run-check-now/", views.run_check_now, name="run_check_now"),
    path("accounts/", views.accounts, name="accounts"),
