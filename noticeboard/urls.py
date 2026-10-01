from django.urls import path

from . import views

urlpatterns = [
    path('announcements/',            views.AnnouncementListView.as_view()),
    path('announcements/<int:pk>/',   views.AnnouncementDetailView.as_view()),
    path('news/',                     views.NewsListView.as_view()),
    path('news/<int:pk>/',            views.NewsDetailView.as_view()),
    path('news/approve/',             views.NewsApproveView.as_view()),
    path('news/fetch/',               views.NewsFetchView.as_view()),
    path('news/sources/',             views.NewsSourceListView.as_view()),
    path('news/sources/<int:pk>/',    views.NewsSourceDetailView.as_view()),
    path('holidays/',                 views.HolidayListView.as_view()),
    path('holidays/<int:pk>/',        views.HolidayDetailView.as_view()),
]
