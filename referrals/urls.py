from django.urls import path

from . import views

urlpatterns = [
    path('submit/', views.SubmitReferralView.as_view()),
    path('admin/', views.AdminReferralsView.as_view()),
    path('admin/<int:referral_id>/', views.AdminReferralDetailView.as_view()),
]
