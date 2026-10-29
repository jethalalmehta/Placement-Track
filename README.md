# Placement Track — Placement Preparation & Tracking System

A full-stack web application designed for B.Tech Computer Science & Engineering students to systematically organize placement preparation, evaluate practice test performance, track campus recruitment drives, monitor online job opportunities, and manage the complete job application lifecycle.

---

## 1. Project Overview & Technology Stack

- **Target Course / Degree:** B.Tech Computer Science & Engineering (Final-Year Mini/Major Project)
- **Backend Language & Framework:** Python 3.10+ / Flask (Lightweight, modular WSGI application)
- **Template Engine:** Jinja2 (HTML5 templating with reusable layouts, flash alerts, and modals)
- **Database:** SQLite3 (`instance/placement.db`) with `PRAGMA foreign_keys = ON`, relational integrity, cascading deletions, and performance indexes
- **Security & Authentication:** `werkzeug.security` (`generate_password_hash`, `check_password_hash`), `@login_required` decorators, HTTP-only session cookies
- **Document Processing & Text Extraction:** `pypdf` (PDF resumes), `python-docx` (Word documents), safe UTF-8 text processing
- **Frontend Architecture:** Semantic HTML5, CSS3 with responsive custom properties, and vanilla JavaScript (zero heavy external frontend framework dependencies)
- **Data Visualizations:** Interactive Canvas trend curves, stage funnel charts, and syllabus progress meters

---

## 2. Key Modules & Features

### A. Placement Hub (`/placement-hub`)
A unified placement section merging company drives with candidate tracking:
- **Explore Opportunities Tab:**
  - Displays verified placement drives and active fresher job listings in responsive cards.
  - Shows company name, role, package/CTC, work mode, location, minimum CGPA eligibility, required skills, and deadlines.
  - Multi-criteria filtering by company name, job role, location, minimum CTC, and recruitment drive type.
  - **In-App Campus Applications:** Directly submit applications for on-campus drives with resume selection.
  - **External Opportunities:** Opens the official company career portal and records the opportunity as `Tracked — Not Yet Confirmed`.
  - **Automatic Online Job Fetching:** Integrates legitimate public APIs and curated campus feeds (Jobicy Remote/Junior API, Arbeitnow Jobs API, Curated Campus Drives) with SHA-256 deduplication and expiration filters. Includes a manual **Refresh Opportunities** button.
- **My Applications Tab:**
  - Displays all campus drives and external jobs tracked or applied for by the logged-in student.
  - Real-time status workflow tracking across all recruitment stages.
  - **"I've Applied" Action:** For external jobs, allows students to record external application submission dates and confirmation reference numbers, safely transitioning status to `Applied`.
  - Edit personal preparation notes, view application references, and withdraw applications.

### B. Automated Recruitment Status Tracking & Recruiter Simulator
- **Recruitment Intelligence Parser (`email_recruiter.py`):**
  - Parses incoming communications (emails/webhooks) for milestone transitions:
    - `Assessment Scheduled` (Online tests, HackerRank, CodeSignal)
    - `Shortlisted` (Advancing to next stage)
    - `Interview Scheduled` (Technical rounds, HR rounds, panel interviews)
    - `Offer Received` / `Selected` (Congratulatory offer letters, LOIs)
    - `Not Selected` (Regret communications)
  - Matches candidate applications by company name and aliases; prevents duplicate alerts using SHA-256 message hashing.
  - Maintains a complete audit trail in `application_status_history` and generates in-app notifications.
- **Recruiter Simulator Modal:**
  - An interactive simulator inside Placement Hub to test status updates by selecting a company and simulated email event.
  - Provides a secure ATS webhook endpoint (`POST /api/webhooks/recruitment-update`) for automated integrations.

### C. Multi-Version Resume History & ATS Evaluator (`/resume-analysis`)
- **Versioned File Storage (`resume_manager.py`):**
  - Resumes are stored sequentially (`v1`, `v2`, `v3`...) in user-specific isolated directories (`uploads/resumes/user_{id}/`).
  - Supports `.pdf`, `.docx`, `.txt`, and `.md` file formats (up to 5MB) with SHA-256 content hashing.
  - Full version history table displaying upload timestamps, target roles, ATS scores, and secure download/delete actions.
