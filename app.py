import os
import json
from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix

# Localhost HTTP testing ke liye; live HTTPS server par automatic disable rahega
if os.environ.get('RENDER') is None and os.environ.get('FLASK_ENV') != 'production':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask import Flask, redirect, url_for, session, request, render_template, flash
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from sheet_parser import (
    extract_sheet_id,
    check_sheet_access,
    get_or_create_date_column,
    parse_subject_sheet,
    update_attendance_column
)

app = Flask(__name__)
# Cloud proxy (Render/Railway) par HTTPS redirect URI sahi detect karne ke liye
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-local-secret-key-attendx')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///attendance_links.db')
db = SQLAlchemy(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRETS_FILE = os.path.join(BASE_DIR, "client_secret.json")

SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.metadata.readonly'
]

def create_oauth_flow(state=None):
    """Local file ya Cloud Environment Variable se Google OAuth Flow banata hai."""
    redirect_uri = url_for('callback', _external=True)
    env_secret = os.environ.get('GOOGLE_CLIENT_SECRET_JSON')
    if env_secret:
        client_config = json.loads(env_secret)
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            state=state,
            redirect_uri=redirect_uri
        )
    return Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri
    )

class LinkedSheet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(150), nullable=False)
    sheet_id = db.Column(db.String(200), nullable=False)
    sheet_title = db.Column(db.String(200), nullable=False)
    sheet_url = db.Column(db.String(500), nullable=False)

with app.app_context():
    db.create_all()

def get_google_creds():
    if 'credentials' not in session:
        return None
    c = session['credentials']
    if not c.get('token') or not c.get('client_id') or not c.get('client_secret'):
        session.clear()
        return None
    return Credentials(**c)

def get_cached_sheet_access(creds, sheet_id, force_refresh=False):
    cache = session.setdefault('access_cache', {})
    if not force_refresh and sheet_id in cache:
        return cache[sheet_id]['title'], cache[sheet_id]['can_edit']
    title, can_edit = check_sheet_access(creds, sheet_id)
    cache[sheet_id] = {'title': title, 'can_edit': can_edit}
    session.modified = True
    return title, can_edit

@app.errorhandler(RefreshError)
def handle_refresh_error(e):
    session.clear()
    flash("Your Google session expired. Please sign in again to continue.", "warning")
    return redirect(url_for('index'))

@app.route('/')
def index():
    if 'user' in session and get_google_creds():
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login')
def login():
    flow = create_oauth_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true'
    )
    session['state'] = state
    session['code_verifier'] = getattr(flow, 'code_verifier', None)
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    flow = create_oauth_flow(state=session.get('state'))
    if session.get('code_verifier'):
        flow.code_verifier = session.get('code_verifier')

    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session['credentials'] = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }
    
    oauth2_service = build('oauth2', 'v2', credentials=creds)
    user_info = oauth2_service.userinfo().get().execute()
    session['user'] = {
        'name': user_info.get('name'),
        'email': user_info.get('email'),
        'picture': user_info.get('picture')
    }
    return redirect(url_for('dashboard'))

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    creds = get_google_creds()
    if not creds:
        return redirect(url_for('index'))
        
    user_email = session['user']['email']
    
    if request.method == 'POST':
        sheet_url = request.form.get('sheet_url', '').strip()
        sheet_id = extract_sheet_id(sheet_url)
        if not sheet_id:
            flash("Invalid Google Sheet URL. Please ensure the link contains '/spreadsheets/d/'.", "danger")
        else:
            existing = LinkedSheet.query.filter_by(user_email=user_email, sheet_id=sheet_id).first()
            if existing:
                flash("This spreadsheet is already connected to your dashboard.", "warning")
            else:
                try:
                    title, _ = get_cached_sheet_access(creds, sheet_id, force_refresh=True)
                    new_link = LinkedSheet(
                        user_email=user_email,
                        sheet_id=sheet_id,
                        sheet_title=title,
                        sheet_url=sheet_url
                    )
                    db.session.add(new_link)
                    db.session.commit()
                    flash(f"Successfully connected '{title}'.", "success")
                except RefreshError:
                    raise
                except Exception as e:
                    flash(f"Failed to connect sheet: {str(e)}", "danger")
        return redirect(url_for('dashboard'))

    saved_sheets = LinkedSheet.query.filter_by(user_email=user_email).order_by(LinkedSheet.id.desc()).all()
    cards = []
    for s in saved_sheets:
        try:
            title, can_edit = get_cached_sheet_access(creds, s.sheet_id)
            cards.append({
                'db_id': s.id,
                'sheet_id': s.sheet_id,
                'title': title,
                'can_edit': can_edit
            })
        except RefreshError:
            raise
        except Exception:
            cards.append({
                'db_id': s.id,
                'sheet_id': s.sheet_id,
                'title': s.sheet_title + " (Access Unavailable)",
                'can_edit': False
            })

    return render_template('dashboard.html', user=session['user'], cards=cards)

