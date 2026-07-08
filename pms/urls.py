from django.urls import path
from .views import (PMSListView, PMSImportView, PMSEmployeeUpdateView, PMSTemplateView,
                    PMSExportView, PMSLoginView, OfferLetterTemplateView, OfferLetterUploadView,
                    OfferLetterApprovalView, OfferLetterStatusView, OfferLetterApprovalListView,
                    OfferLetterSendActivityView, OrgStructureTemplateView, OrgStructureUploadView,
                    OrgStructureDashboardView)

urlpatterns = [
    path('login/',                      PMSLoginView.as_view()),
    path('employees/',                  PMSListView.as_view()),
    path('import/',                     PMSImportView.as_view()),
    path('employees/<int:emp_id>/',     PMSEmployeeUpdateView.as_view()),
    path('template/',                   PMSTemplateView.as_view()),
    path('export/',                     PMSExportView.as_view()),
    path('offer-letter/template/',      OfferLetterTemplateView.as_view()),
    path('offer-letter/upload/',        OfferLetterUploadView.as_view()),
    path('offer-letter/<int:offer_letter_id>/approve/',  OfferLetterApprovalView.as_view()),
    path('offer-letter/<int:offer_letter_id>/status/',   OfferLetterStatusView.as_view()),
    path('offer-letter/approvals/',     OfferLetterApprovalListView.as_view()),
    path('offer-letter/activity/',      OfferLetterSendActivityView.as_view()),
    path('org-structure/template/',     OrgStructureTemplateView.as_view()),
    path('org-structure/upload/',       OrgStructureUploadView.as_view()),
    path('org-structure/dashboard/',    OrgStructureDashboardView.as_view()),
]
