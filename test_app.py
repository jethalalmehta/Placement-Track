import os
import sys

_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor_packages')
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

import tempfile
import unittest
import sqlite3
from datetime import date, datetime
from html import unescape as html_unescape
from app import app, compute_student_analytics
from database import init_db, seed_demo_student, get_db_connection


class PlacementSystemTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        app.config['TESTING'] = True
        app.config['DATABASE'] = self.db_path
        app.config['WTF_CSRF_ENABLED'] = True
        init_db(self.db_path)
        self.client = app.test_client()

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    # ---------------------------------------------------------
    # 1. AUTHENTICATION TESTS
    # ---------------------------------------------------------
    def test_01_register_login_logout_and_persistence(self):
        # 1. Register a new student account
        res = self.client.post('/register', data={
            'name': 'Priya Verma',
            'student_id': 'CSE2026099',
            'email': 'priya.verma@nit.edu',
            'password': 'SecurePass123',
            'college': 'NIT Trichy',
            'degree': 'B.Tech',
            'branch': 'Computer Science & Engineering',
            'graduation_year': '2026',
            'cgpa': '9.12'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('Hello, Priya', html)
        self.assertIn('9.12', html)

        # Duplicate email registration must be rejected
        self.client.get('/logout', follow_redirects=True)
        dup_res = self.client.post('/register', data={
            'name': 'Another User',
            'email': 'priya.verma@nit.edu',
            'password': 'Password123'
        }, follow_redirects=True)
        self.assertIn('already exists', dup_res.data.decode('utf-8'))

        # 2. Log in using Email and Password
        login_res = self.client.post('/login', data={
            'identifier': 'priya.verma@nit.edu',
            'password': 'SecurePass123'
        }, follow_redirects=True)
        self.assertEqual(login_res.status_code, 200)
        self.assertIn('Hello, Priya', login_res.data.decode('utf-8'))

        # 6. Confirm session persists across normal navigation
        for route in ['/dashboard', '/analysis', '/preparation', '/practice-tests', '/companies', '/applications', '/profile']:
            nav_res = self.client.get(route)
            self.assertEqual(nav_res.status_code, 200, f"Route {route} failed to persist session")

        # 5. Log out and verify access restrictions
        logout_res = self.client.get('/logout', follow_redirects=True)
        self.assertEqual(logout_res.status_code, 200)
        self.assertIn('signed out safely', logout_res.data.decode('utf-8'))

        protected_res = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(protected_res.status_code, 302)
        self.assertIn('/login', protected_res.headers['Location'])

        # 2b. Log in using Student ID (case-insensitive)
        login_id_res = self.client.post('/login', data={
            'identifier': 'cse2026099',
            'password': 'SecurePass123'
        }, follow_redirects=True)
        self.assertEqual(login_id_res.status_code, 200)
        self.assertIn('Hello, Priya', login_id_res.data.decode('utf-8'))
        self.client.get('/logout', follow_redirects=True)

        # 3. Confirm that an incorrect password is rejected
        bad_pw_res = self.client.post('/login', data={
            'identifier': 'priya.verma@nit.edu',
            'password': 'WrongPassword999'
        }, follow_redirects=True)
        self.assertIn('Invalid Email/Student ID or password', bad_pw_res.data.decode('utf-8'))
        self.assertEqual(self.client.get('/dashboard', follow_redirects=False).status_code, 302)

        # 4. Confirm that a nonexistent account is rejected
        no_user_res = self.client.post('/login', data={
            'identifier': 'nobody@nowhere.edu',
            'password': 'SecurePass123'
        }, follow_redirects=True)
        self.assertIn('Invalid Email/Student ID or password', no_user_res.data.decode('utf-8'))

    def test_02_data_isolation_between_students(self):
        # Student A registers and adds private records
        self.client.post('/register', data={
            'name': 'Student Alpha',
            'email': 'alpha@college.edu',
            'password': 'AlphaPass123'
        }, follow_redirects=True)
        self.client.post('/preparation/add', data={
            'title': 'Alpha Secret Task',
            'category': 'Coding Practice',
            'priority': 'High'
        }, follow_redirects=True)
        self.client.post('/applications/add', data={
            'company_name': 'Alpha Secret Corp',
            'role': 'Lead Engineer',
            'stage': 'Interview'
        }, follow_redirects=True)
        self.client.get('/logout', follow_redirects=True)

        # Student B registers
        self.client.post('/register', data={
            'name': 'Student Beta',
            'email': 'beta@college.edu',
            'password': 'BetaPass123'
        }, follow_redirects=True)

        # Student B cannot see Student A's tasks or applications
        prep_html = self.client.get('/preparation').data.decode('utf-8')
        self.assertNotIn('Alpha Secret Task', prep_html)

        apps_html = self.client.get('/applications').data.decode('utf-8')
        self.assertNotIn('Alpha Secret Corp', apps_html)

        # Student B cannot edit or delete Student A's records
        conn = get_db_connection(self.db_path)
        alpha_task = conn.execute("SELECT id FROM preparation_tasks WHERE title = 'Alpha Secret Task'").fetchone()
        alpha_app = conn.execute("SELECT id FROM applications WHERE company_name = 'Alpha Secret Corp'").fetchone()
        conn.close()

        self.client.post(f"/preparation/{alpha_task['id']}/delete", follow_redirects=True)
        self.client.post(f"/applications/{alpha_app['id']}/delete", follow_redirects=True)

        # Verify Student A's records still exist untouched in SQLite
        conn = get_db_connection(self.db_path)
        self.assertIsNotNone(conn.execute("SELECT id FROM preparation_tasks WHERE id = ?", (alpha_task['id'],)).fetchone())
        self.assertIsNotNone(conn.execute("SELECT id FROM applications WHERE id = ?", (alpha_app['id'],)).fetchone())
        conn.close()

    # ---------------------------------------------------------
    # 2. DEMO FLOW & IDEMPOTENCY TESTS
    # ---------------------------------------------------------
    def test_03_demo_button_and_idempotency(self):
        # 1. Open landing page
        landing = self.client.get('/')
        self.assertEqual(landing.status_code, 200)
        self.assertIn('/demo-login', landing.data.decode('utf-8'))

        # 2 & 3. Click Demo and verify demo experience opens
        demo_res = self.client.get('/demo-login', follow_redirects=True)
        self.assertEqual(demo_res.status_code, 200)
        demo_html = demo_res.data.decode('utf-8')
        self.assertIn('Aarav Sharma', demo_html)
        self.assertIn('Microsoft IDC', demo_html)

        # 4 & 5. Confirm tracker and analysis operate with demo data
        analysis_res = self.client.get('/analysis')
        self.assertEqual(analysis_res.status_code, 200)
        self.assertIn('Placement Conversion Analysis', analysis_res.data.decode('utf-8'))

        # Verify idempotency: running seed_demo_student repeatedly does not duplicate users or records
        uid1 = seed_demo_student(db_path=self.db_path)
        uid2 = seed_demo_student(db_path=self.db_path)
        self.assertEqual(uid1, uid2)

        conn = get_db_connection(self.db_path)
        user_cnt = conn.execute("SELECT COUNT(*) as c FROM users WHERE email = 'demo.student@university.edu'").fetchone()['c']
        task_cnt = conn.execute("SELECT COUNT(*) as c FROM preparation_tasks WHERE user_id = ?", (uid1,)).fetchone()['c']
        conn.close()
        self.assertEqual(user_cnt, 1)
        self.assertEqual(task_cnt, 10)

    # ---------------------------------------------------------
    # 3. PLACEMENT TRACKER CRUD, SEARCH, FILTER & SORT TESTS
    # ---------------------------------------------------------
    def test_04_placement_tracker_crud_and_filters(self):
        self.client.post('/register', data={
            'name': 'Rohan Mehta',
            'email': 'rohan@college.edu',
            'password': 'RohanPass123'
        }, follow_redirects=True)

        # 1. Create a company drive + linked application
        self.client.post('/companies/add', data={
            'company_name': 'Stripe India',
            'role': 'Backend Engineer',
            'package_ctc': '36.0 LPA',
            'drive_date': '2026-11-15',
            'deadline': '2026-11-05',
            'eligibility': 'CGPA >= 8.0',
            'required_skills': 'Python, Distributed Systems, APIs',
            'job_link': 'https://stripe.com/jobs',
            'notes': '3 technical rounds',
            'create_application': '1'
        }, follow_redirects=True)

        conn = get_db_connection(self.db_path)
        comp = conn.execute("SELECT * FROM companies WHERE company_name = 'Stripe India'").fetchone()
        app_row = conn.execute("SELECT * FROM applications WHERE company_name = 'Stripe India'").fetchone()
        conn.close()
        self.assertIsNotNone(comp)
        self.assertIsNotNone(app_row)
        self.assertEqual(app_row['stage'], 'Applied')

        # 2. Edit the application record
        self.client.post(f"/applications/{app_row['id']}/edit", data={
            'company_id': str(comp['id']),
            'company_name': 'Stripe India',
            'role': 'Senior Backend Grad Engineer',
            'stage': 'Shortlisted',
            'application_date': '2026-10-19',
            'notes': 'Resume shortlisted for coding test'
        }, follow_redirects=True)

        conn = get_db_connection(self.db_path)
        updated_app = conn.execute("SELECT * FROM applications WHERE id = ?", (app_row['id'],)).fetchone()
        conn.close()
        self.assertEqual(updated_app['role'], 'Senior Backend Grad Engineer')
        self.assertEqual(updated_app['stage'], 'Shortlisted')

        # 3. Change status through Assessment/Test -> Interview -> Selected
        for next_stage in ['Assessment/Test', 'Interview', 'Selected']:
            stage_res = self.client.post(f"/applications/{app_row['id']}/stage", data={
                'stage': next_stage,
                'notes': f'Moved to {next_stage}'
            }, follow_redirects=True)
            self.assertEqual(stage_res.status_code, 200)
            conn = get_db_connection(self.db_path)
            curr = conn.execute("SELECT stage FROM applications WHERE id = ?", (app_row['id'],)).fetchone()
            conn.close()
            self.assertEqual(curr['stage'], next_stage)

        # 4. Search and filter the record
        search_res = self.client.get('/applications?search=Stripe&stage=Selected')
        self.assertIn('Stripe India', search_res.data.decode('utf-8'))

        empty_filter_res = self.client.get('/applications?stage=Not+Selected')
        self.assertNotIn('Senior Backend Grad Engineer', empty_filter_res.data.decode('utf-8'))

        # 5 & 6. Delete the record and verify SQLite removal
        del_res = self.client.post(f"/applications/{app_row['id']}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)
        conn = get_db_connection(self.db_path)
        self.assertIsNone(conn.execute("SELECT id FROM applications WHERE id = ?", (app_row['id'],)).fetchone())
        conn.close()

    # ---------------------------------------------------------
    # 4. PLACEMENT & PREPARATION ANALYSIS MATHEMATICAL TESTS
    # ---------------------------------------------------------
    def test_05_analysis_calculations_and_empty_states(self):
        self.client.post('/register', data={
            'name': 'Ananya Rao',
            'email': 'ananya@college.edu',
            'password': 'AnanyaPass123'
        }, follow_redirects=True)

        conn = get_db_connection(self.db_path)
        uid = conn.execute("SELECT id FROM users WHERE email = 'ananya@college.edu'").fetchone()['id']
        conn.close()

        # Verify empty state first
        empty_metrics = compute_student_analytics(uid)
        self.assertEqual(empty_metrics['total_applications'], 0)
        self.assertEqual(empty_metrics['active_applications'], 0)
        self.assertEqual(empty_metrics['selection_rate'], 0.0)
        self.assertEqual(empty_metrics['total_tasks'], 0)
        self.assertEqual(empty_metrics['total_tests'], 0)

        empty_analysis_html = self.client.get('/analysis').data.decode('utf-8')
        self.assertIn('Add your first placement application', empty_analysis_html)

        # Add 5 applications across different stages:
        # 1 Applied, 1 Assessment/Test, 1 Interview, 1 Selected, 1 Not Selected
        stages_to_add = [
            ('Company A', 'SDE', 'Applied'),
            ('Company B', 'SDE', 'Assessment/Test'),
            ('Company C', 'SDE', 'Interview'),
            ('Company D', 'SDE', 'Selected'),
            ('Company E', 'SDE', 'Not Selected'),
        ]
        for cname, role, st in stages_to_add:
            self.client.post('/applications/add', data={
                'company_name': cname,
                'role': role,
                'stage': st
            }, follow_redirects=True)

        # Add 4 preparation tasks (3 completed, 1 pending -> 75%)
        for idx in range(4):
            self.client.post('/preparation/add', data={
                'title': f'Task {idx}',
                'category': 'Aptitude',
                'priority': 'High'
            }, follow_redirects=True)

        conn = get_db_connection(self.db_path)
        task_ids = [r['id'] for r in conn.execute("SELECT id FROM preparation_tasks WHERE user_id = ?", (uid,)).fetchall()]
        conn.close()
        for tid in task_ids[:3]:
            self.client.post(f"/api/tasks/{tid}/toggle")

        # Add 2 practice tests (80% and 90% -> average 85.0%)
        self.client.post('/practice-tests/add', data={
            'title': 'Test 1',
            'category': 'Aptitude',
            'test_date': '2026-10-10',
            'score_obtained': '40',
            'max_score': '50'
        }, follow_redirects=True)
        self.client.post('/practice-tests/add', data={
            'title': 'Test 2',
            'category': 'Coding Practice',
            'test_date': '2026-10-15',
            'score_obtained': '90',
            'max_score': '100'
        }, follow_redirects=True)

        metrics = compute_student_analytics(uid)
        self.assertEqual(metrics['total_applications'], 5)
        self.assertEqual(metrics['active_applications'], 3)  # Applied + Assessment/Test + Interview
        self.assertEqual(metrics['selected_count'], 1)
        self.assertEqual(metrics['not_selected_count'], 1)
        self.assertEqual(metrics['interview_count'], 1)
        self.assertEqual(metrics['assessment_count'], 1)
        self.assertEqual(metrics['completed_outcomes'], 2)
        self.assertEqual(metrics['selection_rate'], 50.0)     # 1 / 2 * 100
        self.assertEqual(metrics['overall_offer_rate'], 20.0) # 1 / 5 * 100
        self.assertEqual(metrics['interview_reach_rate'], 40.0) # (1 + 1) / 5 * 100
        self.assertEqual(metrics['task_completion_pct'], 75)  # 3 / 4 * 100
        self.assertEqual(metrics['avg_score'], 85.0)          # (80 + 90) / 2

        # Verify chart JSON API returns real database values
        charts_json = self.client.get('/api/analytics/charts').get_json()
        self.assertEqual(len(charts_json['tests_timeline']), 2)
        self.assertEqual(charts_json['application_stages']['Selected'], 1)
        self.assertEqual(charts_json['application_stages']['Assessment/Test'], 1)

    # ---------------------------------------------------------
    # 5. END-TO-END CRUD ACROSS ALL MODULES & TEMPLATE AUDIT
    # ---------------------------------------------------------
    def test_06_full_module_crud_and_template_audit(self):
        # Sign in via Demo account
        self.client.get('/demo-login', follow_redirects=True)

        # Profile update test
        prof_res = self.client.post('/profile', data={
            'name': 'Aarav Sharma',
            'student_id': 'CSE2026001',
            'college': 'NIT Surathkal',
            'degree': 'B.Tech',
            'branch': 'Computer Science & Engineering',
            'graduation_year': '2026',
            'cgpa': '9.05',
            'technical_skills': 'Python, Flask, SQLite, System Design',
            'areas_of_interest': 'Distributed Systems',
            'bio': 'Updated bio in SQLite'
        }, follow_redirects=True)
        self.assertEqual(prof_res.status_code, 200)
        self.assertIn('9.05', prof_res.data.decode('utf-8'))
        self.assertIn('NIT Surathkal', prof_res.data.decode('utf-8'))

        # Preparation Task CRUD + Toggle + Filters
        self.client.post('/preparation/add', data={
            'title': 'Dynamic Programming Bitmasking',
            'category': 'Coding Practice',
            'priority': 'High',
            'due_date': '2026-11-01',
            'notes': 'AtCoder DP contest'
        }, follow_redirects=True)
        conn = get_db_connection(self.db_path)
        t_row = conn.execute("SELECT * FROM preparation_tasks WHERE title = 'Dynamic Programming Bitmasking'").fetchone()
        conn.close()
        self.assertIsNotNone(t_row)

        # Edit task
        self.client.post(f"/preparation/{t_row['id']}/edit", data={
            'title': 'DP Bitmasking & Trees',
            'category': 'Coding Practice',
            'priority': 'Medium',
            'status': 'completed',
            'due_date': '2026-11-02',
            'notes': 'Completed all 26 problems'
        }, follow_redirects=True)
        conn = get_db_connection(self.db_path)
        t_updated = conn.execute("SELECT * FROM preparation_tasks WHERE id = ?", (t_row['id'],)).fetchone()
        conn.close()
        self.assertEqual(t_updated['title'], 'DP Bitmasking & Trees')
        self.assertEqual(t_updated['status'], 'completed')

        # API toggle task & verify category counter in response
        api_tog = self.client.post(f"/api/tasks/{t_row['id']}/toggle").get_json()
        self.assertTrue(api_tog['success'])
        self.assertEqual(api_tog['new_status'], 'pending')
        self.assertEqual(api_tog['category'], 'Coding Practice')
        self.assertIn('category_completed', api_tog)
        self.assertIn('category_total', api_tog)

        # Form toggle fallback
        self.client.post(f"/preparation/{t_row['id']}/toggle", follow_redirects=True)
        conn = get_db_connection(self.db_path)
        self.assertEqual(conn.execute("SELECT status FROM preparation_tasks WHERE id = ?", (t_row['id'],)).fetchone()['status'], 'completed')
        conn.close()

        # Practice Test Edit & Delete
        conn = get_db_connection(self.db_path)
        test_row = conn.execute("SELECT * FROM practice_tests LIMIT 1").fetchone()
        conn.close()
        self.client.post(f"/practice-tests/{test_row['id']}/edit", data={
            'title': 'Updated Mock Test 01',
            'category': 'Aptitude',
            'test_date': '2026-10-18',
            'score_obtained': '48',
            'max_score': '50',
            'notes': '96% score'
        }, follow_redirects=True)
        conn = get_db_connection(self.db_path)
        self.assertEqual(conn.execute("SELECT percentage FROM practice_tests WHERE id = ?", (test_row['id'],)).fetchone()['percentage'], 96.0)
        conn.close()

        self.client.post(f"/practice-tests/{test_row['id']}/delete", follow_redirects=True)
        conn = get_db_connection(self.db_path)
        self.assertIsNone(conn.execute("SELECT id FROM practice_tests WHERE id = ?", (test_row['id'],)).fetchone())
        conn.close()

        # Company Edit & Delete
        conn = get_db_connection(self.db_path)
        comp_row = conn.execute("SELECT * FROM companies WHERE company_name = 'Microsoft IDC'").fetchone()
        conn.close()
        self.client.post(f"/companies/{comp_row['id']}/edit", data={
            'company_name': 'Microsoft India R&D',
            'role': 'SDE-1 Cloud',
            'package_ctc': '45.0 LPA',
            'drive_date': '2026-11-20',
            'deadline': '2026-11-10',
            'eligibility': 'CGPA >= 8.0',
            'required_skills': 'C++, Azure, DSA',
            'job_link': 'https://careers.microsoft.com',
            'notes': 'Updated drive details'
        }, follow_redirects=True)
        conn = get_db_connection(self.db_path)
        # Verify linked application also synchronized company_name and role
        linked_app = conn.execute("SELECT * FROM applications WHERE company_id = ?", (comp_row['id'],)).fetchone()
        conn.close()
        self.assertEqual(linked_app['company_name'], 'Microsoft India R&D')
        self.assertEqual(linked_app['role'], 'SDE-1 Cloud')

        # Demo Reset restores clean initial state
        reset_res = self.client.post('/demo-reset', follow_redirects=True)
        self.assertEqual(reset_res.status_code, 200)
        self.assertIn('reset to their clean initial state', reset_res.data.decode('utf-8'))
        conn = get_db_connection(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM preparation_tasks").fetchone()['c'], 10)
        self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM practice_tests").fetchone()['c'], 6)
        self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM companies").fetchone()['c'], 4)
        self.assertEqual(conn.execute("SELECT COUNT(*) as c FROM applications").fetchone()['c'], 6)
        conn.close()

        # Audit all templates to ensure no native confirm(), alert(), or window.open() calls exist
        templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        for fname in os.listdir(templates_dir):
            if fname.endswith('.html'):
                with open(os.path.join(templates_dir, fname), 'r', encoding='utf-8') as f:
                    content = f.read()
                    self.assertNotIn('confirm(', content, f"Native confirm() found in {fname}")
                    self.assertNotIn('alert(', content, f"Native alert() found in {fname}")
                    self.assertNotIn('window.open(', content, f"window.open() found in {fname}")

    # ---------------------------------------------------------
    # 7. PLACEMENT HUB UNIFIED FEATURE & DUPLICATE TRACKING
    # ---------------------------------------------------------
    def test_07_placement_hub_unified_feature_and_duplicate_prevention(self):
        # 1. Sign in as demo student
        self.client.get('/demo-login', follow_redirects=True)

        # 2. Verify /placement-hub renders Explore tab by default
        hub_res = self.client.get('/placement-hub?tab=explore')
        self.assertEqual(hub_res.status_code, 200)
        html = hub_res.data.decode('utf-8')
        self.assertIn('Explore Opportunities', html)
        self.assertIn('My Applications', html)
        self.assertIn('Microsoft IDC', html)
        self.assertIn('Goldman Sachs', html)
        self.assertIn('Apply / Official Portal', html)

        # 3. Verify profile-based eligibility indicators
        self.assertIn('Eligible', html)

        # 4. Add a new company opportunity drive
        add_drive_res = self.client.post('/placement-hub/add-drive', data={
            'company_name': 'Adobe Systems',
            'role': 'Product Intern',
            'package_ctc': '20.0 LPA',
            'location': 'Noida, India',
            'job_type': 'Internship',
            'drive_date': '2026-11-20',
            'deadline': '2026-11-05',
            'eligibility': 'CGPA >= 8.0',
            'required_skills': 'C++, DSA, Computer Graphics',
            'selection_rounds': 'OA + 2 Tech Rounds',
            'job_link': 'https://adobe.com/careers'
        }, follow_redirects=True)
        self.assertEqual(add_drive_res.status_code, 200)
        self.assertIn('Adobe Systems', add_drive_res.data.decode('utf-8'))

        conn = get_db_connection(self.db_path)
        adobe_drive = conn.execute("SELECT * FROM companies WHERE company_name = 'Adobe Systems'").fetchone()
        conn.close()
        self.assertIsNotNone(adobe_drive)
        self.assertEqual(adobe_drive['location'], 'Noida, India')
        self.assertEqual(adobe_drive['job_type'], 'Internship')

        # 5. Track the newly added opportunity to student's applications
        track_res = self.client.post(f"/placement-hub/track/{adobe_drive['id']}", follow_redirects=True)
        self.assertEqual(track_res.status_code, 200)
        self.assertIn('added to My Applications', track_res.data.decode('utf-8'))

        conn = get_db_connection(self.db_path)
        tracked_app = conn.execute("SELECT * FROM applications WHERE company_id = ?", (adobe_drive['id'],)).fetchone()
        conn.close()
        self.assertIsNotNone(tracked_app)
        self.assertEqual(tracked_app['stage'], 'Applied')

        # 6. Verify duplicate prevention: tracking the same opportunity again is rejected
        dup_track_res = self.client.post(f"/placement-hub/track/{adobe_drive['id']}", follow_redirects=True)
        self.assertEqual(dup_track_res.status_code, 200)
        self.assertIn('already in your application tracking list', dup_track_res.data.decode('utf-8'))

        conn = get_db_connection(self.db_path)
        app_count = conn.execute("SELECT COUNT(*) as c FROM applications WHERE company_id = ?", (adobe_drive['id'],)).fetchone()['c']
        conn.close()
        self.assertEqual(app_count, 1, "Duplicate application record was created!")

        # 7. Verify My Applications tab lists the tracked application
        my_apps_res = self.client.get('/placement-hub?tab=applications')
        self.assertEqual(my_apps_res.status_code, 200)
        my_apps_html = my_apps_res.data.decode('utf-8')
        self.assertIn('Adobe Systems', my_apps_html)
        self.assertIn('Applied', my_apps_html)

        # 8. Update status: Applied -> Assessment with status change notification
        stage_res = self.client.post(f"/applications/{tracked_app['id']}/stage", data={
            'stage': 'Assessment'
        }, follow_redirects=True)
        html_text = html_unescape(stage_res.data.decode('utf-8'))
        self.assertIn("Application status for Adobe Systems updated to 'Assessment'", html_text)

        # 9. Update status: Assessment -> Interview
        stage_res2 = self.client.post(f"/applications/{tracked_app['id']}/stage", data={
            'stage': 'Interview'
        }, follow_redirects=True)
        html_text2 = html_unescape(stage_res2.data.decode('utf-8'))
        self.assertIn("Application status for Adobe Systems updated to 'Interview'", html_text2)

        # 10. Edit preparation notes
        edit_res = self.client.post(f"/applications/{tracked_app['id']}/edit", data={
            'company_name': 'Adobe Systems',
            'role': 'Product Intern',
            'stage': 'Interview',
            'application_date': '2026-10-28',
            'notes': 'Interview scheduled for next Tuesday at 3 PM. Revise OpenGL & Trees.'
        }, follow_redirects=True)
        self.assertEqual(edit_res.status_code, 200)
        conn = get_db_connection(self.db_path)
        updated_adobe = conn.execute("SELECT * FROM applications WHERE id = ?", (tracked_app['id'],)).fetchone()
        conn.close()
        self.assertIn('OpenGL & Trees', updated_adobe['notes'])

        # 11. Remove tracked application
        del_app_res = self.client.post(f"/applications/{tracked_app['id']}/delete", follow_redirects=True)
        self.assertEqual(del_app_res.status_code, 200)
        conn = get_db_connection(self.db_path)
        deleted_app = conn.execute("SELECT * FROM applications WHERE id = ?", (tracked_app['id'],)).fetchone()
        conn.close()
        self.assertIsNone(deleted_app)

    # ---------------------------------------------------------
    # 8. PERFORMANCE ANALYTICS, CRUD, VALIDATIONS & WEAK TOPICS
    # ---------------------------------------------------------
    def test_08_performance_analytics_crud_validation_and_weak_topics(self):
        self.client.get('/demo-login', follow_redirects=True)

        # 1. Access Performance Analytics page
        pa_res = self.client.get('/performance-analytics')
        self.assertEqual(pa_res.status_code, 200)
        pa_html = pa_res.data.decode('utf-8')
        self.assertIn('Academic & Mock Test Performance', pa_html)
        self.assertIn('Automatic Weak-Topic Analysis', pa_html)
        self.assertIn('Your Improvement Plan', pa_html)
        self.assertIn('Overall Average Score', pa_html)

        # 2. Record new test marks and verify formula: (Marks / Max) * 100
        # Obtained: 18, Max: 50 -> 36.0% (High Priority: Below 40%)
        add_res = self.client.post('/performance-analytics/add', data={
            'title': 'Quantitative Aptitude Sectional 01',
            'category': 'Quantitative Aptitude',
            'topic': 'Percentages & Ratio',
            'score_obtained': '18',
            'max_score': '50',
            'test_date': '2026-10-28',
            'notes': 'Struggled with successive percentage change problems.'
        }, follow_redirects=True)
        self.assertEqual(add_res.status_code, 200)
        html_add = html_unescape(add_res.data.decode('utf-8'))
        self.assertIn("36.0% on 'Percentages & Ratio'", html_add)

        conn = get_db_connection(self.db_path)
        pt_record = conn.execute("SELECT * FROM practice_tests WHERE topic = 'Percentages & Ratio' ORDER BY id DESC").fetchone()
        conn.close()
        self.assertIsNotNone(pt_record)
        self.assertEqual(pt_record['percentage'], 36.0)

        # 3. Test validation errors:
        # a) Negative marks obtained
        neg_res = self.client.post('/performance-analytics/add', data={
            'title': 'Invalid Negative Marks',
            'category': 'Logical Reasoning',
            'topic': 'Syllogisms',
            'score_obtained': '-5',
            'max_score': '50'
        }, follow_redirects=True)
        self.assertIn('cannot be negative', neg_res.data.decode('utf-8'))

        # b) Zero maximum marks
        zero_max_res = self.client.post('/performance-analytics/add', data={
            'title': 'Invalid Zero Max',
            'category': 'Logical Reasoning',
            'topic': 'Syllogisms',
            'score_obtained': '10',
            'max_score': '0'
        }, follow_redirects=True)
        self.assertIn('strictly greater than zero', zero_max_res.data.decode('utf-8'))

        # c) Obtained marks > maximum marks
        exceed_res = self.client.post('/performance-analytics/add', data={
            'title': 'Invalid Score Exceeded',
            'category': 'Logical Reasoning',
            'topic': 'Syllogisms',
            'score_obtained': '55',
            'max_score': '50'
        }, follow_redirects=True)
        self.assertIn('cannot exceed maximum marks', exceed_res.data.decode('utf-8'))

        # 4. Verify Weak-Topic Diagnostic Thresholds:
        # Record another score on Percentages & Ratio: 19 / 50 -> 38% -> Avg still < 40% (High Priority)
        # Record a 52% topic (Needs Improvement: 40-59%)
        self.client.post('/performance-analytics/add', data={
            'title': 'Probability & Combinatorics Quiz',
            'category': 'Quantitative Aptitude',
            'topic': 'Probability',
            'score_obtained': '26',
            'max_score': '50',
            'test_date': '2026-10-28'
        }, follow_redirects=True)

        # Record a 70% topic (Improving: 60-79%)
        self.client.post('/performance-analytics/add', data={
            'title': 'Verbal Reading Comprehension',
            'category': 'Verbal Ability',
            'topic': 'Reading Comprehension',
            'score_obtained': '35',
            'max_score': '50',
            'test_date': '2026-10-28'
        }, follow_redirects=True)

        # Record a 90% topic (Strong Topic: 80%+)
        self.client.post('/performance-analytics/add', data={
            'title': 'Operating Systems Semaphore Assessment',
            'category': 'Technical Knowledge',
            'topic': 'Process Synchronization',
            'score_obtained': '45',
            'max_score': '50',
            'test_date': '2026-10-28'
        }, follow_redirects=True)

        diag_res = self.client.get('/performance-analytics')
        diag_html = diag_res.data.decode('utf-8')
        self.assertIn('High Priority', diag_html)
        self.assertIn('Needs Improvement', diag_html)
        self.assertIn('Improving', diag_html)
        self.assertIn('Strong Topic', diag_html)
        self.assertIn('Your Improvement Plan', diag_html)

        # 5. Edit assessment record
        edit_res = self.client.post(f"/performance-analytics/{pt_record['id']}/edit", data={
            'title': 'Quantitative Aptitude Sectional 01 (Retake)',
            'category': 'Quantitative Aptitude',
            'topic': 'Percentages & Ratio',
            'score_obtained': '40',
            'max_score': '50',
            'test_date': '2026-10-28',
            'notes': 'Retook and cleared all doubts.'
        }, follow_redirects=True)
        self.assertEqual(edit_res.status_code, 200)
        conn = get_db_connection(self.db_path)
        updated_pt = conn.execute("SELECT * FROM practice_tests WHERE id = ?", (pt_record['id'],)).fetchone()
        conn.close()
        self.assertEqual(updated_pt['percentage'], 80.0)

        # 6. Delete assessment record
        del_res = self.client.post(f"/performance-analytics/{pt_record['id']}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)
        conn = get_db_connection(self.db_path)
        deleted_pt = conn.execute("SELECT * FROM practice_tests WHERE id = ?", (pt_record['id'],)).fetchone()
        conn.close()
        self.assertIsNone(deleted_pt)

    # ---------------------------------------------------------
    # 9. RESUME ANALYSIS ATS EVALUATION
    # ---------------------------------------------------------
    def test_09_resume_analysis_ats_evaluator(self):
        self.client.get('/demo-login', follow_redirects=True)

        # GET request to /resume-analysis
        res_get = self.client.get('/resume-analysis')
        self.assertEqual(res_get.status_code, 200)
        self.assertIn('ATS Resume Evaluator', res_get.data.decode('utf-8'))

        # POST resume text for evaluation
        sample_resume = '''
        Aarav Sharma - B.Tech Computer Science & Engineering, CGPA 8.85
        Education: National Institute of Technology, B.Tech CSE (2022-2026)
        Technical Skills: Data Structures, Algorithms, DSA, Python, Java, C++, SQL, DBMS, Operating Systems, Computer Networks, System Design, Git, GitHub, Docker, Linux, REST API, React, Flask
        Projects:
        1. Placement Preparation & Tracking System: Built using Python Flask, SQLite, and JavaScript. Implemented automated ATS resume evaluation and weak-topic analysis.
        2. Distributed Cache Engine: Implemented consistent hashing and LRU eviction policy in Java.
        Experience:
        Software Engineering Intern at Tech Corp. Developed REST APIs and optimized database queries.
        '''

        eval_res = self.client.post('/resume-analysis', data={
            'resume_text': sample_resume,
            'target_role': 'Software Development Engineer (SDE)'
        }, follow_redirects=True)

        self.assertEqual(eval_res.status_code, 200)
        html = eval_res.data.decode('utf-8')
        self.assertIn('Overall ATS Match Score', html)
        self.assertIn('Technical Keyword Matches', html)
        self.assertIn('Resume Section Completeness', html)
        self.assertIn('/100', html)

    # ---------------------------------------------------------
    # 10. NAVIGATION, SIDEBAR STRUCTURE & BRANDING INTEGRITY
    # ---------------------------------------------------------
    def test_10_navigation_and_branding_integrity(self):
        self.client.get('/demo-login', follow_redirects=True)
        dash_res = self.client.get('/dashboard')
        self.assertEqual(dash_res.status_code, 200)
        html = dash_res.data.decode('utf-8')

        # Sidebar navigation items verification
        self.assertIn('Placement Track', html)
        self.assertIn('/placement-hub', html)
        self.assertIn('Placement Hub', html)
        self.assertIn('/performance-analytics', html)
        self.assertIn('Performance Analytics', html)
        self.assertIn('/resume-analysis', html)
        self.assertIn('Resume Analysis', html)
        self.assertIn('/profile', html)
        self.assertIn('Student Profile', html)

        # Verify Landing Page is removed from authenticated sidebar navigation
        self.assertNotIn('<span>Landing Page</span>', html)

        # Verify logo redirects to dashboard when logged in
        self.assertIn('href="/dashboard', html)

        # When logged out, verify logo redirects to landing page
        self.client.get('/logout', follow_redirects=True)
        logged_out_res = self.client.get('/')
        self.assertEqual(logged_out_res.status_code, 200)
        lo_html = logged_out_res.data.decode('utf-8')
        self.assertIn('Placement Track', lo_html)

    # ---------------------------------------------------------
    # 11. IN-APP APPLICATION, COORDINATOR WORKFLOW & AUDIT TRAIL
    # ---------------------------------------------------------
    def test_11_placement_hub_in_app_apply_and_coordinator_status_workflow(self):
        # 1. Register a brand new student
        new_email = 'fresh.student@univ.edu'
        reg_res = self.client.post('/register', data={
            'name': 'Fresh Student',
            'email': new_email,
            'student_id': 'CSE2026999',
            'college': 'University Institute of Tech',
            'degree': 'B.Tech',
            'branch': 'Computer Science and Engineering',
            'graduation_year': '2026',
            'cgpa': '8.80',
            'password': 'Password@123'
        }, follow_redirects=True)
        self.assertEqual(reg_res.status_code, 200)

        # 2. Visit Placement Hub: Must show shared published drives immediately without having applied
        hub_res = self.client.get('/placement-hub?tab=explore')
        self.assertEqual(hub_res.status_code, 200)
        hub_html = hub_res.data.decode('utf-8')
        self.assertIn('Microsoft IDC', hub_html)
        self.assertIn('Goldman Sachs', hub_html)
        self.assertIn('Apply Now', hub_html)
        # Verify 'Pipeline' word is eliminated
        self.assertNotIn('Application Pipeline', hub_html)
        self.assertNotIn('Hiring Pipeline', hub_html)

        # 3. Get Microsoft IDC company ID
        conn = get_db_connection(self.db_path)
        msft = conn.execute("SELECT * FROM companies WHERE company_name = 'Microsoft IDC' LIMIT 1").fetchone()
        self.assertIsNotNone(msft)
        msft_id = msft['id']
        conn.close()

        # 4. Submit In-App Application via POST /placement-hub/apply/<id>
        apply_res = self.client.post(f'/placement-hub/apply/{msft_id}', data={
            'applicant_name': 'Fresh Student',
            'applicant_email': new_email,
            'student_id': 'CSE2026999',
            'branch': 'Computer Science and Engineering',
            'degree': 'B.Tech',
            'graduation_year': '2026',
            'cgpa': '8.80',
            'phone': '+91 98765 43210',
            'portfolio_url': 'https://github.com/freshstudent',
            'notes': 'Strong interest in Azure Cloud Systems and distributed computing.'
        }, follow_redirects=True)
        self.assertEqual(apply_res.status_code, 200)
        apply_html = apply_res.data.decode('utf-8')
        self.assertIn('Application successfully submitted', apply_html)

        # Verify application record created in database with unique app_ref
        conn = get_db_connection(self.db_path)
        fresh_app = conn.execute("SELECT * FROM applications WHERE company_id = ? ORDER BY id DESC LIMIT 1", (msft_id,)).fetchone()
        self.assertIsNotNone(fresh_app)
        self.assertTrue(fresh_app['app_ref'].startswith('MICR-2026-') or fresh_app['app_ref'].startswith('APP-2026-'))
        self.assertEqual(fresh_app['stage'], 'Applied')
        app_id = fresh_app['id']
        conn.close()

        # 5. Prevent Duplicate Application
        dup_res = self.client.post(f'/placement-hub/apply/{msft_id}', data={
            'applicant_name': 'Fresh Student',
            'applicant_email': new_email,
            'student_id': 'CSE2026999',
            'cgpa': '8.80'
        }, follow_redirects=True)
        dup_html = dup_res.data.decode('utf-8')
        self.assertIn('You have already applied for', dup_html)

        # 6. Verify non-coordinator cannot update official coordinator status (Access Control)
        unauth_update = self.client.post(f'/coordinator/applications/{app_id}/status', data={
            'stage': 'Interview Scheduled',
            'status_note': 'Attempted unauthorized status change'
        }, follow_redirects=True)
        unauth_html = unauth_update.data.decode('utf-8')
        self.assertTrue('Placement Administrator or Coordinator privileges required' in unauth_html or unauth_update.status_code == 403)

        # 7. Log in as Placement Coordinator
        coord_login = self.client.get('/coordinator-login', follow_redirects=True)
        self.assertEqual(coord_login.status_code, 200)
        self.assertIn('Signed in as Placement Coordinator', coord_login.data.decode('utf-8'))

        # 8. Coordinator updates status to 'Interview Scheduled' with schedule details and notes
        coord_update = self.client.post(f'/coordinator/applications/{app_id}/status', data={
            'stage': 'Interview Scheduled',
            'interview_details': 'Technical Round 1 on Microsoft Teams on Nov 12, 2026 at 10:00 AM IST',
            'status_note': 'Candidate cleared OA with top percentile. Shortlisted for Technical Interview.'
        }, follow_redirects=True)
        self.assertEqual(coord_update.status_code, 200)

        # 9. Verify database audit history and updated status
        conn = get_db_connection(self.db_path)
        try:
            updated_app = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
            history = conn.execute("SELECT * FROM application_status_history WHERE application_id = ? ORDER BY id ASC", (app_id,)).fetchall()
            student_user = conn.execute("SELECT id FROM users WHERE email = ?", (new_email,)).fetchone()
            notifs = conn.execute("SELECT * FROM notifications WHERE user_id = ?", (student_user['id'],)).fetchall()
        finally:
            conn.close()

        self.assertEqual(updated_app['stage'], 'Interview Scheduled')
        self.assertIn('Technical Round 1', updated_app['interview_details'])

        self.assertGreaterEqual(len(history), 2)  # initial submission + interview scheduled update
        latest_history = history[-1]
        self.assertEqual(latest_history['stage'], 'Interview Scheduled')
        self.assertIn('Candidate cleared OA', latest_history['status_note'])

        # 10. Verify notification created for student
        self.assertGreaterEqual(len(notifs), 1)
        notif_messages = [n['message'] for n in notifs]
        self.assertTrue(any('Interview Scheduled' in m for m in notif_messages), f"Expected 'Interview Scheduled' in messages: {notif_messages}")

        # 11. Switch back to student and test voluntary withdrawal
        self.client.get('/logout', follow_redirects=True)
        self.client.post('/login', data={'identifier': new_email, 'password': 'Password@123'}, follow_redirects=True)
        withdraw_res = self.client.post(f'/placement-hub/applications/{app_id}/withdraw', data={
            'reason': 'Accepted alternative offer'
        }, follow_redirects=True)
        self.assertEqual(withdraw_res.status_code, 200)

        conn = get_db_connection(self.db_path)
        try:
            withdrawn_app = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        finally:
            conn.close()

        self.assertEqual(withdrawn_app['stage'], 'Withdrawn')
        self.assertIsNotNone(withdrawn_app['withdrawn_at'])

    # ---------------------------------------------------------
    # 12. AUTOMATIC JOB FETCHING, ADAPTERS & EXTERNAL TRACKING
    # ---------------------------------------------------------
    def test_12_automatic_job_fetching_and_external_tracking(self):
        # 1. Sign in as demo student
        self.client.get('/demo-login', follow_redirects=True)

        # 2. Trigger automatic refresh of online job opportunities
        refresh_res = self.client.post('/placement-hub/refresh-jobs', follow_redirects=True)
        self.assertEqual(refresh_res.status_code, 200)
        html = refresh_res.data.decode('utf-8')
        self.assertIn('Opportunities synchronized successfully', html)

        # 3. Verify shared database records created in 'companies' table
        conn = get_db_connection(self.db_path)
        try:
            ext_jobs = conn.execute("SELECT * FROM companies WHERE is_external = 1").fetchall()
            sources = conn.execute("SELECT * FROM job_sources").fetchall()
        finally:
            conn.close()

        self.assertGreater(len(ext_jobs), 0, "Expected external job vacancies in shared database")
        self.assertGreaterEqual(len(sources), 3, "Expected at least 3 configured job sources")

        sample_ext = ext_jobs[0]
        self.assertIsNotNone(sample_ext['source_name'])
        self.assertIsNotNone(sample_ext['last_verified_at'])

        # 4. Idempotent check: repeated refresh must not create duplicate job listings
        initial_count = len(ext_jobs)
        self.client.post('/placement-hub/refresh-jobs', follow_redirects=True)
        conn = get_db_connection(self.db_path)
        try:
            second_count = conn.execute("SELECT COUNT(*) as cnt FROM companies WHERE is_external = 1").fetchone()['cnt']
        finally:
            conn.close()
        self.assertEqual(second_count, initial_count, "Repeated job sync must deduplicate and not inflate count")

        # 5. Track an external vacancy as 'Tracked — Not Yet Confirmed'
        ext_id = sample_ext['id']
        track_res = self.client.post(f'/placement-hub/track-external/{ext_id}', follow_redirects=True)
        self.assertEqual(track_res.status_code, 200)
        track_html = track_res.data.decode('utf-8')
        self.assertIn('Tracked', track_html)

        conn = get_db_connection(self.db_path)
        try:
            tracked_app = conn.execute("SELECT * FROM applications WHERE company_id = ? ORDER BY id DESC LIMIT 1", (ext_id,)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(tracked_app)
        self.assertEqual(tracked_app['stage'], 'Tracked — Not Yet Confirmed')
        app_id = tracked_app['id']

        # 6. Candidate confirms external application submission via "I've Applied" action
        confirm_res = self.client.post(f'/placement-hub/applications/{app_id}/confirm-applied', data={
            'applied_date': date.today().isoformat(),
            'confirmation_reference': 'WD-EXT-2026-99',
            'notes': 'Completed application on company career site with resume v1.'
        }, follow_redirects=True)
        self.assertEqual(confirm_res.status_code, 200)

        conn = get_db_connection(self.db_path)
        try:
            confirmed_app = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
            history = conn.execute("SELECT * FROM application_status_history WHERE application_id = ? ORDER BY id ASC", (app_id,)).fetchall()
        finally:
            conn.close()

        self.assertEqual(confirmed_app['stage'], 'Applied')
        self.assertEqual(confirmed_app['confirmation_reference'], 'WD-EXT-2026-99')
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual(history[-1]['stage'], 'Applied')

    # ---------------------------------------------------------
    # 13. AUTOMATED RECRUITMENT STATUS SYNCHRONIZATION
    # ---------------------------------------------------------
    def test_13_automated_recruitment_email_status_sync_and_simulator(self):
        # 1. Sign in as demo student
        self.client.get('/demo-login', follow_redirects=True)

        # 2. Test status transition: Interview Invitation email for Microsoft IDC
        sim_interview = self.client.post('/api/integrations/email/simulate', data={
            'sender': 'university-recruiting@microsoft.com',
            'subject': 'Invitation to Technical Interview Round 1 — Software Development Engineer',
            'body': """Dear Candidate,
Congratulations! Following your high score on the Online Coding Assessment, we are pleased to inform you that you have been shortlisted for the next stage.
Your Technical Interview Round 1 is scheduled as follows:
Date/Time: Friday at 11:00 AM IST
Meeting Link: https://teams.microsoft.com/l/meetup-join/recruitment-interview-slot-1092
Please be prepared to write code on a shared editor covering Data Structures and System Architecture.
University Campus Recruitment Team
Microsoft IDC"""
        }, follow_redirects=True)
        self.assertEqual(sim_interview.status_code, 200)
        self.assertIn('Verified status update', sim_interview.data.decode('utf-8'))

        conn = get_db_connection(self.db_path)
        try:
            msft_app = conn.execute("SELECT * FROM applications WHERE company_name LIKE '%Microsoft%' LIMIT 1").fetchone()
            history = conn.execute("SELECT * FROM application_status_history WHERE application_id = ? ORDER BY id DESC LIMIT 1", (msft_app['id'],)).fetchone()
        finally:
            conn.close()

        self.assertIn(msft_app['stage'], ('Shortlisted', 'Interview Scheduled'))
        self.assertEqual(history['source'], 'Email (Gmail/IMAP)')

        # 3. Ambiguity & irrelevance protection: email for an unapplied company must be rejected
        unrelated_res = self.client.post('/api/integrations/email/simulate', data={
            'sender': 'no-reply@unrelated-startup-xyz.com',
            'subject': 'Update regarding your application',
            'body': 'Thank you for your interest in XYZ Startup. Unfortunately we will not be proceeding.'
        }, follow_redirects=True)
        self.assertEqual(unrelated_res.status_code, 200)
        self.assertIn('Could not update status', unrelated_res.data.decode('utf-8'))

        # 4. Idempotency test: duplicate sync of exact same email must not spam notifications
        conn = get_db_connection(self.db_path)
        try:
            notif_cnt1 = conn.execute("SELECT COUNT(*) as cnt FROM notifications").fetchone()['cnt']
        finally:
            conn.close()

        # Re-run the exact same interview simulation
        self.client.post('/api/integrations/email/simulate', data={
            'sender': 'university-recruiting@microsoft.com',
            'subject': 'Invitation to Technical Interview Round 1 — Software Development Engineer',
            'body': """Dear Candidate,
Congratulations! Following your high score on the Online Coding Assessment, we are pleased to inform you that you have been shortlisted for the next stage.
Date/Time: Friday at 11:00 AM IST
Meeting Link: https://teams.microsoft.com/l/meetup-join/recruitment-interview-slot-1092
University Campus Recruitment Team
Microsoft IDC"""
        }, follow_redirects=True)

        conn = get_db_connection(self.db_path)
        try:
            notif_cnt2 = conn.execute("SELECT COUNT(*) as cnt FROM notifications").fetchone()['cnt']
        finally:
            conn.close()
        self.assertEqual(notif_cnt2, notif_cnt1, "Duplicate email message must not emit duplicate notification")

        # 5. Webhook integration test
        webhook_res = self.client.post('/api/webhooks/recruitment-update', headers={
            'X-Recruitment-Secret': 'placement-tracker-btech-cse-secret-key-2026'
        }, json={
            'student_email': 'demo.student@university.edu',
            'company_name': 'Microsoft IDC',
            'stage': 'Offer Received',
            'notes': 'Official job offer extended following final round interview.'
        })
        self.assertEqual(webhook_res.status_code, 200)

        conn = get_db_connection(self.db_path)
        try:
            offer_app = conn.execute("SELECT stage FROM applications WHERE id = ?", (msft_app['id'],)).fetchone()
        finally:
            conn.close()
        self.assertEqual(offer_app['stage'], 'Offer Received')

    # ---------------------------------------------------------
    # 14. PERSISTENT MULTI-VERSION RESUME HISTORY & ATS
    # ---------------------------------------------------------
    def test_14_persistent_resume_version_history_and_ats(self):
        import io

        # 1. Sign in as demo student
        self.client.get('/demo-login', follow_redirects=True)

        # 2. Upload Resume Version 1 (Aryan Singh sample resume)
        v1_text = (
            "Aryan Singh\n"
            "Computer Science & Engineering (AI/ML)\n"
            "Skills: Python, C++, Data Structures, Algorithms, SQL, Git, Linux, Flask\n"
            "Projects:\n"
            "- Placement Preparation & Tracking System (Python, Flask, SQLite)\n"
            "- CareerForge AI Guidance (React, Node.js, REST APIs)\n"
            "Education: B.Tech CSE (AI/ML), CGPA: 8.5\n"
            "Experience: Software Engineering Intern at TechLabs"
        ).encode('utf-8')

        data_v1 = {
            'resume_file': (io.BytesIO(v1_text), 'Aryan_Singh_Resume.txt'),
            'target_role': 'Software Development Engineer (SDE)'
        }
        res_v1 = self.client.post('/resume/upload', data=data_v1, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(res_v1.status_code, 200)
        self.assertIn('successfully saved as Version', res_v1.data.decode('utf-8'))

        conn = get_db_connection(self.db_path)
        try:
            r1 = conn.execute("SELECT * FROM resumes WHERE original_filename = 'Aryan_Singh_Resume.txt' ORDER BY version ASC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(r1)
        r1_id = r1['id']
        r1_version = r1['version']

        # Verify physical file exists in persistent storage
        self.assertTrue(os.path.exists(r1['file_path']))

        # 3. Upload Resume Version 2 (added Docker, Kubernetes, PyTorch)
        v2_text = v1_text + b"\nAdditional Skills: Docker, Kubernetes, PyTorch, Deep Learning, Cloud Computing\nAchievements: 1st Place College Hackathon"
        data_v2 = {
            'resume_file': (io.BytesIO(v2_text), 'Aryan_Singh_Resume.txt'),
            'target_role': 'AI/ML & Data Science Engineer'
        }
        res_v2 = self.client.post('/resume/upload', data=data_v2, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(res_v2.status_code, 200)

        conn = get_db_connection(self.db_path)
        try:
            user_resumes = conn.execute("SELECT * FROM resumes WHERE user_id = ? ORDER BY version ASC", (r1['user_id'],)).fetchall()
        finally:
            conn.close()

        # Both versions must be preserved (no silent overwrite)
        self.assertGreaterEqual(len(user_resumes), 2)
        versions = [r['version'] for r in user_resumes]
        self.assertIn(r1_version, versions)
        r2 = user_resumes[-1]
        self.assertGreater(r2['version'], r1_version)
        r2_id = r2['id']

        # 4. Verify secure file download
        dl_res = self.client.get(f'/resume/download/{r1_id}')
        self.assertEqual(dl_res.status_code, 200)
        self.assertEqual(dl_res.data, v1_text)

        # 5. Access control: unauthorized user cannot download or delete another student's resume
        self.client.get('/logout', follow_redirects=True)
        # Register a fresh student B
        self.client.post('/register', data={
            'name': 'Student B',
            'email': 'student.b@nit.edu',
            'student_id': 'CSE2026888',
            'password': 'Password@123',
            'degree': 'B.Tech',
            'branch': 'CSE',
            'graduation_year': '2026',
            'cgpa': '8.2'
        }, follow_redirects=True)

        unauth_dl = self.client.get(f'/resume/download/{r1_id}', follow_redirects=True)
        self.assertIn('Resume file not found or access denied', unauth_dl.data.decode('utf-8'))

        unauth_del = self.client.post(f'/resume/delete/{r1_id}', follow_redirects=True)
        self.assertIn('access denied', unauth_del.data.decode('utf-8').lower())

        # 6. Switch back to owner and test deletion
        self.client.get('/logout', follow_redirects=True)
        self.client.get('/demo-login', follow_redirects=True)
        del_res = self.client.post(f'/resume/delete/{r2_id}', follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)
        self.assertIn('deleted successfully', del_res.data.decode('utf-8'))

        conn = get_db_connection(self.db_path)
        try:
            deleted_check = conn.execute("SELECT id FROM resumes WHERE id = ?", (r2_id,)).fetchone()
        finally:
            conn.close()
        self.assertIsNone(deleted_check)


if __name__ == '__main__':
    unittest.main(verbosity=2)


