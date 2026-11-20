from django.urls import path

from .views import LaunchView, OverviewView

urlpatterns = [
    path('overview/', OverviewView.as_view()),
    path('launch/',   LaunchView.as_view()),
]
