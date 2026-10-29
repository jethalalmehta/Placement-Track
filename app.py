import os
import sys
# Ensure vendored packages are always available even across fresh container boots
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor_packages')
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

import secrets
import hashlib
import sqlite3
import re
from collections import defaultdict
from datetime import datetime, date
from functools import wraps
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse
from flask import (
    Flask, render_template, request, redirect,
    url_for as flask_url_for, flash, session, jsonify, g, abort, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from database import get_db_connection, init_db, seed_demo_student
import job_fetcher
import email_recruiter
import resume_manager

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Secret key configuration (reads from environment in production, safe development default)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'placement-tracker-btech-cse-secret-key-2026')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
upload_dir = os.environ.get('UPLOAD_FOLDER')
if not upload_dir:
    if os.environ.get('VERCEL') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
        upload_dir = '/tmp/uploads/resumes'
    else:
        upload_dir = os.path.join(app.root_path, 'uploads', 'resumes')
app.config['UPLOAD_FOLDER'] = upload_dir
try:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
except OSError:
    pass
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

CATEGORIES = [
    "Quantitative Aptitude",
    "Logical Reasoning",
    "Verbal Ability",
    "Technical Knowledge",
    "Coding Practice",
    "Communication Skills",
    "Interview Preparation"
]

APPLICATION_STAGES = [
    "Tracked — Not Yet Confirmed",
    "Applied",
    "Under Review",
    "Assessment Scheduled",
    "Assessment Completed",
    "Shortlisted",
    "Interview Scheduled",
    "Interview Completed",
    "Offer Received",
    "Selected",
    "Not Selected",
    "On Hold",
    "Withdrawn",
    "Awaiting Company Update",
    # Legacy aliases
    "Assessment",
    "Assessment/Test",
    "Interview",
    "Rejected",
    "Awaiting Update",
    "Tracked"
]

ACTIVE_STAGES = (
    "Tracked — Not Yet Confirmed",
    "Applied",
    "Under Review",
    "Shortlisted",
    "Assessment Scheduled",
    "Assessment Completed",
    "Assessment",
    "Assessment/Test",
    "Interview Scheduled",
    "Interview Completed",
    "Interview",
    "Awaiting Company Update",
    "Awaiting Update",
    "On Hold"
)

def normalize_stage(stage_val):
    if not stage_val:
        return "Applied"
    s = str(stage_val).strip()
    s_lower = s.lower()
    mapping = {
        'tracked — not yet confirmed': 'Tracked — Not Yet Confirmed',
        'tracked - not yet confirmed': 'Tracked — Not Yet Confirmed',
        'tracked': 'Tracked — Not Yet Confirmed',
        'not yet confirmed': 'Tracked — Not Yet Confirmed',
        'assessment/test': 'Assessment/Test',
        'assessment': 'Assessment',
        'test': 'Assessment',
        'oa': 'Assessment',
        'online assessment': 'Assessment',
        'shortlisted': 'Shortlisted',
        'under review': 'Under Review',
        'assessment scheduled': 'Assessment Scheduled',
        'assessment completed': 'Assessment Completed',
        'interview scheduled': 'Interview Scheduled',
        'interview completed': 'Interview Completed',
        'interview': 'Interview',
        'offer received': 'Offer Received',
        'selected': 'Selected',
        'not selected': 'Not Selected',
        'rejected': 'Rejected',
        'on hold': 'On Hold',
        'withdrawn': 'Withdrawn',
        'awaiting company update': 'Awaiting Company Update',
        'awaiting update': 'Awaiting Company Update'
    }
    if s_lower in mapping:
        return mapping[s_lower]
    for valid in APPLICATION_STAGES:
        if s_lower == valid.lower():
            return valid
    return s

def normalize_category(cat_val):
    if not cat_val:
        return "Quantitative Aptitude"
    c = cat_val.strip()
    if c in ("Aptitude", "Quant"):
        return "Quantitative Aptitude"
    if c in ("Technical Subjects", "Technical"):
        return "Technical Knowledge"
    for valid in CATEGORIES:
        if c.lower() == valid.lower():
            return valid
    return c

def get_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='placement-auth-v1')

def db_conn():
    return get_db_connection(app.config.get('DATABASE'))

def create_user_session(user_id):
    """Creates a server-tracked session and returns a signed token for iframe/cookie resilience."""
    sid = secrets.token_hex(16)
    csrf = secrets.token_hex(16)
    conn = db_conn()
    conn.execute('INSERT INTO user_sessions (session_id, user_id) VALUES (?, ?)', (sid, user_id))
    conn.commit()
    conn.close()

    session.clear()
    session['user_id'] = user_id
    session['session_id'] = sid
    session['csrf_token'] = csrf
    session.permanent = True

    token = get_serializer().dumps({'uid': user_id, 'sid': sid, 'csrf': csrf})
    g.auth_token = token
    g.csrf_token = csrf
    return token

def append_token_to_url(target_url, token=None):
    tok = token or getattr(g, 'auth_token', None)
    if not tok or not target_url:
        return target_url
    parsed = urlparse(target_url)
    # Only append to internal URLs
    if parsed.netloc and parsed.netloc != request.host:
        return target_url
    query_params = dict(parse_qsl(parsed.query))
    query_params['_st'] = tok
    new_query = urlencode(query_params)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def custom_url_for(endpoint, **values):
    """Wraps Flask url_for to preserve _st token when operating inside a cookie-restricted iframe."""
    if endpoint == 'static':
        return flask_url_for(endpoint, **values)
    url = flask_url_for(endpoint, **values)
    tok = getattr(g, 'auth_token', None)
    if tok and endpoint not in ('login', 'register'):
        return append_token_to_url(url, tok)
    return url

def verify_user_password(user_row, submitted_password, conn):
    """
    Safely verifies a user's password against their stored hash.
    Handles standard Werkzeug hashes (pbkdf2/scrypt) and safely migrates legacy formats if encountered.
    """
    if not user_row or not submitted_password:
        return False

    stored_hash = user_row['password_hash'] or ''

    # 1. Standard Werkzeug hash verification
    if stored_hash.startswith(('pbkdf2:', 'scrypt:', 'argon2:')):
        if check_password_hash(stored_hash, submitted_password):
            return True
        # Fallback if user accidentally copied trailing/leading space when pasting password
        if submitted_password != submitted_password.strip():
            if check_password_hash(stored_hash, submitted_password.strip()):
                return True
        return False

    # 2. Compatibility check for raw SHA-256 hex digest (if manually seeded into SQLite)
    sha256_hex = hashlib.sha256(submitted_password.encode('utf-8')).hexdigest()
    if len(stored_hash) == 64 and secrets.compare_digest(stored_hash.lower(), sha256_hex.lower()):
        new_hash = generate_password_hash(submitted_password)
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_row['id']))
        conn.commit()
        return True

    # 3. Compatibility check if an existing SQLite record was manually inserted without hashing
    if not stored_hash.startswith(('pbkdf2:', 'scrypt:', 'argon2:')) and secrets.compare_digest(stored_hash, submitted_password):
        new_hash = generate_password_hash(submitted_password)
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_row['id']))
        conn.commit()
        return True

    return False

@app.before_request
def before_request():
    g.user = None
    g.auth_token = None
    g.csrf_token = session.get('csrf_token')

    user_id = session.get('user_id')
    session_id = session.get('session_id')

    # Also check signed token (_st in query/form or X-Auth-Token header) for cross-origin iframe support
    raw_token = (
        request.args.get('_st')
        or request.form.get('_st')
        or request.headers.get('X-Auth-Token')
    )

    if raw_token:
        try:
            payload = get_serializer().loads(raw_token, max_age=86400 * 7)
            t_uid = payload.get('uid')
            t_sid = payload.get('sid')
            t_csrf = payload.get('csrf')
            if t_uid and t_sid:
                user_id = t_uid
                session_id = t_sid
                g.auth_token = raw_token
                g.csrf_token = t_csrf
                session['user_id'] = t_uid
                session['session_id'] = t_sid
                session['csrf_token'] = t_csrf
        except (BadSignature, SignatureExpired):
            pass

    if user_id:
        conn = db_conn()
        cursor = conn.cursor()
        valid_session = True
        if session_id:
            cursor.execute(
                'SELECT 1 FROM user_sessions WHERE session_id = ? AND user_id = ?',
                (session_id, user_id)
            )
            if not cursor.fetchone():
                valid_session = False

        if valid_session:
            cursor.execute('SELECT id, student_id, name, email, COALESCE(role, "student") as role, phone FROM users WHERE id = ?', (user_id,))
            u_row = cursor.fetchone()
            g.user = dict(u_row) if u_row else None
            if g.user and session_id and not g.auth_token:
                if not g.csrf_token:
                    g.csrf_token = secrets.token_hex(16)
                    session['csrf_token'] = g.csrf_token
                g.auth_token = get_serializer().dumps({
                    'uid': g.user['id'],
                    'sid': session_id,
                    'csrf': g.csrf_token
                })
        else:
            session.clear()
        conn.close()

    if not g.csrf_token:
        g.csrf_token = secrets.token_hex(16)
        session['csrf_token'] = g.csrf_token

    # Validate CSRF token if explicitly provided on POST requests
    if request.method == 'POST' and app.config.get('WTF_CSRF_ENABLED', True):
        submitted_csrf = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if submitted_csrf and g.csrf_token and not secrets.compare_digest(submitted_csrf, g.csrf_token):
            flash("Security token mismatch. Please try submitting the form again.", "error")
            return redirect(request.referrer or custom_url_for('index'))

