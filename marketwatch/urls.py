from django.urls import path

from .views import DashboardView, ReportView

urlpatterns = [
    path('dashboard/', DashboardView.as_view()),
    path('report/', ReportView.as_view()),
]
