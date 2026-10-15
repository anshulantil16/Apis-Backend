from django.urls import path

from . import views

urlpatterns = [
    path('template/', views.CtcTemplateView.as_view()),
    path('uploads/', views.CtcUploadListView.as_view()),
    path('uploads/<int:pk>/', views.CtcUploadDetailView.as_view()),
    path('reset/', views.CtcResetView.as_view()),
    path('candidates/<int:pk>/breakdown/', views.CtcBreakdownView.as_view()),
    path('candidates/<int:pk>/pdf/', views.CtcPdfView.as_view()),
    path('settings/', views.CtcSettingsView.as_view()),
]
