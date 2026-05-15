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
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        if not password and not self.instance.password_configured:
            self.add_error("password", "Enter a password for this credential.")
        return cleaned_data

    def save(self, commit=True):
        credential = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            credential.set_password(password)
        if commit:
            credential.save()
            self.save_m2m()
        return credential
