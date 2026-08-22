from urllib.parse import urlparse

from django import forms
from django.core.exceptions import ValidationError


ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def validate_youtube_url(value):
    value = value.strip()

    if not value.startswith(("http://", "https://")):
        value = "https://" + value

    parsed = urlparse(value)

    hostname = (parsed.hostname or "").lower()

    if hostname not in ALLOWED_YOUTUBE_HOSTS:
        raise ValidationError(
            "Only YouTube links are supported."
        )

    return value


class DownloadForm(forms.Form):

    DOWNLOAD_TYPES = [
        ("video", "Video"),
        ("audio", "Audio"),
    ]

    VIDEO_QUALITIES = [
        ("2160", "2160p 4K"),
        ("1440", "1440p 2K"),
        ("1080", "1080p Full HD"),
        ("720", "720p HD"),
        ("480", "480p"),
        ("360", "360p"),
        ("240", "240p"),
        ("144", "144p"),
    ]

    AUDIO_QUALITIES = [
        ("320", "320 kbps"),
        ("192", "192 kbps"),
        ("128", "128 kbps"),
    ]

    url = forms.CharField(
        label="YouTube URL",
        max_length=500,
        widget=forms.URLInput(
            attrs={
                "class": "form-control",
                "placeholder": "Paste YouTube link here",
            }
        ),
    )

    download_type = forms.ChoiceField(
        choices=DOWNLOAD_TYPES,
        widget=forms.RadioSelect,
        initial="video",
    )

    video_quality = forms.ChoiceField(
        choices=VIDEO_QUALITIES,
        required=False,
        initial="720",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    audio_quality = forms.ChoiceField(
        choices=AUDIO_QUALITIES,
        required=False,
        initial="192",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean_url(self):
        return validate_youtube_url(
            self.cleaned_data["url"]
        )

    def clean(self):
        cleaned_data = super().clean()
        download_type = cleaned_data.get("download_type")

        if download_type == "video" and not cleaned_data.get("video_quality"):
            self.add_error("video_quality", "Select a video quality.")
        elif download_type == "audio" and not cleaned_data.get("audio_quality"):
            self.add_error("audio_quality", "Select an audio quality.")

        return cleaned_data
