from django.urls import path
from .views import ExcelUploadView, ExcelExportView

urlpatterns = [
    path("upload-excel/", ExcelUploadView.as_view(), name="upload-excel"),
    path("export-excel/", ExcelExportView.as_view(), name="export-excel"),
]
