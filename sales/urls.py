from django.urls import path
from .views import (
    SalesTemplateView, SalesUploadView, SalesOverviewView, SalesBreakdownView,
    SalesTrendView, SalesForecastView, SalesFiltersView, SalesInsightsView,
    SalesUploadsView, SalesExportView,
    SalesParetoView, SalesMatrixView, SalesMoversView, SalesAnomaliesView,
    SalesSeasonalityView, SalesHeatmapView, SalesRFMView, SalesCohortsView,
    SalesNewRepeatView, SalesYoYView, SalesPacingView, SalesPriceView,
    SalesLoginView, SalesOrgView,
    SalesReviewView, SalesReviewReportView, SalesReviewDeleteView,
    SalesReviewReportHtmlView, SalesReviewReportBundleView,
    SalesRecipientsView, SalesRecipientsEditView, SalesRecipientsTemplateView,
    SalesRecipientsImportView, SalesUploaderView, SalesUploaderEditView,
    SalesTeamReportView, SalesTeamReportHtmlView, SalesMailPreviewView,
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
    # The selling organisation: who reports to whom, and what each sold.
    path('org/',         SalesOrgView.as_view()),
    # The daily GTR-head review sheet, and one head's own report off it.
    path('review/',             SalesReviewView.as_view()),
    path('review/report/',      SalesReviewReportView.as_view()),
    path('review/report/file/', SalesReviewReportHtmlView.as_view()),
    path('review/bundle/',      SalesReviewReportBundleView.as_view()),
    path('review/<int:pk>/',    SalesReviewDeleteView.as_view()),
    # One rolled-up report across several territories.
    path('review/team/',      SalesTeamReportView.as_view()),
    path('review/team/file/', SalesTeamReportHtmlView.as_view()),
    # Who receives which report, and who may load the morning file.
    path('recipients/',               SalesRecipientsView.as_view()),
    path('recipients/edit/',          SalesRecipientsEditView.as_view()),
    path('recipients/edit/<int:pk>/', SalesRecipientsEditView.as_view()),
    path('recipients/template/',      SalesRecipientsTemplateView.as_view()),
    path('recipients/import/',        SalesRecipientsImportView.as_view()),
    path('recipients/mail/',          SalesMailPreviewView.as_view()),
    path('uploaders/',                SalesUploaderView.as_view()),
    path('uploaders/edit/',           SalesUploaderEditView.as_view()),
    path('uploaders/edit/<int:pk>/',  SalesUploaderEditView.as_view()),
]
