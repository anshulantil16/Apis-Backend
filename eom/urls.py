from django.urls import path
from .views.auth_views     import EOMSendOTPView, EOMVerifyOTPView, EOMAdminOTPView, EOMAdminVerifyView
from .views.employee_views import EOMActiveCyclesView, EOMNominationView, EOMSubmitNominationView, EOMDocumentUploadView
from .views.hod_views      import EOMHODTeamView, EOMHODReviewView
from .views.panel_views    import EOMPanelNominationsView, EOMPanelReviewView
from .views.hr_views import (
    EOMEmployeeImportView, EOMEmployeeListView,
    EOMCycleListCreateView, EOMCycleUpdateView,
    EOMAllNominationsView, EOMOverviewView, EOMHRFinalizeView,
    EOMDatabaseResetView,
)
from .views.export_views import EOMExportView

urlpatterns = [
    # Auth
    path('auth/send-otp/',     EOMSendOTPView.as_view()),
    path('auth/verify-otp/',   EOMVerifyOTPView.as_view()),
    path('auth/admin-otp/',    EOMAdminOTPView.as_view()),
    path('auth/admin-verify/', EOMAdminVerifyView.as_view()),

    # Employee
    path('cycles/active/',                                EOMActiveCyclesView.as_view()),
    path('nominations/<str:employee_id>/<int:cycle_id>/', EOMNominationView.as_view()),
    path('nominations/<int:nom_id>/submit/',              EOMSubmitNominationView.as_view()),
    path('nominations/<int:nom_id>/upload-document/',     EOMDocumentUploadView.as_view()),

    # HOD
    path('hod/<str:hod_id>/team/',               EOMHODTeamView.as_view()),
    path('nominations/<int:nom_id>/hod-review/', EOMHODReviewView.as_view()),

    # Panel
    path('panel/nominations/',                       EOMPanelNominationsView.as_view()),
    path('nominations/<int:nom_id>/panel-review/',   EOMPanelReviewView.as_view()),

    # HR
    path('employees/import/',          EOMEmployeeImportView.as_view()),
    path('employees/',                 EOMEmployeeListView.as_view()),
    path('cycles/',                    EOMCycleListCreateView.as_view()),
    path('cycles/<int:cycle_id>/',     EOMCycleUpdateView.as_view()),
    path('all-nominations/',           EOMAllNominationsView.as_view()),
    path('org/overview/',              EOMOverviewView.as_view()),
    path('nominations/<int:nom_id>/hr-finalize/', EOMHRFinalizeView.as_view()),
    path('admin/reset-database/',      EOMDatabaseResetView.as_view()),
    path('export/',                    EOMExportView.as_view()),
]
