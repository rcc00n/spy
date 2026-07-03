from urllib.parse import urlparse

from django import forms

from apps.monitoring.forms import PlatformCredentialForm
from apps.monitoring.models import Keyword, MonitoredAccount


class MonitoredAccountForm(forms.ModelForm):
    class Meta:
        model = MonitoredAccount
        fields = [
            "platform",
            "account_url",
            "account_name",
            "is_active",
            "check_interval_minutes",
            "max_posts_per_check",
            "scroll_rounds",
        ]
        widgets = {
            "platform": forms.Select(attrs={"class": "form-control"}),
            "account_url": forms.URLInput(attrs={"class": "form-control"}),
            "account_name": forms.TextInput(attrs={"class": "form-control"}),
            "check_interval_minutes": forms.NumberInput(
                attrs={"class": "form-control", "min": "5"}
            ),
            "max_posts_per_check": forms.NumberInput(
                attrs={"class": "form-control", "min": "1", "max": "20"}
            ),
            "scroll_rounds": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "max": "5"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

