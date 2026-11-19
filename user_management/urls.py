from django.urls import path
from .views import ExcelUploadView, ExcelExportView, PhotoCheckView, PhotoThumbView

urlpatterns = [
    path("upload-excel/", ExcelUploadView.as_view(), name="upload-excel"),
    path("export-excel/", ExcelExportView.as_view(), name="export-excel"),
    path("photo-check/", PhotoCheckView.as_view(), name="photo-check"),
    path("photo-thumbs/", PhotoThumbView.as_view(), name="photo-thumbs"),
]