@app.after_request
def after_request(response):
    # Ensure cookies work in both HTTPS cross-origin iframes (SameSite=None; Secure; Partitioned) and HTTP localhost
    is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
    if is_https and 'Set-Cookie' in response.headers:
        cookies = response.headers.getlist('Set-Cookie')
        response.headers.pop('Set-Cookie', None)
        for cookie in cookies:
            if 'SameSite=Lax' in cookie:
                cookie = cookie.replace('SameSite=Lax', 'SameSite=None; Secure; Partitioned')
            elif 'SameSite=' not in cookie:
                cookie = f"{cookie}; SameSite=None; Secure; Partitioned"
            response.headers.add('Set-Cookie', cookie)
    return response

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            flash("Please sign in to access your placement dashboard.", "warning")
            return redirect(flask_url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def coordinator_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            flash("Please sign in to access this area.", "warning")
            return redirect(flask_url_for('login', next=request.path))
        if g.user['role'] not in ('admin', 'coordinator'):
            flash("Access denied. Placement Administrator or Coordinator privileges required.", "error")
            return redirect(custom_url_for('placement_hub'))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_globals():
    is_demo_user = bool(g.user and g.user['email'] == 'demo.student@university.edu')
    is_coordinator = bool(g.user and g.user['role'] in ('admin', 'coordinator'))
    unread_notifications_count = 0
    recent_notifications = []
    if g.user:
        try:
            conn = db_conn()
            c = conn.cursor()
            c.execute('SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND is_read = 0', (g.user['id'],))
            unread_notifications_count = c.fetchone()['cnt']
            c.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 5', (g.user['id'],))
            recent_notifications = [dict(r) for r in c.fetchall()]
            conn.close()
        except Exception:
            pass
    return {
        'current_user': g.user,
        'is_demo_user': is_demo_user,
        'is_coordinator': is_coordinator,
        'unread_notifications_count': unread_notifications_count,
        'recent_notifications': recent_notifications,
        'auth_token': getattr(g, 'auth_token', '') or '',
        'csrf_token': getattr(g, 'csrf_token', '') or '',
        'url_for': custom_url_for,
        'categories': CATEGORIES,
        'application_stages': APPLICATION_STAGES,
        'now_year': datetime.now().year,
        'today_iso': date.today().isoformat()
    }

# -------------------------------------------------------------
# SHARED ANALYTICS COMPUTATION ENGINE
# -------------------------------------------------------------

def compute_student_analytics(user_id):
    """
    Computes all dashboard and placement/preparation analytics directly from SQLite records
    for the authenticated student.
    """
    conn = db_conn()
    cursor = conn.cursor()

    # 1. Profile information
    cursor.execute('SELECT * FROM profiles WHERE user_id = ?', (user_id,))
    profile = cursor.fetchone()

    # 2. Task metrics
    cursor.execute('SELECT COUNT(*) as total FROM preparation_tasks WHERE user_id = ?', (user_id,))
    total_tasks = cursor.fetchone()['total'] or 0

    cursor.execute("SELECT COUNT(*) as completed FROM preparation_tasks WHERE user_id = ? AND status = 'completed'", (user_id,))
    completed_tasks = cursor.fetchone()['completed'] or 0
    pending_tasks = total_tasks - completed_tasks
    task_completion_pct = round((completed_tasks / total_tasks * 100), 1) if total_tasks > 0 else 0.0

    # Priority breakdown of pending tasks
    cursor.execute('''
        SELECT priority, COUNT(*) as cnt
        FROM preparation_tasks
        WHERE user_id = ? AND status = 'pending'
        GROUP BY priority
    ''', (user_id,))
    pending_by_priority = {'High': 0, 'Medium': 0, 'Low': 0}
    for r in cursor.fetchall():
        if r['priority'] in pending_by_priority:
            pending_by_priority[r['priority']] = r['cnt']

    # 3. Category-wise task breakdown
    category_progress = []
    for cat in CATEGORIES:
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
            FROM preparation_tasks
            WHERE user_id = ? AND category = ?
        ''', (user_id, cat))
        row = cursor.fetchone()
        t = row['total'] or 0
        c = row['completed'] or 0
        pct = round((c / t * 100)) if t > 0 else 0
        category_progress.append({
            'category': cat,
            'total': t,
            'completed': c,
            'pending': t - c,
            'percentage': pct
        })

    # 4. Practice test metrics
    cursor.execute('''
        SELECT 
            COUNT(*) as total_tests,
            AVG(percentage) as avg_score,
            MAX(percentage) as max_score,
            MIN(percentage) as min_score
        FROM practice_tests
        WHERE user_id = ?
    ''', (user_id,))
    test_stats = cursor.fetchone()
    total_tests = test_stats['total_tests'] or 0
    avg_score = round(test_stats['avg_score'] or 0.0, 1)
    max_test_score = round(test_stats['max_score'] or 0.0, 1)
    min_test_score = round(test_stats['min_score'] or 0.0, 1)

    cursor.execute('''
        SELECT * FROM practice_tests
        WHERE user_id = ?
        ORDER BY test_date DESC, id DESC
        LIMIT 5
    ''', (user_id,))
    recent_tests = cursor.fetchall()

    cursor.execute('''
        SELECT category, AVG(percentage) as avg_pct, COUNT(*) as count, MAX(percentage) as best_pct
        FROM practice_tests
        WHERE user_id = ?
        GROUP BY category
        ORDER BY avg_pct ASC
    ''', (user_id,))
    topic_scores = [dict(r) for r in cursor.fetchall()]
    for t in topic_scores:
        t['avg_pct'] = round(t['avg_pct'], 1)
        t['best_pct'] = round(t['best_pct'], 1)

    weak_topics = [t for t in topic_scores if t['avg_pct'] < 80.0]
    if not weak_topics and len(topic_scores) > 1:
        # Highlight lowest topic if it's notably lower than the top topic
        lowest = topic_scores[0]
        highest = topic_scores[-1]
        if highest['avg_pct'] - lowest['avg_pct'] >= 5.0:
            weak_topics = [lowest]

    # 5. Companies and placement drives
    today_str = date.today().isoformat()
    cursor.execute('''
        SELECT * FROM companies
        WHERE user_id = ? AND (drive_date >= ? OR drive_date IS NULL)
        ORDER BY CASE WHEN drive_date IS NULL THEN 1 ELSE 0 END, drive_date ASC
        LIMIT 5
    ''', (user_id, today_str))
    upcoming_companies = cursor.fetchall()

    cursor.execute('SELECT COUNT(*) as count FROM companies WHERE user_id = ?', (user_id,))
    total_companies = cursor.fetchone()['count'] or 0

    # 6. Placement Applications & Conversion Analysis
    cursor.execute('SELECT COUNT(*) as count FROM applications WHERE user_id = ?', (user_id,))
    total_applications = cursor.fetchone()['count'] or 0

    cursor.execute('''
        SELECT stage, COUNT(*) as count
        FROM applications
        WHERE user_id = ?
        GROUP BY stage
    ''', (user_id,))
    stage_counts = {s: 0 for s in APPLICATION_STAGES}
    for row in cursor.fetchall():
        norm_s = normalize_stage(row['stage'])
        stage_counts[norm_s] = stage_counts.get(norm_s, 0) + row['count']

    applied_count = stage_counts.get('Applied', 0)
    shortlisted_count = stage_counts.get('Shortlisted', 0)
    assessment_count = stage_counts.get('Assessment', 0) + stage_counts.get('Assessment/Test', 0)
    interview_count = stage_counts.get('Interview', 0)
    selected_count = stage_counts.get('Selected', 0)
    rejected_count = stage_counts.get('Rejected', 0) + stage_counts.get('Not Selected', 0)
    not_selected_count = rejected_count
    awaiting_count = stage_counts.get('Awaiting Update', 0)

    # Active applications = in-progress opportunities (Applied, Shortlisted, Assessment, Interview, Awaiting Update)
    active_applications = applied_count + shortlisted_count + assessment_count + interview_count + awaiting_count
    completed_outcomes = selected_count + not_selected_count

    # Conversion rates with safe zero handling
    selection_rate = round((selected_count / completed_outcomes) * 100, 1) if completed_outcomes > 0 else 0.0
    overall_offer_rate = round((selected_count / total_applications) * 100, 1) if total_applications > 0 else 0.0
    interview_reach_rate = round(((interview_count + selected_count) / total_applications) * 100, 1) if total_applications > 0 else 0.0
    shortlist_conversion_rate = round(((shortlisted_count + assessment_count + interview_count + selected_count) / total_applications) * 100, 1) if total_applications > 0 else 0.0

    # Company-wise application summary
    cursor.execute('''
        SELECT 
            a.company_name,
            COUNT(*) as app_count,
            GROUP_CONCAT(a.role, ', ') as roles,
            MAX(a.last_updated) as latest_update,
            c.package_ctc,
            c.drive_date
        FROM applications a
        LEFT JOIN companies c ON a.company_id = c.id
        WHERE a.user_id = ?
        GROUP BY LOWER(a.company_name)
        ORDER BY app_count DESC, latest_update DESC
    ''', (user_id,))
    company_wise_apps = [dict(r) for r in cursor.fetchall()]

    cursor.execute('''
        SELECT * FROM applications
        WHERE user_id = ?
        ORDER BY last_updated DESC, id DESC
        LIMIT 6
    ''', (user_id,))
    recent_applications = cursor.fetchall()

    conn.close()

    return {
        'profile': profile,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'task_completion_pct': int(round(task_completion_pct)),
        'task_completion_exact': task_completion_pct,
        'pending_by_priority': pending_by_priority,
        'category_progress': category_progress,
        'total_tests': total_tests,
        'avg_score': avg_score,
        'max_test_score': max_test_score,
        'min_test_score': min_test_score,
        'recent_tests': recent_tests,
        'topic_scores': topic_scores,
        'weak_topics': weak_topics,
        'upcoming_companies': upcoming_companies,
        'total_companies': total_companies,
        'total_applications': total_applications,
        'active_applications': active_applications,
        'completed_outcomes': completed_outcomes,
        'applied_count': applied_count,
        'shortlisted_count': assessment_count,
        'assessment_count': assessment_count,
        'interview_count': interview_count,
        'selected_count': selected_count,
        'rejected_count': rejected_count,
        'awaiting_count': awaiting_count,
        'not_selected_count': rejected_count,
        'stage_counts': stage_counts,
        'selection_rate': selection_rate,
        'overall_offer_rate': overall_offer_rate,
        'interview_reach_rate': interview_reach_rate,
        'shortlist_conversion_rate': shortlist_conversion_rate,
        'company_wise_apps': company_wise_apps,
        'recent_applications': recent_applications
    }

def compute_performance_analytics(user_id):
    """
    Computes performance analytics, grouped weak-topic analysis with strict threshold classification,
    trend analysis, and personalized improvement plan recommendations directly from SQLite records.
    """
    conn = db_conn()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT 
            COUNT(*) as total_assessments,
            AVG(percentage) as overall_avg,
            MAX(percentage) as highest_score,
            MIN(percentage) as lowest_score
        FROM practice_tests
        WHERE user_id = ?
    ''', (user_id,))
    overall_stats = cursor.fetchone()
    total_assessments = overall_stats['total_assessments'] or 0
    overall_avg = round(overall_stats['overall_avg'] or 0.0, 1)
    highest_score = round(overall_stats['highest_score'] or 0.0, 1)
    lowest_score = round(overall_stats['lowest_score'] or 0.0, 1)

    cursor.execute('''
        SELECT id, title, category, COALESCE(NULLIF(topic, ''), title) as topic_name, 
               test_date, score_obtained, max_score, percentage, notes, created_at
        FROM practice_tests
        WHERE user_id = ?
        ORDER BY test_date ASC, id ASC
    ''', (user_id,))
    all_tests = cursor.fetchall()

    from collections import defaultdict
    topics_dict = defaultdict(list)
    categories_dict = defaultdict(list)
    for row in all_tests:
        t_name = (row['topic_name'] or 'General Assessment').strip()
        topics_dict[t_name].append(dict(row))
        norm_cat = normalize_category(row['category'])
        categories_dict[norm_cat].append(row['percentage'])

    topic_analytics = []
    for t_name, records in topics_dict.items():
        count = len(records)
        scores = [r['percentage'] for r in records]
        avg_pct = round(sum(scores) / count, 1)
        latest_record = records[-1]
        latest_score = round(latest_record['percentage'], 1)
        category = normalize_category(latest_record['category'])

        # Trend calculation: compare latest score to previous attempt if count >= 2
        if count >= 2:
            prev_score = records[-2]['percentage']
            diff = round(latest_score - prev_score, 1)
            if diff > 0:
                trend = f"+{diff}% Improving"
                trend_direction = "up"
            elif diff < 0:
                trend = f"{diff}% Declining"
                trend_direction = "down"
            else:
                trend = "Stable"
                trend_direction = "stable"
        else:
            trend = "Single Attempt"
            trend_direction = "neutral"

        # Classification based on specified thresholds:
        # Below 40%: High Priority
        # 40%–59%: Needs Improvement
        # 60%–79%: Improving
        # 80% and above: Strong Topic
        if avg_pct < 40.0:
            priority = "High Priority"
            badge_class = "badge-danger"
            advice = f"Your average score is {avg_pct}%. Dedicate immediate focus to master fundamentals, review core definitions, and solve foundational problems before timed mocks."
        elif avg_pct < 60.0:
            priority = "Needs Improvement"
            badge_class = "badge-warning"
            advice = f"Your average score is {avg_pct}%. Revise key formulas and solve additional practice problem sets to increase accuracy above 60%."
        elif avg_pct < 80.0:
            priority = "Improving"
            badge_class = "badge-info"
            advice = f"Your average score is {avg_pct}%. Good upward consistency. Work on sectional speed drills to transition this into a strong topic (80%+)."
        else:
            priority = "Strong Topic"
            badge_class = "badge-success"
            advice = f"Your average score is {avg_pct}%. Maintain your mastery with periodic revision and full-length simulated placement mocks."

        topic_analytics.append({
            'topic': t_name,
            'category': category,
            'latest_score': latest_score,
            'avg_pct': avg_pct,
            'count': count,
            'trend': trend,
            'trend_direction': trend_direction,
            'priority': priority,
            'badge_class': badge_class,
            'advice': advice
        })

    # Sort topics from lowest-performing (weakest) to highest-performing (strongest)
    topic_analytics.sort(key=lambda x: (x['avg_pct'], x['latest_score']))

    strongest_topic = topic_analytics[-1]['topic'] if topic_analytics else None
    weakest_topic = topic_analytics[0]['topic'] if topic_analytics else None

    # Recommendations
    recommendations = list(topic_analytics)

    # Category performance breakdown
    category_summary = []
    for cat, pcts in categories_dict.items():
        c_avg = round(sum(pcts) / len(pcts), 1)
        category_summary.append({
            'category': cat,
            'count': len(pcts),
            'avg_pct': c_avg
        })
    category_summary.sort(key=lambda x: x['avg_pct'], reverse=True)

    # Chronological trajectory records for line chart (up to 15 most recent)
    timeline_records = sorted(all_tests, key=lambda x: (x['test_date'], x['id']), reverse=True)[:15]
    timeline_records.reverse()

    conn.close()

    return {
        'total_assessments': total_assessments,
        'overall_avg': overall_avg,
        'highest_score': highest_score,
        'lowest_score': lowest_score,
        'strongest_topic': strongest_topic,
        'weakest_topic': weakest_topic,
        'topic_analytics': topic_analytics,
        'recommendations': recommendations,
        'category_summary': category_summary,
        'timeline_records': timeline_records
    }

# -------------------------------------------------------------
# PUBLIC / MARKETING ROUTES
# -------------------------------------------------------------

@app.route('/')
def index():
    if g.user:
        return redirect(custom_url_for('dashboard'))
    return render_template('index.html')

@app.route('/home')
def home():
    return render_template('index.html')

