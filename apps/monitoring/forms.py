from django import forms

from apps.monitoring.models import PlatformCredential


class PlatformCredentialForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        label="Password",
        help_text="Leave blank to keep the existing stored password.",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Unchanged",
            },
            render_value=False,
        ),
    )

    class Meta:
        model = PlatformCredential
        fields = ["platform", "username", "password", "is_active"]
        widgets = {
            "platform": forms.Select(attrs={"class": "form-control"}),
            "username": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "username",
                    "placeholder": "operator@example.com",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

    def clean(self):
