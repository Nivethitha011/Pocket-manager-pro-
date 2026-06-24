import os
import sqlite3
import csv
import io
from datetime import datetime, date, timedelta
from flask import g, Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.urandom(24) # Secure session signing

DB_PATH = os.path.join(os.path.dirname(__file__), 'database', 'pocket.db')

# Ensure database directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH, timeout=20.0)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('parent', 'child')),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        parent_id INTEGER,
        FOREIGN KEY(parent_id) REFERENCES users(id)
    )
    ''')
    
    # Create wallet table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS wallet (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child_id INTEGER UNIQUE NOT NULL,
        allowance REAL DEFAULT 0.0,
        spent REAL DEFAULT 0.0,
        remaining REAL DEFAULT 0.0,
        FOREIGN KEY(child_id) REFERENCES users(id)
    )
    ''')
    
    # Create transactions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        FOREIGN KEY(child_id) REFERENCES users(id)
    )
    ''')
    
    # Create goals table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child_id INTEGER NOT NULL,
        goal_name TEXT NOT NULL,
        target_amount REAL NOT NULL,
        saved_amount REAL DEFAULT 0.0,
        progress_percentage REAL DEFAULT 0.0,
        status TEXT DEFAULT 'active',
        FOREIGN KEY(child_id) REFERENCES users(id)
    )
    ''')
    
    # Create badges table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS badges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child_id INTEGER NOT NULL,
        badge_name TEXT NOT NULL,
        unlocked_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(child_id, badge_name),
        FOREIGN KEY(child_id) REFERENCES users(id)
    )
    ''')
    
    # Create streaks table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS streaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        child_id INTEGER UNIQUE NOT NULL,
        streak_days INTEGER DEFAULT 0,
        last_saving_date TEXT,
        FOREIGN KEY(child_id) REFERENCES users(id)
    )
    ''')
    
    # Create notifications table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        status TEXT DEFAULT 'unread',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Initialize DB on import
init_db()

# --- HELPER FUNCTIONS ---

def create_notification(user_id, message):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (user_id, message))
    conn.commit()
    

def add_xp(child_id, xp_amount):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT xp, level, username FROM users WHERE id = ?', (child_id,))
    child = cursor.fetchone()
    if not child:
        
        return 0, 0, False
    
    new_xp = child['xp'] + xp_amount
    current_level = child['level']
    
    # Levels logic: Level up at 100, 300, 600, 1000 XP
    new_level = 1
    if new_xp >= 1000:
        new_level = 5
    elif new_xp >= 600:
        new_level = 4
    elif new_xp >= 300:
        new_level = 3
    elif new_xp >= 100:
        new_level = 2
        
    level_up = False
    if new_level > current_level:
        level_up = True
        cursor.execute('UPDATE users SET xp = ?, level = ? WHERE id = ?', (new_xp, new_level, child_id))
        
        # Notify child
        level_titles = {1: "Beginner Saver", 2: "Smart Saver", 3: "Money Expert", 4: "Budget Master", 5: "Finance Champion"}
        title = level_titles.get(new_level, "Saver")
        msg_child = f"🎉 Level Up! You reached Level {new_level} ({title})!"
        cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, msg_child))
        
        # Notify parent
        cursor.execute('SELECT parent_id FROM users WHERE id = ?', (child_id,))
        p_id = cursor.fetchone()['parent_id']
        if p_id:
            msg_parent = f"🧒 Child {child['username']} has reached Level {new_level} ({title})!"
            cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (p_id, msg_parent))
            
        # Unlock badge for reaching level 5
        if new_level == 5:
            try:
                cursor.execute('INSERT INTO badges (child_id, badge_name) VALUES (?, ?)', (child_id, '👑 Finance Champion'))
                cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, '🏆 Badge Unlocked: 👑 Finance Champion!'))
                if p_id:
                    cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (p_id, f"🧒 Child {child['username']} unlocked: 👑 Finance Champion!"))
            except sqlite3.IntegrityError:
                pass
    else:
        cursor.execute('UPDATE users SET xp = ? WHERE id = ?', (new_xp, child_id))
        
    conn.commit()
    
    return new_xp, new_level, level_up

