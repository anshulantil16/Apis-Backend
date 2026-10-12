from django.urls import path

from . import views

urlpatterns = [
    path('documents/', views.PolicyDocumentListView.as_view()),
    path('documents/<int:pk>/', views.PolicyDocumentDetailView.as_view()),
    # The only way to the stored PDF — /media/policies/ is refused. See the
    # views module header.
    path('documents/<int:pk>/view/', views.PolicyDocumentFileView.as_view()),
    path('built-in/removed/', views.BuiltInRemovalView.as_view()),
]
