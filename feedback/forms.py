from django import forms

from .models import CustomerFeedback


class CustomerFeedbackForm(forms.ModelForm):
    class Meta:
        model = CustomerFeedback
        fields = ['rating', 'visit_type', 'liked_most', 'improvement', 'name', 'contact']
        widgets = {
            'rating': forms.RadioSelect,
            'visit_type': forms.RadioSelect,
            'liked_most': forms.TextInput(attrs={
                'placeholder': 'The coffee, the service, the atmosphere…',
                'maxlength': 240,
            }),
            'improvement': forms.Textarea(attrs={
                'placeholder': 'One thing we could do better (optional)',
                'rows': 3,
            }),
            'name': forms.TextInput(attrs={'placeholder': 'Your name (optional)'}),
            'contact': forms.TextInput(attrs={
                'placeholder': 'Phone or email (optional)',
                'autocomplete': 'email',
            }),
        }

    def clean_rating(self):
        rating = self.cleaned_data['rating']
        if rating not in range(1, 6):
            raise forms.ValidationError('Choose a rating from 1 to 5.')
        return rating

