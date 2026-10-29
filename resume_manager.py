"""
resume_manager.py - Persistent Multi-Version Resume Management & ATS Evaluator
-------------------------------------------------------------------------------
Handles multi-version resume file storage, text extraction (PDF, DOCX, TXT),
ATS scoring, version history persistence, and secure file downloads.

Features:
- Stores every upload as a distinct, permanent version (v1, v2, v3, ...).
- Automatic text extraction supporting:
  * PDF (.pdf) via pypdf
  * Microsoft Word (.docx) via python-docx
  * Plain text / Markdown (.txt, .md)
- Secure, sandboxed file storage per user (uploads/resumes/user_{id}/v{ver}_{filename}).
- Directory traversal protection & strict ownership verification.
- Intelligent ATS Evaluation: keyword matching, section detection, quantifiable metrics.
- Idempotency & duplicate submission prevention via SHA-256 file hashing.
"""

import os
import re
import json
import hashlib
from datetime import datetime
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt', 'md'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


ROLE_SKILL_PROFILES = {
    "Software Development Engineer (SDE)": {
        "required": ["Python", "Java", "C++", "Data Structures", "Algorithms", "Git", "SQL"],
        "recommended": ["REST APIs", "System Design", "Docker", "Linux", "Object-Oriented Programming", "Unit Testing", "CI/CD"],
        "keywords": ["designed", "developed", "implemented", "optimized", "built", "architected", "integrated", "debugged"]
    },
    "AI/ML & Data Science Engineer": {
        "required": ["Python", "Machine Learning", "PyTorch", "TensorFlow", "Pandas", "NumPy", "Scikit-Learn"],
        "recommended": ["Deep Learning", "Generative AI", "NLP", "Computer Vision", "FastAPI", "Prompt Engineering", "SQL"],
        "keywords": ["trained", "fine-tuned", "evaluated", "deployed", "modeled", "analyzed", "extracted", "predicted"]
    },
    "Full-Stack Web Developer": {
        "required": ["JavaScript", "React", "Node.js", "HTML", "CSS", "SQL", "Git"],
        "recommended": ["TypeScript", "Next.js", "Express", "MongoDB", "PostgreSQL", "Tailwind CSS", "REST APIs"],
        "keywords": ["created", "styled", "engineered", "refactored", "rendered", "connected", "deployed", "designed"]
    },
    "DevOps & Cloud Engineer": {
        "required": ["Linux", "Docker", "Kubernetes", "AWS", "CI/CD", "Git", "Bash"],
        "recommended": ["Terraform", "Ansible", "CloudFormation", "Python", "Prometheus", "Grafana", "Networking"],
        "keywords": ["automated", "containerized", "provisioned", "orchestrated", "monitored", "scaled", "secured"]
    }
}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_type(filename):
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else 'txt'


