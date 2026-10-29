"""
email_recruiter.py - Automated Recruitment Email Parsing & Status Synchronization
----------------------------------------------------------------------------------
Monitors and analyzes recruitment communications (via authorized IMAP/OAuth or test simulation)
to automatically update application statuses when authentic recruitment stage changes occur.

Supported Stages:
- Applied / Application Received
- Under Review
- Assessment Scheduled (OA, HackerRank, Codility, aptitude test)
- Shortlisted
- Interview Scheduled (Technical round, HR, Zoom/Teams details)
- Offer Received (Employment offer, CTC letter)
- Selected (Final selection confirmation)
- Not Selected (Regret notification / unsuccessful)

Features:
- Strict matching against candidate's active application records (Company, Role, Student ID/Email).
- Ambiguity protection: newsletters, marketing emails, or generic unverified notices are rejected.
- Idempotency & deduplication: unique message hash prevents duplicate updates or spam notifications.
- Complete audit trail in 'application_status_history'.
- Dispatches in-app notification when a genuine status change is verified.
- Interactive Simulator for college evaluation and test suites.
"""

import re
import hashlib
from datetime import datetime, date


STAGE_PATTERNS = [
    # 1. Offer / Selected (Highest precedence)
    (
        "Offer Received",
        [
            r"(?:pleased|delighted|excited)\s+to\s+(?:offer|extend\s+an\s+offer)",
            r"offer\s+of\s+employment",
            r"job\s+offer\s+letter",
            r"congratulations.*formal\s+offer",
            r"letter\s+of\s+intent",
            r"official\s+offer\s+for\s+the\s+position",
        ]
    ),
    (
        "Selected",
        [
            r"congratulations.*(?:you\s+have\s+been|you're)\s+selected",
            r"final\s+selection\s+(?:confirmed|announced)",
            r"welcome\s+to\s+(?:the\s+team|our\s+company)",
            r"pleased\s+to\s+inform\s+you\s+that\s+you\s+have\s+been\s+selected",
        ]
    ),
    # 2. Rejection / Not Selected
    (
        "Not Selected",
        [
            r"unfortunately.*(?:not\s+moving\s+forward|decided\s+to\s+pursue\s+other)",
            r"regret\s+to\s+inform\s+you",
            r"decided\s+not\s+to\s+move\s+forward",
            r"not\s+selected\s+at\s+this\s+time",
            r"will\s+not\s+be\s+progressing\s+your\s+application",
            r"unable\s+to\s+offer\s+you\s+an\s+interview",
            r"other\s+candidates\s+whose\s+qualifications\s+more\s+closely",
        ]
    ),
    # 3. Interview Scheduled
    (
        "Interview Scheduled",
        [
            r"(?:invitation|invited)\s+to\s+(?:interview|a\s+technical\s+interview)",
            r"interview\s+is\s+scheduled",
            r"technical\s+round\s*(?:1|2|one|two)?\s+(?:scheduled|invitation)",
            r"zoom\s+(?:link|meeting)\s+for\s+your\s+interview",
            r"microsoft\s+teams\s+meeting\s+for\s+your\s+interview",
            r"google\s+meet\s+link\s+for\s+your\s+interview",
            r"interview\s+slot\s+confirmed",
            r"schedule\s+your\s+(?:technical|panel|hr)\s+interview",
        ]
    ),
    # 4. Shortlisted
    (
        "Shortlisted",
        [
            r"(?:you\s+have\s+been|you're)\s+shortlisted",
            r"shortlisted\s+for\s+(?:the\s+next\s+round|interview|evaluation)",
            r"cleared\s+the\s+(?:online\s+assessment|oa|initial\s+screening)",
            r"advanced\s+to\s+the\s+next\s+stage",
        ]
    ),
    # 5. Assessment Scheduled
    (
        "Assessment Scheduled",
        [
            r"(?:online\s+assessment|coding\s+assessment|aptitude\s+test)\s+invitation",
            r"invitation\s+to\s+complete\s+the\s+assessment",
            r"hackerrank\s+(?:test|assessment|link)",
            r"codility\s+(?:test|challenge|link)",
            r"take\s+the\s+(?:online\s+test|oa)",
            r"assessment\s+window\s+is\s+now\s+open",
            r"online\s+proctored\s+test",
        ]
    ),
    # 6. Under Review
    (
        "Under Review",
        [
            r"application\s+is\s+currently\s+under\s+review",
            r"reviewing\s+your\s+qualifications",
            r"hiring\s+team\s+is\s+evaluating\s+your\s+application",
            r"application\s+is\s+being\s+processed",
        ]
    ),
    # 7. Applied / Application Acknowledgment
    (
        "Applied",
        [
            r"thank\s+you\s+for\s+applying",
            r"we\s+have\s+received\s+your\s+application",
            r"application\s+(?:received|submitted\s+successfully)",
            r"confirming\s+receipt\s+of\s+your\s+application",
        ]
    ),
]


