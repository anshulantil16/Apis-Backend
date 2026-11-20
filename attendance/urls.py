from django.urls import path

from .views import MetaView, RecordsView

urlpatterns = [
    path("meta/",    MetaView.as_view()),
    path("records/", RecordsView.as_view()),
]
