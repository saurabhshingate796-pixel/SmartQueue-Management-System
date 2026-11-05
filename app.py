
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "smartqueue_secret_key"


# Database Connection
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="my_password",
        database="queue_management"
    )


# Home Page
@app.route('/')
def home():
    return render_template('index.html')


# Customer Registration
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']

        hashed_password = generate_password_hash(password)

        db = get_db_connection()
        cursor = db.cursor()

        try:
            cursor.execute("""
                INSERT INTO users (name, email, phone, password)
                VALUES (%s, %s, %s, %s)
            """, (name, email, phone, hashed_password))

            db.commit()
            flash("Registration successful! Please login.")
            return redirect(url_for('login'))

        except mysql.connector.Error:
            db.rollback()
            flash("Email already registered or registration failed.")

        finally:
            cursor.close()
            db.close()

    return render_template('register.html')


# Customer Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM users WHERE email = %s",
            (email,)
        )

        user = cursor.fetchone()

        cursor.close()
        db.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['name'] = user['name']
            session['role'] = user['role']

            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))

            return redirect(url_for('dashboard'))

        flash("Invalid email or password.")

    return render_template('login.html')


# Customer Dashboard
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM tokens
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (session['user_id'],))

    token = cursor.fetchone()
    people_ahead = 0

    if token and token['status'] == 'waiting':
        cursor.execute("""
            SELECT COUNT(*) AS people_ahead
            FROM tokens
            WHERE service_type = %s
            AND token_number < %s
            AND status IN ('waiting', 'serving')
        """, (token['service_type'], token['token_number']))

        people_ahead = cursor.fetchone()['people_ahead']

    cursor.close()
    db.close()

    return render_template(
        'dashboard.html',
        token=token,
        people_ahead=people_ahead,
        name=session['user_name']
    )


# Generate Token Page
@app.route('/token')
def token():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    return render_template('token.html')


# Generate Token
@app.route('/generate-token', methods=['GET', 'POST'])
def generate_token():

    if 'user_id' not in session:
        return redirect(url_for('login'))

    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    if request.method == 'GET':
        return render_template('token.html')

    service_type = request.form['service_type']

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
        SELECT COALESCE(MAX(token_number), 0) + 1
        FROM tokens
        WHERE service_type = %s
    """, (service_type,))

    token_number = cursor.fetchone()[0]

    cursor.execute("""
        INSERT INTO tokens (user_id, token_number, service_type)
        VALUES (%s, %s, %s)
    """, (session['user_id'], token_number, service_type))

    db.commit()

    cursor.close()
    db.close()

    return redirect(url_for('dashboard'))


# Live Service-wise Queue Status API
@app.route('/queue-status')
def queue_status():

    if 'user_id' not in session:
        return jsonify({'error': 'Please login'}), 401

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM tokens
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT 1
    """, (session['user_id'],))

    token = cursor.fetchone()

    if not token:
        cursor.close()
        db.close()
        return jsonify({'token': None})

    people_ahead = 0

    if token['status'] == 'waiting':
        cursor.execute("""
            SELECT COUNT(*) AS people_ahead
            FROM tokens
            WHERE service_type = %s
            AND token_number < %s
            AND status IN ('waiting', 'serving')
        """, (token['service_type'], token['token_number']))

        people_ahead = cursor.fetchone()['people_ahead']

    cursor.close()
    db.close()

    return jsonify({
        'token': {
            'token_number': token['token_number'],
            'service_type': token['service_type'],
            'status': token['status']
        },
        'people_ahead': people_ahead
    })


# Admin Dashboard - SQL JOIN + Reports & Analytics
@app.route('/admin')
def admin_dashboard():

    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Registered Users
    cursor.execute("""
        SELECT * FROM users
        ORDER BY id DESC
    """)
    users = cursor.fetchall()

    # SQL JOIN: Tokens with Customer Details
    cursor.execute("""
        SELECT
            tokens.id,
            tokens.token_number,
            tokens.service_type,
            tokens.status,
            tokens.counter_number,
            tokens.created_at,
            users.name,
            users.email,
            users.phone
        FROM tokens
        INNER JOIN users
        ON tokens.user_id = users.id
        ORDER BY tokens.id DESC
    """)

    tokens = cursor.fetchall()

    # Overall Token Statistics
    cursor.execute("""
        SELECT
            COUNT(*) AS total_tokens,
            SUM(status = 'waiting') AS waiting_tokens,
            SUM(status = 'serving') AS serving_tokens,
            SUM(status = 'completed') AS completed_tokens,
            SUM(status = 'skipped') AS skipped_tokens
        FROM tokens
    """)

    analytics = cursor.fetchone()

    # Service-wise Token Report
    cursor.execute("""
        SELECT
            service_type,
            COUNT(*) AS total,
            SUM(status = 'waiting') AS waiting,
            SUM(status = 'completed') AS completed,
            SUM(status = 'skipped') AS skipped
        FROM tokens
        GROUP BY service_type
        ORDER BY total DESC
    """)

    service_reports = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        'admin.html',
        users=users,
        tokens=tokens,
        analytics=analytics,
        service_reports=service_reports
    )


# Admin: Complete or Skip Token
@app.route('/update-token/<int:token_id>/<action>', methods=['POST'])
def update_token(token_id, action):

    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if action not in ['complete', 'skip']:
        return redirect(url_for('admin_dashboard'))

    new_status = 'completed' if action == 'complete' else 'skipped'

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
        UPDATE tokens
        SET status = %s
        WHERE id = %s
    """, (new_status, token_id))

    db.commit()

    cursor.close()
    db.close()

    return redirect(url_for('admin_dashboard'))


# Counter Management
@app.route('/counters', methods=['GET', 'POST'])
def manage_counters():

    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    if request.method == 'POST':
        counter_name = request.form['counter_name']
        counter_number = request.form['counter_number']

        try:
            cursor.execute("""
                INSERT INTO counters (counter_name, counter_number)
                VALUES (%s, %s)
            """, (counter_name, counter_number))

            db.commit()
            flash("Counter added successfully.")

        except mysql.connector.Error:
            db.rollback()
            flash("Counter number already exists or could not be added.")

    cursor.execute("""
        SELECT * FROM counters
        ORDER BY counter_number
    """)

    all_counters = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        'counters.html',
        counters=all_counters
    )


# Update Counter Status
@app.route('/update-counter/<int:counter_id>', methods=['POST'])
def update_counter(counter_id):

    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
        UPDATE counters
        SET status = CASE
            WHEN status = 'active' THEN 'inactive'
            ELSE 'active'
        END
        WHERE id = %s
    """, (counter_id,))

    db.commit()

    cursor.close()
    db.close()

    return redirect(url_for('manage_counters'))


# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


if __name__ == '__main__':
    app.run(debug=True)
