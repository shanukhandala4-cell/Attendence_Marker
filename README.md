# AttendX — Academic Attendance Tracker & Analytics Portal

A multi-tenant Flask web application that connects directly to Google Sheets via shareable links, enabling faculty to mark attendance and students to analyze their attendance records in real time.

## Key Features
- **Google OAuth 2.0 Sign-In:** Secure login with automatic role detection (`Faculty Editor` vs `Student View-Only`) based on Google Sheet permissions.
- **Link-Only Multi-Sheet Dashboard:** Connect multiple batch or semester Google Sheets via URL without scanning unrelated Drive files.
- **Live Attendance Analytics:** Subject-wise tabs, KPI cards, last-class attendance badges, search filters, and customizable defaulter threshold alerts.
- **Smart Calendar Attendance Marking:** Pick any date from a calendar; if the date does not exist in the sheet, a new column is safely inserted before the monthly summary column while preserving all existing formulas.
- **Dark & Light Mode:** Persistent theme switcher across all views.

## 🛠️ Tech Stack & Libraries Used

* **Backend Framework:** Python 3, Flask (`app.py`)
* **Database & ORM:** SQLite + Flask-SQLAlchemy (`attendance_links.db`) — stores user-linked spreadsheet URLs and metadata per Google account.
* **Authentication & Security:** Google OAuth 2.0 (`google-auth-oauthlib`, `google-auth`) with PKCE (`code_verifier`) and offline refresh tokens.
* **Spreadsheet Engine:** Google Sheets API v4 & `gspread` (`sheet_parser.py`) — dynamically parses multi-tab academic workbooks, verifies edit/view permissions, and writes attendance cells (`1` for Present, blank for Absent).
* **Frontend & UI:** HTML5, Bootstrap 5.3, Bootstrap Icons, Vanilla JavaScript, and CSS Variables for persistent Dark/Light themes.

---

## ⚙️ How the System Works (Page-by-Page Breakdown)

### 1. Authentication & Login Page (`/` & `/login` → `templates/login.html`)
* **Purpose:** Authenticates users securely using their Google Account without needing separate passwords.
* **How It Works:**
  1. Clicking **Continue with Google** initiates an OAuth 2.0 flow requesting `openid`, `userinfo.email`, `userinfo.profile`, `spreadsheets`, and `drive.metadata.readonly` scopes.
  2. Upon Google callback (`/callback`), the app verifies the PKCE `code_verifier`, stores the OAuth access/refresh tokens in the Flask session, and fetches the user's name, email, and profile picture.
  3. If an access token expires later, a global `RefreshError` handler safely clears the stale session and redirects to the login screen.

---

### 2. Multi-Sheet Dashboard (`/dashboard` → `templates/dashboard.html`)
* **Purpose:** Acts as a personalized workspace where each user sees only the batch/semester spreadsheets they have explicitly linked.
* **How It Works:**
  1. **Link-Only Connection (Floating `+` Button):** Clicking the bottom-right floating action button opens a modal where the user pastes a Google Sheet URL.
  2. **ID Extraction & Permission Detection:** `extract_sheet_id()` extracts the `spreadsheet_id` from the URL. `check_sheet_access()` opens the sheet via `gspread` and performs a non-destructive write check on cell `A1` to determine the user's real Google Drive permission:
     * **Write Access (`can_edit = True`):** Displays a green **Faculty Access (Edit & Analyze)** badge.
     * **Read-Only Access (`can_edit = False`):** Displays a blue **Student Access (View Only)** badge.
  3. **Persistent Storage & Caching:** Linked sheet records are stored per user email in SQLite (`LinkedSheet` table), while verified permissions are cached in the session (`get_cached_sheet_access`) to eliminate redundant API calls on page reloads.

---

### 3. Analytics & Inspection Page (`/sheet/<sheet_id>` → `templates/sheet_view.html`)
* **Purpose:** A dedicated, read-only analytical view for inspecting subject-wise attendance, class averages, and defaulter lists.
* **How It Works:**
  1. **Dynamic Structure Detection (`_detect_structure`):** Scans the top 15 rows of the active worksheet tab (`601`, `601 L`, `602`, etc.) to automatically locate `Enrollment No.`, `Student Name`, `Total Classes`, `Overall %`, and date columns—even if row/column positions shift slightly across semesters.
  2. **Accurate Percentage & KPI Calculation:** Counts actual `1` entries across all conducted date columns to compute live attendance percentages, class averages, and last-class status (`Present` / `Absent`) badges.
  3. **Interactive Filtering:**
     * **Subject Tabs:** Switch seamlessly between theory tabs, lab tabs, and the `Summary` sheet.
     * **Variable Defaulter Threshold:** Enter any minimum percentage (default `75%`) and click **Defaulters Only** to isolate at-risk students in red.
     * **Live Search:** Instant client-side filtering by student name or enrollment number.
  4. **Role-Gated Trigger:** The **Mark Attendance** button in the top navigation bar is rendered **only** if `can_edit` is `True`. Students never see marking controls.

---

### 4. Pre-Marking Setup Modal & Dedicated Marking Page (`/sheet/<sheet_id>/mark` → `templates/mark_attendance.html`)
* **Purpose:** Provides a distraction-free, mobile-friendly roster interface for faculty to record or edit attendance for a specific subject and calendar date.
* **How It Works:**
  1. **Calendar Date & Subject Selection:** Clicking **Mark Attendance** opens a setup modal where the teacher selects the target subject tab and picks a date from a calendar input (defaulting to today's date).
  2. **Smart Column Resolution (`get_or_create_date_column`):**
     * If the chosen date already exists in the sheet, that exact column opens with existing `1` marks pre-checked for editing.
     * If the chosen date does **not** exist in the sheet, the backend locates that month's summary column (e.g., `FEB CLASS`, `MAR CLASS`, `JUNE CLASS`), inserts a new column immediately before it, adds the date header and `=SUM(...)` row formula, and updates the adjacent monthly `=COUNTIF(...)` and student `=SUM(...)` ranges so all master sheet formulas stay intact.
  3. **Batch Cell Update (`update_attendance_column`):** Tapping any student row toggles their status (`Present (1)` vs `Absent`) and updates the live Present counter. Submitting the form sends a single batched `ws.update_cells()` request with `USER_ENTERED` mode—writing `1` for present students and clearing cells (`""`) for absent students—triggering automatic recalculation across the entire Google Sheet.

---

## 📁 Project Structure

```text
Attendance_ marker/
├── app.py                  # Flask routes, OAuth2 flow, SQLite models & session caching
├── sheet_parser.py         # Dynamic Google Sheets parser, formula-safe column inserter & batch writer
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes client_secret.json, local SQLite DB & cache files
├── README.md               # Project documentation
├── client_secret.json      # Google Cloud OAuth credentials (not tracked in Git)
└── templates/
    ├── login.html          # Google OAuth login screen (Dark/Light theme)
    ├── dashboard.html      # Linked batch sheets list & floating '+' link modal
    ├── sheet_view.html     # Subject tabs, KPI cards, defaulter filter & calendar setup modal
    └── mark_attendance.html# Dedicated attendance marking roster with live counter
