import re
from datetime import datetime
import gspread
from gspread.utils import rowcol_to_a1
from gspread.exceptions import APIError

DATE_PATTERN = re.compile(
    r'^(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}[-\s][A-Za-z]{3,9}(?:[-\s]\d{2,4})?)'
)

MONTH_NAMES = {
    1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR', 5: 'MAY', 6: 'JUNE',
    7: 'JULY', 8: 'AUG', 9: 'SEP', 10: 'OCT', 11: 'NOV', 12: 'DEC'
}

def extract_sheet_id(url):
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

def check_sheet_access(creds, sheet_id):
    """Checks sheet title and Edit vs View-Only permissions cleanly."""
    gc = gspread.authorize(creds)
    try:
        sh = gc.open_by_key(sheet_id)
    except APIError as e:
        err_msg = e.response.text if hasattr(e, 'response') else repr(e)
        if "Operation is not supported for this document" in err_msg or "FAILED_PRECONDITION" in err_msg:
            raise Exception("This file is still in Excel (.xlsx) format. Open it in Google Sheets, click 'File > Save as Google Sheets', and paste the new link.")
        raise Exception(f"Google Sheets API Error ({e.response.status_code}): Unable to access spreadsheet.")

    title = sh.title
    ws = sh.sheet1
    
    try:
        current_val = ws.acell('A1').value
        ws.update_acell('A1', current_val if current_val is not None else "")
        can_edit = True
    except APIError as e:
        if e.response.status_code in [403, 400]:
            can_edit = False
        else:
            raise Exception("Unable to verify write permissions for this spreadsheet.")
            
    return title, can_edit

def _normalize_date_str(raw_val):
    s = str(raw_val).strip().split(' ')[0]
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%d-%b-%Y', '%d-%B-%Y'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return s

def _detect_structure(all_values):
    student_header_idx = 8
    enroll_col = 1
    name_col = 2
    attended_col = 3
    pct_col = 4

    for r_idx in range(min(15, len(all_values))):
        row_lower = [str(c).strip().lower() for c in all_values[r_idx]]
        for c_idx, val in enumerate(row_lower):
            if 'enrollment' in val or 'roll' in val:
                student_header_idx = r_idx
                enroll_col = c_idx
            elif 'student name' in val or val == 'name':
                student_header_idx = r_idx
                name_col = c_idx

    found_attended = False
    found_pct = False
    for check_r in [max(0, student_header_idx - 1), student_header_idx]:
        if check_r < len(all_values):
            for c_idx in range(name_col + 1, min(10, len(all_values[check_r]))):
                v = str(all_values[check_r][c_idx]).strip().lower()
                if not found_attended and ('total' in v and 'class' in v):
                    attended_col = c_idx
                    found_attended = True
                elif not found_pct and ('overall' in v or v == '%'):
                    pct_col = c_idx
                    found_pct = True

    best_date_row_idx = max(0, student_header_idx - 1)
    max_dates_found = 0
    for r_idx in range(min(15, len(all_values))):
        count = sum(
            1 for c_idx in range(name_col + 1, len(all_values[r_idx]))
            if DATE_PATTERN.match(str(all_values[r_idx][c_idx]).strip())
        )
        if count > max_dates_found:
            max_dates_found = count
            best_date_row_idx = r_idx

    return {
        'student_header_idx': student_header_idx,
        'data_start_idx': student_header_idx + 1,
        'date_row_idx': best_date_row_idx,
        'enroll_col': enroll_col,
        'name_col': name_col,
        'attended_col': attended_col,
        'pct_col': pct_col
    }

def _extract_dates(all_values, meta):
    date_row = all_values[meta['date_row_idx']] if len(all_values) > meta['date_row_idx'] else []
    header_row = all_values[meta['student_header_idx']] if len(all_values) > meta['student_header_idx'] else []
    start_col = meta['name_col'] + 1
    data_start = meta['data_start_idx']

    date_columns = []
    seen_labels = {}
    for col_idx in range(start_col, len(date_row)):
        raw_val = str(date_row[col_idx]).strip()
        if DATE_PATTERN.match(raw_val):
            iso_date = _normalize_date_str(raw_val)
            if iso_date in seen_labels:
                seen_labels[iso_date] += 1
                label = f"{iso_date} (Lec {seen_labels[iso_date]})"
            else:
                seen_labels[iso_date] = 1
                label = iso_date

            conducted_count = header_row[col_idx] if col_idx < len(header_row) else "0"
            has_data = any(
                len(r) > col_idx and str(r[col_idx]).strip() in ['1', 'P', 'p']
                for r in all_values[data_start:]
            )
            date_columns.append({
                'col_idx': col_idx,
                'col_num': col_idx + 1,
                'date': label,
                'iso_date': iso_date,
                'conducted': conducted_count,
                'is_marked': has_data
            })
    return date_columns

