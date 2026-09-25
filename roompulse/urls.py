from django.urls import path

from .attachments import AttachmentView
from .views.desk_staff import DeskStaffView, WhoAmIView
from .views.my_tasks import MyTasksView
from .views import (
    RoomPulseLoginView, RoomListView, RoomDetailView,
    BookingListView, BookingActionView, RoomCalendarView,
    ResourceRequestListView, ResourceRequestActionView,
    TicketListView, TicketActionView,
    EmployeeTemplateView, EmployeeUploadView, EmployeeListView,
    EmployeeSyncView, EmployeeCreateView, EmployeeRowView,
    AdminRosterView, AdminRoleView, AnalyticsView, OverviewView, WorkReportView, WorkReportExportView,
    ResetDatabaseView,
)

urlpatterns = [
    # Signed, expiring links to ticket attachments. Under /api/ so the
    # proxy forwards it, unlike /media/.
    path('attachments/<str:token>/', AttachmentView.as_view()),
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
    path('employees/<int:employee_id>/', EmployeeRowView.as_view()),

    # Who a request can be addressed to -- the roster, reduced to a picker.
    path('desk-staff/', DeskStaffView.as_view()),
    # Who this session is, freshly named -- the browser's copy can be old.
    path('me/', WhoAmIView.as_view()),
    # The other half of 'My Requests': what has been given to me.
    path('my-tasks/', MyTasksView.as_view()),
    path('admins/', AdminRosterView.as_view()),
    path('admins/role/', AdminRoleView.as_view()),

    path('analytics/', AnalyticsView.as_view()),
    path('overview/', OverviewView.as_view()),
    path('work-report/', WorkReportView.as_view()),
    path('work-report/export/', WorkReportExportView.as_view()),

    path('reset/', ResetDatabaseView.as_view()),
]
