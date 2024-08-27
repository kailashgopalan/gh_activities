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
    
    # Drop existing tables
    cur.execute("DROP TABLE IF EXISTS activities")
    cur.execute("DROP TABLE IF EXISTS habits")
    
    # Create tables with new schema
    cur.execute('''CREATE TABLE habits
                   (id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL)''')
    cur.execute('''CREATE TABLE activities
                   (id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    date DATE NOT NULL,
                    habit_id INTEGER REFERENCES habits(id),
                    description TEXT NOT NULL,
                    hours REAL NOT NULL)''')
    
    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized with new schema.")

def get_habits(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM habits WHERE user_id = %s', (user_id,))
    habits = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(habit) for habit in habits]

def add_habit(user_id, habit_name):
    emoji = generate_emoji(habit_name)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO habits (user_id, name, emoji) VALUES (%s, %s, %s) RETURNING id',
                (user_id, habit_name, emoji))
    habit_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return habit_id

def generate_emoji(habit_name):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that generates emojis."},
                {"role": "user", "content": f"Generate a single emoji that best represents the habit: {habit_name}. Respond with only the emoji."}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error in emoji generation: {e}")
        return "😊"  # Default emoji if generation fails

def classify_activity(activity_description, user_habits):
    habit_names = [habit['name'] for habit in user_habits]
    habits_str = ", ".join(habit_names)
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that classifies activities into habits."},
                {"role": "user", "content": f"Classify the following activity into one of these habits: {habits_str}. Only respond with the habit name. Activity: {activity_description}"}
            ]
        )
        classified_habit = response.choices[0].message.content.strip()
        for habit in user_habits:
            if habit['name'].lower() == classified_habit.lower():
                return habit['id']
        return None  # If no match found
    except Exception as e:
        print(f"Error in classification: {e}")
        return None

def add_activity(user_id, date, habit_id, description, hours):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO activities (user_id, date, habit_id, description, hours) VALUES (%s, %s, %s, %s, %s)',
                 (user_id, date, habit_id, description, hours))
    conn.commit()
    cur.close()
    conn.close()

def get_activities(user_id, date):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.*, h.name as habit_name, h.emoji
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        WHERE a.user_id = %s AND a.date = %s
        ORDER BY a.id DESC
    ''', (user_id, date))
    activities = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(activity) for activity in activities]

def get_summary(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT h.name as habit, h.emoji, SUM(a.hours) as total_hours
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        WHERE a.user_id = %s
        GROUP BY h.id, h.name, h.emoji
    ''', (user_id,))
    summary = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(item) for item in summary]

def get_activity(activity_id, user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.*, h.name as habit_name, h.emoji
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        WHERE a.id = %s AND a.user_id = %s
    ''', (activity_id, user_id))
    activity = cur.fetchone()
    cur.close()
    conn.close()
    return dict(activity) if activity else None

def update_activity(activity_id, user_id, date, habit_id, description, hours):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        UPDATE activities 
        SET date = %s, habit_id = %s, description = %s, hours = %s 
        WHERE id = %s AND user_id = %s
    ''', (date, habit_id, description, hours, activity_id, user_id))
    conn.commit()
    cur.close()
    conn.close()

@app.route('/login')
def login():
    redirect_uri = url_for('authorized', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/logout')
def logout():
    session.pop('google_token', None)
    session.pop('user_id', None)
    session.pop('email', None)
    session.pop('name', None)
    return redirect(url_for('index'))

@app.route('/callback')
def authorized():
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v2/userinfo')
    user_info = resp.json()
    session['google_token'] = token
    session['user_id'] = user_info['id']
    session['email'] = user_info['email']
    session['name'] = user_info.get('name', 'User')
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
        action = request.form.get('action')
        if action == 'add_habit':
            habit_name = request.form.get('habit_name')
            add_habit(session['user_id'], habit_name)
            flash('New habit added successfully!', 'success')
        elif action == 'add_activity':
            description = request.form.get('description')
            hours = float(request.form.get('hours'))
            date = datetime.now().strftime("%Y-%m-%d")
            habits = get_habits(session['user_id'])
            habit_id = classify_activity(description, habits)
            if habit_id:
                add_activity(session['user_id'], date, habit_id, description, hours)
                flash('Activity added and classified successfully!', 'success')
            else:
                flash('Could not classify activity. Please try again.', 'error')
        return redirect(url_for('index'))
    
    date_param = request.args.get('date')
    if date_param:
        current_date = datetime.strptime(date_param, '%Y-%m-%d').date()
    else:
        current_date = datetime.now().date()

    habits = get_habits(session['user_id'])
    activities = get_activities(session['user_id'], current_date.isoformat())
    summary = get_summary(session['user_id'])

    # Calculate daily hours for the past year
    daily_hours = {}
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=365)
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT date, SUM(hours) as total_hours
        FROM activities
        WHERE user_id = %s AND date BETWEEN %s AND %s
        GROUP BY date
    ''', (session['user_id'], start_date, end_date))
    
    for row in cur.fetchall():
        daily_hours[row['date'].isoformat()] = row['total_hours']
    
    # Prepare data for charts
    habit_data = defaultdict(lambda: {'dates': [], 'hours': []})
    
    cur.execute('''
        SELECT h.name as habit, a.date, SUM(a.hours) as total_hours
        FROM activities a
        JOIN habits h ON a.habit_id = h.id
        WHERE a.user_id = %s
        GROUP BY h.name, a.date
        ORDER BY h.name, a.date
    ''', (session['user_id'],))
    
    for row in cur.fetchall():
        habit_data[row['habit']]['dates'].append(row['date'].strftime('%Y-%m-%d'))
        habit_data[row['habit']]['hours'].append(float(row['total_hours']))
    
    cur.close()
    conn.close()

    return render_template('index.html', habits=habits, activities=activities, summary=summary, 
                           current_date=current_date, user_name=session.get('name'),
                           is_admin=(session.get('email') == app.config['ADMIN_EMAIL']),
                           daily_hours=daily_hours, habit_data=dict(habit_data))

@app.route('/edit/<int:activity_id>', methods=['GET', 'POST'])
@login_required
def edit_activity(activity_id):
    activity = get_activity(activity_id, session['user_id'])
    if not activity:
        flash('Activity not found or you do not have permission to edit it.', 'error')
        return redirect(url_for('index'))

    habits = get_habits(session['user_id'])

    if request.method == 'POST':
        habit_id = request.form.get('habit_id')
        description = request.form.get('description')
        hours = float(request.form.get('hours'))
        date = request.form.get('date')
        
        update_activity(activity_id, session['user_id'], date, habit_id, description, hours)
        flash('Activity updated successfully!', 'success')
        return redirect(url_for('index'))

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)