from django.urls import path
from .views import (
    PMSListView, PMSImportView, PMSEmployeeUpdateView, PMSTemplateView,
    PMSExportView, PMSSettingsView, OfferLetterTemplateView,
    OfferLetterUploadView, OfferLetterPDFView, OfferLetterBatchStatusView,
    OfferLetterHistoryView, OfferLetterDownloadAllView, PMSLoginView,
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
]