# -------------------------------------------------------------
# AUTHENTICATION ROUTES
# -------------------------------------------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(custom_url_for('dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        student_id = request.form.get('student_id', '').strip().upper() or None
        password = request.form.get('password', '')
        college = request.form.get('college', '').strip()
        degree = request.form.get('degree', 'B.Tech').strip()
        branch = request.form.get('branch', 'Computer Science & Engineering').strip()
        grad_year = request.form.get('graduation_year', '2026').strip()
        cgpa = request.form.get('cgpa', '').strip()

        if not name or not email or not password:
            flash("Please fill in all required fields (Name, Email, and Password).", "error")
            return render_template('register.html', form_data=request.form)

        if '@' not in email or '.' not in email.split('@')[-1]:
            flash("Please enter a valid email address.", "error")
            return render_template('register.html', form_data=request.form)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template('register.html', form_data=request.form)

        conn = db_conn()
        cursor = conn.cursor()

        # Check duplicate email
        cursor.execute('SELECT id FROM users WHERE LOWER(email) = ?', (email,))
        if cursor.fetchone():
            conn.close()
            flash("An account with this email address already exists. Please sign in.", "error")
            return render_template('register.html', form_data=request.form)

        # Check duplicate student_id if provided
        if student_id:
            cursor.execute('SELECT id FROM users WHERE LOWER(student_id) = ?', (student_id.lower(),))
            if cursor.fetchone():
                conn.close()
                flash("An account with this Student ID already exists. Please sign in.", "error")
                return render_template('register.html', form_data=request.form)

        try:
            cgpa_val = float(cgpa) if cgpa else None
            if cgpa_val is not None and (cgpa_val < 0 or cgpa_val > 10):
                conn.close()
                flash("CGPA must be between 0.0 and 10.0.", "error")
                return render_template('register.html', form_data=request.form)
        except ValueError:
            conn.close()
            flash("Please enter a valid numeric CGPA.", "error")
            return render_template('register.html', form_data=request.form)

        try:
            grad_year_val = int(grad_year) if grad_year else 2026
        except ValueError:
            grad_year_val = 2026

        try:
            pw_hash = generate_password_hash(password)
            cursor.execute(
                'INSERT INTO users (student_id, name, email, password_hash) VALUES (?, ?, ?, ?)',
                (student_id, name, email, pw_hash)
            )
            user_id = cursor.lastrowid

            cursor.execute('''
                INSERT INTO profiles (user_id, college, degree, branch, graduation_year, cgpa, technical_skills, areas_of_interest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, college, degree, branch, grad_year_val, cgpa_val, "Python, SQL, Data Structures", "Software Engineering"))

            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("Account with those credentials already exists. Please sign in.", "error")
            return render_template('register.html', form_data=request.form)

        conn.close()

        token = create_user_session(user_id)
        flash(f"Welcome, {name}! Your placement workspace has been created.", "success")
        return redirect(append_token_to_url(flask_url_for('dashboard'), token))

    return render_template('register.html', form_data={})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(custom_url_for('dashboard'))

    if request.method == 'POST':
        # Accept identifier from 'identifier', 'email', 'student_id', or 'username'
        identifier = (
            request.form.get('identifier')
            or request.form.get('email')
            or request.form.get('student_id')
            or request.form.get('username')
            or ''
        ).strip()
        password = request.form.get('password', '')

        if not identifier or not password:
            flash("Please enter both your Email / Student ID and Password.", "error")
            return render_template('login.html', email=identifier)

        # Ensure demo student exists if someone types demo credentials directly into the login form
        if identifier.lower() in ('demo.student@university.edu', 'cse2026001', 'aarav sharma'):
            seed_demo_student(db_path=app.config.get('DATABASE'))

        conn = db_conn()
        cursor = conn.cursor()
        ident_lower = identifier.lower()
        cursor.execute('''
            SELECT id, student_id, name, email, password_hash
            FROM users
            WHERE LOWER(email) = ? OR LOWER(student_id) = ? OR LOWER(name) = ?
        ''', (ident_lower, ident_lower, ident_lower))
        user = cursor.fetchone()

        if user and verify_user_password(user, password, conn):
            conn.close()
            token = create_user_session(user['id'])
            flash(f"Welcome back, {user['name']}!", "success")
            next_url = request.args.get('next') or request.form.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(append_token_to_url(next_url, token))
            return redirect(append_token_to_url(flask_url_for('dashboard'), token))
        else:
            conn.close()
            flash("Invalid Email/Student ID or password. Please verify your credentials and try again.", "error")
            return render_template('login.html', email=identifier)

    return render_template('login.html', email='')

@app.route('/demo-login', methods=['GET', 'POST'])
def demo_login():
    """
    Controlled Demo button handler:
    - If already logged in as a student, opens their dashboard (or switches to demo if requested via ?switch=1).
    - If not logged in, idempotently seeds the Demo Student account and signs into the live demo workspace.
    """
    if g.user and request.args.get('switch') != '1':
        flash(f"Continuing as {g.user['name']}.", "info")
        return redirect(custom_url_for('dashboard'))

    user_id = seed_demo_student(db_path=app.config.get('DATABASE'))
    token = create_user_session(user_id)
    flash("Signed in to Demo Account: Aarav Sharma (ID: CSE2026001). All modules and SQLite records are live.", "success")
    return redirect(append_token_to_url(flask_url_for('dashboard'), token))

@app.route('/demo-reset', methods=['POST'])
@login_required
def demo_reset():
    """Resets sample data for the demo account only, without affecting any other student account."""
    if g.user['email'] != 'demo.student@university.edu':
        flash("Demo reset is only available for the Demo Student account.", "error")
        return redirect(custom_url_for('dashboard'))

    seed_demo_student(db_path=app.config.get('DATABASE'), force_reset=True)
    flash("Demo account sample records have been reset to their clean initial state.", "success")
    return redirect(custom_url_for('dashboard'))

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session_id = session.get('session_id')
    if not session_id and getattr(g, 'auth_token', None):
        try:
            payload = get_serializer().loads(g.auth_token)
            session_id = payload.get('sid')
        except Exception:
            session_id = None

    if session_id:
        conn = db_conn()
        conn.execute('DELETE FROM user_sessions WHERE session_id = ?', (session_id,))
        conn.commit()
        conn.close()

    session.clear()
    g.user = None
    g.auth_token = None
    flash("You have been signed out safely.", "info")
    return redirect(flask_url_for('login', logged_out='1'))

# -------------------------------------------------------------
# STUDENT DASHBOARD & PLACEMENT ANALYSIS ROUTES
# -------------------------------------------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    analytics = compute_student_analytics(g.user['id'])
    return render_template('dashboard.html', **analytics)

@app.route('/analysis')
@login_required
def analysis():
    analytics = compute_student_analytics(g.user['id'])
    return render_template('analysis.html', **analytics)

# -------------------------------------------------------------
# STUDENT PROFILE ROUTE
# -------------------------------------------------------------

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        student_id = request.form.get('student_id', '').strip().upper() or None
        college = request.form.get('college', '').strip()
        degree = request.form.get('degree', '').strip()
        branch = request.form.get('branch', '').strip()
        grad_year = request.form.get('graduation_year', '').strip()
        cgpa = request.form.get('cgpa', '').strip()
        technical_skills = request.form.get('technical_skills', '').strip()
        areas_of_interest = request.form.get('areas_of_interest', '').strip()
        bio = request.form.get('bio', '').strip()

        if not name:
            conn.close()
            flash("Full Name cannot be empty.", "error")
            return redirect(custom_url_for('profile'))

        # Check if student_id collides with another user
        if student_id:
            cursor.execute('SELECT id FROM users WHERE LOWER(student_id) = ? AND id != ?', (student_id.lower(), user_id))
            if cursor.fetchone():
                conn.close()
                flash("That Student ID is already assigned to another account.", "error")
                return redirect(custom_url_for('profile'))

        try:
            cgpa_val = float(cgpa) if cgpa else None
            if cgpa_val is not None and (cgpa_val < 0 or cgpa_val > 10):
                conn.close()
                flash("CGPA must be between 0.0 and 10.0.", "error")
                return redirect(custom_url_for('profile'))
        except ValueError:
            conn.close()
            flash("Invalid CGPA value.", "error")
            return redirect(custom_url_for('profile'))

        try:
            grad_year_val = int(grad_year) if grad_year else None
        except ValueError:
            grad_year_val = None

        cursor.execute('UPDATE users SET name = ?, student_id = ? WHERE id = ?', (name, student_id, user_id))

        cursor.execute('SELECT id FROM profiles WHERE user_id = ?', (user_id,))
        if cursor.fetchone():
            cursor.execute('''
                UPDATE profiles
                SET college = ?, degree = ?, branch = ?, graduation_year = ?, cgpa = ?,
                    technical_skills = ?, areas_of_interest = ?, bio = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (college, degree, branch, grad_year_val, cgpa_val, technical_skills, areas_of_interest, bio, user_id))
        else:
            cursor.execute('''
                INSERT INTO profiles (user_id, college, degree, branch, graduation_year, cgpa, technical_skills, areas_of_interest, bio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, college, degree, branch, grad_year_val, cgpa_val, technical_skills, areas_of_interest, bio))

        conn.commit()
        conn.close()
        flash("Profile updated and saved to SQLite.", "success")
        return redirect(custom_url_for('profile'))

    cursor.execute('SELECT * FROM profiles WHERE user_id = ?', (user_id,))
    profile_data = cursor.fetchone()
    conn.close()

    return render_template('profile.html', profile=profile_data)

# -------------------------------------------------------------
# PREPARATION TRACKER ROUTES
# -------------------------------------------------------------

@app.route('/preparation', methods=['GET'])
@login_required
def preparation():
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    selected_category = request.args.get('category', 'All').strip()
    selected_status = request.args.get('status', 'All').strip()
    selected_priority = request.args.get('priority', 'All').strip()
    search_query = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'default').strip()

    query = 'SELECT * FROM preparation_tasks WHERE user_id = ?'
    params = [user_id]

    if selected_category and selected_category != 'All':
        query += ' AND category = ?'
        params.append(selected_category)

    if selected_status and selected_status != 'All':
        query += ' AND status = ?'
        params.append(selected_status.lower())

    if selected_priority and selected_priority != 'All':
        query += ' AND priority = ?'
        params.append(selected_priority)

    if search_query:
        query += ' AND (title LIKE ? OR notes LIKE ? OR category LIKE ?)'
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard])

    if sort_by == 'due_date':
        query += " ORDER BY CASE WHEN due_date IS NULL OR due_date = '' THEN 1 ELSE 0 END, due_date ASC, id DESC"
    elif sort_by == 'priority':
        query += " ORDER BY CASE priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, id DESC"
    elif sort_by == 'title':
        query += " ORDER BY LOWER(title) ASC"
    else:
        query += " ORDER BY CASE status WHEN 'pending' THEN 1 ELSE 2 END, CASE priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, due_date ASC, id DESC"

    cursor.execute(query, params)
    tasks = cursor.fetchall()

    # Category summaries
    category_progress = []
    for cat in CATEGORIES:
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
            FROM preparation_tasks
            WHERE user_id = ? AND category = ?
        ''', (user_id, cat))
        r = cursor.fetchone()
        t = r['total'] or 0
        c = r['completed'] or 0
        pct = round((c / t * 100)) if t > 0 else 0
        category_progress.append({
            'category': cat,
            'total': t,
            'completed': c,
            'percentage': pct
        })

    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM preparation_tasks
        WHERE user_id = ?
    ''', (user_id,))
    totals = cursor.fetchone()
    total_count = totals['total'] or 0
    completed_count = totals['completed'] or 0

    conn.close()

    return render_template(
        'preparation.html',
        tasks=tasks,
        category_progress=category_progress,
        total_count=total_count,
        completed_count=completed_count,
        selected_category=selected_category,
        selected_status=selected_status,
        selected_priority=selected_priority,
        search_query=search_query,
        sort_by=sort_by
    )

@app.route('/preparation/add', methods=['POST'])
@login_required
def add_preparation_task():
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'Coding Practice').strip()
    priority = request.form.get('priority', 'Medium').strip()
    due_date = request.form.get('due_date', '').strip() or None
    notes = request.form.get('notes', '').strip()

    if not title:
        flash("Task title is required.", "error")
        return redirect(custom_url_for('preparation'))

    if category not in CATEGORIES:
        category = 'Coding Practice'
    if priority not in ('High', 'Medium', 'Low'):
        priority = 'Medium'

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO preparation_tasks (user_id, title, category, priority, status, due_date, notes)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
    ''', (g.user['id'], title, category, priority, due_date, notes))
    conn.commit()
    conn.close()

    flash(f"Task '{title}' added to {category}.", "success")
    return redirect(custom_url_for('preparation'))

