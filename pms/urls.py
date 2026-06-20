from django.urls import path
from .views import PMSListView, PMSImportView, PMSEmployeeUpdateView, PMSTemplateView, PMSExportView, PMSLoginView

urlpatterns = [
    path('login/',                  PMSLoginView.as_view()),
    path('employees/',              PMSListView.as_view()),
    path('import/',                 PMSImportView.as_view()),
    path('employees/<int:emp_id>/', PMSEmployeeUpdateView.as_view()),
    path('template/',               PMSTemplateView.as_view()),
    path('export/',                 PMSExportView.as_view()),
]