def get_or_create_date_column(creds, sheet_id, tab_name, target_iso_date):
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab_name)
    all_values = ws.get_all_values()
    meta = _detect_structure(all_values)
    date_columns = _extract_dates(all_values, meta)

    for dc in date_columns:
        if dc['iso_date'] == target_iso_date:
            return dc['col_num'], dc['date'], False

    dt = datetime.strptime(target_iso_date, '%Y-%m-%d')
    month_tag = MONTH_NAMES.get(dt.month, '')
    date_row_idx = meta['date_row_idx']
    header_row_idx = meta['student_header_idx']
    data_start_row_num = meta['data_start_idx'] + 1
    date_row = all_values[date_row_idx]

    last_student_row_num = data_start_row_num
    for r_idx in range(meta['data_start_idx'], len(all_values)):
        if len(all_values[r_idx]) > meta['name_col'] and str(all_values[r_idx][meta['name_col']]).strip():
            last_student_row_num = r_idx + 1

    insert_col_num = None
    for c_idx in range(meta['name_col'] + 1, len(date_row)):
        cell_upper = str(date_row[c_idx]).strip().upper()
        if month_tag and month_tag in cell_upper and 'CLASS' in cell_upper:
            insert_col_num = c_idx + 1
            break

    if insert_col_num is None:
        if date_columns:
            insert_col_num = date_columns[-1]['col_num'] + 1
        else:
            insert_col_num = 8

    ws.insert_cols([[]], col=insert_col_num, value_input_option='USER_ENTERED')

    col_letter = rowcol_to_a1(1, insert_col_num)[:-1]
    sum_formula = f"=SUM({col_letter}{data_start_row_num}:{col_letter}{last_student_row_num})"

    ws.update_cell(date_row_idx + 1, insert_col_num, target_iso_date)
    ws.update_cell(header_row_idx + 1, insert_col_num, sum_formula)

    updated_date_row = ws.row_values(date_row_idx + 1)
    summary_col_num = insert_col_num + 1
    if summary_col_num <= len(updated_date_row) and 'CLASS' in str(updated_date_row[summary_col_num - 1]).upper():
        first_month_col = insert_col_num
        for c in range(insert_col_num - 1, meta['name_col'] + 1, -1):
            val = str(updated_date_row[c - 1]).strip()
            if DATE_PATTERN.match(val):
                first_month_col = c
            else:
                break
        start_let = rowcol_to_a1(1, first_month_col)[:-1]
        end_let = col_letter
        hdr_row_num = header_row_idx + 1

        ws.update_cell(hdr_row_num, summary_col_num, f'=COUNTIF({start_let}{hdr_row_num}:{end_let}{hdr_row_num},">0")')

        batch_updates = []
        for r_num in range(data_start_row_num, last_student_row_num + 1):
            batch_updates.append(
                gspread.Cell(row=r_num, col=summary_col_num, value=f"=SUM({start_let}{r_num}:{end_let}{r_num})")
            )
        if batch_updates:
            ws.update_cells(batch_updates, value_input_option='USER_ENTERED')

    return insert_col_num, target_iso_date, True

