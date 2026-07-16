from django.urls import path
from .views import (
    SendOTPView, VerifyOTPView, AdminOTPView, AdminVerifyView,
    UserTemplateView, UserImportView, UsersListView, CapsView,
    AdminOverviewView, AdminResetView,
    MyRequestsView, RequestDetailView,
    CreateTourSanctionView, CreateTravelExpenseView, CreateLocalTravelView,
    BillDownloadView, PendingQueueView, ActionView,
)

urlpatterns = [
    # Auth
    path('auth/send-otp/',     SendOTPView.as_view()),
    path('auth/verify-otp/',   VerifyOTPView.as_view()),
    path('auth/admin-otp/',    AdminOTPView.as_view()),
    path('auth/admin-verify/', AdminVerifyView.as_view()),

    # User directory
    path('users/template/',  UserTemplateView.as_view()),
    path('users/import/',    UserImportView.as_view()),
    path('users/',           UsersListView.as_view()),
    path('caps/',            CapsView.as_view()),
    path('admin/overview/',  AdminOverviewView.as_view()),
    path('admin/reset/',     AdminResetView.as_view()),

    # Employee — create & view requests
    path('requests/mine/',                 MyRequestsView.as_view()),
    path('requests/<int:req_id>/',         RequestDetailView.as_view()),
    path('requests/tour-sanction/',        CreateTourSanctionView.as_view()),
    path('requests/travel-expense/',       CreateTravelExpenseView.as_view()),
    path('requests/local-travel/',         CreateLocalTravelView.as_view()),
    path('bill/<int:item_id>/',            BillDownloadView.as_view()),

    # Approvals (Manager / HR / Finance)
    path('queue/',                         PendingQueueView.as_view()),
    path('requests/<int:req_id>/action/',  ActionView.as_view()),
]
