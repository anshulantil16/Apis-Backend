from django.urls import path

from .views import (AdminBulkAccessView, AdminHrmsPreviewView, AdminSessionsView, AdminSyncView,
                    AdminUserDetailView, AdminUsersView,
                    LogoutView, MeView, RequestOTPView, VerifyOTPView)

urlpatterns = [
    # Sign-in
    path('portal/request-otp/', RequestOTPView.as_view()),
    path('portal/verify-otp/',  VerifyOTPView.as_view()),
    path('portal/me/',          MeView.as_view()),
    path('portal/logout/',      LogoutView.as_view()),

    # Console (superadmin only)
    path('portal/admin/users/',                  AdminUsersView.as_view()),
    path('portal/admin/users/<int:user_id>/',    AdminUserDetailView.as_view()),
    path('portal/admin/bulk-access/',            AdminBulkAccessView.as_view()),
    path('portal/admin/sync/',                   AdminSyncView.as_view()),
    path('portal/admin/hrms-preview/',           AdminHrmsPreviewView.as_view()),
    path('portal/admin/sessions/',               AdminSessionsView.as_view()),
    path('portal/admin/sessions/<int:session_id>/', AdminSessionsView.as_view()),
]