@app.route('/preparation/<int:task_id>/edit', methods=['POST'])
@login_required
def edit_preparation_task(task_id):
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'Coding Practice').strip()
    priority = request.form.get('priority', 'Medium').strip()
    status = request.form.get('status', 'pending').strip().lower()
    due_date = request.form.get('due_date', '').strip() or None
    notes = request.form.get('notes', '').strip()

    if not title:
        flash("Task title is required.", "error")
        return redirect(custom_url_for('preparation'))

    if category not in CATEGORIES:
        category = 'Coding Practice'
    if priority not in ('High', 'Medium', 'Low'):
        priority = 'Medium'
    if status not in ('pending', 'completed'):
        status = 'pending'

    completed_at = datetime.now().isoformat() if status == 'completed' else None

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE preparation_tasks
        SET title = ?, category = ?, priority = ?, status = ?, due_date = ?, notes = ?, completed_at = ?
        WHERE id = ? AND user_id = ?
    ''', (title, category, priority, status, due_date, notes, completed_at, task_id, g.user['id']))
    updated = cursor.rowcount
    conn.commit()
    conn.close()

    if updated:
        flash("Task updated successfully.", "success")
    else:
        flash("Task not found or access denied.", "error")
    return redirect(custom_url_for('preparation'))

@app.route('/preparation/<int:task_id>/toggle', methods=['POST'])
@login_required
def toggle_preparation_task_form(task_id):
    """Standard form-based toggle endpoint (also supports non-JS form submissions)."""
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT title, status FROM preparation_tasks WHERE id = ? AND user_id = ?', (task_id, g.user['id']))
    task = cursor.fetchone()
    if not task:
        conn.close()
        flash("Task not found.", "error")
        return redirect(custom_url_for('preparation'))

    new_status = 'pending' if task['status'] == 'completed' else 'completed'
    completed_at = datetime.now().isoformat() if new_status == 'completed' else None
    cursor.execute('''
        UPDATE preparation_tasks
        SET status = ?, completed_at = ?
        WHERE id = ? AND user_id = ?
    ''', (new_status, completed_at, task_id, g.user['id']))
    conn.commit()
    conn.close()

    flash(f"Marked '{task['title']}' as {new_status}.", "success")
    return redirect(custom_url_for('preparation'))

@app.route('/preparation/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_preparation_task(task_id):
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM preparation_tasks WHERE id = ? AND user_id = ?', (task_id, g.user['id']))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        flash("Preparation task deleted.", "info")
    else:
        flash("Task not found or unauthorized.", "error")
    return redirect(custom_url_for('preparation'))

@app.route('/api/tasks/<int:task_id>/toggle', methods=['POST'])
@login_required
def toggle_task(task_id):
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT status, category FROM preparation_tasks WHERE id = ? AND user_id = ?', (task_id, g.user['id']))
    task = cursor.fetchone()
    if not task:
        conn.close()
        return jsonify({'success': False, 'error': 'Task not found'}), 404

    new_status = 'pending' if task['status'] == 'completed' else 'completed'
    completed_at = datetime.now().isoformat() if new_status == 'completed' else None

    cursor.execute('''
        UPDATE preparation_tasks
        SET status = ?, completed_at = ?
        WHERE id = ? AND user_id = ?
    ''', (new_status, completed_at, task_id, g.user['id']))

    # Return updated counts for accurate UI sync
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM preparation_tasks
        WHERE user_id = ?
    ''', (g.user['id'],))
    totals = cursor.fetchone()
    total_count = totals['total'] or 0
    completed_count = totals['completed'] or 0
    overall_pct = round((completed_count / total_count * 100)) if total_count > 0 else 0

    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM preparation_tasks
        WHERE user_id = ? AND category = ?
    ''', (g.user['id'], task['category']))
    cat_row = cursor.fetchone()
    cat_total = cat_row['total'] or 0
    cat_completed = cat_row['completed'] or 0

    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'new_status': new_status,
        'total_count': total_count,
        'completed_count': completed_count,
        'overall_pct': overall_pct,
        'category': task['category'],
        'category_total': cat_total,
        'category_completed': cat_completed
    })

# -------------------------------------------------------------
# PRACTICE TESTS & PERFORMANCE ANALYTICS ROUTES (Replaces Practice Tests)
# -------------------------------------------------------------

@app.route('/performance-analytics', methods=['GET'])
@login_required
def performance_analytics():
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    selected_category = request.args.get('category', 'All').strip()
    search_query = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'date_desc').strip()

    query = 'SELECT * FROM practice_tests WHERE user_id = ?'
    params = [user_id]
    if selected_category and selected_category != 'All':
        norm_cat = normalize_category(selected_category)
        query += ' AND (category = ? OR category = ?)'
        if norm_cat == 'Quantitative Aptitude':
            params.extend(['Quantitative Aptitude', 'Aptitude'])
        elif norm_cat == 'Technical Knowledge':
            params.extend(['Technical Knowledge', 'Technical Subjects'])
        else:
            params.extend([norm_cat, selected_category])

    if search_query:
        query += ' AND (title LIKE ? OR topic LIKE ? OR notes LIKE ? OR category LIKE ?)'
        wildcard = f"%{search_query}%"
        params.extend([wildcard, wildcard, wildcard, wildcard])

    if sort_by == 'date_asc':
        query += ' ORDER BY test_date ASC, id ASC'
    elif sort_by == 'score_desc':
        query += ' ORDER BY percentage DESC, test_date DESC'
    elif sort_by == 'score_asc':
        query += ' ORDER BY percentage ASC, test_date DESC'
    elif sort_by == 'topic':
        query += ' ORDER BY COALESCE(NULLIF(topic, ""), title) ASC, test_date DESC'
    else:
        query += ' ORDER BY test_date DESC, id DESC'

    cursor.execute(query, params)
    raw_tests = cursor.fetchall()
    tests = []
    for r in raw_tests:
        d = dict(r)
        d['category'] = normalize_category(d['category'])
        if not d.get('topic'):
            d['topic'] = d['title']
        tests.append(d)

    conn.close()

    analytics = compute_performance_analytics(user_id)

    return render_template(
        'performance_analytics.html',
        tests=tests,
        selected_category=selected_category,
        search_query=search_query,
        sort_by=sort_by,
        **analytics
    )

@app.route('/practice-tests', methods=['GET'])
@login_required
def practice_tests():
    return performance_analytics()

@app.route('/performance-analytics/add', methods=['POST'])
@app.route('/practice-tests/add', methods=['POST'])
@login_required
def add_performance_test():
    title = request.form.get('title', '').strip()
    category = normalize_category(request.form.get('category', 'Quantitative Aptitude'))
    topic = request.form.get('topic', '').strip()
    test_date = request.form.get('test_date', '').strip() or date.today().isoformat()
    score_obtained_raw = request.form.get('score_obtained', '').strip()
    max_score_raw = request.form.get('max_score', '').strip()
    notes = request.form.get('notes', '').strip()

    if not title:
        flash("Assessment name is required.", "error")
        return redirect(custom_url_for('performance_analytics'))

    if not topic:
        topic = title

    try:
        score_obtained = float(score_obtained_raw)
        max_score = float(max_score_raw)
        if max_score <= 0:
            flash("Maximum marks must be strictly greater than zero.", "error")
            return redirect(custom_url_for('performance_analytics'))
        if score_obtained < 0:
            flash("Marks obtained cannot be negative.", "error")
            return redirect(custom_url_for('performance_analytics'))
        if score_obtained > max_score:
            flash("Marks obtained cannot exceed maximum marks.", "error")
            return redirect(custom_url_for('performance_analytics'))
        percentage = round((score_obtained / max_score) * 100, 2)
    except (ValueError, TypeError):
        flash("Please enter valid numeric values for marks obtained and maximum marks.", "error")
        return redirect(custom_url_for('performance_analytics'))

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO practice_tests (user_id, title, category, topic, test_date, score_obtained, max_score, percentage, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (g.user['id'], title, category, topic, test_date, score_obtained, max_score, percentage, notes))
    conn.commit()
    conn.close()

    flash(f"Assessment '{title}' recorded successfully: {percentage}% on '{topic}'.", "success")
    return redirect(custom_url_for('performance_analytics'))

@app.route('/performance-analytics/<int:test_id>/edit', methods=['POST'])
@app.route('/practice-tests/<int:test_id>/edit', methods=['POST'])
@login_required
def edit_performance_test(test_id):
    title = request.form.get('title', '').strip()
    category = normalize_category(request.form.get('category', 'Quantitative Aptitude'))
    topic = request.form.get('topic', '').strip()
    test_date = request.form.get('test_date', '').strip() or date.today().isoformat()
    score_obtained_raw = request.form.get('score_obtained', '').strip()
    max_score_raw = request.form.get('max_score', '').strip()
    notes = request.form.get('notes', '').strip()

    if not title:
        flash("Assessment name is required.", "error")
        return redirect(custom_url_for('performance_analytics'))

    if not topic:
        topic = title

    try:
        score_obtained = float(score_obtained_raw)
        max_score = float(max_score_raw)
        if max_score <= 0 or score_obtained < 0 or score_obtained > max_score:
            flash("Invalid marks: Obtained must be non-negative and <= maximum score (> 0).", "error")
            return redirect(custom_url_for('performance_analytics'))
        percentage = round((score_obtained / max_score) * 100, 2)
    except (ValueError, TypeError):
        flash("Please enter valid numeric marks.", "error")
        return redirect(custom_url_for('performance_analytics'))

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE practice_tests
        SET title = ?, category = ?, topic = ?, test_date = ?, score_obtained = ?, max_score = ?, percentage = ?, notes = ?
        WHERE id = ? AND user_id = ?
    ''', (title, category, topic, test_date, score_obtained, max_score, percentage, notes, test_id, g.user['id']))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    if updated:
        flash(f"Assessment '{title}' updated successfully.", "success")
    else:
        flash("Assessment record not found or access denied.", "error")
    return redirect(custom_url_for('performance_analytics'))

@app.route('/performance-analytics/<int:test_id>/delete', methods=['POST'])
@app.route('/practice-tests/<int:test_id>/delete', methods=['POST'])
@login_required
def delete_performance_test(test_id):
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM practice_tests WHERE id = ? AND user_id = ?', (test_id, g.user['id']))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    if deleted:
        flash("Assessment record deleted successfully.", "success")
    else:
        flash("Assessment record not found or access denied.", "error")
    return redirect(custom_url_for('performance_analytics'))

# -------------------------------------------------------------
# PLACEMENT HUB ROUTES (Unified Drives + Application Tracker)
# -------------------------------------------------------------

@app.route('/placement-hub', methods=['GET'])
@login_required
def placement_hub():
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']
    is_coord = bool(g.user and g.user['role'] in ('admin', 'coordinator'))

    active_tab = request.args.get('tab', 'explore').strip().lower()
    if active_tab not in ('explore', 'applications'):
        active_tab = 'explore'

    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('timeline', 'all').strip()
    job_type_filter = request.args.get('job_type', 'all').strip()
    work_mode_filter = request.args.get('work_mode', 'all').strip()
    sort_by = request.args.get('sort', 'deadline').strip()
    stage_filter = request.args.get('stage', 'All').strip()
    status_group = request.args.get('group', 'all').strip().lower()
    highlight_id = request.args.get('highlight', type=int)

    # 1. Fetch Student Profile for dynamic eligibility check
    cursor.execute('SELECT * FROM profiles WHERE user_id = ?', (user_id,))
    student_profile = cursor.fetchone()
    student_cgpa = student_profile['cgpa'] if student_profile and student_profile['cgpa'] else None

    today_str = date.today().isoformat()

    # Ensure published campus opportunities exist for all students
    from database import seed_shared_placement_drives
    cursor.execute("SELECT COUNT(*) as cnt FROM companies WHERE status = 'Published'")
    if cursor.fetchone()['cnt'] == 0:
        seed_shared_placement_drives(cursor, user_id=user_id)
        conn.commit()

    # 2. Fetch shared companies for Explore Opportunities tab
    # Published drives are globally shared; user-owned drives are also visible to their creator
    if is_coord:
        comp_query = 'SELECT * FROM companies WHERE 1=1'
        comp_params = []
    else:
        comp_query = "SELECT * FROM companies WHERE (status = 'Published' OR status IS NULL OR user_id = ?)"
        comp_params = [user_id]

    if search_query and active_tab == 'explore':
        comp_query += ' AND (company_name LIKE ? OR role LIKE ? OR required_skills LIKE ? OR eligibility LIKE ? OR location LIKE ?)'
        wildcard = f"%{search_query}%"
        comp_params.extend([wildcard, wildcard, wildcard, wildcard, wildcard])

    if status_filter == 'upcoming':
        comp_query += ' AND (deadline >= ? OR deadline IS NULL OR deadline = "")'
        comp_params.append(today_str)
    elif status_filter == 'past':
        comp_query += ' AND deadline < ? AND deadline != ""'
        comp_params.append(today_str)

    if job_type_filter != 'all':
        comp_query += ' AND job_type = ?'
        comp_params.append(job_type_filter)

    if work_mode_filter != 'all':
        comp_query += ' AND work_mode = ?'
        comp_params.append(work_mode_filter)

    if sort_by == 'company_name':
        comp_query += ' ORDER BY LOWER(company_name) ASC, id DESC'
    elif sort_by == 'package':
        comp_query += ' ORDER BY package_ctc DESC, id DESC'
    elif sort_by == 'drive_date':
        comp_query += ' ORDER BY CASE WHEN drive_date >= ? THEN 0 ELSE 1 END, drive_date ASC, id DESC'
        comp_params.append(today_str)
    elif sort_by == 'recent':
        comp_query += ' ORDER BY id DESC'
    else:  # default deadline
        comp_query += " ORDER BY CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END, deadline ASC, id DESC"

    cursor.execute(comp_query, comp_params)
    raw_companies = cursor.fetchall()

    # Dynamic eligibility evaluator for each company card
    company_list = []
    for comp in raw_companies:
        c = dict(comp)
        elig_text = c.get('eligibility') or ''
        min_cgpa = c.get('min_cgpa') or 0.0
        if not min_cgpa:
            cgpa_match = re.search(r'(?:cgpa|gpa)\s*(?:>=|>|:)?\s*([0-9]+(?:\.[0-9]+)?)', elig_text, re.IGNORECASE)
            if cgpa_match:
                min_cgpa = float(cgpa_match.group(1))

        if min_cgpa > 0.0 and student_cgpa is not None:
            if student_cgpa >= min_cgpa:
                c['eligibility_status'] = 'Eligible'
                c['eligibility_class'] = 'badge-success'
                c['eligibility_detail'] = f"Eligible (Your CGPA: {student_cgpa} ≥ {min_cgpa})"
                c['is_eligible'] = True
            else:
                c['eligibility_status'] = 'Criteria Mismatch'
                c['eligibility_class'] = 'badge-danger'
                c['eligibility_detail'] = f"Requires CGPA ≥ {min_cgpa} (Your CGPA: {student_cgpa})"
                c['is_eligible'] = False
        elif student_cgpa is not None:
            c['eligibility_status'] = 'Eligible'
            c['eligibility_class'] = 'badge-success'
            c['eligibility_detail'] = f"Profile CGPA: {student_cgpa}"
            c['is_eligible'] = True
        else:
            c['eligibility_status'] = 'Check Criteria'
            c['eligibility_class'] = 'badge-info'
            c['eligibility_detail'] = 'Update CGPA in profile to verify'
            c['is_eligible'] = None

        # Deadline countdown & expired indicator
        if c.get('deadline'):
            try:
                dl_date = datetime.strptime(c['deadline'], '%Y-%m-%d').date()
                curr_date = date.today()
                diff_days = (dl_date - curr_date).days
                if diff_days < 0:
                    c['is_expired'] = True
                    c['deadline_text'] = f"Expired ({abs(diff_days)}d ago)"
                elif diff_days == 0:
                    c['is_expired'] = False
                    c['deadline_text'] = "Expires Today!"
                elif diff_days == 1:
                    c['is_expired'] = False
                    c['deadline_text'] = "1 day left"
                else:
                    c['is_expired'] = False
                    c['deadline_text'] = f"{diff_days} days left"
            except Exception:
                c['is_expired'] = False
                c['deadline_text'] = c['deadline']
        else:
            c['is_expired'] = False
            c['deadline_text'] = "Open"

        company_list.append(c)

    # 3. Fetch applications for My Applications tab
    # Students view only their own applications; coordinators can inspect all submissions
    if is_coord and request.args.get('view_all') == '1':
        app_query = '''
            SELECT a.*, u.name as student_name, u.email as student_email, u.student_id as student_roll,
                   c.package_ctc as drive_package, c.location as drive_location, c.work_mode as drive_work_mode,
                   c.job_type as drive_job_type, c.drive_date as drive_date, c.deadline as drive_deadline,
                   c.eligibility as drive_eligibility, c.required_skills as drive_skills,
                   c.selection_rounds as drive_rounds, c.job_link as drive_link, c.job_description as drive_description
            FROM applications a
            JOIN users u ON a.user_id = u.id
            LEFT JOIN companies c ON a.company_id = c.id
            WHERE 1=1
        '''
        app_params = []
    else:
        app_query = '''
            SELECT a.*,
                   c.package_ctc as drive_package, c.location as drive_location, c.work_mode as drive_work_mode,
                   c.job_type as drive_job_type, c.drive_date as drive_date, c.deadline as drive_deadline,
                   c.eligibility as drive_eligibility, c.required_skills as drive_skills,
                   c.selection_rounds as drive_rounds, c.job_link as drive_link, c.job_description as drive_description
            FROM applications a
            LEFT JOIN companies c ON a.company_id = c.id
            WHERE a.user_id = ?
        '''
        app_params = [user_id]

    if stage_filter and stage_filter != 'All':
        norm_filter = normalize_stage(stage_filter)
        app_query += ' AND a.stage = ?'
        app_params.append(norm_filter)

    if status_group == 'in_progress':
        app_query += " AND a.stage IN ('Applied', 'Under Review', 'Shortlisted', 'Assessment Scheduled', 'Assessment Completed', 'Assessment', 'Assessment/Test', 'Interview Scheduled', 'Interview Completed', 'Interview', 'Awaiting Update', 'On Hold')"
    elif status_group == 'selected':
        app_query += " AND a.stage IN ('Selected', 'Offer Received')"
    elif status_group == 'not_selected':
        app_query += " AND a.stage IN ('Not Selected', 'Rejected')"
    elif status_group == 'withdrawn':
        app_query += " AND a.stage = 'Withdrawn'"

    if search_query and active_tab == 'applications':
        app_query += ' AND (a.company_name LIKE ? OR a.role LIKE ? OR a.notes LIKE ? OR a.app_ref LIKE ?)'
        wildcard = f"%{search_query}%"
        app_params.extend([wildcard, wildcard, wildcard, wildcard])

    app_sort = request.args.get('app_sort', 'updated_desc').strip()
    if app_sort == 'date_desc':
        app_query += ' ORDER BY a.application_date DESC, a.id DESC'
    elif app_sort == 'date_asc':
        app_query += ' ORDER BY a.application_date ASC, a.id ASC'
    elif app_sort == 'company_asc':
        app_query += ' ORDER BY LOWER(a.company_name) ASC, a.id DESC'
    elif app_sort == 'stage_order':
        app_query += '''
            ORDER BY CASE a.stage
                WHEN 'Offer Received' THEN 1
                WHEN 'Selected' THEN 2
                WHEN 'Interview Scheduled' THEN 3
                WHEN 'Interview' THEN 4
                WHEN 'Assessment Scheduled' THEN 5
                WHEN 'Assessment' THEN 6
                WHEN 'Shortlisted' THEN 7
                WHEN 'Under Review' THEN 8
                WHEN 'Applied' THEN 9
                WHEN 'Awaiting Update' THEN 10
                WHEN 'On Hold' THEN 11
                WHEN 'Not Selected' THEN 12
                WHEN 'Rejected' THEN 13
                WHEN 'Withdrawn' THEN 14
                ELSE 15
            END, a.last_updated DESC
        '''
    else:
        app_query += ' ORDER BY a.last_updated DESC, a.id DESC'

    cursor.execute(app_query, app_params)
    raw_apps = cursor.fetchall()
    app_list = []
    for row in raw_apps:
        d = dict(row)
        d['stage'] = normalize_stage(d['stage'])
        app_list.append(d)

    # Fetch complete status history audit log for every application
    app_ids = [a['id'] for a in app_list]
    status_histories = {}
    if app_ids:
        placeholders = ','.join('?' * len(app_ids))
        cursor.execute(f'''
            SELECT * FROM application_status_history
            WHERE application_id IN ({placeholders})
            ORDER BY created_at ASC, id ASC
        ''', app_ids)
        for h in cursor.fetchall():
            aid = h['application_id']
            if aid not in status_histories:
                status_histories[aid] = []
            status_histories[aid].append(dict(h))

    # Build mapping of company_id to application for Explore tab
    cursor.execute('SELECT id, company_id, stage, app_ref FROM applications WHERE user_id = ? AND company_id IS NOT NULL', (user_id,))
    app_mappings = {}
    app_id_mappings = {}
    app_ref_mappings = {}
    for r in cursor.fetchall():
        app_mappings[r['company_id']] = normalize_stage(r['stage'])
        app_id_mappings[r['company_id']] = r['id']
        app_ref_mappings[r['company_id']] = r['app_ref']

    # Stage counts for Application Progress summary
    cursor.execute('''
        SELECT stage, COUNT(*) as count
        FROM applications
        WHERE user_id = ?
        GROUP BY stage
    ''', (user_id,))
    stage_counts = {s: 0 for s in APPLICATION_STAGES}
    for r in cursor.fetchall():
        ns = normalize_stage(r['stage'])
        stage_counts[ns] = stage_counts.get(ns, 0) + r['count']

    total_apps_count = len(app_list)
    active_apps_count = sum(stage_counts.get(s, 0) for s in ACTIVE_STAGES)

    # 4. Job Sources & Email Integration status
    cursor.execute('SELECT * FROM job_sources ORDER BY id ASC')
    job_sources = [dict(r) for r in cursor.fetchall()]

    cursor.execute('SELECT * FROM recruitment_integrations WHERE user_id = ? ORDER BY id DESC LIMIT 1', (user_id,))
    email_integration_row = cursor.fetchone()
    email_integration = dict(email_integration_row) if email_integration_row else None

    demo_email_templates = email_recruiter.get_demo_email_templates(app_list)

    conn.close()

    return render_template(
        'placement_hub.html',
        active_tab=active_tab,
        companies=company_list,
        applications=app_list,
        app_mappings=app_mappings,
        app_id_mappings=app_id_mappings,
        app_ref_mappings=app_ref_mappings,
        status_histories=status_histories,
        stage_counts=stage_counts,
        total_apps_count=total_apps_count,
        active_apps_count=active_apps_count,
        student_profile=student_profile,
        student_cgpa=student_cgpa,
        search_query=search_query,
        status_filter=status_filter,
        job_type_filter=job_type_filter,
        work_mode_filter=work_mode_filter,
        sort_by=sort_by,
        stage_filter=stage_filter,
        status_group=status_group,
        app_sort=app_sort,
        highlight_id=highlight_id,
        today=today_str,
        is_coordinator=is_coord,
        job_sources=job_sources,
        email_integration=email_integration,
        demo_email_templates=demo_email_templates
    )

