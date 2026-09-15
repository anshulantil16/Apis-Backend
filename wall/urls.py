from django.urls import path

from . import views

urlpatterns = [
    path('photos/', views.WallPhotoListView.as_view()),
    path('photos/<int:pk>/', views.WallPhotoDetailView.as_view()),
]
