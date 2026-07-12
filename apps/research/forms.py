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
        help_text="Selected pages/groups are read directly; selected search phrases run independently. Configuration is saved with this scan.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['query'].required = False
        if not self.is_bound:
            self.fields['sources'].initial = list(FacebookDiscoverySource.objects.filter(enabled=True).values_list('pk', flat=True))

    class Meta(ResearchJobForm.Meta):
        fields = ["title", "query", "sources", "depth"]
        help_texts = {
            "query": "Optional extra phrases, Facebook page/group URLs or post/Reel URLs, one per line; up to 12. Select watchlist sources below. Results may be old or unrelated.",
            "depth": "Quick: up to 5 links per query, posts only. Standard: 10 links per query, batches of 40 comments. Deep: 20 links per query, batches of 100. All discovered links are queued. Up to 3 attempts per discussion per cycle; Continue collection preserves saved evidence.",
        }

    def clean_query(self):
        from apps.research.services.facebook_browser import canonical_post_url
        from apps.research.services.facebook_targets import canonical_source_url
        lines = list(dict.fromkeys(line.strip() for line in self.cleaned_data.get('query', '').splitlines() if line.strip()))
        if len(lines)>12 or any(len(line)>500 for line in lines):
            raise forms.ValidationError('Use up to 12 lines, each at most 500 characters.')
        for line in lines:
            if '://' in line and not (canonical_post_url(line) or canonical_source_url(line)):
                raise forms.ValidationError('Use HTTPS Facebook page, group, post or Reel links.')
        self.manual_lines = lines
        return '\n'.join(lines) or 'Watchlist scan'

    def clean(self):
        data = super().clean()
        sources = data.get('sources')
        if sources is not None and len(sources)>30:
            raise forms.ValidationError('Select at most 30 sources per scan.')
        if not getattr(self, 'manual_lines', []) and not sources:
            raise forms.ValidationError('Enter a query or select at least one source.')
        return data


class FacebookDiscoverySourceForm(forms.ModelForm):
    class Meta:
        model = FacebookDiscoverySource
        fields = ['name', 'kind', 'target', 'enabled', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows':3})}
        help_texts = {'target':'HTTPS Facebook page/group URL or a search phrase. Group visibility is checked before discovery.',
                      'enabled':'Included in future monitor discovery cycles and available for manual scans. Existing scans keep their saved configuration.'}

    def clean(self):
        from apps.research.services.facebook_targets import canonical_source_url
        data=super().clean()
        target=data.get('target','').strip();kind=data.get('kind')
        if kind in ('page','group'):
            normalized=canonical_source_url(target,kind)
            if not normalized:self.add_error('target','Enter a valid HTTPS Facebook URL for the selected source type.')
            else:data['target']=normalized
        elif kind=='search':
            if '://' in target:self.add_error('target','Use a phrase for a search source, not a URL.')
            data['target']=' '.join(target.split())
        return data