def check_and_unlock_badges(child_id):
    conn = get_db()
    cursor = conn.cursor()
    
    # Retrieve wallet
    cursor.execute('SELECT allowance, spent, remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    if not wallet:
        
        return []
    
    unlocked_badges = []
    
    # Get active parent ID to notify
    cursor.execute('SELECT parent_id, username FROM users WHERE id = ?', (child_id,))
    child_info = cursor.fetchone()
    parent_id = child_info['parent_id']
    child_name = child_info['username']
    
    # 1. Gold Saver: Save ₹5000 (total savings or remaining balance >= 5000)
    cursor.execute('SELECT SUM(saved_amount) as total_saved FROM goals WHERE child_id = ?', (child_id,))
    total_saved = cursor.fetchone()['total_saved'] or 0.0
    if total_saved >= 5000 or wallet['remaining'] >= 5000:
        badge_name = '🏆 Gold Saver'
        cursor.execute('SELECT id FROM badges WHERE child_id = ? AND badge_name = ?', (child_id, badge_name))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO badges (child_id, badge_name) VALUES (?, ?)', (child_id, badge_name))
            unlocked_badges.append(badge_name)
            cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, "🏆 Badge Unlocked: 🏆 Gold Saver (Saved over ₹5000)!"))
            if parent_id:
                cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (parent_id, f"🧒 Child {child_name} unlocked badge: 🏆 Gold Saver!"))
                
    # 2. Smart Saver: Unlocked when child has at least 2 transactions and remaining balance is >= 50% of allowance
    cursor.execute('SELECT COUNT(*) as tx_count FROM transactions WHERE child_id = ?', (child_id,))
    tx_count = cursor.fetchone()['tx_count']
    if tx_count >= 2 and wallet['allowance'] > 0 and (wallet['remaining'] / wallet['allowance']) >= 0.5:
        badge_name = '💰 Smart Saver'
        cursor.execute('SELECT id FROM badges WHERE child_id = ? AND badge_name = ?', (child_id, badge_name))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO badges (child_id, badge_name) VALUES (?, ?)', (child_id, badge_name))
            unlocked_badges.append(badge_name)
            cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, "🏆 Badge Unlocked: 💰 Smart Saver (Kept >50% of allowance)!"))
            if parent_id:
                cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (parent_id, f"🧒 Child {child_name} unlocked badge: 💰 Smart Saver!"))
                
    # 3. Goal Achiever: Completed at least 1 goal
    cursor.execute("SELECT COUNT(*) as comp_goals FROM goals WHERE child_id = ? AND status = 'completed'", (child_id,))
    comp_goals = cursor.fetchone()['comp_goals']
    if comp_goals >= 1:
        badge_name = '🎯 Goal Achiever'
        cursor.execute('SELECT id FROM badges WHERE child_id = ? AND badge_name = ?', (child_id, badge_name))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO badges (child_id, badge_name) VALUES (?, ?)', (child_id, badge_name))
            unlocked_badges.append(badge_name)
            cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, "🏆 Badge Unlocked: 🎯 Goal Achiever (Completed a Savings Goal)!"))
            if parent_id:
                cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (parent_id, f"🧒 Child {child_name} unlocked badge: 🎯 Goal Achiever!"))
                
    # 4. Consistent Saver: Streak of 7 days or more
    cursor.execute('SELECT streak_days FROM streaks WHERE child_id = ?', (child_id,))
    streak_row = cursor.fetchone()
    if streak_row and streak_row['streak_days'] >= 7:
        badge_name = '🔥 Consistent Saver'
        cursor.execute('SELECT id FROM badges WHERE child_id = ? AND badge_name = ?', (child_id, badge_name))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO badges (child_id, badge_name) VALUES (?, ?)', (child_id, badge_name))
            unlocked_badges.append(badge_name)
            cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, "🏆 Badge Unlocked: 🔥 Consistent Saver (Maintained a 7-day savings streak)!"))
            if parent_id:
                cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (parent_id, f"🧒 Child {child_name} unlocked badge: 🔥 Consistent Saver!"))
                
    # 5. Money Master: Completed 3 or more goals
    if comp_goals >= 3:
        badge_name = '💎 Money Master'
        cursor.execute('SELECT id FROM badges WHERE child_id = ? AND badge_name = ?', (child_id, badge_name))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO badges (child_id, badge_name) VALUES (?, ?)', (child_id, badge_name))
            unlocked_badges.append(badge_name)
            cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (child_id, "🏆 Badge Unlocked: 💎 Money Master (Completed 3+ savings goals)!"))
            if parent_id:
                cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)', (parent_id, f"🧒 Child {child_name} unlocked badge: 💎 Money Master!"))

    conn.commit()
    
    return unlocked_badges

# --- VIEWS / CONTROLLERS ---

@app.route('/')
def index():
    return render_template('loading.html')

@app.route('/welcome')
def welcome():
    return render_template('welcome.html')

# --- AUTHENTICATION ---

@app.route('/register/parent', methods=['GET', 'POST'])
def register_parent():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template('register_parent.html')
            
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('register_parent.html')
            
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            flash("Username is already taken.", "danger")
            
            return render_template('register_parent.html')
            
        hashed_password = generate_password_hash(password)
        cursor.execute('INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
                       (username, hashed_password, 'parent'))
        conn.commit()
        
        flash("Parent account created! Please log in.", "success")
        return redirect(url_for('login_parent'))
        
    return render_template('register_parent.html')

@app.route('/register/child', methods=['GET', 'POST'])
def register_child():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        parent_username = request.form.get('parent_username', '').strip()
        
        if not username or not password or not parent_username:
            flash("All fields are required.", "danger")
            return render_template('register_child.html')
            
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('register_child.html')
            
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if parent exists
        cursor.execute('SELECT id FROM users WHERE username = ? AND role = ?', (parent_username, 'parent'))
        parent = cursor.fetchone()
        if not parent:
            flash("Parent username not found. Please ensure your parent registers first.", "danger")
            
            return render_template('register_child.html')
            
        # Check if username taken
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            flash("Username is already taken.", "danger")
            
            return render_template('register_child.html')
            
        hashed_password = generate_password_hash(password)
        cursor.execute('INSERT INTO users (username, password_hash, role, parent_id) VALUES (?, ?, ?, ?)',
                       (username, hashed_password, 'child', parent['id']))
        child_id = cursor.lastrowid
        
        # Initialize wallet, streak, and default system notifications
        cursor.execute('INSERT INTO wallet (child_id, allowance, spent, remaining) VALUES (?, 0.0, 0.0, 0.0)', (child_id,))
        cursor.execute('INSERT INTO streaks (child_id, streak_days) VALUES (?, 0)', (child_id,))
        
        # Welcome notification
        cursor.execute('INSERT INTO notifications (user_id, message) VALUES (?, ?)',
                       (child_id, "Welcome to Pocket Money Manager Pro! Set a Savings Goal or ask PocketAI for advice to begin!"))
        
        conn.commit()
        
        flash("Child account created! Please log in.", "success")
        return redirect(url_for('login_child'))
        
    return render_template('register_child.html')

