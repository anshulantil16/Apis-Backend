from django.urls import path
from .views import TerritoryTemplateView, TerritoryUploadView, TerritoryDashboardView

urlpatterns = [
    path('template/', TerritoryTemplateView.as_view(), name='territory-template'),
    path('upload/', TerritoryUploadView.as_view(), name='territory-upload'),
    path('dashboard/', TerritoryDashboardView.as_view(), name='territory-dashboard'),
]
