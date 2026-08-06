from django.urls import path
from .views import (
    SalesTemplateView, SalesUploadView, SalesOverviewView, SalesBreakdownView,
    SalesTrendView, SalesForecastView, SalesFiltersView, SalesInsightsView,
    SalesUploadsView, SalesExportView,
    SalesParetoView, SalesMatrixView, SalesMoversView, SalesAnomaliesView,
    SalesSeasonalityView, SalesHeatmapView, SalesRFMView, SalesCohortsView,
    SalesNewRepeatView, SalesYoYView, SalesPacingView, SalesPriceView,
    SalesLoginView,
)

urlpatterns = [
    path('login/',     SalesLoginView.as_view()),
    # core
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
    # advanced analytics
    path('pareto/',      SalesParetoView.as_view()),
    path('matrix/',      SalesMatrixView.as_view()),
    path('movers/',      SalesMoversView.as_view()),
    path('anomalies/',   SalesAnomaliesView.as_view()),
    path('seasonality/', SalesSeasonalityView.as_view()),
    path('heatmap/',     SalesHeatmapView.as_view()),
    path('rfm/',         SalesRFMView.as_view()),
    path('cohorts/',     SalesCohortsView.as_view()),
    path('new-repeat/',  SalesNewRepeatView.as_view()),
    path('yoy/',         SalesYoYView.as_view()),
    path('pacing/',      SalesPacingView.as_view()),
    path('price/',       SalesPriceView.as_view()),
]
