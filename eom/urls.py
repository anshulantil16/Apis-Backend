from django.urls import path
from .views.employee_views import EOMActiveCyclesView, EOMNominationView, EOMSubmitNominationView
from .views.manager_views import EOMManagerTeamView, EOMManagerReviewView
from .views.hod_views import EOMHODTeamView, EOMHODReviewView
from .views.hr_views import (
    EOMCycleListCreateView, EOMCycleUpdateView,
    EOMAllNominationsView, EOMOverviewView, EOMHRFinalizeView,
)

urlpatterns = [
    # Employee
    path('cycles/active/',                           EOMActiveCyclesView.as_view()),
    path('nominations/<str:employee_id>/<int:cycle_id>/', EOMNominationView.as_view()),
    path('nominations/<int:nom_id>/submit/',         EOMSubmitNominationView.as_view()),

    # Manager
    path('manager/<str:manager_id>/team/',           EOMManagerTeamView.as_view()),
    path('nominations/<int:nom_id>/manager-review/', EOMManagerReviewView.as_view()),

    # HOD
    path('hod/<str:hod_id>/team/',                   EOMHODTeamView.as_view()),
    path('nominations/<int:nom_id>/hod-review/',     EOMHODReviewView.as_view()),

    # HR
    path('cycles/',                                  EOMCycleListCreateView.as_view()),
    path('cycles/<int:cycle_id>/',                   EOMCycleUpdateView.as_view()),
    path('all-nominations/',                         EOMAllNominationsView.as_view()),
    path('org/overview/',                            EOMOverviewView.as_view()),
    path('nominations/<int:nom_id>/hr-finalize/',    EOMHRFinalizeView.as_view()),
]
