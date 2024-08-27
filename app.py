import importlib.metadata
import sys

sys.modules['importlib_metadata'] = importlib.metadata

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
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    conn.cursor_factory = DictCursor
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS activities
                   (id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    habit_id INTEGER NOT NULL,
                    hours REAL NOT NULL)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS habits
                   (id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    classification_id INTEGER NOT NULL)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS classifications
                   (id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL)''')
    conn.commit()
    cur.close()
    conn.close()

def classify_activity(activity, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT name FROM classifications WHERE user_id = %s', (user_id,))
    user_classifications = [row['name'] for row in cur.fetchall()]
    cur.close()
    conn.close()

    classifications = "fitness, reading, housework, drive, cooking, playtime, " + ", ".join(user_classifications)

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that classifies activities into categories."},
                {"role": "user", "content": f"Classify the following activity into one of these categories: {classifications}. Only respond with the category name. Activity: {activity}"}
            ]
        )
        return response.choices[0].message.content.strip().lower()
    except Exception as e:
        print(f"Error in classification: {e}")
        return 'other'

def get_activities_grouped(user_id, date):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT c.name as category, h.name as habit, a.hours, a.id
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        JOIN classifications c ON h.classification_id = c.id
        WHERE a.user_id = %s AND a.date = %s
        ORDER BY c.name, h.name
    ''', (user_id, date))
    activities = cur.fetchall()
    cur.close()
    conn.close()
    
    grouped_activities = defaultdict(list)
    for activity in activities:
        grouped_activities[activity['category']].append(activity)
    
    return dict(grouped_activities)

def get_summary(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT c.name as category, SUM(a.hours) as total_hours
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        JOIN classifications c ON h.classification_id = c.id
        WHERE a.user_id = %s
        GROUP BY c.name
    ''', (user_id,))
    summary = cur.fetchall()
    cur.close()
    conn.close()
    return summary

def get_habits(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM habits WHERE user_id = %s', (user_id,))
    habits = cur.fetchall()
    cur.close()
    conn.close()
    return habits

def get_classifications(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM classifications WHERE user_id = %s', (user_id,))
    classifications = cur.fetchall()
    cur.close()
    conn.close()
    return classifications

def add_activity(user_id, date, habit_id, hours):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO activities (user_id, date, habit_id, hours) VALUES (%s, %s, %s, %s)',
                 (user_id, date, habit_id, hours))
    conn.commit()
    cur.close()
    conn.close()

def update_activity(activity_id, user_id, date, habit_id, hours):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE activities SET date = %s, habit_id = %s, hours = %s WHERE id = %s AND user_id = %s',
                 (date, habit_id, hours, activity_id, user_id))
    conn.commit()
    cur.close()
    conn.close()

def get_activity(activity_id, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.*, h.name as habit_name, c.name as category_name
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        JOIN classifications c ON h.classification_id = c.id
        WHERE a.id = %s AND a.user_id = %s
    ''', (activity_id, user_id))
    activity = cur.fetchone()
    cur.close()
    conn.close()
    return dict(activity) if activity else None

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
        habit_id = request.form.get('habit_id')
        hours = float(request.form.get('hours'))
        date = datetime.now().strftime("%Y-%m-%d")
        
        add_activity(session['user_id'], date, habit_id, hours)
        flash('Activity added successfully!', 'success')
        return redirect(url_for('index'))
    
    today = datetime.now().date()
    activities = get_activities_grouped(session['user_id'], today.isoformat())
    summary = get_summary(session['user_id'])
    habits = get_habits(session['user_id'])
    classifications = get_classifications(session['user_id'])
    return render_template('index.html', activities=activities, summary=summary, habits=habits, classifications=classifications, current_date=today.isoformat(), config=app.config)

@app.route('/add_habit', methods=['POST'])
@login_required
def add_habit():
    habit_name = request.form.get('habit_name')
    classification_id = request.form.get('classification_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO habits (user_id, name, classification_id) VALUES (%s, %s, %s)',
                (session['user_id'], habit_name, classification_id))
    conn.commit()
    cur.close()
    conn.close()
    flash('New habit added successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/add_classification', methods=['POST'])
@login_required
def add_classification():
    classification_name = request.form.get('classification_name')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO classifications (user_id, name) VALUES (%s, %s)',
                (session['user_id'], classification_name))
    conn.commit()
    cur.close()
    conn.close()
    flash('New classification added successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/edit/<int:activity_id>', methods=['GET', 'POST'])
@login_required
def edit_activity(activity_id):
    activity = get_activity(activity_id, session['user_id'])
    if not activity:
        flash('Activity not found or you do not have permission to edit it.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        new_habit_id = request.form.get('habit_id')
        new_hours = float(request.form.get('hours'))
        new_date = request.form.get('date')
        
        update_activity(activity_id, session['user_id'], new_date, new_habit_id, new_hours)
        flash('Activity updated successfully!', 'success')
        return redirect(url_for('index'))

    habits = get_habits(session['user_id'])
    return render_template('edit_activity.html', activity=activity, habits=habits)

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)