def parse_subject_sheet(creds, sheet_id, tab_name=None):
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    worksheets = [ws.title for ws in sh.worksheets()]
    
    if not tab_name or tab_name not in worksheets:
        tab_name = worksheets[0]
        
    ws = sh.worksheet(tab_name)
    all_values = ws.get_all_values()
    
    if tab_name.lower() in ['summary', 'summary copy']:
        return {
            'is_summary': True,
            'tabs': worksheets,
            'current_tab': tab_name,
            'rows': all_values[6:] if len(all_values) > 6 else all_values
        }

    meta = _detect_structure(all_values)
    date_columns = _extract_dates(all_values, meta)

    faculty_name = all_values[1][2].strip() if len(all_values) > 1 and len(all_values[1]) > 2 and all_values[1][2].strip() else "Faculty Not Specified"
    subject_name = all_values[2][2].strip() if len(all_values) > 2 and len(all_values[2]) > 2 and all_values[2][2].strip() else tab_name

    conducted_date_cols = [dc for dc in date_columns if dc['is_marked']]
    calc_total_classes = len(conducted_date_cols)

    header_row = all_values[meta['student_header_idx']] if len(all_values) > meta['student_header_idx'] else []
    sheet_total_str = str(header_row[meta['attended_col']]).strip() if len(header_row) > meta['attended_col'] else ""
    try:
        total_classes_num = int(float(sheet_total_str)) if sheet_total_str else calc_total_classes
    except ValueError:
        total_classes_num = calc_total_classes
    if total_classes_num == 0 and calc_total_classes > 0:
        total_classes_num = calc_total_classes

    last_class_col = conducted_date_cols[-1]['col_idx'] if conducted_date_cols else None
    last_class_date = conducted_date_cols[-1]['date'] if conducted_date_cols else "N/A"

    students = []
    pct_sum = 0.0
    for row_idx in range(meta['data_start_idx'], len(all_values)):
        row = all_values[row_idx]
        if len(row) <= meta['name_col'] or not str(row[meta['name_col']]).strip():
            continue
            
        enroll_no = str(row[meta['enroll_col']]).strip() if len(row) > meta['enroll_col'] else "-"
        name = str(row[meta['name_col']]).strip()

        direct_attended = sum(
            1 for dc in date_columns
            if len(row) > dc['col_idx'] and str(row[dc['col_idx']]).strip() in ['1', 'P', 'p']
        )

        raw_att = str(row[meta['attended_col']]).strip() if len(row) > meta['attended_col'] else ""
        try:
            attended_val = int(float(raw_att)) if raw_att else direct_attended
        except ValueError:
            attended_val = direct_attended
        if attended_val == 0 and direct_attended > 0:
            attended_val = direct_attended

        raw_pct = str(row[meta['pct_col']]).strip().replace('%', '') if len(row) > meta['pct_col'] else ""
        try:
            pct = round(float(raw_pct), 2)
        except ValueError:
            pct = 0.0

        if pct == 0.0 and attended_val > 0 and total_classes_num > 0:
            pct = round((attended_val / total_classes_num) * 100, 2)

        pct_sum += pct

        last_status = "N/A"
        if last_class_col is not None:
            cell_val = str(row[last_class_col]).strip() if len(row) > last_class_col else ""
            last_status = "Present" if cell_val in ["1", "P", "p"] else "Absent"

        attendance_map = {}
        for dc in date_columns:
            c_idx = dc['col_idx']
            attendance_map[dc['date']] = (str(row[c_idx]).strip() in ['1', 'P', 'p']) if len(row) > c_idx else False

        students.append({
            'row_num': row_idx + 1,
            'enroll_no': enroll_no,
            'name': name,
            'attended': attended_val,
            'percentage': pct,
            'last_status': last_status,
            'attendance_map': attendance_map
        })

    avg_attendance = round(pct_sum / len(students), 1) if students else 0.0
    subject_tabs = [w for w in worksheets if w.lower() not in ['summary', 'summary copy', 'training']]

    return {
        'is_summary': False,
        'tabs': worksheets,
        'subject_tabs': subject_tabs,
        'current_tab': tab_name,
        'faculty': faculty_name,
        'subject': subject_name,
        'total_classes': total_classes_num,
        'avg_attendance': avg_attendance,
        'date_columns': date_columns,
        'last_class_date': last_class_date,
        'students': students
    }

def update_attendance_column(creds, sheet_id, tab_name, col_num, present_row_nums, all_student_rows):
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(sheet_id).worksheet(tab_name)
    
    cells_to_update = []
    for r_num in all_student_rows:
        val = 1 if r_num in present_row_nums else ""
        cells_to_update.append(gspread.Cell(row=r_num, col=col_num, value=val))
        
    if cells_to_update:
        ws.update_cells(cells_to_update, value_input_option='USER_ENTERED')