@app.route('/companies', methods=['GET'])
@login_required
def companies():
    return placement_hub()

@app.route('/applications', methods=['GET'])
@login_required
def applications():
    return placement_hub()

@app.route('/placement-hub/track/<int:company_id>', methods=['POST'])
@login_required
def track_company_opportunity(company_id):
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    cursor.execute('SELECT * FROM companies WHERE id = ? AND (status = "Published" OR status IS NULL OR user_id = ?)', (company_id, user_id))
    comp = cursor.fetchone()
    if not comp:
        conn.close()
        flash("Opportunity record not found or access denied.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    # Prevent duplicate tracking
    cursor.execute('SELECT id, stage, app_ref FROM applications WHERE user_id = ? AND company_id = ?', (user_id, company_id))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        flash(f"'{comp['company_name']}' is already in your application tracking list (Status: {normalize_stage(existing['stage'])}).", "info")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    clean_code = re.sub(r'[^A-Z]', '', comp['company_name'].upper())[:4] or "APP"
    import random
    app_ref = f"{clean_code}-2026-{random.randint(1000, 9999)}"
    app_date = date.today().isoformat()

    cursor.execute('''
        INSERT INTO applications (
            user_id, company_id, company_name, role, stage, application_date,
            app_ref, next_step, status_note, notes
        ) VALUES (?, ?, ?, ?, 'Applied', ?, ?, ?, ?, ?)
    ''', (
        user_id, company_id, comp['company_name'], comp['role'], app_date,
        app_ref, "Application added to tracking list. Awaiting campus review.",
        "Tracked from Explore Opportunities in Placement Hub.",
        "Tracked from Explore Opportunities in Placement Hub."
    ))
    new_app_id = cursor.lastrowid

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by
        ) VALUES (?, ?, 'Applied', 'Tracked from Explore Opportunities in Placement Hub.', ?)
    ''', (new_app_id, user_id, g.user['name']))

    conn.commit()
    conn.close()

    flash(f"'{comp['company_name']} — {comp['role']}' added to My Applications (Status: Applied).", "success")
    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/placement-hub/apply/<int:company_id>', methods=['POST'])
@login_required
def apply_to_placement_drive(company_id):
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    cursor.execute('SELECT * FROM companies WHERE id = ?', (company_id,))
    comp = cursor.fetchone()
    if not comp:
        conn.close()
        flash("Placement drive not found or has been closed.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    # Check if drive is closed
    if comp['status'] and comp['status'] in ('Closed', 'Unpublished', 'Draft'):
        conn.close()
        flash(f"Placement drive for '{comp['company_name']}' is currently closed or not accepting applications.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    # Check deadline
    today_str = date.today().isoformat()
    if comp['deadline'] and comp['deadline'] < today_str:
        conn.close()
        flash(f"Application deadline ({comp['deadline']}) for '{comp['company_name']}' has passed.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    # Check duplicate active application
    cursor.execute('''
        SELECT id, stage, app_ref FROM applications
        WHERE user_id = ? AND company_id = ? AND stage != 'Withdrawn'
    ''', (user_id, company_id))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        flash(f"You have already applied for '{comp['company_name']} — {comp['role']}' (Ref: {existing['app_ref'] or 'Registered'}, Status: {normalize_stage(existing['stage'])}).", "warning")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    phone = request.form.get('phone', '').strip()
    notes = request.form.get('notes', '').strip()
    cgpa_raw = request.form.get('cgpa', '').strip()

    # CGPA & Eligibility check
    cursor.execute('SELECT * FROM profiles WHERE user_id = ?', (user_id,))
    profile = cursor.fetchone()
    student_cgpa = None
    if cgpa_raw:
        try:
            student_cgpa = float(cgpa_raw)
        except ValueError:
            pass
    elif profile and profile['cgpa']:
        student_cgpa = profile['cgpa']

    min_cgpa = comp['min_cgpa'] if 'min_cgpa' in comp.keys() and comp['min_cgpa'] else 0.0
    if not min_cgpa and comp['eligibility']:
        cgpa_match = re.search(r'(?:cgpa|gpa)\s*(?:>=|>|:)?\s*([0-9]+(?:\.[0-9]+)?)', comp['eligibility'], re.IGNORECASE)
        if cgpa_match:
            min_cgpa = float(cgpa_match.group(1))

    if min_cgpa > 0.0:
        if student_cgpa is None:
            conn.close()
            flash(f"Please enter your CGPA or update your profile to apply for '{comp['company_name']}' (Requires CGPA ≥ {min_cgpa}).", "error")
            return redirect(custom_url_for('placement_hub', tab='explore'))
        elif student_cgpa < min_cgpa:
            conn.close()
            flash(f"Your CGPA ({student_cgpa}) is below the required minimum of {min_cgpa} for '{comp['company_name']}'.", "error")
            return redirect(custom_url_for('placement_hub', tab='explore'))

    # Generate reference code
    clean_code = re.sub(r'[^A-Z]', '', comp['company_name'].upper())[:4] or "APP"
    import random
    app_ref = f"{clean_code}-2026-{random.randint(1000, 9999)}"

    cursor.execute('''
        INSERT INTO applications (
            user_id, company_id, company_name, role, stage, application_date,
            app_ref, phone, next_step, status_note, notes
        ) VALUES (?, ?, ?, ?, 'Applied', ?, ?, ?, ?, ?, ?)
    ''', (
        user_id, company_id, comp['company_name'], comp['role'], today_str,
        app_ref, phone or (g.user['phone'] if 'phone' in g.user.keys() else ''),
        "Application submitted and under review by campus placement cell.",
        "Application submitted directly through Placement Hub.",
        notes or "Applied via campus Placement Hub portal."
    ))
    app_id = cursor.lastrowid

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by
        ) VALUES (?, ?, 'Applied', 'Direct campus application submitted via Placement Hub portal.', ?)
    ''', (app_id, user_id, g.user['name']))

    cursor.execute('''
        INSERT INTO notifications (user_id, title, message, link)
        VALUES (?, ?, ?, ?)
    ''', (
        user_id,
        f"Application Submitted: {comp['company_name']}",
        f"Your application for {comp['role']} at {comp['company_name']} has been recorded with reference {app_ref}.",
        "/placement-hub?tab=applications"
    ))

    conn.commit()
    conn.close()

    flash(f"Application successfully submitted for '{comp['company_name']} — {comp['role']}'! (Application Ref: {app_ref})", "success")
    return redirect(custom_url_for('placement_hub', tab='applications', highlight=app_id))