@app.route('/remove_sheet/<int:db_id>')
def remove_sheet(db_id):
    if 'user' not in session:
        return redirect(url_for('index'))
    sheet = LinkedSheet.query.filter_by(id=db_id, user_email=session['user']['email']).first()
    if sheet:
        cache = session.get('access_cache', {})
        cache.pop(sheet.sheet_id, None)
        db.session.delete(sheet)
        db.session.commit()
        flash("Spreadsheet removed from your list.", "info")
    return redirect(url_for('dashboard'))

@app.route('/sheet/')
def sheet_root_redirect():
    return redirect(url_for('dashboard'))

@app.route('/sheet/<sheet_id>')
def view_sheet(sheet_id):
    creds = get_google_creds()
    if not creds:
        return redirect(url_for('index'))
        
    tab_name = request.args.get('tab')
    threshold = float(request.args.get('threshold', 75))
    
    try:
        title, can_edit = get_cached_sheet_access(creds, sheet_id)
        data = parse_subject_sheet(creds, sheet_id, tab_name)
    except RefreshError:
        raise
    except Exception as e:
        flash(f"Error loading spreadsheet: {str(e)}", "danger")
        return redirect(url_for('dashboard'))

    today_str = datetime.now().strftime('%Y-%m-%d')
    
    return render_template(
        'sheet_view.html',
        user=session['user'],
        sheet_id=sheet_id,
        title=title,
        can_edit=can_edit,
        threshold=threshold,
        data=data,
        today_str=today_str
    )

@app.route('/sheet/<sheet_id>/mark', methods=['GET', 'POST'])
def mark_attendance(sheet_id):
    creds = get_google_creds()
    if not creds:
        return redirect(url_for('index'))
        
    title, can_edit = get_cached_sheet_access(creds, sheet_id)
    if not can_edit:
        flash("Access Denied: You only have view permissions for this spreadsheet.", "danger")
        return redirect(url_for('view_sheet', sheet_id=sheet_id))

    tab_name = request.args.get('tab') or request.form.get('tab')
    selected_date = request.args.get('date') or request.form.get('selected_date') or datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        col_num = int(request.form.get('col_num'))
        present_rows = [int(r) for r in request.form.getlist('present_rows')]
        all_rows = [int(r) for r in request.form.getlist('all_rows')]
        update_attendance_column(creds, sheet_id, tab_name, col_num, present_rows, all_rows)
        flash(f"Attendance for {tab_name} on {selected_date} has been saved to Google Sheets.", "success")
        return redirect(url_for('view_sheet', sheet_id=sheet_id, tab=tab_name))

    col_num, resolved_date, was_created = get_or_create_date_column(creds, sheet_id, tab_name, selected_date)
    if was_created:
        flash(f"New date column '{resolved_date}' was automatically added to '{tab_name}' while preserving all formulas.", "info")

    data = parse_subject_sheet(creds, sheet_id, tab_name)

    return render_template(
        'mark_attendance.html',
        user=session['user'],
        sheet_id=sheet_id,
        title=title,
        data=data,
        selected_date=resolved_date,
        target_col_num=col_num
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
