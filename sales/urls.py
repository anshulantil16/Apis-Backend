from django.urls import path
from .views import (
    SalesTemplateView, SalesUploadView, SalesOverviewView, SalesBreakdownView,
    SalesTrendView, SalesForecastView, SalesFiltersView, SalesInsightsView,
    SalesUploadsView, SalesExportView,
)

urlpatterns = [
    path('template/',  SalesTemplateView.as_view()),
    path('upload/',    SalesUploadView.as_view()),
    path('overview/',  SalesOverviewView.as_view()),
    path('breakdown/', SalesBreakdownView.as_view()),
    path('trend/',     SalesTrendView.as_view()),
    path('forecast/',  SalesForecastView.as_view()),
    path('filters/',   SalesFiltersView.as_view()),
    path('insights/',  SalesInsightsView.as_view()),
    path('uploads/',   SalesUploadsView.as_view()),
    path('export/',    SalesExportView.as_view()),
]