@app.route('/placement-hub/applications/<int:app_id>/withdraw', methods=['POST'])
@login_required
def withdraw_application(app_id):
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    cursor.execute('SELECT * FROM applications WHERE id = ? AND user_id = ?', (app_id, user_id))
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        flash("Application record not found or access denied.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    if app_row['stage'] in ('Selected', 'Offer Received'):
        conn.close()
        flash("Cannot withdraw an application once an offer has been accepted or extended.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    now_ts = datetime.now().isoformat()
    cursor.execute('''
        UPDATE applications
        SET stage = 'Withdrawn', withdrawn_at = ?, last_updated = ?
        WHERE id = ? AND user_id = ?
    ''', (now_ts, now_ts, app_id, user_id))

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by
        ) VALUES (?, ?, 'Withdrawn', 'Application voluntarily withdrawn by candidate.', ?)
    ''', (app_id, user_id, g.user['name']))

    cursor.execute('''
        INSERT INTO notifications (user_id, title, message, link)
        VALUES (?, ?, ?, ?)
    ''', (
        user_id,
        f"Application Withdrawn: {app_row['company_name']}",
        f"You have withdrawn your application for {app_row['role']} at {app_row['company_name']}.",
        "/placement-hub?tab=applications"
    ))

    conn.commit()
    conn.close()

    flash(f"Application for '{app_row['company_name']} — {app_row['role']}' has been withdrawn.", "info")
    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/coordinator/applications/<int:app_id>/status', methods=['POST'])
@login_required
@coordinator_required
def coordinator_update_status(app_id):
    new_stage_raw = request.form.get('stage', '').strip()
    status_note = request.form.get('status_note', '').strip()
    next_step = request.form.get('next_step', '').strip()
    interview_date = request.form.get('interview_date', '').strip()
    interview_venue = request.form.get('interview_venue', '').strip()
    interview_details = request.form.get('interview_details', '').strip()
    assessment_details = request.form.get('assessment_details', '').strip()

    new_stage = normalize_stage(new_stage_raw)
    conn = db_conn()
    cursor = conn.cursor()

    cursor.execute('SELECT a.*, u.name as student_name, u.email as student_email FROM applications a JOIN users u ON a.user_id = u.id WHERE a.id = ?', (app_id,))
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        flash("Application record not found.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    now_ts = datetime.now().isoformat()
    interview_text = interview_details or (f"Date/Time: {interview_date} | Venue: {interview_venue}" if (interview_date or interview_venue) else app_row['interview_details'])

    cursor.execute('''
        UPDATE applications
        SET stage = ?, status_note = ?, next_step = ?,
            interview_details = ?, assessment_details = ?, last_updated = ?
        WHERE id = ?
    ''', (
        new_stage,
        status_note or f"Status officially updated to '{new_stage}' by Placement Coordinator.",
        next_step or app_row['next_step'],
        interview_text,
        assessment_details or app_row['assessment_details'],
        now_ts,
        app_id
    ))

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, interview_date, interview_venue, updated_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (app_id, app_row['user_id'], new_stage, status_note, interview_date, interview_venue, g.user['name']))

    cursor.execute('''
        INSERT INTO notifications (user_id, title, message, link)
        VALUES (?, ?, ?, ?)
    ''', (
        app_row['user_id'],
        f"Status Update: {app_row['company_name']}",
        f"Your application status for {app_row['role']} at {app_row['company_name']} is now '{new_stage}'. {status_note}",
        "/placement-hub?tab=applications"
    ))

    conn.commit()
    conn.close()

    flash(f"Application for student {app_row['student_name']} updated to '{new_stage}'. In-app notification sent.", "success")
    return redirect(custom_url_for('placement_hub', tab='applications', highlight=app_id))

# -------------------------------------------------------------
# ONLINE OPPORTUNITIES SYNC & EXTERNAL APPLICATION WORKFLOWS
# -------------------------------------------------------------

@app.route('/placement-hub/refresh-jobs', methods=['POST'])
@login_required
def refresh_placement_jobs():
    """Manually fetches latest job vacancies from Jobicy, Arbeitnow, and Campus feeds."""
    try:
        res = job_fetcher.sync_all_sources(db_conn, current_user_id=g.user['id'])
        msg = f"Opportunities synchronized successfully! Fetched {res['total_fetched']} vacancies ({res['new_inserted']} new added) across {res['sources_synced']} sources."
        if res['errors']:
            msg += f" Note: {len(res['errors'])} source(s) had warnings."
        flash(msg, "success")
    except Exception as e:
        flash(f"Error refreshing job feeds: {e}", "error")
    return redirect(custom_url_for('placement_hub', tab='explore'))

@app.route('/placement-hub/track-external/<int:company_id>', methods=['POST'])
@login_required
def track_external_opportunity(company_id):
    """
    Saves an external job vacancy to the student's tracker as 'Tracked — Not Yet Confirmed'
    without falsely claiming submission occurred.
    """
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    cursor.execute('SELECT * FROM companies WHERE id = ?', (company_id,))
    comp_row = cursor.fetchone()
    if not comp_row:
        conn.close()
        flash("Opportunity listing not found.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))
    comp = dict(comp_row)

    # Check for duplicate active tracking
    cursor.execute('''
        SELECT id, stage, app_ref FROM applications
        WHERE user_id = ? AND company_id = ? AND stage != 'Withdrawn'
    ''', (user_id, company_id))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        flash(f"You are already tracking '{comp['company_name']} — {comp['role']}' (Status: {normalize_stage(existing['stage'])}).", "info")
        return redirect(custom_url_for('placement_hub', tab='applications', highlight=existing['id']))

    today_str = date.today().isoformat()
    clean_code = re.sub(r'[^A-Z]', '', comp['company_name'].upper())[:4] or "EXT"
    import random
    app_ref = f"{clean_code}-2026-{random.randint(1000, 9999)}"

    cursor.execute('''
        INSERT INTO applications (
            user_id, company_id, company_name, role, stage, application_date,
            app_ref, phone, next_step, status_note, notes, is_external,
            status_source, auto_tracking_enabled, auto_tracking_note
        ) VALUES (?, ?, ?, ?, 'Tracked — Not Yet Confirmed', ?, ?, ?, ?, ?, ?, 1, 'Manual / Portal', 1, ?)
    ''', (
        user_id, company_id, comp['company_name'], comp['role'], today_str,
        app_ref, g.user.get('phone', '') if isinstance(g.user, dict) else '',
        "Complete application on company portal and click 'I've Applied' to record submission.",
        "External job opportunity saved to application tracker.",
        comp.get('notes') or "Saved from online opportunity listings.",
        f"Monitors recruitment status via official notifications from {comp['company_name']}."
    ))
    app_id = cursor.lastrowid

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by, source
        ) VALUES (?, ?, 'Tracked — Not Yet Confirmed', 'Opportunity tracked in personal placement workspace. Pending candidate submission on official portal.', ?, 'Student / Portal')
    ''', (app_id, user_id, g.user['name']))

    conn.commit()
    conn.close()

    flash(f"Opportunity for '{comp['company_name']}' is now saved to your tracker as 'Tracked — Not Yet Confirmed'. After completing the application on their portal, click 'I\'ve Applied'.", "success")
    return redirect(custom_url_for('placement_hub', tab='applications', highlight=app_id))

@app.route('/placement-hub/applications/<int:app_id>/confirm-applied', methods=['POST'])
@login_required
def confirm_external_application(app_id):
    """
    Candidate action: confirms that external submission was completed, transitioning
    from 'Tracked — Not Yet Confirmed' to 'Applied'.
    """
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    cursor.execute('SELECT * FROM applications WHERE id = ? AND user_id = ?', (app_id, user_id))
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        flash("Application record not found.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    applied_date = request.form.get('applied_date', '').strip() or date.today().isoformat()
    confirm_ref = request.form.get('confirmation_reference', '').strip()
    notes = request.form.get('notes', '').strip()

    now_ts = datetime.now().isoformat()
    note_text = f"Candidate confirmed official portal submission on {applied_date}."
    if confirm_ref:
        note_text += f" Portal Ref / Job ID: {confirm_ref}."
    if notes:
        note_text += f" Notes: {notes}."

    cursor.execute('''
        UPDATE applications 
        SET stage = 'Applied', application_date = ?, confirmation_reference = ?,
            status_note = ?, next_step = 'Application under preliminary screening by employer.',
            last_updated = ?
        WHERE id = ? AND user_id = ?
    ''', (applied_date, confirm_ref or app_row['confirmation_reference'], note_text, now_ts, app_id, user_id))

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by, source
        ) VALUES (?, ?, 'Applied', ?, ?, 'Student / Portal')
    ''', (app_id, user_id, note_text, g.user['name']))

    cursor.execute('''
        INSERT INTO notifications (user_id, title, message, link)
        VALUES (?, ?, ?, ?)
    ''', (
        user_id,
        f"Application Confirmed: {app_row['company_name']}",
        f"Application for {app_row['role']} at {app_row['company_name']} is now recorded as 'Applied'.",
        f"/placement-hub?tab=applications&highlight={app_id}"
    ))

    conn.commit()
    conn.close()

    flash(f"Application for '{app_row['company_name']} — {app_row['role']}' confirmed and marked as 'Applied'!", "success")
    return redirect(custom_url_for('placement_hub', tab='applications', highlight=app_id))

# -------------------------------------------------------------
# AUTOMATED RECRUITMENT STATUS INTEGRATIONS & SIMULATOR
# -------------------------------------------------------------

@app.route('/api/integrations/email/simulate', methods=['POST'])
@login_required
def simulate_recruitment_email_route():
    """
    Evaluates a recruitment communication (using pre-built templates or custom pasted email),
    detects status transitions, and automatically updates the candidate's application.
    """
    sender = request.form.get('sender', '').strip()
    subject = request.form.get('subject', '').strip()
    body = request.form.get('body', '').strip()
    template_id = request.form.get('template_id', '').strip()

    # If template chosen, populate from demo templates
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM applications WHERE user_id = ? AND stage != "Withdrawn"', (g.user['id'],))
    user_apps = [dict(r) for r in cursor.fetchall()]

    if template_id and not body:
        templates = email_recruiter.get_demo_email_templates(user_apps)
        matching_tmpl = next((t for t in templates if t['id'] == template_id), None)
        if matching_tmpl:
            sender = sender or matching_tmpl['sender']
            subject = subject or matching_tmpl['subject']
            body = matching_tmpl['body']

    if not body:
        conn.close()
        flash("Please provide the email content or choose a test template to simulate.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    res = email_recruiter.process_recruitment_email(
        conn, g.user['id'], sender, subject, body,
        message_id=None, email_date=date.today().isoformat()
    )
    conn.close()

    if res.get('success'):
        if res.get('skipped'):
            flash(res.get('message'), "info")
        else:
            flash(f"Verified status update! {res.get('message')}", "success")
    else:
        flash(f"Could not update status: {res.get('reason')}", "warning")

    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/api/integrations/email/connect', methods=['POST'])
@login_required
def connect_email_integration_route():
    """Saves user email synchronization configuration."""
    provider = request.form.get('provider', 'gmail').strip().lower()
    account_email = request.form.get('account_email', '').strip().lower()
    enable_sync = request.form.get('enable_sync') == '1'

    conn = db_conn()
    cursor = conn.cursor()
    now_ts = datetime.now().isoformat()

    cursor.execute('SELECT id FROM recruitment_integrations WHERE user_id = ? AND provider = ?', (g.user['id'], provider))
    existing = cursor.fetchone()
    if existing:
        cursor.execute('''
            UPDATE recruitment_integrations 
            SET account_email = ?, is_connected = ?, last_synced_at = ?
            WHERE id = ?
        ''', (account_email or g.user['email'], 1 if enable_sync else 0, now_ts, existing['id']))
    else:
        cursor.execute('''
            INSERT INTO recruitment_integrations (user_id, provider, account_email, is_connected, last_synced_at, last_sync_status)
            VALUES (?, ?, ?, ?, ?, 'Connected')
        ''', (g.user['id'], provider, account_email or g.user['email'], 1 if enable_sync else 0, now_ts))

    conn.commit()
    conn.close()

    flash(f"Recruitment email integration configured for {provider.capitalize()} ({account_email or g.user['email']}).", "success")
    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/api/webhooks/recruitment-update', methods=['POST'])
def recruitment_webhook():
    """
    Webhook endpoint for verified Applicant Tracking Systems (ATS) and recruitment platforms.
    Requires header X-Recruitment-Secret matching SECRET_KEY or configured webhook token.
    """
    auth_header = request.headers.get('X-Recruitment-Secret') or request.headers.get('Authorization')
    expected_secret = os.environ.get('WEBHOOK_SECRET', app.config['SECRET_KEY'])
    if not auth_header or expected_secret not in auth_header:
        return jsonify({'error': 'Unauthorized webhook request'}), 401

    payload = request.get_json(silent=True) or request.form.to_dict()
    student_email = payload.get('student_email', '').strip().lower()
    company_name = payload.get('company_name', '').strip()
    new_stage = payload.get('stage', '').strip()
    status_note = payload.get('notes', '').strip() or f"Official update from recruitment webhook."

    if not student_email or not company_name or not new_stage:
        return jsonify({'error': 'Missing required fields: student_email, company_name, stage'}), 400

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name FROM users WHERE LOWER(email) = ?', (student_email,))
    user_row = cursor.fetchone()
    if not user_row:
        conn.close()
        return jsonify({'error': 'Student not found'}), 404

    cursor.execute('''
        SELECT id, stage FROM applications 
        WHERE user_id = ? AND LOWER(company_name) LIKE ? AND stage != 'Withdrawn'
        ORDER BY id DESC LIMIT 1
    ''', (user_row['id'], f"%{company_name.lower()}%"))
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        return jsonify({'error': 'No matching active application found for student'}), 404

    norm_stage = normalize_stage(new_stage)
    now_ts = datetime.now().isoformat()

    cursor.execute('''
        UPDATE applications 
        SET stage = ?, status_note = ?, status_source = 'Recruitment Webhook', last_updated = ?
        WHERE id = ?
    ''', (norm_stage, status_note, now_ts, app_row['id']))

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by, source
        ) VALUES (?, ?, ?, ?, 'Recruitment Webhook API', 'Recruitment Webhook')
    ''', (app_row['id'], user_row['id'], norm_stage, status_note))

    cursor.execute('''
        INSERT INTO notifications (user_id, title, message, link)
        VALUES (?, ?, ?, ?)
    ''', (
        user_row['id'],
        f"Recruitment Webhook Update: {company_name}",
        f"Your application status for {company_name} was updated to '{norm_stage}' via recruitment platform integration.",
        "/placement-hub?tab=applications"
    ))

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'company': company_name, 'new_stage': norm_stage}), 200

# -------------------------------------------------------------
# RESUME VERSIONING & ATS MANAGEMENT ENDPOINTS
# -------------------------------------------------------------