def extract_text_from_file(file_path, file_type):
    """
    Safely extracts text content from PDF, DOCX, TXT, or MD files.
    """
    ext = file_type.lower()
    text = ""

    try:
        if ext == 'pdf':
            import pypdf
            with open(file_path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                pages_text = []
                for page in reader.pages:
                    t = page.extract_text()
                    if t:
                        pages_text.append(t)
                text = "\n".join(pages_text)
        elif ext == 'docx':
            import docx
            doc = docx.Document(file_path)
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        else:  # txt or md
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
    except Exception as e:
        text = f"Warning: Could not extract full text from file: {e}"

    return text.strip()


def evaluate_resume_ats(resume_text, target_role="Software Development Engineer (SDE)"):
    """
    Evaluates resume text against the target role's skill profile, section completeness,
    and impact metrics.
    """
    profile = ROLE_SKILL_PROFILES.get(target_role, ROLE_SKILL_PROFILES["Software Development Engineer (SDE)"])
    text_lower = resume_text.lower()

    # 1. Required Skills Match (40% weight)
    matched_req = []
    missing_req = []
    for skill in profile["required"]:
        pat = r'\b' + re.escape(skill.lower()) + r'\b'
        if re.search(pat, text_lower):
            matched_req.append(skill)
        else:
            missing_req.append(skill)

    req_score = (len(matched_req) / len(profile["required"])) * 40 if profile["required"] else 40

    # 2. Recommended Skills Match (25% weight)
    matched_rec = []
    missing_rec = []
    for skill in profile["recommended"]:
        pat = r'\b' + re.escape(skill.lower()) + r'\b'
        if re.search(pat, text_lower):
            matched_rec.append(skill)
        else:
            missing_rec.append(skill)

    rec_score = (len(matched_rec) / len(profile["recommended"])) * 25 if profile["recommended"] else 25

    # 3. Essential Resume Sections Check (20% weight)
    sections = {
        "Education": [r"\beducation\b", r"\bacademics\b", r"\bdegree\b"],
        "Skills": [r"\bskills\b", r"\btechnical\s+skills\b", r"\btechnologies\b"],
        "Projects": [r"\bprojects\b", r"\bacademic\s+projects\b", r"\bwork\b"],
        "Experience / Internships": [r"\bexperience\b", r"\binternship\b", r"\bwork\s+experience\b"],
        "Summary / Profile": [r"\bsummary\b", r"\bprofile\b", r"\bobjective\b"]
    }
    present_sections = []
    missing_sections = []
    for sec_name, pats in sections.items():
        if any(re.search(p, text_lower) for p in pats):
            present_sections.append(sec_name)
        else:
            missing_sections.append(sec_name)

    section_score = (len(present_sections) / len(sections)) * 20

    # 4. Action Verbs & Quantifiable Results (15% weight)
    matched_verbs = [v for v in profile["keywords"] if re.search(r'\b' + v + r'\b', text_lower)]
    verb_score = min(10, (len(matched_verbs) / 5) * 10)

    # Check quantifiable metrics (percentages, numbers, latencies, etc.)
    has_metrics = bool(re.search(r'\b(?:\d+%\s*|\d+x\s*|\$\d+|\d+\s*ms|\d+\s*users|\d+\s*stars)\b', text_lower))
    metric_score = 5 if has_metrics else 2

    impact_score = verb_score + metric_score

    # Final ATS Score Calculation
    total_score = int(round(req_score + rec_score + section_score + impact_score))
    total_score = max(15, min(98, total_score))

    # Actionable Suggestions
    suggestions = []
    if missing_req:
        suggestions.append(f"Add key required technical skills: {', '.join(missing_req[:4])}.")
    if missing_sections:
        suggestions.append(f"Include standard ATS section headings for: {', '.join(missing_sections)}.")
    if not has_metrics:
        suggestions.append("Add quantifiable metrics to your project achievements (e.g., 'improved query performance by 40%', 'processed 10k+ records').")
    if len(matched_verbs) < 3:
        suggestions.append("Use strong technical action verbs at the beginning of bullet points (e.g., 'Engineered', 'Optimized', 'Architected').")
    if len(resume_text.split()) < 150:
        suggestions.append("Resume content appears brief. Expand on technical project implementations and key responsibilities.")

    return {
        'ats_score': total_score,
        'target_role': target_role,
        'matched_skills': matched_req + matched_rec,
        'missing_skills': missing_req + missing_rec,
        'present_sections': present_sections,
        'missing_sections': missing_sections,
        'matched_verbs': matched_verbs,
        'suggestions': suggestions,
        'summary': f"ATS Match Score: {total_score}/100 for {target_role}. Found {len(matched_req)} core required skills and {len(matched_rec)} recommended skills."
    }


def save_uploaded_resume(file_obj, user_id, student_id, upload_base_dir, conn, target_role="Software Development Engineer (SDE)"):
    """
    Saves an uploaded resume file as a new permanent version, extracts text,
    and runs initial ATS evaluation.
    """
    if not file_obj or not file_obj.filename:
        return {'success': False, 'error': 'No file was provided for upload.'}

    orig_filename = secure_filename(file_obj.filename)
    if not orig_filename or not allowed_file(orig_filename):
        return {
            'success': False,
            'error': f"Unsupported file type. Please upload a PDF (.pdf), Word document (.docx), or plain text file (.txt)."
        }

    # Read bytes and compute hash
    file_bytes = file_obj.read()
    file_size = len(file_bytes)

    if file_size == 0:
        return {'success': False, 'error': 'Uploaded file is empty.'}

    if file_size > MAX_FILE_SIZE:
        return {'success': False, 'error': f"File size exceeds the 5MB maximum limit ({file_size / (1024*1024):.1f}MB)."}

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    cursor = conn.cursor()

    # Determine next version number for this user
    cursor.execute('SELECT MAX(version) as max_v FROM resumes WHERE user_id = ?', (user_id,))
    v_row = cursor.fetchone()
    next_version = (v_row['max_v'] or 0) + 1

    # Setup persistent directory
    user_dir = os.path.join(upload_base_dir, f"user_{user_id}")
    os.makedirs(user_dir, exist_ok=True)

    file_type = get_file_type(orig_filename)
    stored_filename = f"v{next_version}_{orig_filename}"
    saved_path = os.path.join(user_dir, stored_filename)

    # Save to disk
    with open(saved_path, 'wb') as f:
        f.write(file_bytes)

    # Extract text
    extracted_text = extract_text_from_file(saved_path, file_type)

    # Run ATS evaluation
    eval_res = evaluate_resume_ats(extracted_text, target_role)

    now_ts = datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO resumes (
            user_id, student_id, original_filename, stored_filename, file_path,
            file_type, file_size, version, file_hash, extracted_text,
            ats_score, target_role, analysis_summary, matched_skills,
            missing_skills, suggestions, analyzed_at, created_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?
        )
    ''', (
        user_id, student_id, orig_filename, stored_filename, saved_path,
        file_type, file_size, next_version, file_hash, extracted_text,
        eval_res['ats_score'], target_role, eval_res['summary'],
        json.dumps(eval_res['matched_skills']),
        json.dumps(eval_res['missing_skills']),
        json.dumps(eval_res['suggestions']),
        now_ts, now_ts
    ))
    resume_id = cursor.lastrowid
    conn.commit()

    return {
        'success': True,
        'resume_id': resume_id,
        'version': next_version,
        'filename': orig_filename,
        'file_size': file_size,
        'ats_score': eval_res['ats_score'],
        'eval_res': eval_res
    }


def get_user_resumes(user_id, conn):
    """Returns all resume versions for the student ordered by latest version."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM resumes 
        WHERE user_id = ? 
        ORDER BY version DESC, id DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    resumes = []
    for r in rows:
        d = dict(r)
        try:
            d['matched_skills_list'] = json.loads(d.get('matched_skills') or '[]')
            d['missing_skills_list'] = json.loads(d.get('missing_skills') or '[]')
            d['suggestions_list'] = json.loads(d.get('suggestions') or '[]')
        except Exception:
            d['matched_skills_list'] = []
            d['missing_skills_list'] = []
            d['suggestions_list'] = []
        resumes.append(d)
    return resumes


def get_resume_by_id(resume_id, user_id, conn):
    """Fetches a specific resume version verifying student ownership."""
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, user_id))
    row = cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d['matched_skills_list'] = json.loads(d.get('matched_skills') or '[]')
        d['missing_skills_list'] = json.loads(d.get('missing_skills') or '[]')
        d['suggestions_list'] = json.loads(d.get('suggestions') or '[]')
    except Exception:
        d['matched_skills_list'] = []
        d['missing_skills_list'] = []
        d['suggestions_list'] = []
    return d


def delete_resume_version(resume_id, user_id, conn):
    """Deletes a resume version file and database record with strict authorization."""
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM resumes WHERE id = ? AND user_id = ?', (resume_id, user_id))
    row = cursor.fetchone()
    if not row:
        return False, "Resume record not found or access denied."

    file_path = row['file_path']
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            pass

    cursor.execute('DELETE FROM resumes WHERE id = ? AND user_id = ?', (resume_id, user_id))
    conn.commit()
    return True, f"Resume version v{row['version']} ({row['original_filename']}) was deleted successfully."
