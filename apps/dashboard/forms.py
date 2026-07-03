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

    def clean_account_url(self):
        value = self.cleaned_data["account_url"].strip()
        platform = self.cleaned_data.get("platform")
        host = urlparse(value).netloc.lower().removeprefix("www.")

        if platform == MonitoredAccount.Platform.FACEBOOK and not (
            host == "facebook.com" or host.endswith(".facebook.com")
        ):
            raise forms.ValidationError("Facebook accounts must use a facebook.com URL.")
        if platform == MonitoredAccount.Platform.INSTAGRAM and not (
            host == "instagram.com" or host.endswith(".instagram.com")
        ):
            raise forms.ValidationError(
                "Instagram accounts must use an instagram.com URL."
            )
        return value


class KeywordForm(forms.ModelForm):
    class Meta:
        model = Keyword
        fields = ["phrase", "is_active"]
        widgets = {
            "phrase": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

    def clean_phrase(self):
        phrase = " ".join(self.cleaned_data["phrase"].split())
        if not phrase:
            raise forms.ValidationError("Enter a keyword phrase.")
        return phrase


class ManualCheckForm(forms.Form):
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=20,
        label="Limit to last N posts",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-small",
                "min": "1",
                "max": "20",
                "placeholder": "Default",
            }
        ),
