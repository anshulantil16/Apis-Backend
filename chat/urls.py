from django.urls import path

from . import views

urlpatterns = [
    path('conversations/',                                views.ConversationsView.as_view()),
    path('conversations/<int:conversation_id>/messages/', views.MessagesView.as_view()),
    path('conversations/<int:conversation_id>/messages/<int:message_id>/',
         views.MessageDetailView.as_view()),
    path('conversations/<int:conversation_id>/read/',     views.ReadView.as_view()),
    path('conversations/<int:conversation_id>/clear/',    views.ClearView.as_view()),
    path('people/',                                       views.PeopleView.as_view()),
    path('unread/',                                       views.UnreadView.as_view()),
]
