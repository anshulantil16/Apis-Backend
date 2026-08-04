from django.urls import path
from .views import (
    PMSListView, PMSImportView, PMSEmployeeUpdateView, PMSTemplateView,
    PMSExportView, PMSSettingsView, OfferLetterTemplateView,
    OfferLetterUploadView, OfferLetterPDFView, OfferLetterBatchStatusView,
    OfferLetterHistoryView, OfferLetterDownloadAllView, PMSLoginView,
    WarningLetterTemplateView, WarningLetterUploadView, WarningLetterCreateView,
    WarningLetterPDFView, WarningLetterBatchStatusView, WarningLetterHistoryView,
    WarningLetterDownloadAllView,
)

urlpatterns = [
    path('login/',                  PMSLoginView.as_view()),
    path('employees/',              PMSListView.as_view()),
    path('import/',                 PMSImportView.as_view()),
    path('employees/<int:emp_id>/', PMSEmployeeUpdateView.as_view()),
    path('template/',               PMSTemplateView.as_view()),
    path('export/',                 PMSExportView.as_view()),
    path('settings/',               PMSSettingsView.as_view()),
    path('offer-letter/template/',  OfferLetterTemplateView.as_view()),
    path('offer-letter/upload/',    OfferLetterUploadView.as_view()),
    path('offer-letter/batch/<str:batch_id>/', OfferLetterBatchStatusView.as_view()),
    path('offer-letter/<int:offer_letter_id>/pdf/', OfferLetterPDFView.as_view()),
    path('offer-letter/history/',   OfferLetterHistoryView.as_view()),
    path('offer-letter/download-all/', OfferLetterDownloadAllView.as_view()),

    # Warning / disciplinary letters (Letters Generator component #2)
    path('warning-letter/template/',  WarningLetterTemplateView.as_view()),
    path('warning-letter/create/',    WarningLetterCreateView.as_view()),
    path('warning-letter/upload/',    WarningLetterUploadView.as_view()),
    path('warning-letter/batch/<str:batch_id>/', WarningLetterBatchStatusView.as_view()),
    path('warning-letter/history/',   WarningLetterHistoryView.as_view()),
    path('warning-letter/download-all/', WarningLetterDownloadAllView.as_view()),
    path('warning-letter/<int:warning_letter_id>/pdf/', WarningLetterPDFView.as_view()),
]
