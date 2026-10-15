from django.urls import path

from . import views

urlpatterns = [
    path('template/', views.CtcTemplateView.as_view()),
    path('uploads/', views.CtcUploadListView.as_view()),
    path('uploads/<int:pk>/', views.CtcUploadDetailView.as_view()),
]