@app.route('/login/parent', methods=['GET', 'POST'])
def login_parent():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? AND role = ?', (username, 'parent'))
        user = cursor.fetchone()
        
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = 'parent'
            return redirect(url_for('parent_dashboard'))
        else:
            flash("Invalid credentials.", "danger")
            
    return render_template('login_parent.html')

@app.route('/login/child', methods=['GET', 'POST'])
def login_child():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? AND role = ?', (username, 'child'))
        user = cursor.fetchone()
        
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = 'child'
            return redirect(url_for('child_dashboard'))
        else:
            flash("Invalid credentials.", "danger")
            
    return render_template('login_child.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("Successfully logged out.", "info")
    return redirect(url_for('welcome'))

# --- PARENT DASHBOARD ---

@app.route('/parent/dashboard')
def parent_dashboard():
    if 'user_id' not in session or session.get('role') != 'parent':
        return redirect(url_for('welcome'))
        
    parent_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    
    # Get children linked to parent
    cursor.execute('SELECT id, username, xp, level FROM users WHERE parent_id = ?', (parent_id,))
    children = [dict(row) for row in cursor.fetchall()]
    
    # Process dynamic data for children
    selected_child_id = request.args.get('child_id')
    
    # If no child_id specified, pick the first child if available
    if not selected_child_id and children:
        selected_child_id = children[0]['id']
        
    selected_child_info = None
    wallet = None
    transactions = []
    goals = []
    badges = []
    streak = None
    
    # Aggregate Stats across all children
    agg_allowance = 0
    agg_spent = 0
    agg_remaining = 0
    agg_savings = 0
    agg_badges = 0
    
    for ch in children:
        cursor.execute('SELECT allowance, spent, remaining FROM wallet WHERE child_id = ?', (ch['id'],))
        w = cursor.fetchone()
        if w:
            agg_allowance += w['allowance']
            agg_spent += w['spent']
            agg_remaining += w['remaining']
            
        cursor.execute('SELECT SUM(saved_amount) as total_saved FROM goals WHERE child_id = ?', (ch['id'],))
        s = cursor.fetchone()['total_saved'] or 0.0
        agg_savings += s
        
        cursor.execute('SELECT COUNT(*) as badge_count FROM badges WHERE child_id = ?', (ch['id'],))
        b = cursor.fetchone()['badge_count']
        agg_badges += b

    if selected_child_id:
        cursor.execute('SELECT id, username, xp, level FROM users WHERE id = ? AND parent_id = ?', (selected_child_id, parent_id))
        selected_child_info = cursor.fetchone()
        if selected_child_info:
            selected_child_info = dict(selected_child_info)
            cursor.execute('SELECT allowance, spent, remaining FROM wallet WHERE child_id = ?', (selected_child_id,))
            wallet = cursor.fetchone()
            
            # Get child transactions
            cursor.execute('SELECT * FROM transactions WHERE child_id = ? ORDER BY date DESC, time DESC LIMIT 20', (selected_child_id,))
            transactions = [dict(row) for row in cursor.fetchall()]
            
            # Get child goals
            cursor.execute('SELECT * FROM goals WHERE child_id = ?', (selected_child_id,))
            goals = [dict(row) for row in cursor.fetchall()]
            
            # Get child badges
            cursor.execute('SELECT * FROM badges WHERE child_id = ?', (selected_child_id,))
            badges = [dict(row) for row in cursor.fetchall()]
            
            # Get child streak
            cursor.execute('SELECT * FROM streaks WHERE child_id = ?', (selected_child_id,))
            streak = cursor.fetchone()
            
    # Parent notifications
    cursor.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 15', (parent_id,))
    notifications = [dict(row) for row in cursor.fetchall()]
    
    
    
    return render_template(
        'parent_dashboard.html',
        children=children,
        selected_child_id=int(selected_child_id) if selected_child_id else None,
        selected_child_info=selected_child_info,
        wallet=wallet,
        transactions=transactions,
        goals=goals,
        badges=badges,
        streak=streak,
        notifications=notifications,
        agg_allowance=agg_allowance,
        agg_spent=agg_spent,
        agg_remaining=agg_remaining,
        agg_savings=agg_savings,
        agg_badges=agg_badges
    )

@app.route('/parent/add_money', methods=['POST'])
def parent_add_money():
    if 'user_id' not in session or session.get('role') != 'parent':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    child_id = request.form.get('child_id')
    amount = float(request.form.get('amount', 0.0))
    
    if not child_id or amount <= 0:
        flash("Invalid amount or child ID.", "danger")
        return redirect(url_for('parent_dashboard'))
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Verify child belongs to parent
    cursor.execute('SELECT username FROM users WHERE id = ? AND parent_id = ?', (child_id, session['user_id']))
    child = cursor.fetchone()
    if not child:
        flash("Child not found.", "danger")
        
        return redirect(url_for('parent_dashboard'))
        
    # Update wallet
    cursor.execute('SELECT allowance, remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    
    new_allowance = wallet['allowance'] + amount
    new_remaining = wallet['remaining'] + amount
    
    cursor.execute('UPDATE wallet SET allowance = ?, remaining = ? WHERE child_id = ?',
                   (new_allowance, new_remaining, child_id))
    
    # Log this deposit as a pseudo transaction (positive category)
    now = datetime.now()
    cursor.execute('INSERT INTO transactions (child_id, category, amount, description, date, time) VALUES (?, ?, ?, ?, ?, ?)',
                   (child_id, 'Allowance', amount, 'Allowance pocket money deposit', now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S')))
    
    conn.commit()
    
    
    # Notify child and parent
    create_notification(child_id, f"💰 Pocket money received! Your parent added ₹{amount} to your allowance.")
    create_notification(session['user_id'], f"💰 Added ₹{amount} pocket money to {child['username']}'s wallet.")
    
    # Award some XP to the child for parent depositing money (encouragement)
    add_xp(child_id, 10)
    
    flash(f"Pocket money of ₹{amount} added to {child['username']}.", "success")
    return redirect(url_for('parent_dashboard', child_id=child_id))

@app.route('/parent/edit_allowance', methods=['POST'])
def parent_edit_allowance():
    if 'user_id' not in session or session.get('role') != 'parent':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    child_id = request.form.get('child_id')
    new_allowance = float(request.form.get('allowance', 0.0))
    
    if not child_id or new_allowance < 0:
        flash("Invalid child ID or allowance.", "danger")
        return redirect(url_for('parent_dashboard'))
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Verify child belongs to parent
    cursor.execute('SELECT username FROM users WHERE id = ? AND parent_id = ?', (child_id, session['user_id']))
    child = cursor.fetchone()
    if not child:
        flash("Child not found.", "danger")
        
        return redirect(url_for('parent_dashboard'))
        
    # Fetch current stats
    cursor.execute('SELECT spent FROM wallet WHERE child_id = ?', (child_id,))
    w = cursor.fetchone()
    spent = w['spent']
    remaining = new_allowance - spent
    
    cursor.execute('UPDATE wallet SET allowance = ?, remaining = ? WHERE child_id = ?',
                   (new_allowance, remaining, child_id))
    
    conn.commit()
    
    
    create_notification(child_id, f"✏️ Allowance updated! Your total budget is now ₹{new_allowance}.")
    create_notification(session['user_id'], f"✏️ Set {child['username']}'s baseline allowance to ₹{new_allowance}.")
    
    flash(f"Allowance updated for {child['username']}.", "success")
    return redirect(url_for('parent_dashboard', child_id=child_id))

@app.route('/parent/export')
def parent_export():
    if 'user_id' not in session or session.get('role') != 'parent':
        return "Unauthorized", 403
        
    child_id = request.args.get('child_id')
    if not child_id:
        return "Missing child ID", 400
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Verify child belongs to parent
    cursor.execute('SELECT username FROM users WHERE id = ? AND parent_id = ?', (child_id, session['user_id']))
    child = cursor.fetchone()
    if not child:
        
        return "Unauthorized", 403
        
    cursor.execute('SELECT category, amount, description, date, time FROM transactions WHERE child_id = ? ORDER BY date DESC, time DESC', (child_id,))
    txs = cursor.fetchall()
    
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Category', 'Amount (₹)', 'Description', 'Date', 'Time'])
    for tx in txs:
        writer.writerow([tx['category'], tx['amount'], tx['description'], tx['date'], tx['time']])
        
    response = Response(output.getvalue(), mimetype='text/csv')
    filename = f"{child['username']}_transactions_{date.today().strftime('%Y%m%d')}.csv"
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response

# --- CHILD DASHBOARD ---

@app.route('/child/dashboard')
def child_dashboard():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    child_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    
    # Child info
    cursor.execute('SELECT username, xp, level FROM users WHERE id = ?', (child_id,))
    child_info = dict(cursor.fetchone())
    
    # Wallet stats
    cursor.execute('SELECT allowance, spent, remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    
    # Active goals
    cursor.execute("SELECT * FROM goals WHERE child_id = ? AND status = 'active'", (child_id,))
    goals = [dict(row) for row in cursor.fetchall()]
    
    # Earned badges
    cursor.execute('SELECT * FROM badges WHERE child_id = ?', (child_id,))
    badges = [dict(row) for row in cursor.fetchall()]
    
    # Streaks
    cursor.execute('SELECT * FROM streaks WHERE child_id = ?', (child_id,))
    streak = cursor.fetchone()
    
    # Recent Transactions
    cursor.execute('SELECT * FROM transactions WHERE child_id = ? ORDER BY date DESC, time DESC LIMIT 5', (child_id,))
    recent_transactions = [dict(row) for row in cursor.fetchall()]
    
    # Notifications
    cursor.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 10', (child_id,))
    notifications = [dict(row) for row in cursor.fetchall()]
    
    
    
    # Evaluate badges just in case
    check_and_unlock_badges(child_id)
    
    # XP thresholds
    xp = child_info['xp']
    lvl = child_info['level']
    lvl_ranges = {1: (0, 100), 2: (100, 300), 3: (300, 600), 4: (600, 1000), 5: (1000, 2000)}
    min_xp, max_xp = lvl_ranges.get(lvl, (1000, 2000))
    progress_xp = xp - min_xp
    total_range = max_xp - min_xp
    xp_percentage = min(100, max(0, int((progress_xp / total_range) * 100))) if total_range > 0 else 100
    
    level_titles = {1: "Beginner Saver", 2: "Smart Saver", 3: "Money Expert", 4: "Budget Master", 5: "Finance Champion"}
    level_title = level_titles.get(lvl, "Saver")
    
    return render_template(
        'child_dashboard.html',
        child_info=child_info,
        wallet=wallet,
        goals=goals,
        badges=badges,
        streak=streak,
        recent_transactions=recent_transactions,
        notifications=notifications,
        xp_percentage=xp_percentage,
        max_xp=max_xp,
        level_title=level_title
    )

@app.route('/child/spend', methods=['POST'])
def child_spend():
    if 'user_id' not in session or session.get('role') != 'child':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    child_id = session['user_id']
    amount = float(request.form.get('amount', 0.0))
    category = request.form.get('category')
    description = request.form.get('description', '').strip()
    
    if amount <= 0 or not category:
        flash("Invalid amount or category.", "danger")
        return redirect(url_for('child_dashboard'))
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Get wallet
    cursor.execute('SELECT allowance, spent, remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    
    if wallet['remaining'] < amount:
        flash("Oops! You don't have enough allowance remaining for this purchase.", "warning")
        
        return redirect(url_for('child_dashboard'))
        
    # Deduct wallet
    new_spent = wallet['spent'] + amount
    new_remaining = wallet['remaining'] - amount
    cursor.execute('UPDATE wallet SET spent = ?, remaining = ? WHERE child_id = ?', (new_spent, new_remaining, child_id))
    
    # Save transaction
    now = datetime.now()
    tx_date = now.strftime('%Y-%m-%d')
    tx_time = now.strftime('%H:%M:%S')
    cursor.execute('INSERT INTO transactions (child_id, category, amount, description, date, time) VALUES (?, ?, ?, ?, ?, ?)',
                   (child_id, category, amount, description, tx_date, tx_time))
    
    # Notify parent setup
    cursor.execute('SELECT parent_id, username FROM users WHERE id = ?', (child_id,))
    child_info = cursor.fetchone()
    parent_id = child_info['parent_id']
    child_username = child_info['username']
        
    conn.commit()
    
    
    # Notifications
    create_notification(child_id, f"📉 You spent ₹{amount} on {category} ({description or 'No desc'}).")
    if parent_id:
        create_notification(parent_id, f"🧒 Child {child_username} spent ₹{amount} on {category} ({description or 'No desc'}).")
    
    # Add XP for logging expense
    add_xp(child_id, 10)
    check_and_unlock_badges(child_id)
    
    flash(f"Logged expenditure of ₹{amount} on {category}!", "success")
    return redirect(url_for('child_dashboard'))

# --- GOALS SYSTEM ---

@app.route('/child/goals')
def child_goals():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    child_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    
    # Fetch goals
    cursor.execute('SELECT * FROM goals WHERE child_id = ?', (child_id,))
    goals = [dict(row) for row in cursor.fetchall()]
    
    # Fetch wallet remaining balance
    cursor.execute('SELECT remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    
    
    return render_template('goals.html', goals=goals, wallet=wallet)

@app.route('/child/goal/add', methods=['POST'])
def add_goal():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    child_id = session['user_id']
    goal_name = request.form.get('goal_name', '').strip()
    target_amount = float(request.form.get('target_amount', 0.0))
    
    if not goal_name or target_amount <= 0:
        flash("Invalid goal details.", "danger")
        return redirect(url_for('child_goals'))
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO goals (child_id, goal_name, target_amount, saved_amount, progress_percentage, status) VALUES (?, ?, ?, 0.0, 0.0, "active")',
                   (child_id, goal_name, target_amount))
    conn.commit()
    
    
    # XP system - setting a goal awards 50 XP
    add_xp(child_id, 50)
    
    flash(f"Goal '{goal_name}' created successfully! You earned +50 XP! 🚀", "success")
    return redirect(url_for('child_goals'))

@app.route('/child/goal/edit', methods=['POST'])
def edit_goal():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    goal_id = request.form.get('goal_id')
    goal_name = request.form.get('goal_name', '').strip()
    target_amount = float(request.form.get('target_amount', 0.0))
    
    if not goal_id or not goal_name or target_amount <= 0:
        flash("Invalid goal edit details.", "danger")
        return redirect(url_for('child_goals'))
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Calculate new progress percentage
    cursor.execute('SELECT saved_amount FROM goals WHERE id = ? AND child_id = ?', (goal_id, session['user_id']))
    g = cursor.fetchone()
    if not g:
        flash("Goal not found.", "danger")
        
        return redirect(url_for('child_goals'))
        
    saved = g['saved_amount']
    progress = min(100.0, (saved / target_amount) * 100.0)
    status = 'completed' if progress >= 100.0 else 'active'
    
    cursor.execute('UPDATE goals SET goal_name = ?, target_amount = ?, progress_percentage = ?, status = ? WHERE id = ? AND child_id = ?',
                   (goal_name, target_amount, progress, status, goal_id, session['user_id']))
    conn.commit()
    
    
    flash("Goal updated successfully.", "success")
    return redirect(url_for('child_goals'))

@app.route('/child/goal/delete', methods=['POST'])
def delete_goal():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    goal_id = request.form.get('goal_id')
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Return saved money back to wallet remaining
    cursor.execute('SELECT saved_amount, goal_name FROM goals WHERE id = ? AND child_id = ?', (goal_id, session['user_id']))
    g = cursor.fetchone()
    if g:
        saved_amount = g['saved_amount']
        if saved_amount > 0:
            cursor.execute('SELECT remaining FROM wallet WHERE child_id = ?', (session['user_id'],))
            w = cursor.fetchone()
            new_remaining = w['remaining'] + saved_amount
            cursor.execute('UPDATE wallet SET remaining = ? WHERE child_id = ?', (new_remaining, session['user_id']))
            
        cursor.execute('DELETE FROM goals WHERE id = ? AND child_id = ?', (goal_id, session['user_id']))
        conn.commit()
        
        if saved_amount > 0:
            create_notification(session['user_id'], f"💰 Goal deleted: ₹{saved_amount} returned to your remaining balance.")
            
        flash(f"Goal '{g['goal_name']}' deleted.", "info")
    else:
        flash("Goal not found.", "danger")
        
    
    return redirect(url_for('child_goals'))

@app.route('/child/goal/save', methods=['POST'])
def save_for_goal():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    child_id = session['user_id']
    goal_id = request.form.get('goal_id')
    amount = float(request.form.get('amount', 0.0))
    
    if not goal_id or amount <= 0:
        flash("Invalid savings amount.", "danger")
        return redirect(url_for('child_goals'))
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Get goal
    cursor.execute('SELECT * FROM goals WHERE id = ? AND child_id = ?', (goal_id, child_id))
    goal = cursor.fetchone()
    
    # Get wallet
    cursor.execute('SELECT remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    
    if not goal:
        flash("Goal not found.", "danger")
        
        return redirect(url_for('child_goals'))
        
    if wallet['remaining'] < amount:
        flash("You do not have enough remaining balance to save this much.", "warning")
        
        return redirect(url_for('child_goals'))
        
    # Update goal
    new_saved = goal['saved_amount'] + amount
    new_progress = min(100.0, (new_saved / goal['target_amount']) * 100.0)
    new_status = 'completed' if new_progress >= 100.0 else 'active'
    
    cursor.execute('UPDATE goals SET saved_amount = ?, progress_percentage = ?, status = ? WHERE id = ?',
                   (new_saved, new_progress, new_status, goal_id))
    
    # Deduct wallet remaining (note: 'spent' does not increase because it's saved, not spent)
    new_remaining = wallet['remaining'] - amount
    cursor.execute('UPDATE wallet SET remaining = ? WHERE child_id = ?', (new_remaining, child_id))
    
    # Log savings transaction
    now = datetime.now()
    cursor.execute('INSERT INTO transactions (child_id, category, amount, description, date, time) VALUES (?, ?, ?, ?, ?, ?)',
                   (child_id, 'Savings', amount, f"Saved for: {goal['goal_name']}", now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S')))
    
    # Update streaks
    cursor.execute('SELECT * FROM streaks WHERE child_id = ?', (child_id,))
    streak_row = cursor.fetchone()
    today_str = now.strftime('%Y-%m-%d')
    yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    new_streak = 1
    if streak_row:
        last_date = streak_row['last_saving_date']
        if last_date == today_str:
            new_streak = streak_row['streak_days'] # Keep current streak if already saved today
        elif last_date == yesterday_str:
            new_streak = streak_row['streak_days'] + 1
        else:
            new_streak = 1
        cursor.execute('UPDATE streaks SET streak_days = ?, last_saving_date = ? WHERE child_id = ?',
                       (new_streak, today_str, child_id))
    else:
        cursor.execute('INSERT INTO streaks (child_id, streak_days, last_saving_date) VALUES (?, 1, ?)',
                       (child_id, today_str))
                       
    conn.commit()
    
    
    # Award XP for saving money
    # 20 XP standard for saving + extra 30 XP if streak increased
    xp_to_add = 20
    if streak_row and new_streak > streak_row['streak_days']:
        xp_to_add += 30
        create_notification(child_id, f"🔥 Streak Extended! You are on a {new_streak} day saving streak!")
        # Notify parent about streak
        cursor.execute('SELECT parent_id, username FROM users WHERE id = ?', (child_id,))
        ch_info = cursor.fetchone()
        if ch_info['parent_id']:
            create_notification(ch_info['parent_id'], f"🧒 Child {ch_info['username']} is on a {new_streak} day saving streak!")
            
    add_xp(child_id, xp_to_add)
    
    if new_status == 'completed':
        # Completion bonus
        add_xp(child_id, 150)
        create_notification(child_id, f"🎯 Goal Achieved! You completed your goal '{goal['goal_name']}'! Earned +150 XP! 🏆")
        cursor.execute('SELECT parent_id, username FROM users WHERE id = ?', (child_id,))
        ch_info = cursor.fetchone()
        if ch_info['parent_id']:
            create_notification(ch_info['parent_id'], f"🧒 Child {ch_info['username']} achieved their goal: '{goal['goal_name']}'!")
            
    check_and_unlock_badges(child_id)
    
    flash(f"Saved ₹{amount} for '{goal['goal_name']}'!", "success")
    return redirect(url_for('child_goals'))

# --- TRANSACTIONS LOGS VIEW ---

@app.route('/child/transactions')
def child_transactions():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    child_id = session['user_id']
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    
    conn = get_db()
    cursor = conn.cursor()
    
    query = 'SELECT * FROM transactions WHERE child_id = ?'
    params = [child_id]
    
    if search:
        query += ' AND (description LIKE ?)'
        params.append(f'%{search}%')
    if category:
        query += ' AND category = ?'
        params.append(category)
        
    query += ' ORDER BY date DESC, time DESC'
    
    cursor.execute(query, params)
    transactions = [dict(row) for row in cursor.fetchall()]
    
    
    return render_template('transactions.html', transactions=transactions, search=search, category=category)

# --- ANALYTICS ---

@app.route('/child/analytics')
def child_analytics():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    child_id = session['user_id']
    return render_template('analytics.html', child_id=child_id)

@app.route('/api/analytics_data')
def api_analytics_data():
    user_id = session.get('user_id')
    role = session.get('role')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    target_child_id = request.args.get('child_id')
    if not target_child_id:
        if role == 'child':
            target_child_id = user_id
        else:
            return jsonify({'success': False, 'message': 'Child ID required for parents'}), 400
            
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Pie Chart: Category spending breakdown
    cursor.execute('''
        SELECT category, SUM(amount) as total 
        FROM transactions 
        WHERE child_id = ? AND category != 'Allowance' AND category != 'Savings'
        GROUP BY category
    ''', (target_child_id,))
    spending_categories = [dict(row) for row in cursor.fetchall()]
    
    # 2. Bar Chart: Spending per month
    cursor.execute('''
        SELECT STRFTIME('%Y-%m', date) as month, SUM(amount) as total 
        FROM transactions 
        WHERE child_id = ? AND category != 'Allowance' AND category != 'Savings'
        GROUP BY month 
        ORDER BY month ASC
    ''', (target_child_id,))
    monthly_spending = [dict(row) for row in cursor.fetchall()]
    
    # 3. Line Chart: Savings Growth over time
    cursor.execute('''
        SELECT date, amount 
        FROM transactions 
        WHERE child_id = ? AND category = 'Savings'
        ORDER BY date ASC
    ''', (target_child_id,))
    savings_txs = cursor.fetchall()
    savings_labels = []
    savings_data = []
    cumulative = 0.0
    for tx in savings_txs:
        cumulative += tx['amount']
        savings_labels.append(tx['date'])
        savings_data.append(cumulative)
        
    # 4. Goal Progress: List of active/completed goals
    cursor.execute('SELECT goal_name, saved_amount, target_amount FROM goals WHERE child_id = ?', (target_child_id,))
    goals_data = [dict(row) for row in cursor.fetchall()]
    
    # 5. Streak & XP information
    cursor.execute('SELECT xp, level FROM users WHERE id = ?', (target_child_id,))
    ch_info = cursor.fetchone()
    cursor.execute('SELECT streak_days FROM streaks WHERE child_id = ?', (target_child_id,))
    str_info = cursor.fetchone()
    
    
    
    return jsonify({
        'spending_categories': spending_categories,
        'monthly_spending': monthly_spending,
        'savings_growth': {
            'labels': savings_labels,
            'data': savings_data
        },
        'goals': goals_data,
        'xp': ch_info['xp'] if ch_info else 0,
        'level': ch_info['level'] if ch_info else 1,
        'streak': str_info['streak_days'] if str_info else 0
    })

# --- POCKETAI FINANCIAL ASSISTANT ---

@app.route('/child/ai_assistant')
def ai_assistant():
    if 'user_id' not in session or session.get('role') != 'child':
        return redirect(url_for('welcome'))
        
    return render_template('ai_assistant.html')

@app.route('/api/pocket_ai', methods=['POST'])
def api_pocket_ai():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    role = session.get('role')
    user_id = session['user_id']
    query = request.json.get('query', '').lower().strip()
    
    conn = get_db()
    cursor = conn.cursor()
    
    if role == 'child':
        child_id = user_id
    else:
        # Parent asking about child
        child_id = request.json.get('child_id')
        if not child_id:
            cursor.execute('SELECT id FROM users WHERE parent_id = ? LIMIT 1', (user_id,))
            row = cursor.fetchone()
            child_id = row['id'] if row else None
            
    if not child_id:
        
        return jsonify({'response': "I couldn't find a linked child profile to analyze."})
        
    # Get child's username
    cursor.execute('SELECT username FROM users WHERE id = ?', (child_id,))
    child_name = cursor.fetchone()['username']
        
    # Gather database data for AI logic
    cursor.execute('SELECT allowance, spent, remaining FROM wallet WHERE child_id = ?', (child_id,))
    wallet = cursor.fetchone()
    
    cursor.execute('SELECT category, amount, date FROM transactions WHERE child_id = ? ORDER BY date DESC', (child_id,))
    transactions = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT goal_name, target_amount, saved_amount, status FROM goals WHERE child_id = ? AND status = 'active'", (child_id,))
    active_goals = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute('SELECT streak_days FROM streaks WHERE child_id = ?', (child_id,))
    streak = cursor.fetchone()
    streak_days = streak['streak_days'] if streak else 0
    
    
    
    if not wallet:
        return jsonify({'response': "It looks like the child wallet profile isn't fully set up yet. Please add pocket money first."})
        
    spent_transactions = [t for t in transactions if t['category'] not in ('Allowance', 'Savings')]
    total_spent = sum(t['amount'] for t in spent_transactions)
    
    category_totals = {}
    for t in spent_transactions:
        cat = t['category']
        category_totals[cat] = category_totals.get(cat, 0.0) + t['amount']
        
    highest_cat = max(category_totals, key=category_totals.get) if category_totals else "None"
    highest_amt = category_totals.get(highest_cat, 0.0)
    
    # 1. Spending Queries
    if 'spend' in query or 'spent' in query or 'expense' in query:
        response = f"📊 **Spending Analysis for {child_name}:**<br>"
        response += f"You have spent a total of **₹{total_spent:.2f}** out of your ₹{wallet['allowance']:.2f} allowance.<br>"
        if category_totals:
            response += "Here is your breakdown by category:<br>"
            for cat, amt in category_totals.items():
                response += f"- {cat}: **₹{amt:.2f}**<br>"
            response += f"Your highest expenditure was in **{highest_cat}** totaling **₹{highest_amt:.2f}**."
        else:
            response += "You haven't logged any expenditures yet. Keep it up!"
            
    # 2. Saving Suggestions & Tips
    elif 'save' in query or 'saving' in query or 'tip' in query or 'suggest' in query:
        response = "💡 **PocketAI Savings Suggestions:**<br>"
        if highest_cat != "None" and highest_amt > 0:
            suggested_cut = highest_amt * 0.15
            response += f"1. **Target Category**: Reduce spending in **{highest_cat}** (your biggest category) by **15%**. This would save you **₹{suggested_cut:.2f}**!<br>"
        else:
            response += "1. Make it a habit to save 10% of your allowance immediately when you receive it.<br>"
            
        if active_goals:
            response += f"2. You have **{len(active_goals)} active goal(s)**. Putting just ₹20 daily into your '{active_goals[0]['goal_name']}' goal will speed up your achievements.<br>"
        else:
            response += "2. **Set a Savings Goal** on the Goals page. Having a concrete target like 'Buy Books' or 'Buy Toy' makes saving highly motivating.<br>"
            
        response += f"3. Your current saving streak is **{streak_days} days**. Keep saving consecutive days to maintain your saving streak multipliers!"
        
    # 3. Goal Forecast Queries
    elif 'goal' in query or 'forecast' in query or 'when' in query:
        response = "🎯 **Goal Forecast Analysis:**<br>"
        if active_goals:
            response += "Here is when you will achieve your target goals based on saving habits:<br>"
            savings_deposits = [t['amount'] for t in transactions if t['category'] == 'Savings']
            avg_deposit = sum(savings_deposits) / len(savings_deposits) if savings_deposits else 50.0
            
            for g in active_goals:
                needed = g['target_amount'] - g['saved_amount']
                if needed <= 0:
                    response += f"- Goal **'{g['goal_name']}'** is already completed!<br>"
                else:
                    days_est = int(needed / (avg_deposit / 7.0)) if avg_deposit > 0 else 999
                    response += f"- **{g['goal_name']}** (Needs ₹{needed:.2f}): Estimated achievement in **{days_est} days** (assuming a weekly saving pace of ₹{avg_deposit:.2f}).<br>"
        else:
            response += "You have no active goals right now! Go to the **Savings Goals** page and set one up."
            
    # 4. Budget Recommendations
    elif 'budget' in query or 'recommend' in query or 'limit' in query:
        response = "🛡️ **Budget Recommendations:**<br>"
        pct_used = (wallet['spent'] / wallet['allowance'] * 100.0) if wallet['allowance'] > 0 else 0
        response += f"You have utilized **{pct_used:.1f}%** of your total allowance budget.<br>"
        
        if pct_used > 80:
            response += "⚠️ **Warning**: You are close to exhausting your monthly allowance. We recommend locking down all non-essential shopping and entertainment category spending.<br>"
        elif pct_used > 50:
            response += "💡 **Budget Tip**: You have used more than half of your allowance. Try to pace your expenses.<br>"
        else:
            response += "🟢 **Safe Zone**: You are within a safe budget buffer! Good job pace-setting your wallet.<br>"
            
        response += "Recommended budget splits:<br>- Food & Travel: 30%<br>- Entertainment & Shopping: 20%<br>- Books & School: 20%<br>- Savings: 30%"
        
    # 5. Default General Intelligence fallback
    else:
        response = "🤖 **Hello! I am PocketAI, your financial advisor.**<br>"
        response += f"Here is a quick snapshot of **{child_name}'s** profile:<br>"
        response += f"- Wallet Balance: **₹{wallet['remaining']:.2f}** remaining.<br>"
        response += f"- Streak Status: **{streak_days} 🔥 days** saving streak.<br>"
        if active_goals:
            response += f"- Active Goal: **{active_goals[0]['goal_name']}** ({int((active_goals[0]['saved_amount']/active_goals[0]['target_amount'])*100)}% complete).<br>"
        response += "<br>Try asking me questions like:<br>"
        response += "- *How much did I spend this month?*<br>"
        response += "- *What category costs most?*<br>"
        response += "- *How can I save more money?*<br>"
        response += "- *When will I achieve my goal?*"
        
    return jsonify({'response': response})

# --- GENERAL NOTIFICATIONS DISMISS API ---

@app.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET status = 'read' WHERE user_id = ?", (user_id,))
    conn.commit()
    
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
