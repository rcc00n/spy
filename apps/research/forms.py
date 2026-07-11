from django import forms

from apps.research.models import ResearchJob, FacebookDiscoverySource


class ResearchJobForm(forms.ModelForm):
    class Meta:
        model = ResearchJob
        fields = ["title", "query", "depth", "include_social"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Optional short label",
                }
            ),
            "query": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 8,
                    "placeholder": "Describe the research question, target entity, and desired report.",
                }
            ),
            "depth": forms.Select(attrs={"class": "form-control"}),
