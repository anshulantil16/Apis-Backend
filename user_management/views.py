import pandas as pd
import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse
import io
from rest_framework.parsers import MultiPartParser, FormParser
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side


def _format_cell(val):
    """Convert any pandas/Excel cell value to a clean string."""
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except (TypeError, ValueError):
        pass
    if isinstance(val, datetime.time):
        return val.strftime('%H:%M')
    if isinstance(val, datetime.datetime):
        return val.strftime('%d/%m/%Y')
    if isinstance(val, datetime.date):
        return val.strftime('%d/%m/%Y')
    s = str(val).strip()
    return '' if s in ('nan', 'NaT', 'None', 'NaN') else s


def _read_raw(file, file_name):
    """Read file into a headerless DataFrame, resetting the file cursor first."""
    file.seek(0)
    if file_name.endswith('.csv'):
        try:
            return pd.read_csv(file, header=None, dtype=str)
        except UnicodeDecodeError:
            file.seek(0)
            return pd.read_csv(file, header=None, dtype=str, encoding='latin1')
    else:
        return pd.read_excel(file, header=None)


def _parse_pocket_hrms(file, file_name):
    """
    Pocket HRMS exports embed company metadata rows before the actual column
    headers.  This function finds the real header row (the one containing
    'Code', 'Name', and 'Type') and returns a clean DataFrame.
    """
    df_raw = _read_raw(file, file_name)

    required = {'code', 'name', 'type'}
    header_idx = None
    for idx, row in df_raw.iterrows():
        vals = {str(v).strip().lower() for v in row.values if str(v).strip().lower() not in ('', 'nan')}
        if required.issubset(vals):
            header_idx = idx
            break

    if header_idx is None:
        return None, "Could not locate column headers (Code / Name / Type) in the uploaded file."

    file.seek(0)
    if file_name.endswith('.csv'):
        try:
            df = pd.read_csv(file, header=header_idx)
        except UnicodeDecodeError:
            file.seek(0)
            df = pd.read_csv(file, header=header_idx, encoding='latin1')
    else:
        df = pd.read_excel(file, header=header_idx)

    df.columns = df.columns.astype(str).str.replace('\n', ' ', regex=False).str.strip()

    # Drop leftover metadata rows (any row whose 'Code' cell is empty or
    # still contains the literal word "Code" from a repeated header band)
    code_col = next((c for c in df.columns if c.strip().lower() == 'code'), None)
    if code_col:
        mask = df[code_col].apply(lambda x: str(x).strip().lower() not in ('', 'nan', 'code'))
        df = df[mask]

    # Stringify everything (handles datetime.time cells from Excel)
    for col in df.columns:
        df[col] = df[col].apply(_format_cell)

    return df, None


class ExcelUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        file = request.FILES.get('file')

        tool = request.data.get('tool', 'joining')

        if not file:
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            file_name = file.name.lower()

            # ── Delhi / Pocket HRMS attendance ──────────────────────────────
            if tool == 'delhi':
                df, err = _parse_pocket_hrms(file, file_name)
                if err:
                    return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
                headers = list(df.columns)
                data = df.to_dict(orient='records')
                return Response({"message": "File processed successfully", "headers": headers, "data": data})

            # ── Generic read for all other non-joining tools ─────────────────
            if file_name.endswith('.csv'):
                try:
                    df = pd.read_csv(file)
                except UnicodeDecodeError:
                    file.seek(0)
                    df = pd.read_csv(file, encoding='latin1')
            else:
                df = pd.read_excel(file)

            df.columns = df.columns.astype(str).str.replace('\n', ' ', regex=False).str.replace('\r', '', regex=False).str.strip()

            if tool != 'joining':
                df = df.fillna('')
                headers = list(df.columns)
                data = df.to_dict(orient='records')
                return Response({
                    "message": "File processed successfully",
                    "headers": headers,
                    "data": data
                })
            
            # Filter rows where 'HR Remarks' contains 'Done'
            if 'HR Remarks' in df.columns:
                remarks = df['HR Remarks'].astype(str).str.lower()
                is_done = remarks.str.contains(r'\bdone\b', regex=True, na=False) & ~remarks.str.contains('not done', na=False)
                df = df[is_done]
            
            df = df.fillna('')
            
            final_rows = []
            sr_no = 1
            
            import re
            def get_val(row, *substrings):
                for col in df.columns:
                    col_lower = str(col).lower()
                    for sub in substrings:
                        if re.search(r'\b' + re.escape(sub.lower()) + r'\b', col_lower):
                            val = row[col]
                            if val != '':
                                return val
                return ''

            def format_date(val):
                if pd.isna(val) or val == '':
                    return ''
                try:
                    dt = pd.to_datetime(val)
                    return dt.strftime('%d-%m-%Y')
                except:
                    s = str(val).strip()
                    if ' 00:00:00' in s:
                        return s.replace(' 00:00:00', '')
                    return s

            def infer_gender(relation, explicit_gender):
                if explicit_gender and str(explicit_gender).strip() != '':
                    return explicit_gender
                rel = str(relation).strip().upper()
                if rel in ['WIFE', 'DAUGHTER', 'MOTHER']:
                    return 'FEMALE'
                if rel in ['HUSBAND', 'SON', 'FATHER']:
                    return 'MALE'
                return ''

            for idx, row in df.iterrows():
                # Extract Primary Employee Names
                first_name = get_val(row, 'first name')
                middle_name = get_val(row, 'middle name')
                last_name = get_val(row, 'last name')
                
                names = [str(n).strip() for n in [first_name, middle_name, last_name] if str(n).strip()]
                full_name = ' '.join(names) if names else get_val(row, 'name', 'employee name')
                
                emp_no = get_val(row, 'emp id', 'employee code', 'employee number', 'emp code')
                
                primary_row = {
                    "Sr. No": sr_no,
                    "Employee Number": "",
                    "Name Of Member": full_name,
                    "Relation": "SELF",
                    "Gender": get_val(row, 'gender', 'sex'),
                    "Date Of Birth": format_date(get_val(row, 'dob', 'date of birth')),
                    "Age": get_val(row, 'age'),
                    "Sum Insured": "",
                    "GPA": "",
                    "DOJ": format_date(get_val(row, 'doj', 'date of joining')),
                    "Designation Name": get_val(row, 'designation'),
                    "Location": get_val(row, 'location', 'headquarter', 'district'),
                    "Contact number": get_val(row, 'mobile', 'contact', 'phone'),
                    "Email details": get_val(row, 'email')
                }
                final_rows.append(primary_row)
                
                # Dynamically extract family members
                # Common patterns in HR forms for family
                family_patterns = [
                    ('Spouse', 'WIFE'), ('Wife', 'WIFE'), ('Husband', 'HUSBAND'), 
                    ('Child 1', 'CHILD'), ('Child 2', 'CHILD'), ('Child 3', 'CHILD'), 
                    ('Son', 'SON'), ('Daughter', 'DAUGHTER'), 
                    ('Father', 'FATHER'), ('Mother', 'MOTHER'),
                    ('Dependant 1', 'DEPENDANT'), ('Dependant 2', 'DEPENDANT')
                ]
                
                for pat, rel_name in family_patterns:
                    pat_name_col = None
                    pat_dob_col = None
                    pat_gender_col = None
                    pat_age_col = None
                    
                    for col in df.columns:
                        col_lower = str(col).lower()
                        pat_lower = pat.lower()
                        
                        if re.search(r'\b' + re.escape(pat_lower) + r'\b', col_lower):
                            if 'email' in col_lower or 'mail' in col_lower:
                                continue # Skip email columns so they aren't incorrectly mapped as names
                            if 'name' in col_lower:
                                pat_name_col = col
                            elif 'dob' in col_lower or 'birth' in col_lower:
                                pat_dob_col = col
                            elif 'gender' in col_lower or 'sex' in col_lower:
                                pat_gender_col = col
                            elif 'age' in col_lower:
                                pat_age_col = col
                            elif not pat_name_col:
                                pat_name_col = col # Fallback if it just says "Spouse"
                                
                    if pat_name_col:
                        member_name = row[pat_name_col]
                        if member_name != '' and pd.notna(member_name):
                            computed_gender = infer_gender(rel_name, row[pat_gender_col] if pat_gender_col and pd.notna(row[pat_gender_col]) else "")
                            computed_relation = rel_name
                            if rel_name in ['CHILD', 'DEPENDANT']:
                                if str(computed_gender).strip().upper() == 'MALE':
                                    computed_relation = 'SON'
                                elif str(computed_gender).strip().upper() == 'FEMALE':
                                    computed_relation = 'DAUGHTER'
                                    
                            family_row = {
                                "Sr. No": "",
                                "Employee Number": "",
                                "Name Of Member": str(member_name).strip(),
                                "Relation": computed_relation,
                                "Gender": computed_gender,
                                "Date Of Birth": format_date(row[pat_dob_col] if pat_dob_col else ""),
                                "Age": row[pat_age_col] if pat_age_col else "",
                                "Sum Insured": "",
                                "GPA": "",
                                "DOJ": "",
                                "Designation Name": "",
                                "Location": "",
                                "Contact number": "",
                                "Email details": ""
                            }
                            # Check if name is mistakenly an email address (e.g. they put email in the 'Son' column)
                            if '@' not in str(member_name):
                                final_rows.append(family_row)
                
                # Insert blank row to separate families
                blank_row = {k: "" for k in primary_row.keys()}
                final_rows.append(blank_row)
                
                sr_no += 1
            
            if not final_rows:
                return Response({"headers": [], "data": []})

            headers = list(final_rows[0].keys())
            
            return Response({
                "message": "File processed successfully",
                "headers": headers,
                "data": final_rows
            })
            
        except Exception as e:
            import traceback
            traceback.print_exc()
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
                df.to_excel(writer, index=False, sheet_name='Extracted Data')
                
                workbook = writer.book
                worksheet = writer.sheets['Extracted Data']
                
                # Styles
                header_fill = PatternFill(start_color='333F50', end_color='333F50', fill_type='solid') # Dark Blue
                header_font = Font(color='FFFFFF', bold=True)
                center_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                
                green_fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')
                
                thin_border = Border(
                    left=Side(style='thin'), 
                    right=Side(style='thin'), 
                    top=Side(style='thin'), 
                    bottom=Side(style='thin')
                )
                
                # Format Headers
                for cell in worksheet[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = center_alignment
                    cell.border = thin_border
                
                # Find column indices dynamically
                emp_col_idx = None
                rel_col_idx = None
                for idx, col in enumerate(df.columns, start=1):
                    if str(col).strip().upper() == 'EMPLOYEE NUMBER':
                        emp_col_idx = idx
                    elif str(col).strip().upper() == 'RELATION':
                        rel_col_idx = idx
                
                # Format Data Rows
                for row_idx, row in enumerate(worksheet.iter_rows(min_row=2, max_col=worksheet.max_column, max_row=worksheet.max_row), start=2):
                    relation_val = ""
                    if rel_col_idx:
                        relation_val = str(worksheet.cell(row=row_idx, column=rel_col_idx).value).upper()
                    
                    for col_idx, cell in enumerate(row, start=1):
                        cell.border = thin_border
                        cell.alignment = center_alignment
                        
                        # If it's the "Employee Number" column and Relation is SELF, color it green
                        if emp_col_idx and col_idx == emp_col_idx and relation_val == 'SELF':
                            cell.fill = green_fill
                            cell.font = Font(color='000000', bold=False)
                            
                # Adjust column widths
                for col in worksheet.columns:
                    max_length = 0
                    column = col[0].column_letter
                    for cell in col:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(cell.value)
                        except:
                            pass
                    adjusted_width = (max_length + 2)
                    # Set max width to avoid extremely wide columns
                    worksheet.column_dimensions[column].width = min(adjusted_width, 40)
            
            buffer.seek(0)
            
            response = HttpResponse(
                buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = 'attachment; filename=filtered_data.xlsx'
            
            return response
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
