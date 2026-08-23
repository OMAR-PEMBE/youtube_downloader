from django.urls import path

from . import views


urlpatterns = [
    path("health/live/", views.health_live, name="health-live"),
    path("health/ready/", views.health_ready, name="health-ready"),
    path("ads/<uuid:advertisement_id>/click/", views.advertisement_click, name="advertisement-click"),
    path(
        "",
        views.home,
        name="home"
    ),
    path(
        "downloads/start/",
        views.start_download,
        name="download-start",
    ),
    path(
        "downloads/<uuid:job_id>/progress/",
        views.download_progress,
        name="download-progress",
    ),
    path(
        "downloads/<uuid:job_id>/file/",
        views.download_file,
        name="download-file",
    ),
    path(
        "downloads/<uuid:job_id>/cancel/",
        views.cancel_download,
        name="download-cancel",
    ),
]
