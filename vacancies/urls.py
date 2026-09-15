from django.urls import path

from . import views

urlpatterns = [
    path('', views.VacancyListView.as_view()),
    path('<int:pk>/', views.VacancyDetailView.as_view()),
    path('<int:pk>/status/', views.VacancyStatusView.as_view()),
]
