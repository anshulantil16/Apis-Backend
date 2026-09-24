from django.urls import path
from .views import (
    RoomPulseLoginView, RoomListView, RoomDetailView,
    BookingListView, BookingActionView, RoomCalendarView,
    ResourceRequestListView, ResourceRequestActionView,
    TicketListView, TicketActionView,
    EmployeeTemplateView, EmployeeUploadView, EmployeeListView,
    EmployeeSyncView, EmployeeCreateView,
    AdminRosterView, AnalyticsView, WorkReportView, WorkReportExportView,
    ResetDatabaseView,
)

urlpatterns = [
    path('login/', RoomPulseLoginView.as_view()),

    path('rooms/', RoomListView.as_view()),
    path('rooms/<int:room_id>/', RoomDetailView.as_view()),
    path('rooms/<int:room_id>/calendar/', RoomCalendarView.as_view()),

    path('bookings/', BookingListView.as_view()),
    path('bookings/<int:booking_id>/', BookingActionView.as_view()),

    path('resource-requests/', ResourceRequestListView.as_view()),
    path('resource-requests/<int:request_id>/', ResourceRequestActionView.as_view()),

    path('tickets/', TicketListView.as_view()),
    path('tickets/<int:ticket_id>/', TicketActionView.as_view()),

    path('employees/template/', EmployeeTemplateView.as_view()),
    path('employees/upload/', EmployeeUploadView.as_view()),
    path('employees/', EmployeeListView.as_view()),
    path('employees/sync/', EmployeeSyncView.as_view()),
    path('employees/add/', EmployeeCreateView.as_view()),

    path('admins/', AdminRosterView.as_view()),

    path('analytics/', AnalyticsView.as_view()),
    path('work-report/', WorkReportView.as_view()),
    path('work-report/export/', WorkReportExportView.as_view()),

    path('reset/', ResetDatabaseView.as_view()),
]