- **Role-Benchmarked ATS Scoring:**
  - Evaluates resume content against standard industry profiles:
    - *Software Development Engineer (SDE)*
    - *AI/ML & Data Science Engineer*
    - *Frontend Engineer*
    - *Backend Engineer*
    - *Full Stack Engineer*
  - Breaks down match percentages, identifying matching keywords, missing critical competencies, and actionable recommendations.

### D. Executive Student Dashboard (`/dashboard`)
- 6 real-time metrics calculated from database tables (Syllabus completion %, Tasks completed/pending, Mock tests evaluated, Cumulative average score, Active applications count, Upcoming campus drives).
- Chronological test score trajectory canvas chart.
- Topic-wise syllabus progress meters across 7 core domains.
- Weak topic diagnostics alerting students to areas requiring revision.

### E. Preparation Tasks & Practice Tests (`/preparation`, `/practice-tests`)
- **Preparation Tracker:** Manage preparation tasks across Aptitude, Logical Reasoning, Verbal Ability, Technical Core (OS, DBMS, CN), Coding (DSA), and Interview Prep with instant asynchronous status toggles.
- **Practice Assessments:** Record mock test scores, view automated percentages, track score trajectory, and compute category averages.

---

## 3. Database Schema

The SQLite database is initialized at `instance/placement.db`:

- `users` — Student authentication credentials and profile information.
- `profiles` — Academic details (College, Degree, Branch, Graduation Year, CGPA, Technical Skills).
- `companies` — Placement drives, campus visits, and external job openings.
- `applications` — Personal student application records, stages, references, and notes.
- `application_status_history` — Audit trail of all stage transitions, timestamps, actors, and milestone triggers.
- `resumes` — Multi-version resume records, file paths, versions, extracted text, and ATS evaluation results.
- `preparation_tasks` — Curriculum tasks, categories, priorities, due dates, and completion status.
- `practice_tests` — Mock test scores, maximum marks, percentages, and categories.
- `recruitment_integrations` — Recruitment communication audit logs and message hashes for idempotency.
- `job_sources` — Configuration and sync logs for online job APIs and feeds.

---

## 4. Installation & Local Setup

### Prerequisites
- Python 3.10 or higher installed (`python --version`)
- Node.js 18+ (optional, for proxy dev server)

### Step 1: Clone the Repository
```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
```

### Step 2: Set Up Python Virtual Environment
```bash
# On Linux / macOS:
python3 -m venv venv
source venv/bin/activate

# On Windows (PowerShell):
python -m venv venv
venv\Scripts\Activate.ps1
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Environment Variables (Optional)
Copy `.env.example` to `.env` to configure optional custom settings:
```bash
cp .env.example .env
```

### Step 5: Initialize SQLite Database & Seed Data
```bash
python database.py
```
*This safely initializes all tables, foreign keys, indexes, campus opportunities, and a sample demo student profile.*

### Step 6: Start the Application
```bash
python app.py
```
The application will start on: **`http://127.0.0.1:5001`** (or port specified in `PORT` env var).

---

## 5. Running the Automated Test Suite

A comprehensive test suite of 14 end-to-end integration tests covers all core workflows:
```bash
python test_app.py
```

Test coverage includes:
1. Registration, login, logout, and session persistence.
2. Data isolation between distinct student accounts.
3. 1-Click Live Demo button idempotency.
4. Placement tracker CRUD and stage filters.
5. Student analytics calculations and zero-state handling.
6. Module CRUD operations and template audit.
7. Placement Hub unified view and duplicate tracking prevention.
8. Performance analytics and weak topic detection.
9. Resume analysis and ATS evaluator.
10. Navigation and branding integrity.
11. In-app campus drive application and status workflows.
12. Automatic online job fetching and external application tracking.
13. Automated recruitment status sync (Email/ATS) and simulator.
14. Persistent resume version history, access controls, and file management.

---

## 6. Security & Privacy Safeguards

- **No Secrets in Repository:** Passwords, API keys, and sensitive tokens are read exclusively from environment variables or secure configurations.
- **Data Exclusion:** Local SQLite databases (`*.db`, `instance/`), candidate resume files (`uploads/resumes/`), and local environment configurations (`.env`) are strictly excluded via `.gitignore`.
- **Parameterized SQL:** All database interactions use parameter bindings (`?`) to prevent SQL injection vulnerabilities.
- **Strict Access Control:** Resume files and application records are strictly validated against `g.user['id']` to prevent unauthorized cross-student access.
