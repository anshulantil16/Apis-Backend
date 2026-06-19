from django.urls import path
from .views import (
    SessionListCreateView, SessionDetailView,
    ImportEmployeesView, DownloadTemplateView,
    EmployeeUpdateView, BulkUpdateView, ExportResultsView,
)

urlpatterns = [
    path('sessions/', SessionListCreateView.as_view()),
    path('sessions/<int:session_id>/', SessionDetailView.as_view()),
    path('sessions/<int:session_id>/import/', ImportEmployeesView.as_view()),
    path('sessions/<int:session_id>/export/', ExportResultsView.as_view()),
    path('sessions/<int:session_id>/bulk-update/', BulkUpdateView.as_view()),
    path('employees/<int:emp_id>/', EmployeeUpdateView.as_view()),
    path('template/', DownloadTemplateView.as_view()),
]
