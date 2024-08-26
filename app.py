from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
from authlib.integrations.flask_client import OAuth
from datetime import datetime, timedelta
import os
import psycopg2
from psycopg2.extras import DictCursor
from urllib.parse import urlparse
from collections import Counter, defaultdict
from dotenv import load_dotenv
from openai import OpenAI
from functools import wraps
import importlib.metadata
import sys

# Add compatibility layer for importlib_metadata
sys.modules['importlib_metadata'] = importlib.metadata

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your_secret_key')
app.config['ADMIN_EMAIL'] = os.getenv('ADMIN_EMAIL')

oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Database setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sqlite_path = os.path.join(BASE_DIR, 'activities.db')
DATABASE_URL = os.environ.get('DATABASE_URL', f'sqlite:///{sqlite_path}')

def get_db_connection():
    global DATABASE_URL
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    url = urlparse(DATABASE_URL)
    if url.scheme == 'sqlite':
        import sqlite3
        db_path = url.path[1:] if url.path.startswith('/') else url.path
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        conn = psycopg2.connect(
            dbname=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
        conn.cursor_factory = DictCursor
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS activities
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    hours REAL NOT NULL)''')
    conn.commit()
    cur.close()
    conn.close()

def classify_activity(activity):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that classifies activities into categories."},
                {"role": "user", "content": f"Classify the following activity into one of these categories: fitness, reading, housework, drive, cooking, or other. Only respond with the category name. Activity: {activity}"}
            ]
        )
        return response.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"Error in classification: {e}")
        return 'other'

def get_activities(user_id, date):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM activities WHERE user_id = ? AND date = ? ORDER BY id DESC', (user_id, date))
    activities = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(activity) for activity in activities]

def add_activity(user_id, date, activity, category, hours):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO activities (user_id, date, activity, category, hours) VALUES (?, ?, ?, ?, ?)',
                 (user_id, date, activity, category, hours))
    conn.commit()
    cur.close()
    conn.close()

def update_activity(activity_id, user_id, date, activity, category, hours):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE activities SET date = ?, activity = ?, category = ?, hours = ? WHERE id = ? AND user_id = ?',
                 (date, activity, category, hours, activity_id, user_id))
    conn.commit()
    cur.close()
    conn.close()

def get_activity(activity_id, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM activities WHERE id = ? AND user_id = ?', (activity_id, user_id))
    activity = cur.fetchone()
    cur.close()
    conn.close()
    return dict(activity) if activity else None

def generate_grid_data(user_id):
    today = datetime.now().date()
    start_date = today - timedelta(days=364)
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM activities WHERE user_id = ? AND date >= ?', 
                              (user_id, start_date.isoformat()))
    activities = cur.fetchall()
    cur.close()
    conn.close()

    date_activities = defaultdict(list)
    for activity in activities:
        date_activities[activity['date']].append(dict(activity))
    
    grid_data = []
    for i in range(365):
        date = (start_date + timedelta(days=i)).isoformat()
        daily_activities = date_activities.get(date, [])
        total_hours = sum(activity['hours'] for activity in daily_activities)
        category_hours = Counter()
        for activity in daily_activities:
            category_hours[activity['category']] += activity['hours']
        
        summary = [f"{category}: {hours:.1f}h" for category, hours in category_hours.items()]
        grid_data.append({
            "date": date,
            "hours": total_hours,
            "summary": summary
        })
    
    return grid_data

@app.route('/login')
def login():
    redirect_uri = url_for('authorized', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/logout')
def logout():
    session.pop('google_token', None)
    session.pop('user_id', None)
    session.pop('email', None)
    return redirect(url_for('index'))

@app.route('/callback')
def authorized():
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v2/userinfo')
    user_info = resp.json()
    session['google_token'] = token
    session['user_id'] = user_info['id']
    session['email'] = user_info['email']
    return redirect(url_for('index'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        activity = request.form.get('activity')
        category = request.form.get('category') or classify_activity(activity)
        hours = float(request.form.get('hours'))
        date = datetime.now().strftime("%Y-%m-%d")
        
        add_activity(session['user_id'], date, activity, category, hours)
        flash('Activity added successfully!', 'success')
        return redirect(url_for('index'))
    
    today = datetime.now().date()
    activities = get_activities(session['user_id'], today.isoformat())
    grid_data = generate_grid_data(session['user_id'])
    return render_template('index.html', activities=activities, grid_data=grid_data, current_date=today.isoformat(), config=app.config)

@app.route('/activities/<date>')
@login_required
def get_activities_for_date(date):
    activities = get_activities(session['user_id'], date)
    return jsonify(activities)

@app.route('/edit/<int:activity_id>', methods=['GET', 'POST'])
@login_required
def edit_activity(activity_id):
    activity = get_activity(activity_id, session['user_id'])
    if not activity:
        flash('Activity not found or you do not have permission to edit it.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        new_activity = request.form.get('activity')
        new_category = request.form.get('category') or classify_activity(new_activity)
        new_hours = float(request.form.get('hours'))
        new_date = request.form.get('date')
        
        update_activity(activity_id, session['user_id'], new_date, new_activity, new_category, new_hours)
        flash('Activity updated successfully!', 'success')
        return redirect(url_for('index'))

    return render_template('edit_activity.html', activity=activity)

@app.route('/admin/query', methods=['GET', 'POST'])
@login_required
def admin_query():
    if session['email'] != app.config['ADMIN_EMAIL']:
        abort(403)
    if request.method == 'POST':
        query = request.form.get('query')
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(query)
            results = cur.fetchall()
            return render_template('admin_query.html', results=results, query=query)
        except Exception as e:
            return render_template('admin_query.html', error=str(e), query=query)
        finally:
            cur.close()
            conn.close()
    return render_template('admin_query.html')

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Initialized the database.")

if __name__ == '__main__':
    init_db()
    app.run(debug=True)