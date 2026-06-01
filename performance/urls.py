from django.urls import path
from .views.auth_views import SendOTPView, VerifyOTPView, AdminBootstrapOTPView, AdminBootstrapVerifyView
from .views.employee_views import (
    EmployeeProfileView, ActiveCyclesView,
    EmployeeGoalCardView, SubmitGoalCardView,
    EmployeeAllGoalCardsView, SubmitQuarterlyReviewView,
)
from .views.manager_views import (
    ManagerTeamView, ManagerReviewGoalCardView,
    ManagerRateReviewView, ManagerPendingReviewsView,
)
from .views.hod_views import HODTeamView, HODReviewGoalCardView
from .views.hr_views import (
    EmployeeImportView, EmployeeListView,
    CycleListCreateView, CycleUpdateView,
    HRFinalizeReviewView, OrgOverviewView,
    LeaderboardView, AllGoalCardsView, AllReviewsView,
    ResetDatabaseView,
)
from .views.progress_views import (
    GoalProgressView, EmployeeProgressReportView,
    TeamProgressReportView, OrgAnalyticsView,
)

urlpatterns = [
    # Auth (OTP login)
    path('auth/send-otp/', SendOTPView.as_view()),
    path('auth/verify-otp/', VerifyOTPView.as_view()),
    path('auth/admin-otp/', AdminBootstrapOTPView.as_view()),
    path('auth/admin-verify/', AdminBootstrapVerifyView.as_view()),

    # Employee routes
    path('employee/<str:employee_id>/', EmployeeProfileView.as_view()),
    path('employee/<str:employee_id>/goal-cards/', EmployeeAllGoalCardsView.as_view()),
    path('employee/<str:employee_id>/progress-report/', EmployeeProgressReportView.as_view()),
    path('cycles/active/', ActiveCyclesView.as_view()),
    path('goal-cards/<str:employee_id>/<int:cycle_id>/', EmployeeGoalCardView.as_view()),
    path('goal-cards/<int:gc_id>/submit/', SubmitGoalCardView.as_view()),
    path('reviews/<int:gc_id>/', SubmitQuarterlyReviewView.as_view()),

    # Goal progress updates
    path('goals/<int:goal_id>/progress/', GoalProgressView.as_view()),

    # Manager routes
    path('manager/<str:manager_id>/team/', ManagerTeamView.as_view()),
    path('manager/<str:manager_id>/pending-reviews/', ManagerPendingReviewsView.as_view()),
    path('manager/<str:manager_id>/team-progress/', TeamProgressReportView.as_view()),
    path('goal-cards/<int:gc_id>/manager-review/', ManagerReviewGoalCardView.as_view()),
    path('reviews/<int:review_id>/manager-rate/', ManagerRateReviewView.as_view()),

    # HOD routes
    path('hod/<str:hod_id>/team/', HODTeamView.as_view()),
    path('goal-cards/<int:gc_id>/hod-review/', HODReviewGoalCardView.as_view()),

    # HR routes
    path('employees/import/', EmployeeImportView.as_view()),
    path('employees/', EmployeeListView.as_view()),
    path('cycles/', CycleListCreateView.as_view()),
    path('cycles/<int:cycle_id>/', CycleUpdateView.as_view()),
    path('reviews/<int:review_id>/hr-finalize/', HRFinalizeReviewView.as_view()),
    path('org/overview/', OrgOverviewView.as_view()),
    path('org/analytics/', OrgAnalyticsView.as_view()),
    path('leaderboard/', LeaderboardView.as_view()),
    path('all-goal-cards/', AllGoalCardsView.as_view()),
    path('all-reviews/', AllReviewsView.as_view()),
    path('org/reset/', ResetDatabaseView.as_view()),
]