@app.route('/resume/upload', methods=['POST'])
@login_required
def upload_resume_file():
    """
    Uploads a new resume file, creates a permanent version record in the database,
    saves the file safely in uploads/resumes/user_{id}/, and computes initial ATS score.
    """
    file = request.files.get('resume_file')
    target_role = request.form.get('target_role', 'Software Development Engineer (SDE)').strip()

    conn = db_conn()
    res = resume_manager.save_uploaded_resume(
        file, g.user['id'], g.user.get('student_id', '') if isinstance(g.user, dict) else '',
        app.config['UPLOAD_FOLDER'], conn, target_role=target_role
    )
    conn.close()

    if res['success']:
        flash(f"Resume '{res['filename']}' successfully saved as Version v{res['version']}! Initial ATS Score: {res['ats_score']}/100.", "success")
        return redirect(custom_url_for('resume_analysis', resume_id=res['resume_id']))
    else:
        flash(res['error'], "error")
        return redirect(custom_url_for('resume_analysis'))

@app.route('/resume/download/<int:resume_id>')
@login_required
def download_resume_file(resume_id):
    """Securely serves the student's saved resume file with ownership verification."""
    conn = db_conn()
    res_row = resume_manager.get_resume_by_id(resume_id, g.user['id'], conn)
    conn.close()

    if not res_row or not res_row.get('file_path') or not os.path.exists(res_row['file_path']):
        flash("Resume file not found or access denied.", "error")
        return redirect(custom_url_for('resume_analysis'))

    return send_file(
        res_row['file_path'],
        as_attachment=True,
        download_name=res_row['original_filename']
    )

@app.route('/resume/delete/<int:resume_id>', methods=['POST'])
@login_required
def delete_resume_file(resume_id):
    """Deletes a specific resume version with authorization verification."""
    conn = db_conn()
    success, msg = resume_manager.delete_resume_version(resume_id, g.user['id'], conn)
    conn.close()

    flash(msg, "success" if success else "error")
    return redirect(custom_url_for('resume_analysis'))

@app.route('/resume/analyze/<int:resume_id>', methods=['POST'])
@login_required
def reanalyze_resume_version(resume_id):
    """Re-analyzes an existing resume version against a selected target role."""
    target_role = request.form.get('target_role', 'Software Development Engineer (SDE)').strip()
    conn = db_conn()
    res_row = resume_manager.get_resume_by_id(resume_id, g.user['id'], conn)
    if not res_row:
        conn.close()
        flash("Resume record not found.", "error")
        return redirect(custom_url_for('resume_analysis'))

    extracted = res_row.get('extracted_text') or ''
    eval_res = resume_manager.evaluate_resume_ats(extracted, target_role)
    now_ts = datetime.now().isoformat()

    cursor = conn.cursor()
    cursor.execute('''
        UPDATE resumes 
        SET ats_score = ?, target_role = ?, analysis_summary = ?,
            matched_skills = ?, missing_skills = ?, suggestions = ?, analyzed_at = ?
        WHERE id = ? AND user_id = ?
    ''', (
        eval_res['ats_score'], target_role, eval_res['summary'],
        json.dumps(eval_res['matched_skills']),
        json.dumps(eval_res['missing_skills']),
        json.dumps(eval_res['suggestions']),
        now_ts, resume_id, g.user['id']
    ))
    conn.commit()
    conn.close()

    flash(f"ATS Evaluation updated for v{res_row['version']} against {target_role}: {eval_res['ats_score']}/100.", "success")
    return redirect(custom_url_for('resume_analysis', resume_id=resume_id))

@app.route('/coordinator/drives/toggle-status/<int:company_id>', methods=['POST'])
@login_required
@coordinator_required
def coordinator_toggle_drive_status(company_id):
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT company_name, status FROM companies WHERE id = ?', (company_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("Drive not found.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    new_stat = 'Closed' if row['status'] == 'Published' else 'Published'
    cursor.execute('UPDATE companies SET status = ? WHERE id = ?', (new_stat, company_id))
    conn.commit()
    conn.close()

    flash(f"Placement drive '{row['company_name']}' is now {new_stat}.", "info")
    return redirect(custom_url_for('placement_hub', tab='explore'))

@app.route('/api/notifications/mark-read', methods=['POST'])
@login_required
def mark_notifications_read():
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (g.user['id'],))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/coordinator-login')
def coordinator_login():
    """One-click evaluation access to Placement Coordinator role."""
    conn = db_conn()
    cursor = conn.cursor()
    coord_email = "coordinator@university.edu"
    cursor.execute('SELECT id FROM users WHERE LOWER(email) = ?', (coord_email,))
    row = cursor.fetchone()
    if not row:
        seed_demo_student(db_path=app.config.get('DATABASE'))
        cursor.execute('SELECT id FROM users WHERE LOWER(email) = ?', (coord_email,))
        row = cursor.fetchone()
    conn.close()
    if row:
        token = create_user_session(row['id'])
        flash("Signed in as Placement Coordinator: Dr. Vikram Malhotra. You have administrative authority to manage placement drives and officially update student application stages.", "success")
        return redirect(append_token_to_url(flask_url_for('placement_hub'), token))
    flash("Coordinator account could not be found.", "error")
    return redirect(custom_url_for('login'))

@app.route('/placement-hub/add-drive', methods=['POST'])
@app.route('/companies/add', methods=['POST'])
@login_required
def add_company_drive():
    company_name = request.form.get('company_name', '').strip()
    role = request.form.get('role', '').strip()
    package_ctc = request.form.get('package_ctc', '').strip()
    location = request.form.get('location', '').strip() or 'On-Campus'
    work_mode = request.form.get('work_mode', 'On-site').strip()
    job_type = request.form.get('job_type', 'Full-time').strip()
    drive_date = request.form.get('drive_date', '').strip() or None
    deadline = request.form.get('deadline', '').strip() or None
    eligibility = request.form.get('eligibility', '').strip()
    min_cgpa_raw = request.form.get('min_cgpa', '').strip()
    eligible_branches = request.form.get('eligible_branches', 'CSE, IT, ECE').strip()
    required_skills = request.form.get('required_skills', '').strip()
    selection_rounds = request.form.get('selection_rounds', '').strip()
    job_description = request.form.get('job_description', '').strip()
    company_website = request.form.get('company_website', '').strip()
    job_link = request.form.get('job_link', '').strip() or company_website
    notes = request.form.get('notes', '').strip()

    if not company_name or not role:
        flash("Company name and job role are required.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    min_cgpa = 0.0
    if min_cgpa_raw:
        try:
            min_cgpa = float(min_cgpa_raw)
        except ValueError:
            pass

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO companies (
            user_id, company_name, role, package_ctc, location, work_mode, job_type,
            drive_date, deadline, eligibility, min_cgpa, eligible_branches,
            required_skills, selection_rounds, job_description, company_website,
            job_link, status, is_demo, notes, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Published', 0, ?, ?)
    ''', (
        g.user['id'], company_name, role, package_ctc, location, work_mode, job_type,
        drive_date, deadline, eligibility, min_cgpa, eligible_branches,
        required_skills, selection_rounds, job_description, company_website,
        job_link, notes, g.user['id']
    ))
    company_id = cursor.lastrowid

    create_app = request.form.get('create_application') == '1'
    if create_app:
        clean_code = re.sub(r'[^A-Z]', '', company_name.upper())[:4] or "APP"
        import random
        app_ref = f"{clean_code}-2026-{random.randint(1000, 9999)}"
        cursor.execute('''
            INSERT INTO applications (user_id, company_id, company_name, role, stage, application_date, app_ref, notes)
            VALUES (?, ?, ?, ?, 'Applied', ?, ?, ?)
        ''', (g.user['id'], company_id, company_name, role, date.today().isoformat(), app_ref, "Tracked automatically when creating placement drive."))

    conn.commit()
    conn.close()

    flash(f"Placement drive for '{company_name} — {role}' published successfully.", "success")
    return redirect(custom_url_for('placement_hub', tab='explore'))

@app.route('/companies/<int:company_id>/edit', methods=['POST'])
@login_required
def edit_company(company_id):
    company_name = request.form.get('company_name', '').strip()
    role = request.form.get('role', '').strip()
    package_ctc = request.form.get('package_ctc', '').strip()
    location = request.form.get('location', '').strip() or 'On-Campus'
    work_mode = request.form.get('work_mode', 'On-site').strip()
    job_type = request.form.get('job_type', 'Full-time').strip()
    drive_date = request.form.get('drive_date', '').strip() or None
    deadline = request.form.get('deadline', '').strip() or None
    eligibility = request.form.get('eligibility', '').strip()
    required_skills = request.form.get('required_skills', '').strip()
    selection_rounds = request.form.get('selection_rounds', '').strip()
    job_description = request.form.get('job_description', '').strip()
    job_link = request.form.get('job_link', '').strip()
    notes = request.form.get('notes', '').strip()

    if not company_name or not role:
        flash("Company name and job role are required.", "error")
        return redirect(custom_url_for('placement_hub', tab='explore'))

    conn = db_conn()
    cursor = conn.cursor()
    # Coordinators can edit any drive; users can edit their own
    is_coord = bool(g.user and g.user['role'] in ('admin', 'coordinator'))
    if is_coord:
        cursor.execute('''
            UPDATE companies
            SET company_name = ?, role = ?, package_ctc = ?, location = ?, work_mode = ?, job_type = ?, drive_date = ?, deadline = ?, eligibility = ?, required_skills = ?, selection_rounds = ?, job_description = ?, job_link = ?, notes = ?
            WHERE id = ?
        ''', (company_name, role, package_ctc, location, work_mode, job_type, drive_date, deadline, eligibility, required_skills, selection_rounds, job_description, job_link, notes, company_id))
    else:
        cursor.execute('''
            UPDATE companies
            SET company_name = ?, role = ?, package_ctc = ?, location = ?, work_mode = ?, job_type = ?, drive_date = ?, deadline = ?, eligibility = ?, required_skills = ?, selection_rounds = ?, job_description = ?, job_link = ?, notes = ?
            WHERE id = ? AND user_id = ?
        ''', (company_name, role, package_ctc, location, work_mode, job_type, drive_date, deadline, eligibility, required_skills, selection_rounds, job_description, job_link, notes, company_id, g.user['id']))
    updated = cursor.rowcount > 0

    if updated:
        cursor.execute('''
            UPDATE applications
            SET company_name = ?, role = ?
            WHERE company_id = ?
        ''', (company_name, role, company_id))

    conn.commit()
    conn.close()

    if updated:
        flash(f"Company drive for '{company_name}' updated successfully.", "success")
    else:
        flash("Company drive not found or access denied.", "error")
    return redirect(custom_url_for('placement_hub', tab='explore'))

@app.route('/companies/<int:company_id>/delete', methods=['POST'])
@login_required
def delete_company(company_id):
    conn = db_conn()
    cursor = conn.cursor()
    is_coord = bool(g.user and g.user['role'] in ('admin', 'coordinator'))
    if is_coord:
        cursor.execute('DELETE FROM companies WHERE id = ?', (company_id,))
    else:
        cursor.execute('DELETE FROM companies WHERE id = ? AND user_id = ?', (company_id, g.user['id']))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    if deleted:
        flash("Company drive removed from directory.", "info")
    else:
        flash("Company drive not found or access denied.", "error")
    return redirect(custom_url_for('placement_hub', tab='explore'))

@app.route('/applications/add', methods=['POST'])
@login_required
def add_application():
    company_id_raw = request.form.get('company_id', '').strip()
    company_name = request.form.get('company_name', '').strip()
    role = request.form.get('role', '').strip()
    stage_raw = request.form.get('stage', 'Applied').strip()
    app_date = request.form.get('application_date', '').strip() or date.today().isoformat()
    notes = request.form.get('notes', '').strip()

    stage = normalize_stage(stage_raw)
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    company_id = int(company_id_raw) if company_id_raw and company_id_raw.isdigit() else None
    if company_id:
        cursor.execute('SELECT id, company_name, role FROM companies WHERE id = ?', (company_id,))
        comp = cursor.fetchone()
        if comp:
            if not company_name:
                company_name = comp['company_name']
            if not role:
                role = comp['role']
            cursor.execute('SELECT id, stage FROM applications WHERE user_id = ? AND company_id = ?', (user_id, company_id))
            if cursor.fetchone():
                conn.close()
                flash(f"'{company_name}' is already tracked in your applications.", "warning")
                return redirect(custom_url_for('placement_hub', tab='applications'))
        else:
            company_id = None

    if not company_name or not role:
        conn.close()
        flash("Company name and job role are required.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    clean_code = re.sub(r'[^A-Z]', '', company_name.upper())[:4] or "APP"
    import random
    app_ref = f"{clean_code}-2026-{random.randint(1000, 9999)}"

    cursor.execute('''
        INSERT INTO applications (user_id, company_id, company_name, role, stage, application_date, app_ref, next_step, status_note, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, company_id, company_name, role, stage, app_date, app_ref, f"Status recorded as {stage}.", f"Application recorded ({stage}).", notes))
    new_app_id = cursor.lastrowid

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by
        ) VALUES (?, ?, ?, ?, ?)
    ''', (new_app_id, user_id, stage, f"Application recorded ({stage}).", g.user['name']))

    conn.commit()
    conn.close()

    flash(f"Application for '{company_name} — {role}' added ({stage}).", "success")
    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/applications/<int:app_id>/edit', methods=['POST'])
@app.route('/placement-hub/applications/<int:app_id>/edit', methods=['POST'])
@login_required
def edit_application(app_id):
    company_name = request.form.get('company_name', '').strip()
    role = request.form.get('role', '').strip()
    stage_raw = request.form.get('stage', 'Applied').strip()
    app_date = request.form.get('application_date', '').strip() or date.today().isoformat()
    notes = request.form.get('notes', '').strip()

    stage = normalize_stage(stage_raw)
    if not company_name or not role:
        flash("Company name and job role cannot be empty.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT stage, company_name FROM applications WHERE id = ? AND user_id = ?', (app_id, g.user['id']))
    prev_row = cursor.fetchone()
    if not prev_row:
        conn.close()
        flash("Application record not found or access denied.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    prev_stage = normalize_stage(prev_row['stage'])
    now_ts = datetime.now().isoformat()

    cursor.execute('''
        UPDATE applications
        SET company_name = ?, role = ?, stage = ?, application_date = ?, notes = ?, last_updated = ?
        WHERE id = ? AND user_id = ?
    ''', (company_name, role, stage, app_date, notes, now_ts, app_id, g.user['id']))

    if prev_stage != stage:
        cursor.execute('''
            INSERT INTO application_status_history (
                application_id, user_id, stage, status_note, updated_by
            ) VALUES (?, ?, ?, ?, ?)
        ''', (app_id, g.user['id'], stage, f"Status updated: '{prev_stage}' → '{stage}'.", g.user['name']))

    conn.commit()
    conn.close()

    if prev_stage != stage:
        flash(f"Status changed for {company_name}: '{prev_stage}' → '{stage}'. Application progress updated.", "success")
    else:
        flash(f"Updated application details and preparation notes for {company_name}.", "success")
    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/applications/<int:app_id>/stage', methods=['POST'])
@app.route('/placement-hub/applications/<int:app_id>/stage', methods=['POST'])
@login_required
def update_application_stage(app_id):
    stage_raw = request.form.get('stage', '').strip()
    notes = request.form.get('notes')
    new_stage = normalize_stage(stage_raw)

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT company_name, stage FROM applications WHERE id = ? AND user_id = ?', (app_id, g.user['id']))
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        flash("Application record not found.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    old_stage = normalize_stage(app_row['stage'])
    now_ts = datetime.now().isoformat()

    if notes is not None:
        cursor.execute('''
            UPDATE applications
            SET stage = ?, notes = ?, last_updated = ?
            WHERE id = ? AND user_id = ?
        ''', (new_stage, notes.strip(), now_ts, app_id, g.user['id']))
    else:
        cursor.execute('''
            UPDATE applications
            SET stage = ?, last_updated = ?
            WHERE id = ? AND user_id = ?
        ''', (new_stage, now_ts, app_id, g.user['id']))

    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, updated_by
        ) VALUES (?, ?, ?, ?, ?)
    ''', (app_id, g.user['id'], new_stage, (notes or f"Application progress moved from '{old_stage}' to '{new_stage}'."), g.user['name']))

    conn.commit()
    conn.close()

    flash(f"Application status for {app_row['company_name']} updated to '{new_stage}' (was '{old_stage}').", "success")
    return redirect(custom_url_for('placement_hub', tab='applications'))

