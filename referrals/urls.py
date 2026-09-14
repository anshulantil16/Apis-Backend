from django.urls import path

from . import views

urlpatterns = [
    path('submit/', views.SubmitReferralView.as_view()),
]
