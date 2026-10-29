import sqlite3
import os
import sys

_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor_packages')
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import shutil

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
DEFAULT_DB_PATH = os.path.join(DB_DIR, 'placement.db')

def get_db_path(custom_path=None):
    if custom_path:
        return custom_path
    env_path = os.environ.get('DATABASE_PATH')
    if env_path:
        return env_path
    if os.environ.get('VERCEL') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
        tmp_db = '/tmp/placement.db'
        if not os.path.exists(tmp_db):
            bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'placement.db')
            if os.path.exists(bundled):
                try:
                    shutil.copyfile(bundled, tmp_db)
                except Exception:
                    pass
        return tmp_db
    return DEFAULT_DB_PATH

def ensure_db_dir(db_path=None):
    target = get_db_path(db_path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass

def get_db_connection(db_path=None):
    target = get_db_path(db_path)
    ensure_db_dir(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def seed_shared_placement_drives(cursor, user_id=1):
    """
    Ensures shared published placement opportunities exist in the system.
    These opportunities are visible to all registered students in Explore Opportunities.
    """
    cursor.execute("SELECT COUNT(*) as cnt FROM companies WHERE status = 'Published'")
    if cursor.fetchone()['cnt'] == 0:
        today = date.today()
        # Seed 4 realistic CSE campus recruitment drives marked as published demo drives
        shared_drives = [
            (
                user_id, "Microsoft IDC", "Software Development Engineer", "44.0 LPA",
                "Hyderabad, India", "Hybrid", "Full-time",
                (today + timedelta(days=18)).isoformat(), (today + timedelta(days=10)).isoformat(),
                "CGPA >= 8.0, B.Tech/M.Tech (CSE, IT, ECE), 2026 batch, No active backlogs",
                8.0, "CSE, IT, ECE", "B.Tech, M.Tech", "2026",
                "Data Structures, Algorithms, C++, Java, Operating Systems, Cloud Basics",
                "Online Assessment (OA) + 3 Technical Rounds + 1 Managerial/HR",
                "Design and implement scalable microservices, distributed systems, and cloud infrastructure for Azure and Microsoft 365.",
                "https://careers.microsoft.com", "https://careers.microsoft.com",
                "Published", 1, "On-campus Day 1 recruitment drive. 3 technical rounds + 1 managerial fitment round."
            ),
            (
                user_id, "Goldman Sachs", "Analyst - Technology & Engineering", "24.0 LPA",
                "Bengaluru, India", "On-site", "Full-time",
                (today + timedelta(days=24)).isoformat(), (today + timedelta(days=14)).isoformat(),
                "CGPA >= 7.5, B.Tech (All Engineering Branches), 2026 batch",
                7.5, "CSE, IT, ECE, EE", "B.Tech", "2026",
                "Algorithms, Applied Mathematics, Database Systems, SQL, Java/Python",
                "HackerRank OA + 2 Technical Interviews + Fitment Round",
                "Build high-frequency electronic trading systems, real-time risk engines, and financial analytics platforms.",
                "https://goldmansachs.com/careers", "https://goldmansachs.com/careers",
                "Published", 1, "Written assessment on HackerRank followed by 2 technical interview loops."
            ),
            (
                user_id, "Amazon Development Centre", "SDE-1", "32.0 LPA",
                "Bengaluru, India", "On-site", "Full-time",
                (today + timedelta(days=30)).isoformat(), (today + timedelta(days=18)).isoformat(),
                "CGPA >= 7.0, B.Tech/M.Tech (CSE, IT), 2026 batch",
                7.0, "CSE, IT", "B.Tech, M.Tech", "2026",
                "Data Structures, Object-Oriented Design, Java/C++, Leadership Principles",
                "Online Assessment (Debugging + Coding + Work Style) + 3 Technical Loops",
                "Work on customer-facing retail systems, distributed transaction systems, and cloud-scale services.",
                "https://amazon.jobs", "https://amazon.jobs",
                "Published", 1, "Online assessment includes code debugging, algorithmic problem solving, and work simulation."
            ),
            (
                user_id, "Oracle Cloud Infrastructure", "Member of Technical Staff", "18.5 LPA",
                "Pune, India", "Hybrid", "Full-time",
                (today + timedelta(days=14)).isoformat(), (today + timedelta(days=6)).isoformat(),
                "CGPA >= 7.5, B.Tech CSE/IT, 2026 batch",
                7.5, "CSE, IT", "B.Tech", "2026",
                "Distributed Systems, Java/Go, Linux Concurrency, Databases",
                "Coding Test + 2 Technical Panels + System Architecture",
                "Build high-performance compute and virtual networking infrastructure for Oracle Cloud.",
                "https://oracle.com/careers", "https://oracle.com/careers",
                "Published", 1, "Campus drive confirmed. Shortlisted candidates will be invited for live technical interviews."
            )
        ]

        for (u_id, c_name, role, ctc, loc, w_mode, j_type, d_date, d_line, elig, min_cgpa, branches, courses, grad_yrs, skills, rounds, desc, web, link, status, is_demo, notes) in shared_drives:
            cursor.execute('''
                INSERT INTO companies (
                    user_id, company_name, role, package_ctc, location, work_mode, job_type,
                    drive_date, deadline, eligibility, min_cgpa, eligible_branches, eligible_courses, graduation_years,
                    required_skills, selection_rounds, job_description, company_website, job_link, status, is_demo, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                u_id, c_name, role, ctc, loc, w_mode, j_type,
                d_date, d_line, elig, min_cgpa, branches, courses, grad_yrs,
                skills, rounds, desc, web, link, status, is_demo, notes
            ))

def init_db(db_path=None):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Users table (supports email, student_id, and name lookup)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT UNIQUE,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Safe migration for existing databases that were created before student_id was added
    cursor.execute("PRAGMA table_info(users)")
    user_cols = [col['name'] for col in cursor.fetchall()]
    if 'student_id' not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN student_id TEXT")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_student_id ON users (student_id) WHERE student_id IS NOT NULL")
    if 'role' not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'student'")
    if 'phone' not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT")

    # Active user sessions table (for reliable session persistence and server-side logout invalidation)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Student profiles table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        college TEXT,
        degree TEXT,
        branch TEXT,
        graduation_year INTEGER,
        cgpa REAL,
        technical_skills TEXT,
        areas_of_interest TEXT,
        bio TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Preparation tasks table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS preparation_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'Medium',
        status TEXT NOT NULL DEFAULT 'pending',
        due_date DATE,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Practice test records table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS practice_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        topic TEXT,
        test_date DATE NOT NULL,
        score_obtained REAL NOT NULL,
        max_score REAL NOT NULL,
        percentage REAL NOT NULL,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Safe migration for practice_tests topic column
    cursor.execute("PRAGMA table_info(practice_tests)")
    pt_cols = [col['name'] for col in cursor.fetchall()]
    if 'topic' not in pt_cols:
        cursor.execute("ALTER TABLE practice_tests ADD COLUMN topic TEXT")

    # Companies and placement drives table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        company_name TEXT NOT NULL,
        role TEXT NOT NULL,
        package_ctc TEXT,
        location TEXT,
        job_type TEXT DEFAULT 'Full-time',
        drive_date DATE,
        deadline DATE,
        eligibility TEXT,
        required_skills TEXT,
        selection_rounds TEXT,
        job_link TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Safe migration for companies extra columns
    cursor.execute("PRAGMA table_info(companies)")
    comp_cols = [col['name'] for col in cursor.fetchall()]
    if 'location' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN location TEXT")
    if 'job_type' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN job_type TEXT DEFAULT 'Full-time'")
    if 'selection_rounds' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN selection_rounds TEXT")
    if 'status' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN status TEXT DEFAULT 'Published'")
    if 'work_mode' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN work_mode TEXT DEFAULT 'On-site'")
    if 'min_cgpa' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN min_cgpa REAL DEFAULT 0.0")
    if 'eligible_branches' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN eligible_branches TEXT DEFAULT 'CSE, IT, ECE'")
    if 'eligible_courses' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN eligible_courses TEXT DEFAULT 'B.Tech'")
    if 'graduation_years' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN graduation_years TEXT DEFAULT '2026'")
    if 'job_description' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN job_description TEXT")
    if 'company_website' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN company_website TEXT")
    if 'is_demo' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN is_demo INTEGER DEFAULT 0")
    if 'created_by' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN created_by INTEGER")
    if 'source_name' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN source_name TEXT DEFAULT 'Campus Placement Cell'")
    if 'source_job_id' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN source_job_id TEXT")
    if 'external_url' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN external_url TEXT")
    if 'posted_date' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN posted_date TEXT")
    if 'last_verified_at' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN last_verified_at TIMESTAMP")
    if 'is_external' not in comp_cols:
        cursor.execute("ALTER TABLE companies ADD COLUMN is_external INTEGER DEFAULT 0")

    # Placement applications tracking table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        company_id INTEGER,
        company_name TEXT NOT NULL,
        role TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT 'Applied',
        application_date DATE,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        notes TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE SET NULL
    )
    ''')

    # Safe migration for applications extra columns
    cursor.execute("PRAGMA table_info(applications)")
    app_cols = [col['name'] for col in cursor.fetchall()]
    if 'app_ref' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN app_ref TEXT")
    if 'phone' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN phone TEXT")
    if 'next_step' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN next_step TEXT")
    if 'interview_details' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN interview_details TEXT")
    if 'assessment_details' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN assessment_details TEXT")
    if 'status_note' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN status_note TEXT")
    if 'withdrawn_at' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN withdrawn_at TIMESTAMP")
    if 'is_external' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN is_external INTEGER DEFAULT 0")
    if 'status_source' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN status_source TEXT DEFAULT 'Campus Placement Cell'")
    if 'confirmation_reference' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN confirmation_reference TEXT")
    if 'auto_tracking_enabled' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN auto_tracking_enabled INTEGER DEFAULT 1")
    if 'auto_tracking_note' not in app_cols:
        cursor.execute("ALTER TABLE applications ADD COLUMN auto_tracking_note TEXT")

    # Application Status History table (Audit log of all status transitions & notes)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS application_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        stage TEXT NOT NULL,
        status_note TEXT,
        interview_date TEXT,
        interview_venue TEXT,
        updated_by TEXT NOT NULL DEFAULT 'Placement Coordinator',
        source TEXT DEFAULT 'Placement Coordinator',
        external_message_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')
    cursor.execute("PRAGMA table_info(application_status_history)")
    ash_cols = [col['name'] for col in cursor.fetchall()]
    if 'source' not in ash_cols:
        cursor.execute("ALTER TABLE application_status_history ADD COLUMN source TEXT DEFAULT 'Placement Coordinator'")
    if 'external_message_id' not in ash_cols:
        cursor.execute("ALTER TABLE application_status_history ADD COLUMN external_message_id TEXT")

    # Resumes table (Multi-version resume storage & ATS analysis history)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        student_id TEXT,
        original_filename TEXT NOT NULL,
        stored_filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        file_hash TEXT,
        extracted_text TEXT,
        ats_score INTEGER,
        target_role TEXT,
        analysis_summary TEXT,
        matched_skills TEXT,
        missing_skills TEXT,
        suggestions TEXT,
        analyzed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Job Sources Configuration & Monitoring table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS job_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'api',
        feed_url TEXT,
        is_enabled INTEGER DEFAULT 1,
        last_synced_at TIMESTAMP,
        sync_status TEXT DEFAULT 'Pending',
        items_count INTEGER DEFAULT 0,
        last_error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Seed default job sources if empty
    cursor.execute("SELECT COUNT(*) as cnt FROM job_sources")
    if (cursor.fetchone()['cnt'] or 0) == 0:
        cursor.execute('''
            INSERT INTO job_sources (source_key, display_name, source_type, feed_url, is_enabled, sync_status)
            VALUES 
            ('jobicy', 'Jobicy Remote Tech Feed', 'api', 'https://jobicy.com/api/v2/remote-jobs?count=20&tag=software', 1, 'Active'),
            ('arbeitnow', 'Arbeitnow Tech Jobs API', 'api', 'https://www.arbeitnow.com/api/job-board-api', 1, 'Active'),
            ('campus_curated', 'Curated Campus Fresher Drives', 'feed', 'local://campus-curated-feed', 1, 'Active')
        ''')

    # Recruitment Integrations table (Email & Webhook status listeners)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS recruitment_integrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        account_email TEXT,
        is_connected INTEGER DEFAULT 0,
        last_synced_at TIMESTAMP,
        last_sync_status TEXT,
        config_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # In-App Notifications table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        link TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    ''')

    # Normalize application stage values
    cursor.execute("UPDATE applications SET stage = 'Assessment' WHERE stage IN ('Assessment/Test', 'Test')")
    cursor.execute("UPDATE applications SET stage = 'Rejected' WHERE stage = 'Not Selected'")

    # Create Indexes for fast lookups
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user ON preparation_tasks (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tests_user ON practice_tests (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_companies_user ON companies (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_companies_status ON companies (status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_apps_user ON applications (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_apps_company ON applications (company_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status_hist_app ON application_status_history (application_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_resumes_user ON resumes (user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_sources_key ON job_sources (source_key)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_companies_source ON companies (source_name, source_job_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_integrations_user ON recruitment_integrations (user_id)')

    conn.commit()
    conn.close()

def seed_demo_student(email="demo.student@university.edu", db_path=None, force_reset=False):
    """
    Idempotently seeds or repairs the Demo Student account with sample placement data.
    Running this repeatedly will NOT create duplicate users or duplicate records.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    demo_email = email.strip().lower()
    demo_student_id = "CSE2026001"
    demo_password = "Placement@2026"

    cursor.execute('SELECT * FROM users WHERE LOWER(email) = ? OR LOWER(student_id) = ?', (demo_email, demo_student_id.lower()))
    existing = cursor.fetchone()

    if existing:
        user_id = existing['id']
        # Ensure password hash is valid and student_id is set
        if not existing['password_hash'] or not check_password_hash(existing['password_hash'], demo_password):
            cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(demo_password), user_id))
        if not existing['student_id']:
            cursor.execute('UPDATE users SET student_id = ? WHERE id = ?', (demo_student_id, user_id))
        if force_reset:
            cursor.execute('UPDATE users SET name = ? WHERE id = ?', ("Aarav Sharma", user_id))
        cursor.execute("UPDATE users SET role = 'student' WHERE id = ? AND (role IS NULL OR role = '')", (user_id,))
    else:
        pw_hash = generate_password_hash(demo_password)
        cursor.execute(
            'INSERT INTO users (student_id, name, email, password_hash, role) VALUES (?, ?, ?, ?, ?)',
            (demo_student_id, "Aarav Sharma", demo_email, pw_hash, 'student')
        )
        user_id = cursor.lastrowid

    # Seed Placement Coordinator Account if not present
    coord_email = "coordinator@university.edu"
    coord_student_id = "STAFF2026"
    coord_password = "Placement@2026"
    cursor.execute('SELECT id FROM users WHERE LOWER(email) = ?', (coord_email,))
    coord_row = cursor.fetchone()
    if not coord_row:
        cursor.execute('''
            INSERT INTO users (student_id, name, email, password_hash, role)
            VALUES (?, ?, ?, ?, 'coordinator')
        ''', (coord_student_id, "Dr. Vikram Malhotra (Placement Officer)", coord_email, generate_password_hash(coord_password)))

    # Ensure Profile exists
    cursor.execute('SELECT id FROM profiles WHERE user_id = ?', (user_id,))
    profile_row = cursor.fetchone()
    if not profile_row:
        cursor.execute('''
            INSERT INTO profiles (user_id, college, degree, branch, graduation_year, cgpa, technical_skills, areas_of_interest, bio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            "National Institute of Technology",
            "B.Tech",
            "Computer Science & Engineering",
            2026,
            8.85,
            "Data Structures, Algorithms, Python, JavaScript, SQL, System Design, Operating Systems, Flask",
            "Backend Engineering, Distributed Systems, Cloud Architecture",
            "Final year B.Tech CSE student preparing for product engineering and tech consulting campus placements."
        ))

    if force_reset:
        cursor.execute('DELETE FROM preparation_tasks WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM practice_tests WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM applications WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM companies WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM application_status_history WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM notifications WHERE user_id = ?', (user_id,))

    today = date.today()

    # Seed Preparation Tasks only if user currently has none
    cursor.execute('SELECT COUNT(*) as cnt FROM preparation_tasks WHERE user_id = ?', (user_id,))
    if cursor.fetchone()['cnt'] == 0:
        tasks = [
            ("Solve 50 LeetCode Medium Problems on Trees & Graphs", "Coding Practice", "High", "pending", (today + timedelta(days=7)).isoformat(), "Focus on BFS, DFS, and Dijkstra shortest path."),
            ("Complete OS Process Synchronization & Semaphores", "Technical Subjects", "High", "completed", (today - timedelta(days=2)).isoformat(), "Reviewed Peterson's algorithm, Dining Philosophers, and deadlock prevention."),
            ("DBMS Normalization (1NF, 2NF, 3NF, BCNF) & Indexing", "Technical Subjects", "Medium", "completed", (today - timedelta(days=5)).isoformat(), "Created cheat sheet for candidate keys and lossless join decomposition."),
            ("Quantitative Aptitude: Time & Work, Speed & Distance", "Aptitude", "High", "completed", (today - timedelta(days=4)).isoformat(), "Practiced 30 shortcut formulas from RS Aggarwal."),
            ("Logical Reasoning: Puzzles, Seating Arrangements & Syllogisms", "Logical Reasoning", "Medium", "pending", (today + timedelta(days=5)).isoformat(), "Practice circular and linear arrangement problems."),
            ("Verbal Ability: Reading Comprehension & Sentence Correction", "Verbal Ability", "Low", "completed", (today - timedelta(days=8)).isoformat(), "Scored 92% in mock reading comprehension passage."),
            ("Master System Design: URL Shortener & Rate Limiter", "Coding Practice", "High", "pending", (today + timedelta(days=12)).isoformat(), "Cover hashing, consistent hashing, caching with Redis."),
            ("Mock Technical Interview with Senior Engineer", "Interview Preparation", "High", "pending", (today + timedelta(days=10)).isoformat(), "Schedule on mock interview platform for live DSA problem solving."),
            ("Behavioral & HR Questions: STAR Method Framework", "Communication Skills", "Medium", "completed", (today - timedelta(days=10)).isoformat(), "Prepared 5 project stories highlighting leadership, conflict, and deadlines."),
            ("Computer Networks: TCP 3-way Handshake & DNS Resolution", "Technical Subjects", "Medium", "pending", (today + timedelta(days=14)).isoformat(), "Understand OSI layers, TCP vs UDP, and HTTP/HTTPS headers.")
        ]
        for title, cat, prio, stat, due, notes in tasks:
            comp = datetime.now().isoformat() if stat == 'completed' else None
            cursor.execute('''
                INSERT INTO preparation_tasks (user_id, title, category, priority, status, due_date, notes, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, title, cat, prio, stat, due, notes, comp))

    # Seed Practice Tests only if user currently has none
    cursor.execute('SELECT COUNT(*) as cnt FROM practice_tests WHERE user_id = ?', (user_id,))
    if cursor.fetchone()['cnt'] == 0:
        tests = [
            ("TCS NQT National Mock Aptitude", "Aptitude", "Percentages & Profit-Loss", (today - timedelta(days=28)).isoformat(), 42.0, 50.0, 84.0, "Speed in profit and loss was slightly low, accuracy 90%."),
            ("Infosys Verbal & Reasoning Assessment", "Logical Reasoning", "Blood Relations & Puzzles", (today - timedelta(days=21)).isoformat(), 28.0, 50.0, 56.0, "Need more practice on complex blood relation puzzles."),
            ("Accenture Cognitive & Technical Mock", "Technical Subjects", "SQL & DBMS Normalization", (today - timedelta(days=15)).isoformat(), 45.0, 50.0, 90.0, "Strong performance in SQL and OOPs concepts."),
            ("DSA & Coding Assessment 01", "Coding Practice", "Dynamic Programming", (today - timedelta(days=10)).isoformat(), 35.0, 100.0, 35.0, "Need revision on recurrence relations and memoization."),
            ("Full-Length Campus Mock Test (AMCAT)", "Aptitude", "Time, Speed & Work", (today - timedelta(days=6)).isoformat(), 72.0, 100.0, 72.0, "Good overall percentile; speed improved on relative speed."),
            ("System Architecture & Database Design Test", "Technical Subjects", "OS & Process Concurrency", (today - timedelta(days=2)).isoformat(), 46.0, 50.0, 92.0, "Excellent grasp on B+ Tree indexing and ACID properties.")
        ]
        for title, cat, topic, t_date, score, max_s, pct, notes in tests:
            cursor.execute('''
                INSERT INTO practice_tests (user_id, title, category, topic, test_date, score_obtained, max_score, percentage, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, title, cat, topic, t_date, score, max_s, pct, notes))

    # Seed Companies if user currently has none
    cursor.execute('SELECT COUNT(*) as cnt FROM companies WHERE user_id = ?', (user_id,))
    if cursor.fetchone()['cnt'] == 0:
        seed_shared_placement_drives(cursor, user_id=user_id)

    # Map company names to IDs for this user
    cursor.execute('SELECT id, company_name FROM companies WHERE user_id = ?', (user_id,))
    comp_ids = {row['company_name']: row['id'] for row in cursor.fetchall()}

    # Seed Applications if user currently has none
    cursor.execute('SELECT COUNT(*) as cnt FROM applications WHERE user_id = ?', (user_id,))
    if cursor.fetchone()['cnt'] == 0:
        demo_apps = [
            (
                user_id, comp_ids.get("Microsoft IDC"), "Microsoft IDC", "Software Development Engineer",
                "Assessment", (today - timedelta(days=3)).isoformat(), "APP-2026-0001",
                "Online assessment test window opens this Saturday at 10:00 AM.",
                None, "HackerRank Platform (3 algorithmic coding questions + 15 CS MCQ). Test link active.",
                "Shortlisted for online assessment test by Placement Office.",
                "Online coding assessment link received. Slot booked."
            ),
            (
                user_id, comp_ids.get("Oracle Cloud Infrastructure"), "Oracle Cloud Infrastructure", "Member of Technical Staff",
                "Interview", (today - timedelta(days=14)).isoformat(), "APP-2026-0002",
                "Technical Interview Round 1 (Data Structures & Systems Architecture).",
                "Date: Friday 11:00 AM | Mode: Zoom Video Conference (Link shared via placement portal)",
                "Cleared online assessment with 94 percentile.",
                "Moved to technical interview rounds following high assessment score.",
                "Technical Round 1 scheduled for this Friday at 11:00 AM."
            ),
            (
                user_id, comp_ids.get("Goldman Sachs"), "Goldman Sachs", "Analyst - Technology & Engineering",
                "Applied", (today - timedelta(days=1)).isoformat(), "APP-2026-0003",
                "Application under verification by campus recruitment coordinator.",
                None, None,
                "Application successfully registered for campus drive.",
                "Submitted campus registration and resume."
            ),
            (
                user_id, None, "Cisco Systems", "Software Engineer - Networks",
                "Selected", (today - timedelta(days=35)).isoformat(), "APP-2026-0004",
                "Campus selection finalized. Onboarding document verification pending.",
                "Final Managerial & HR fitment round concluded.",
                "Network & Aptitude OA cleared.",
                "Formal campus offer released: Base 16 LPA + joining bonus.",
                "Offer received! Base: 16 LPA + joining bonus."
            ),
            (
                user_id, None, "Deloitte USI", "Consultant - Technology Analyst",
                "Awaiting Update", (today - timedelta(days=7)).isoformat(), "APP-2026-0005",
                "Awaiting interview scheduling from company recruitment panel.",
                None, "Cognitive & Technical mock assessment cleared.",
                "Application forwarded to recruitment panel for interview slotting.",
                "Resume and aptitude test cleared. Waiting for interview schedule confirmation."
            ),
            (
                user_id, None, "Atlassian", "Software Engineer - Intern/Grad",
                "Rejected", (today - timedelta(days=25)).isoformat(), "APP-2026-0006",
                "Process concluded.",
                "System Design Round completed.",
                "OA cleared.",
                "Candidate did not clear technical round 2. Encouraged to reapply next cycle.",
                "Cleared OA, could not clear system design round."
            )
        ]

        for u_id, c_id, c_name, role, stage, app_d, ref, nxt, int_det, ass_det, st_note, notes in demo_apps:
            cursor.execute('''
                INSERT INTO applications (
                    user_id, company_id, company_name, role, stage, application_date,
                    app_ref, next_step, interview_details, assessment_details, status_note, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (u_id, c_id, c_name, role, stage, app_d, ref, nxt, int_det, ass_det, st_note, notes))
            new_app_id = cursor.lastrowid

            # Seed status history for each application
            cursor.execute('''
                INSERT INTO application_status_history (
                    application_id, user_id, stage, status_note, interview_date, interview_venue, updated_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                new_app_id, u_id, "Applied",
                f"Application registered for {role} at {c_name}.",
                None, None, "Student / Portal",
                (today - timedelta(days=20)).isoformat()
            ))

            if stage != "Applied":
                cursor.execute('''
                    INSERT INTO application_status_history (
                        application_id, user_id, stage, status_note, interview_date, interview_venue, updated_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    new_app_id, u_id, stage,
                    st_note or f"Status transitioned to {stage}.",
                    int_det, None, "Placement Coordinator",
                    (today - timedelta(days=2)).isoformat()
                ))

        # Seed sample notifications for demo student
        cursor.execute('''
            INSERT INTO notifications (user_id, title, message, link, is_read, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        ''', (
            user_id,
            "Assessment Window Announced: Microsoft IDC",
            "Online assessment scheduled for this Saturday at 10:00 AM on HackerRank. Slot confirmed.",
            "/placement-hub?tab=applications",
            (today - timedelta(days=1)).isoformat()
        ))
        cursor.execute('''
            INSERT INTO notifications (user_id, title, message, link, is_read, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        ''', (
            user_id,
            "Interview Round Scheduled: Oracle Cloud Infrastructure",
            "Technical Round 1 scheduled for this Friday at 11:00 AM via Zoom.",
            "/placement-hub?tab=applications",
            today.isoformat()
        ))

    # Seed initial demo resume version for student if none exists
    cursor.execute('SELECT COUNT(*) as cnt FROM resumes WHERE user_id = ?', (user_id,))
    if (cursor.fetchone()['cnt'] or 0) == 0:
        demo_resume_text = (
            "Aryan Singh\n"
            "Greater Noida | 9971307020 | aryan.sengar0201@gmail.com | LinkedIn | GitHub\n\n"
            "Summary\n"
            "Motivated second-year AI/ML student with an interest in software development, Python programming, "
            "and building user-focused applications. Experienced in developing academic and personal projects using "
            "modern development tools. Looking for an internship to apply programming skills, learn industry "
            "practices, and contribute to impactful software solutions.\n\n"
            "Education\n"
            "Abdul Kalam Technical University, Lucknow\n"
            "B.Tech in Computer Science and Engineering (AI/ML) | 2025 – 2029\n"
            "CGPA: 7.0 (after 2 semesters)\n"
            "Relevant coursework: Data Structures, Probability & Statistics, Linear Algebra, Python Programming\n\n"
            "Technical Skills\n"
            "• Languages: Python, C, C++\n"
            "• ML / AI: Generative AI, Prompt Engineering, PyTorch\n"
            "• Web / Backend: Streamlit, FastAPI, Flask, React, SQL\n"
            "• Tools: Git/GitHub, VS Code, Linux\n"
            "• Core CS: Data Structures, Algorithms, Object-Oriented Programming\n\n"
            "Projects\n"
            "ResumeGenie | Python, Streamlit, PyMuPDF, python-docx | GitHub\n"
            "• Building a resume analysis web application using Python and Streamlit to help students evaluate resume quality.\n"
            "• Implementing rule-based resume scoring and skill extraction using PyMuPDF and python-docx to analyze PDF and DOCX resumes.\n"
            "• Designing career recommendations, skill-gap analysis, and resume improvement features.\n\n"
            "CareerForge | React, Vite, Tailwind CSS, Gemini API, React Flow | GitHub\n"
            "• Built an AI-powered career guidance platform to help students plan career paths and identify skill gaps.\n"
            "• Integrated the Gemini API for AI-driven career recommendations and personalized roadmap generation.\n"
            "• Designed a responsive interface featuring interactive career roadmaps, quizzes, and progress tracking."
        )
        import os
        upload_base_dir = os.environ.get('UPLOAD_FOLDER')
        if not upload_base_dir:
            if os.environ.get('VERCEL') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
                upload_base_dir = '/tmp/uploads/resumes'
            else:
                upload_base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'resumes')
        upload_base = os.path.join(upload_base_dir, f'user_{user_id}')
        res_file_path = os.path.join(upload_base, 'v1_Aryan_Singh_AIML_Resume.txt')
        try:
            os.makedirs(upload_base, exist_ok=True)
            with open(res_file_path, 'w', encoding='utf-8') as f:
                f.write(demo_resume_text)
        except Exception:
            pass

        import json
        cursor.execute('''
            INSERT INTO resumes (
                user_id, student_id, original_filename, stored_filename, file_path,
                file_type, file_size, version, file_hash, extracted_text,
                ats_score, target_role, analysis_summary, matched_skills,
                missing_skills, suggestions, analyzed_at, created_at
            ) VALUES (
                ?, ?, 'Aryan_Singh_AIML_Resume.txt', 'v1_Aryan_Singh_AIML_Resume.txt', ?,
                'txt', ?, 1, 'demo_hash_v1', ?,
                82, 'AI/ML & Data Science Engineer',
                'Strong alignment for AI/ML & Python roles with solid project demonstrations.',
                ?, ?, ?, ?, ?
            )
        ''', (
            user_id, demo_student_id, res_file_path, len(demo_resume_text.encode('utf-8')),
            demo_resume_text,
            json.dumps(["Python", "C++", "React", "FastAPI", "Machine Learning", "PyTorch", "SQL", "Git"]),
            json.dumps(["Deep Learning", "TensorFlow", "Docker"]),
            json.dumps(["Add quantifiable metrics to CareerForge and ResumeGenie project descriptions.", "Highlight cloud deployment experience on AWS or GCP."]),
            today.isoformat(), (today - timedelta(days=5)).isoformat()
        ))

    conn.commit()
    conn.close()
    return user_id

if __name__ == '__main__':
    init_db()
    seed_demo_student()
    print("Database initialized and demo student seeded successfully at", get_db_path())