@app.route('/api/applications/<int:app_id>/stage', methods=['POST'])
@login_required
def api_update_application_stage(app_id):
    data = request.get_json(silent=True) or request.form
    new_stage = normalize_stage(data.get('stage', ''))

    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE applications
        SET stage = ?, last_updated = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    ''', (new_stage, app_id, g.user['id']))
    updated = cursor.rowcount > 0

    if updated:
        cursor.execute('''
            INSERT INTO application_status_history (
                application_id, user_id, stage, status_note, updated_by
            ) VALUES (?, ?, ?, 'Status updated via API.', ?)
        ''', (app_id, g.user['id'], new_stage, g.user['name']))

    conn.commit()
    conn.close()

    if not updated:
        return jsonify({'success': False, 'error': 'Application not found'}), 404

    return jsonify({'success': True, 'new_stage': new_stage})

@app.route('/applications/<int:app_id>/delete', methods=['POST'])
@app.route('/placement-hub/applications/<int:app_id>/delete', methods=['POST'])
@login_required
def delete_application(app_id):
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT company_name FROM applications WHERE id = ? AND user_id = ?', (app_id, g.user['id']))
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        flash("Application record not found.", "error")
        return redirect(custom_url_for('placement_hub', tab='applications'))

    c_name = app_row['company_name']
    cursor.execute('DELETE FROM applications WHERE id = ? AND user_id = ?', (app_id, g.user['id']))
    conn.commit()
    conn.close()

    flash(f"Tracked application for '{c_name}' removed successfully.", "info")
    return redirect(custom_url_for('placement_hub', tab='applications'))

# -------------------------------------------------------------
# RESUME ANALYSIS ROUTE (Sidebar Slot #4)
# -------------------------------------------------------------

@app.route('/resume-analysis', methods=['GET', 'POST'])
@login_required
def resume_analysis():
    conn = db_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM profiles WHERE user_id = ?', (g.user['id'],))
    profile = cursor.fetchone()

    # Load persistent resume version history for this student
    user_resumes = resume_manager.get_user_resumes(g.user['id'], conn)

    results = None
    resume_text = ""
    active_resume = None

    selected_resume_id = request.args.get('resume_id', type=int)
    if selected_resume_id:
        active_resume = resume_manager.get_resume_by_id(selected_resume_id, g.user['id'], conn)

    CORE_CS = ["data structures", "algorithms", "dsa", "oop", "dbms", "sql", "operating systems", "computer networks", "system design"]
    LANGUAGES = ["python", "java", "c++", "c", "javascript", "typescript", "golang", "c#"]
    FRAMEWORKS_DEV = ["react", "node.js", "express", "flask", "django", "spring boot", "rest api", "html", "css", "tailwind"]
    TOOLS_DEVOPS = ["git", "github", "docker", "linux", "ci/cd", "aws", "azure", "gcp", "postman", "agile"]

    if request.method == 'POST':
        resume_text = request.form.get('resume_text', '').strip()
        target_role = request.form.get('target_role', 'Software Development Engineer (SDE)').strip()

        if not resume_text:
            if profile and profile['technical_skills']:
                resume_text = f"Student Profile Resume Summary\nName: {g.user['name']}\nSkills: {profile['technical_skills']}\nInterests: {profile['areas_of_interest'] or ''}\nBio: {profile['bio'] or ''}"
            else:
                conn.close()
                flash("Please paste your resume text to perform ATS evaluation.", "error")
                return render_template('resume_analysis.html', profile=profile, results=None, resume_text='', user_resumes=user_resumes, active_resume=None)

        text_lower = resume_text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        word_count = len(words)

        matched_core = [k.title() for k in CORE_CS if k in text_lower]
        matched_lang = [k.title() for k in LANGUAGES if k in text_lower]
        matched_fw = [k.title() for k in FRAMEWORKS_DEV if k in text_lower]
        matched_tools = [k.title() for k in TOOLS_DEVOPS if k in text_lower]

        total_matched = len(matched_core) + len(matched_lang) + len(matched_fw) + len(matched_tools)

        has_education = any(w in text_lower for w in ["education", "b.tech", "degree", "university", "college", "cgpa", "btech"])
        has_projects = any(w in text_lower for w in ["project", "projects", "developed", "built", "implemented"])
        has_skills = any(w in text_lower for w in ["skills", "technical skills", "technologies", "proficiencies", "tools"])
        has_experience = any(w in text_lower for w in ["experience", "intern", "internship", "work", "responsibilities", "leadership"])

        keyword_score = min(50, int((total_matched / 14.0) * 50))
        structure_score = (int(has_education) + int(has_projects) + int(has_skills) + int(has_experience)) * 7.5
        if 250 <= word_count <= 950:
            length_score = 20
        elif 150 <= word_count < 250:
            length_score = 12
        else:
            length_score = 8

        ats_score = int(min(100, keyword_score + structure_score + length_score))

        missing_critical = []
        for cs_kw in ["dsa", "algorithms", "dbms", "sql", "git", "system design"]:
            if cs_kw not in text_lower:
                missing_critical.append(cs_kw.upper())

        results = {
            'ats_score': ats_score,
            'word_count': word_count,
            'target_role': target_role,
            'matched_core': matched_core,
            'matched_lang': matched_lang,
            'matched_fw': matched_fw,
            'matched_tools': matched_tools,
            'missing_critical': missing_critical,
            'has_education': has_education,
            'has_projects': has_projects,
            'has_skills': has_skills,
            'has_experience': has_experience
        }
    elif active_resume:
        # Load analysis from active resume version
        resume_text = active_resume.get('extracted_text') or ''
        text_lower = resume_text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        word_count = len(words)
        matched_core = [k.title() for k in CORE_CS if k in text_lower]
        matched_lang = [k.title() for k in LANGUAGES if k in text_lower]
        matched_fw = [k.title() for k in FRAMEWORKS_DEV if k in text_lower]
        matched_tools = [k.title() for k in TOOLS_DEVOPS if k in text_lower]
        has_education = any(w in text_lower for w in ["education", "b.tech", "degree", "university", "college", "cgpa", "btech"])
        has_projects = any(w in text_lower for w in ["project", "projects", "developed", "built", "implemented"])
        has_skills = any(w in text_lower for w in ["skills", "technical skills", "technologies", "proficiencies", "tools"])
        has_experience = any(w in text_lower for w in ["experience", "intern", "internship", "work", "responsibilities", "leadership"])
        missing_critical = []
        for cs_kw in ["dsa", "algorithms", "dbms", "sql", "git", "system design"]:
            if cs_kw not in text_lower:
                missing_critical.append(cs_kw.upper())

        results = {
            'ats_score': active_resume.get('ats_score') or 60,
            'word_count': word_count,
            'target_role': active_resume.get('target_role') or 'Software Development Engineer (SDE)',
            'matched_core': matched_core,
            'matched_lang': matched_lang,
            'matched_fw': matched_fw,
            'matched_tools': matched_tools,
            'missing_critical': missing_critical,
            'has_education': has_education,
            'has_projects': has_projects,
            'has_skills': has_skills,
            'has_experience': has_experience,
            'suggestions': active_resume.get('suggestions_list', []),
            'analysis_summary': active_resume.get('analysis_summary', '')
        }
    elif user_resumes and len(user_resumes) > 0:
        active_resume = user_resumes[0]
        resume_text = active_resume.get('extracted_text') or ''
        text_lower = resume_text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        word_count = len(words)
        matched_core = [k.title() for k in CORE_CS if k in text_lower]
        matched_lang = [k.title() for k in LANGUAGES if k in text_lower]
        matched_fw = [k.title() for k in FRAMEWORKS_DEV if k in text_lower]
        matched_tools = [k.title() for k in TOOLS_DEVOPS if k in text_lower]
        has_education = any(w in text_lower for w in ["education", "b.tech", "degree", "university", "college", "cgpa", "btech"])
        has_projects = any(w in text_lower for w in ["project", "projects", "developed", "built", "implemented"])
        has_skills = any(w in text_lower for w in ["skills", "technical skills", "technologies", "proficiencies", "tools"])
        has_experience = any(w in text_lower for w in ["experience", "intern", "internship", "work", "responsibilities", "leadership"])
        missing_critical = []
        for cs_kw in ["dsa", "algorithms", "dbms", "sql", "git", "system design"]:
            if cs_kw not in text_lower:
                missing_critical.append(cs_kw.upper())

        results = {
            'ats_score': active_resume.get('ats_score') or 60,
            'word_count': word_count,
            'target_role': active_resume.get('target_role') or 'Software Development Engineer (SDE)',
            'matched_core': matched_core,
            'matched_lang': matched_lang,
            'matched_fw': matched_fw,
            'matched_tools': matched_tools,
            'missing_critical': missing_critical,
            'has_education': has_education,
            'has_projects': has_projects,
            'has_skills': has_skills,
            'has_experience': has_experience,
            'suggestions': active_resume.get('suggestions_list', []),
            'analysis_summary': active_resume.get('analysis_summary', '')
        }

    conn.close()

    return render_template(
        'resume_analysis.html',
        profile=profile,
        results=results,
        resume_text=resume_text,
        user_resumes=user_resumes,
        active_resume=active_resume
    )

# -------------------------------------------------------------
# JSON API FOR REAL-TIME CHARTS & ANALYTICS
# -------------------------------------------------------------

@app.route('/api/analytics/charts')
@login_required
def analytics_charts():
    conn = db_conn()
    cursor = conn.cursor()
    user_id = g.user['id']

    # 1. Timeline trend of practice tests
    cursor.execute('''
        SELECT id, title, category, test_date, score_obtained, max_score, percentage
        FROM practice_tests
        WHERE user_id = ?
        ORDER BY test_date ASC, id ASC
    ''', (user_id,))
    tests = [dict(r) for r in cursor.fetchall()]

    # 2. Topic performance averages
    cursor.execute('''
        SELECT category, AVG(percentage) as avg_score, COUNT(*) as count
        FROM practice_tests
        WHERE user_id = ?
        GROUP BY category
        ORDER BY avg_score DESC
    ''', (user_id,))
    topics = [{
        'category': r['category'],
        'avg_score': round(r['avg_score'], 1),
        'count': r['count']
    } for r in cursor.fetchall()]

    # 3. Preparation category task completion
    cursor.execute('''
        SELECT 
            category,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM preparation_tasks
        WHERE user_id = ?
        GROUP BY category
    ''', (user_id,))
    task_cats = [{
        'category': r['category'],
        'total': r['total'],
        'completed': r['completed'] or 0,
        'percentage': round(((r['completed'] or 0) / r['total'] * 100) if r['total'] > 0 else 0)
    } for r in cursor.fetchall()]

    # 4. Application stage breakdown
    cursor.execute('''
        SELECT stage, COUNT(*) as count
        FROM applications
        WHERE user_id = ?
        GROUP BY stage
    ''', (user_id,))
    app_stages = {s: 0 for s in APPLICATION_STAGES}
    for r in cursor.fetchall():
        st = r['stage']
        if st in app_stages:
            app_stages[st] += r['count']
        else:
            ns = normalize_stage(st)
            app_stages[ns] = app_stages.get(ns, 0) + r['count']

    conn.close()

    return jsonify({
        'tests_timeline': tests,
        'topic_performance': topics,
        'task_categories': task_cats,
        'application_stages': app_stages
    })

# Initialize DB on startup
try:
    init_db()
except Exception as e:
    app.logger.warning(f"Database initialization notice on startup: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1')
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