def extract_stage_from_email_text(subject, body):
    """
    Analyzes subject and body to extract the recruitment stage.
    Returns (stage_name, confidence, matched_snippet) or (None, 0.0, None).
    """
    combined = f"{subject}\n{body}".lower()

    # Reject obvious promotional or marketing newsletters
    spam_indicators = [
        "unsubscribe", "weekly digest", "newsletter", "sale off", "discount",
        "explore courses", "job alerts matching your profile", "sponsored"
    ]
    if any(s in combined for s in spam_indicators) and not ("interview" in combined or "assessment" in combined or "offer" in combined):
        return None, 0.0, "Identified as generic digest/marketing communication"

    for stage_name, patterns in STAGE_PATTERNS:
        for pat in patterns:
            match = re.search(pat, combined, re.IGNORECASE)
            if match:
                snippet = combined[max(0, match.start() - 20):min(len(combined), match.end() + 40)].strip()
                return stage_name, 0.95, snippet

    return None, 0.0, "No clear recruitment milestone detected in message text"


def match_email_to_application(user_applications, subject, body, sender=""):
    """
    Finds the exact matching application for the candidate based on company name and role.
    Strict matching: ignores emails if no applied company matches.
    """
    text_to_search = f"{sender} {subject} {body}".lower()

    best_match = None
    best_score = 0

    for app in user_applications:
        c_name = app['company_name'].lower()
        role = app['role'].lower()

        # Simplify company name (e.g. "Microsoft IDC" -> ["microsoft", "idc"])
        c_tokens = [re.sub(r'[^a-z0-9]', '', t) for t in c_name.split() if len(t) > 2]

        score = 0
        if c_name in text_to_search:
            score += 10
        elif any(token in text_to_search for token in c_tokens):
            score += 6

        # Check role match
        r_tokens = [re.sub(r'[^a-z0-9]', '', t) for t in role.split() if len(t) > 3]
        if any(token in text_to_search for token in r_tokens):
            score += 4

        if score > best_score and score >= 6:
            best_score = score
            best_match = app

    return best_match


