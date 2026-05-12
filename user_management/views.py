import pandas as pd
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse
import io
from rest_framework.parsers import MultiPartParser, FormParser

class ExcelUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        file = request.FILES.get('file')
        
        if not file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Read the entire excel sheet
            df = pd.read_excel(file)
            
            # Clean headers (remove newlines and extra spaces)
            df.columns = df.columns.astype(str).str.replace('\n', ' ', regex=False).str.replace('\r', '', regex=False).str.strip()
            
            # Filter rows where 'HR Remarks' contains 'Done' (handling trailing spaces, punctuation, or extra text)
            if 'HR Remarks' in df.columns:
                remarks = df['HR Remarks'].astype(str).str.lower()
                # Matches 'done' as a distinct word, but excludes 'not done' just in case
                is_done = remarks.str.contains(r'\bdone\b', regex=True, na=False) & ~remarks.str.contains('not done', na=False)
                df = df[is_done]
            
            # Extract only the requested columns
            expected_columns = [
                "First Name",
                "First Name (As per Bank Name)",
                "(As per Bank Name)",
                "Middle Name",
                "Last Name",
                "Designation",
                "Email address",
                "Mobile Number",
                "Headquarter- District"
            ]
            
            # Keep only columns that exist in the dataframe
            existing_columns = [col for col in expected_columns if col in df.columns]
            if existing_columns:
                df = df[existing_columns]
            
            # Fill NaN values with empty string for JSON serialization
            df = df.fillna('')
            
            data = df.to_dict(orient='records')
            
            return Response({
                "message": "File processed successfully",
                "headers": df.columns.tolist(),
                "data": data
            })
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ExcelExportView(APIView):
    def post(self, request, *args, **kwargs):
        try:
            data = request.data.get('data', [])
            if not data:
                return Response({"error": "No data provided to export"}, status=status.HTTP_400_BAD_REQUEST)
                
            df = pd.DataFrame(data)
            
            # Create an in-memory buffer
            buffer = io.BytesIO()
            
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            buffer.seek(0)
            
            response = HttpResponse(
                buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = 'attachment; filename=filtered_data.xlsx'
            
            return response
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
