import importlib.metadata
import sys
sys.modules['importlib_metadata'] = importlib.metadata

from authlib.integrations.flask_client import OAuth
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort, json
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
import os
from openai import OpenAI
from sqlalchemy import func, cast, String, text

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

# Determine the environment
ENVIRONMENT = os.getenv("FLASK_ENV", "development")

if ENVIRONMENT == "production":
    # Use the DATABASE_URL for Heroku deployment
    DATABASE_URL = os.getenv("DATABASE_URL")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Use a local SQLite database for development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///local_database.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Define your models
class User(db.Model):
    id = db.Column(db.String(120), primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

class Habit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(128), db.ForeignKey('user.id'), nullable=False)  # Change this to String
    name = db.Column(db.String(120), nullable=False)
    emoji = db.Column(db.String(10))  # Add this line


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(120), db.ForeignKey('user.id'), nullable=False)
    habit_id = db.Column(db.Integer, db.ForeignKey('habit.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(255))  # Add this line
    date = db.Column(db.Date, nullable=False)
    habit = db.relationship('Habit', backref='activities')

def init_db():
    with app.app_context():
        db.drop_all()
        db.create_all()

def add_habit(user_id, habit_name):
    emoji = generate_emoji(habit_name)
    new_habit = Habit(user_id=user_id, name=habit_name, emoji=emoji)
    db.session.add(new_habit)
    db.session.commit()

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
    
def get_habits(user_id):
    return Habit.query.filter_by(user_id=user_id).all()

def add_activity(user_id, habit_id, date, description, hours):
    new_activity = Activity(user_id=user_id, habit_id=habit_id, date=date, description=description, hours=hours)
    db.session.add(new_activity)
    db.session.commit()

def classify_activity(activity_description, user_habits):
    habit_names = [habit.name for habit in user_habits]
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
            if habit.name.lower() == classified_habit.lower():
                return habit.id
        return None  # If no match found
    except Exception as e:
        print(f"Error in classification: {e}")
        return None

def get_activities(user_id, date):
    return Activity.query.filter_by(user_id=user_id, date=date).all()

def get_summary(user_id):
    activities = db.session.query(
        Habit.name, 
        func.sum(Activity.hours).label('total_hours')
    ).join(Activity).filter(
        Activity.user_id == user_id
    ).group_by(Habit.name).all()
    return {activity.name: float(activity.total_hours) for activity in activities}

def get_activity(activity_id, user_id):
    return Activity.query.filter_by(id=activity_id, user_id=user_id).first()

def update_activity(activity_id, user_id, date, habit_id, description, hours):
    activity = Activity.query.filter_by(id=activity_id, user_id=user_id).first()
    if activity:
        activity.date = date
        activity.habit_id = habit_id
        activity.description = description
        activity.hours = hours
        db.session.commit()

def get_habit_data(user_id):
    habit_data = {}
    habits = get_habits(user_id)
    
    for habit in habits:
        activities = db.session.query(Activity.date, func.sum(Activity.hours).label('total_hours'))\
            .filter(Activity.user_id == user_id, Activity.habit_id == habit.id)\
            .group_by(Activity.date)\
            .order_by(Activity.date)\
            .all()
        
        dates = []
        hours = []
        for activity in activities:
            dates.append(activity.date.strftime('%Y-%m-%d'))
            hours.append(float(activity.total_hours))
        
        habit_data[habit.name] = {
            'dates': dates,
            'hours': hours
        }
    
    return habit_data

@app.route('/login')
def login():
    redirect_uri = url_for('authorized', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/callback')
def authorized():
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v2/userinfo')
    user_info = resp.json()
    user = User.query.get(user_info['id'])
    if not user:
        user = User(id=user_info['id'], username=user_info['email'], email=user_info['email'])
        db.session.add(user)
        db.session.commit()
    session['user_id'] = user.id
    session['email'] = user.email
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
            date = datetime.now().date()
            habits = get_habits(session['user_id'])
            habit_id = classify_activity(description, habits)
            if habit_id:
                add_activity(session['user_id'], habit_id, date, description, hours)
                flash('Activity logged and classified successfully!', 'success')
            else:
                flash('Could not classify activity. Please try again or select a habit manually.', 'error')
        return redirect(url_for('index'))

    habits = get_habits(session['user_id'])
    habits_json = json.dumps([{'id': h.id, 'name': h.name, 'emoji': h.emoji} for h in habits])
    current_date = datetime.now().date()
    habit_data = get_habit_data(session['user_id'])

    return render_template('index.html', 
                           habits=habits,
                           habits_json=habits_json,
                           current_date=current_date,
                           habit_data=habit_data,
                           is_admin=(session.get('email') == app.config['ADMIN_EMAIL'])
                           )

@app.route('/activities/<date>')
@login_required
def get_activities_for_date(date):
    activities = get_activities(session['user_id'], datetime.strptime(date, '%Y-%m-%d').date())
    return jsonify([{
        'id': activity.id,
        'description': activity.description,
        'habit_name': activity.habit.name,
        'emoji': activity.habit.emoji or "😊",
        'hours': activity.hours
    } for activity in activities])

@app.route('/add_activity', methods=['POST'])
@login_required
def add_activity():
    data = request.json
    habit_id = data.get('habit_id')
    description = data.get('description')
    hours = float(data.get('hours'))
    date_str = data.get('date')

    if not date_str:
        date = datetime.now().date()
    else:
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid date format'}), 400

    try:
        if not habit_id:
            # Auto-classify the activity
            habits = get_habits(session['user_id'])
            habit_id = classify_activity(description, habits)
            if not habit_id:
                return jsonify({'success': False, 'message': 'Could not classify activity'}), 400

        new_activity = Activity(
            user_id=session['user_id'],
            habit_id=habit_id,
            description=description,
            hours=hours,
            date=date
        )
        db.session.add(new_activity)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Activity added successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    

@app.route('/update_activity/<int:activity_id>', methods=['POST'])
@login_required
def update_activity(activity_id):
    data = request.json
    activity = Activity.query.filter_by(id=activity_id, user_id=session['user_id']).first()
    
    if not activity:
        return jsonify({'success': False, 'message': 'Activity not found'}), 404

    activity.habit_id = data['habit_id']
    activity.description = data['description']
    activity.hours = float(data['hours'])
    
    try:
        db.session.commit()
        return jsonify({'success': True, 'message': 'Activity updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/activity_grid_data')
@login_required
def activity_grid_data():
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=364)
    
    activities = db.session.query(
        Activity.date,
        func.sum(Activity.hours).label('total_hours'),
        func.string_agg(
            Habit.name + ': ' + cast(Activity.hours, String) + ' hours',
            ', '
        ).label('summary')
    ).join(Habit).filter(
        Activity.user_id == session['user_id'],
        Activity.date >= start_date,
        Activity.date <= end_date
    ).group_by(Activity.date).all()
    
    activity_dict = {a.date: {'hours': float(a.total_hours), 'summary': a.summary.split(',')} for a in activities}
    
    grid_data = []
    for day in (start_date + timedelta(n) for n in range(365)):
        if day in activity_dict:
            grid_data.append({
                'date': day.isoformat(),
                'hours': activity_dict[day]['hours'],
                'summary': activity_dict[day]['summary']
            })
        else:
            grid_data.append({
                'date': day.isoformat(),
                'hours': 0,
                'summary': []
            })
    
    return jsonify(grid_data)

@app.route('/habit_data')
@login_required
def habit_data():
    habits = get_habits(session['user_id'])
    habit_data = get_habit_data(session['user_id'])
    return jsonify({habit.name: {**habit_data[habit.name], 'id': habit.id} for habit in habits})

@app.route('/delete_activity/<int:activity_id>', methods=['DELETE'])
@login_required
def delete_activity(activity_id):
    activity = Activity.query.filter_by(id=activity_id, user_id=session['user_id']).first()
    
    if not activity:
        return jsonify({'success': False, 'message': 'Activity not found'}), 404

    try:
        db.session.delete(activity)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Activity deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    
@app.route('/admin')
@login_required
def admin():
    if session.get('email') != app.config['ADMIN_EMAIL']:
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('index'))
    return render_template('admin.html')

@app.route('/admin/query', methods=['POST'])
@login_required
def admin_query():
    if session.get('email') != app.config['ADMIN_EMAIL']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    query = request.json.get('query')
    if not query:
        return jsonify({'error': 'No query provided'}), 400
    
    try:
        result = db.session.execute(text(query))
        columns = result.keys()
        results = [dict(zip(columns, row)) for row in result.fetchall()]
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Initialized the database.")


if __name__ == '__main__':
    app.run(debug=True)