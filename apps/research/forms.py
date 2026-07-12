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
            "include_social": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }
        help_texts = {
            "title": "Used only in the portal list.",
            "query": "This is sent to the remote research engine.",
            "depth": "Deep jobs can run for a long time on the model server.",
            "include_social": "Allow the research engine to use social monitoring as one source.",
        }

    def clean_title(self):
        return " ".join(self.cleaned_data.get("title", "").split())

    def clean_query(self):
        query = self.cleaned_data["query"].strip()
        if not query:
            raise forms.ValidationError("Enter a research request.")
        return query



class FacebookScanForm(ResearchJobForm):
    sources = forms.ModelMultipleChoiceField(
        queryset=FacebookDiscoverySource.objects.filter(enabled=True), required=False,
        widget=forms.CheckboxSelectMultiple,
