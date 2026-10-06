from django.urls import path

from . import views

urlpatterns = [
    path('documents/', views.PolicyDocumentListView.as_view()),
    path('documents/<int:pk>/', views.PolicyDocumentDetailView.as_view()),
]
