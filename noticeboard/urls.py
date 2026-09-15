from django.urls import path

from . import views

urlpatterns = [
    path('announcements/',            views.AnnouncementListView.as_view()),
    path('announcements/<int:pk>/',   views.AnnouncementDetailView.as_view()),
    path('holidays/',                 views.HolidayListView.as_view()),
    path('holidays/<int:pk>/',        views.HolidayDetailView.as_view()),
]
