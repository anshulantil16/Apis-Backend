from django.urls import path

from . import views

urlpatterns = [
    # sign in
    path('auth/send-otp/', views.SendOTPView.as_view()),
    path('auth/verify-otp/', views.VerifyOTPView.as_view()),
    path('auth/admin-otp/', views.AdminOTPView.as_view()),
    path('auth/admin-verify/', views.AdminVerifyView.as_view()),

    # reference data
    path('meta/', views.MetaView.as_view()),
    path('cycles/', views.CycleListView.as_view()),
    path('cycles/<int:cycle_id>/', views.CycleDetailView.as_view()),

    # the goal sheet
    path('plans/<str:employee_id>/<int:cycle_id>/', views.PlanView.as_view()),
    path('plans/<int:plan_id>/', views.PlanDetailView.as_view()),
    path('plans/<int:plan_id>/action/', views.PlanActionView.as_view()),
    path('plans/<int:plan_id>/reopen/', views.PlanReopenView.as_view()),
    path('plans/<int:plan_id>/status/', views.PlanStatusView.as_view()),
    path('my/<str:employee_id>/plans/', views.MyPlansView.as_view()),

    # reviewers
    path('manager/<str:manager_id>/team/', views.ManagerTeamView.as_view()),
    path('hod/<str:hod_id>/team/', views.HODTeamView.as_view()),

    # admin
    path('employees/import/', views.EmployeeImportView.as_view()),
    path('employees/', views.EmployeeListView.as_view()),
    path('employees/create/', views.EmployeeCreateView.as_view()),
    path('activity/', views.ActivityView.as_view()),
    path('employees/<str:employee_id>/', views.EmployeeDetailView.as_view()),
    path('all-plans/', views.AllPlansView.as_view()),
    path('overview/', views.OverviewView.as_view()),
]