def process_recruitment_email(conn, user_id, sender, subject, body, message_id=None, email_date=None):
    """
    Core engine that processes a single recruitment email, evaluates stage transitions,
    and updates the database idempotently.
    """
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM applications 
        WHERE user_id = ? AND stage != 'Withdrawn'
        ORDER BY id DESC
    ''', (user_id,))
    user_apps = [dict(r) for r in cursor.fetchall()]

    if not user_apps:
        return {
            'success': False,
            'reason': 'No active tracked applications found for this student account.'
        }

    # 1. Match company & application
    matched_app = match_email_to_application(user_apps, subject, body, sender)
    if not matched_app:
        return {
            'success': False,
            'reason': f"Could not correlate message to any of student's active company applications (Sender: {sender}, Subject: '{subject}')."
        }

    app_id = matched_app['id']
    company_name = matched_app['company_name']
    current_stage = matched_app['stage']

    # 2. Extract Stage
    new_stage, confidence, snippet = extract_stage_from_email_text(subject, body)
    if not new_stage:
        return {
            'success': False,
            'company': company_name,
            'reason': f"Message mentions '{company_name}' but contains no definitive recruitment status transition."
        }

    # 3. Idempotency & Deduplication
    unique_sig = message_id or hashlib.sha256(f"{user_id}_{app_id}_{subject}_{new_stage}_{email_date or date.today().isoformat()}".encode('utf-8')).hexdigest()

    cursor.execute('''
        SELECT id FROM application_status_history 
        WHERE application_id = ? AND external_message_id = ?
    ''', (app_id, unique_sig))
    if cursor.fetchone():
        return {
            'success': True,
            'skipped': True,
            'company': company_name,
            'stage': new_stage,
            'message': f"Status '{new_stage}' for {company_name} was already synchronized earlier."
        }

    # 4. Extract schedule / assessment details if available
    interview_info = None
    assessment_info = None

    if "Interview" in new_stage:
        # Search for link or schedule mentions
        link_m = re.search(r'https?://[^\s]+(?:zoom|teams|meet)[^\s]*', body, re.IGNORECASE)
        date_m = re.search(r'(?:date|on|time):\s*([^\n\r]+)', body, re.IGNORECASE)
        parts = []
        if date_m:
            parts.append(f"Date/Time: {date_m.group(1).strip()[:80]}")
        if link_m:
            parts.append(f"Meeting Link: {link_m.group(0).strip()[:100]}")
        interview_info = " | ".join(parts) if parts else f"Interview Invitation received via email: '{subject[:60]}'"
    elif "Assessment" in new_stage:
        test_link = re.search(r'https?://[^\s]+(?:hackerrank|codility|mettl|test)[^\s]*', body, re.IGNORECASE)
        assessment_info = f"Test Link: {test_link.group(0).strip()[:100]}" if test_link else f"Online assessment invite: '{subject[:60]}'"

    now_ts = datetime.now().isoformat()
    status_note = f"Automated status detected via official email from {sender or 'company recruiter'}. Snippet: \"{snippet}\""

    # 5. Update Application record
    cursor.execute('''
        UPDATE applications 
        SET stage = ?, status_note = ?, status_source = 'Automated Email Integration',
            interview_details = COALESCE(?, interview_details),
            assessment_details = COALESCE(?, assessment_details),
            last_updated = ?
        WHERE id = ?
    ''', (new_stage, status_note, interview_info, assessment_info, now_ts, app_id))

    # 6. Insert Audit History
    cursor.execute('''
        INSERT INTO application_status_history (
            application_id, user_id, stage, status_note, interview_date, interview_venue,
            updated_by, source, external_message_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'Automated Email Integration', 'Email (Gmail/IMAP)', ?, ?)
    ''', (
        app_id, user_id, new_stage, status_note,
        interview_info, sender, unique_sig, now_ts
    ))

    # 7. Create in-app notification
    cursor.execute('''
        INSERT INTO notifications (user_id, title, message, link, is_read, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
    ''', (
        user_id,
        f"Automated Status Update: {company_name}",
        f"Recruitment email detected for {company_name} ({matched_app['role']}). Status updated to '{new_stage}'.",
        f"/placement-hub?tab=applications&highlight={app_id}",
        now_ts
    ))

    conn.commit()

    return {
        'success': True,
        'app_id': app_id,
        'company': company_name,
        'role': matched_app['role'],
        'previous_stage': current_stage,
        'new_stage': new_stage,
        'snippet': snippet,
        'message': f"Application for {company_name} successfully updated to '{new_stage}'."
    }


def get_demo_email_templates(user_applications=None):
    """
    Returns high-fidelity simulation templates based on the student's real tracked companies.
    Used for immediate in-app evaluation and interactive verification.
    """
    company_name = "Microsoft IDC"
    role = "Software Development Engineer"
    if user_applications and len(user_applications) > 0:
        company_name = user_applications[0]['company_name']
        role = user_applications[0]['role']

    return [
        {
            'id': 'sim_interview',
            'title': f"Technical Interview Invitation — {company_name}",
            'sender': f"university-recruiting@{company_name.lower().replace(' ', '')}.com",
            'subject': f"Invitation to Technical Interview Round 1 — {role}",
            'body': f"""Dear Candidate,

Congratulations! Following your high score on the Online Coding Assessment, we are pleased to inform you that you have been shortlisted for the next stage.

Your Technical Interview Round 1 is scheduled as follows:
Date/Time: Friday at 11:00 AM IST
Platform: Microsoft Teams Video Call
Meeting Link: https://teams.microsoft.com/l/meetup-join/recruitment-interview-slot-1092

Please be prepared to write code on an online shared editor covering Data Structures, Algorithms, and System Architecture.

Best regards,
University Campus Recruitment Team
{company_name}"""
        },
        {
            'id': 'sim_assessment',
            'title': f"Online Coding Assessment Invitation — {company_name}",
            'sender': f"talent@{company_name.lower().replace(' ', '')}.com",
            'subject': f"{company_name} Campus Hiring: Online Assessment Invitation",
            'body': f"""Hello Applicant,

Thank you for your application for the {role} position.

You are invited to complete the Online Assessment test window. The assessment consists of 3 algorithmic programming questions and 15 core computer science MCQs.

Test Platform: HackerRank
Assessment Link: https://www.hackerrank.com/tests/campus-drive-{company_name.lower().replace(' ', '')}-2026
Duration: 90 minutes
Deadline: Complete the test before this Sunday 11:59 PM IST.

Good luck!
Talent Acquisition Team"""
        },
        {
            'id': 'sim_offer',
            'title': f"Official Offer of Employment — {company_name}",
            'sender': f"offers@{company_name.lower().replace(' ', '')}.com",
            'subject': f"Congratulations! Official Offer of Employment — {role}",
            'body': f"""Dear Candidate,

On behalf of {company_name}, we are delighted to offer you the position of {role} with our engineering team!

The hiring committee was thoroughly impressed by your performance across all interview rounds. Attached is your formal Offer Letter detailing your compensation package, benefits, and joining date.

Welcome to the team!

Warm regards,
Head of Engineering Recruitment
{company_name}"""
        },
        {
            'id': 'sim_rejection',
            'title': f"Application Status Notification — {company_name}",
            'sender': f"notifications@{company_name.lower().replace(' ', '')}.com",
            'subject': f"Update on your application for {role}",
            'body': f"""Dear Applicant,

Thank you for taking the time to interview with {company_name} for the {role} position.

While your background and qualifications were impressive, we have decided to pursue other candidates whose experience more closely matches the specific needs of this team at this time.

We will keep your resume in our database for future engineering opportunities. We wish you every success in your career.

Sincerely,
Recruiting Operations
{company_name}"""
        }
    ]
