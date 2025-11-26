from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from collections import Counter
import os
import re
import random
from datetime import datetime

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

from config import config
from questions.main_questions import QUESTIONS
from questions.tie_breaker_questions import TIE_BREAKER_QUESTIONS

# -----------------------------
# Google Sheets config
# -----------------------------
CREDS_FILE = "/project-aradhya-dengre-7a134824493f.json"
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "R1"

# -----------------------------
# App Initialization
# -----------------------------
def create_app():
    app = Flask(__name__)
    env = os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])
    app.secret_key = os.urandom(24)
    return app

app = create_app()

# -----------------------------
# Aptitudes & Mapping
# -----------------------------
NEW_APTITUDES = [
    "Logical Reasoning", "Mechanical", "Creative", "Verbal Communication",
    "Numerical", "Social/Helping", "Leadership/Persuasion", "Digital/Computer",
    "Organizing/Structuring", "Writing/Expression", "Scientific", "Spatial/Design"
]

OLD_TO_NEW_APT_MAP = {
    "Analytical": ["Logical Reasoning"],
    "Technical": ["Mechanical"],
    "Spatial": ["Spatial/Design"],
    "Verbal": ["Verbal Communication"],
    "Creative": ["Creative"],
}

KEYWORD_TO_APTS = {
    r"mechanic|machin|tool|repair|operate|equipment|assembly": ["Mechanical", "Spatial/Design"],
    r"design|creative|art|visual|graphic|illustrat|style|compose": ["Creative", "Writing/Expression", "Spatial/Design"],
    r"analy|research|study|evaluate|experiment|data|statistic": ["Logical Reasoning", "Scientific", "Numerical"],
    r"teach|help|support|counsel|mentor|coach": ["Social/Helping", "Verbal Communication"],
    r"lead|manage|supervis|coordinate|direct|influence|persuad": ["Leadership/Persuasion", "Organizing/Structuring"],
    r"software|digital|computer|it|program|code|data entry|excel": ["Digital/Computer", "Organizing/Structuring"],
    r"write|document|report|communicat|present": ["Writing/Expression", "Verbal Communication"],
    r"budget|finance|cost|account|number|math|calculate": ["Numerical"],
}

# -----------------------------
# Session Initialization
# -----------------------------
def initialize_session():
    session['current_question'] = 1
    session['answers'] = {}
    session['riasec_scores'] = {'R':0,'I':0,'A':0,'S':0,'E':0,'C':0}
    session['tie_breaker_phase'] = False
    session['tie_breaker_questions'] = []
    session['tie_breaker_pairs_asked'] = []
    session['tie_breaker_answered'] = 0
    session['shuffled_questions'] = random.sample(QUESTIONS, len(QUESTIONS))
    session['total_questions'] = len(QUESTIONS)

# -----------------------------
# Score Calculation
# -----------------------------
def enrich_from_text(text):
    boosts = Counter()
    if not text or not isinstance(text, str):
        return boosts
    txt = text.lower()
    for patt, apt_list in KEYWORD_TO_APTS.items():
        if re.search(patt, txt):
            for a in apt_list:
                boosts[a] += 1
    return boosts

def calculate_scores(use_text_enrichment=False):
    riasec_scores = {'R':0,'I':0,'A':0,'S':0,'E':0,'C':0}
    aptitude_scores = Counter({a: 0 for a in NEW_APTITUDES})
    main_total = len(QUESTIONS)

    for qnum_key, selected in session.get('answers', {}).items():
        try:
            qnum = int(qnum_key)
        except:
            continue

        if qnum <= main_total:
            question = next((q for q in QUESTIONS if q['number'] == qnum), None)
        else:
            question = next((q for q in TIE_BREAKER_QUESTIONS if q['number'] == qnum), None)

        if not question or selected not in question.get('options', {}):
            continue

        option = question['options'][selected]
        q_weight = question.get('weight', 1)

        # RIASEC
        riasec_code = option.get('riasec')
        if riasec_code in riasec_scores:
            riasec_scores[riasec_code] += q_weight

        # Aptitudes
        if qnum <= main_total:
            option_apts = option.get('aptitudes', {}) or {}
            for old_key, score in option_apts.items():
                if old_key in OLD_TO_NEW_APT_MAP:
                    for new_key in OLD_TO_NEW_APT_MAP[old_key]:
                        aptitude_scores[new_key] += int(score) * q_weight
                else:
                    if old_key in NEW_APTITUDES:
                        aptitude_scores[old_key] += int(score) * q_weight

            if use_text_enrichment:
                for field in ('explain','hint','job_text'):
                    if field in question:
                        boosts = enrich_from_text(question[field])
                        for k,v in boosts.items():
                            aptitude_scores[k] += v

    return riasec_scores, dict(aptitude_scores)

# -----------------------------
# Tie-breaker Logic (FINAL)
# -----------------------------
MAX_TIE_BREAKER_QS = 3
RIASEC_ORDER = ['R','I','A','S','E','C']

def sort_pairs_resolver_style(pairs):
    def key_func(pair):
        a, b = pair.split('-')
        return (RIASEC_ORDER.index(a), RIASEC_ORDER.index(b))
    return sorted(pairs, key=key_func)

def identify_tie_pairs(riasec_scores):

    sorted_scores = sorted(
        riasec_scores.items(),
        key=lambda x: (-x[1], RIASEC_ORDER.index(x[0]))
    )

    pairs = set()

    # Top-1 vs Top-2 (keep original rule)
    top1_code, top1_score = sorted_scores[0]
    top2_code, top2_score = sorted_scores[1]

    if abs(top1_score - top2_score) < 2:
        pairs.add(f"{min(top1_code, top2_code)}-{max(top1_code, top2_code)}")

    # NEW — Top-2 vs Top-3 ONLY
    if len(sorted_scores) >= 3:
        top3_code, top3_score = sorted_scores[2]
        if abs(top2_score - top3_score) < 2:
            pairs.add(f"{min(top2_code, top3_code)}-{max(top2_code, top3_code)}")

    return pairs

def get_questions_for_pairs(pairs, already_asked):
    new_qs = []
    for pair in pairs:
        if pair in already_asked:
            continue
        matched = [q for q in TIE_BREAKER_QUESTIONS if q.get('pair') == pair]
        new_qs.extend(matched[:MAX_TIE_BREAKER_QS])
    return new_qs

# -----------------------------
# Routes
# -----------------------------
@app.route('/')
def index():
    return redirect(url_for('basic_info'))

@app.route('/basic_info')
def basic_info():
    return render_template('basic_info.html')

@app.route('/save_basic_info', methods=['POST'])
def save_basic_info():
    session['user_info'] = {
        'name': request.form.get('name', 'Anonymous'),
        'occupation': request.form.get('occupation', ''),
        'education': request.form.get('education', '')
    }
    initialize_session()
    return redirect(url_for('assessment'))

@app.route('/assessment')
def assessment():

    if 'user_info' not in session:
        return redirect(url_for('basic_info'))

    # ---- MAIN PHASE ----
    if not session.get('tie_breaker_phase', False):

        if session['current_question'] <= len(session['shuffled_questions']):
            q = session['shuffled_questions'][session['current_question'] - 1]
            return render_template(
                'assessment.html',
                question=q,
                phase="main",
                total_questions=len(session['shuffled_questions']),
                current_question=session['current_question']
            )

        # main finished — check tie-break needs
        riasec_scores,_ = calculate_scores()
        pairs_needed = identify_tie_pairs(riasec_scores)

        already = set(session.get('tie_breaker_pairs_asked', []))
        remaining_pairs = pairs_needed - already

        if remaining_pairs:
            session['tie_breaker_phase'] = True

            # FIX — SORT PAIRS ALWAYS
            sorted_pairs = sort_pairs_resolver_style(remaining_pairs)

            session['tie_breaker_pairs_asked'].extend(sorted_pairs)

            new_qs = get_questions_for_pairs(sorted_pairs, already)
            if not new_qs:
                return redirect(url_for('submit_all_answers'))

            session['tie_breaker_questions'] = new_qs
            session['tie_breaker_answered'] = 0
            session['total_questions'] = len(session['shuffled_questions']) + len(new_qs)

            return redirect(url_for('assessment'))

        return redirect(url_for('submit_all_answers'))

    # ---- TIE BREAKER PHASE ----
    tie_qs = session.get('tie_breaker_questions', [])
    answered = session.get('tie_breaker_answered', 0)

    if answered < len(tie_qs):
        q = tie_qs[answered]
        display_index = len(session['shuffled_questions']) + answered + 1
        return render_template(
            'assessment.html',
            question=q,
            phase="tie_breaker",
            total_questions=session.get('total_questions'),
            current_question=display_index
        )

    return redirect(url_for('submit_all_answers'))

@app.route('/save_answer', methods=['POST'])
def save_answer():

    if 'current_question' not in session:
        return jsonify({'success': False, 'redirect': url_for('basic_info')})

    data = request.get_json(force=True)
    qnum = data.get('question_number')
    ans = data.get('answer')

    session['answers'][str(qnum)] = ans
    session.modified = True

    if not session.get('tie_breaker_phase', False):
        session['current_question'] += 1
    else:
        session['tie_breaker_answered'] += 1
        session['current_question'] = (
            len(session['shuffled_questions']) +
            session['tie_breaker_answered'] + 1
        )

    riasec_scores, _ = calculate_scores()
    session['riasec_scores'] = riasec_scores

    return jsonify({'success': True, 'redirect': url_for('assessment')})

@app.route('/submit_all_answers')
def submit_all_answers():
    if 'answers' not in session or not session['answers']:
        return redirect(url_for('basic_info'))
    return redirect(url_for('results'))

# -----------------------------
# RIASEC Resolver (unchanged)
# -----------------------------
def resolve_riasec_code(riasec_scores):

    # Stable sorted list
    sorted_scores = sorted(
        riasec_scores.items(),
        key=lambda x: (-x[1], RIASEC_ORDER.index(x[0]))
    )

    # Just take first 3 after sorting
    top3 = [code for code, score in sorted_scores[:3]]

    return ''.join(top3)


# -----------------------------
# Google Sheets Saving
# -----------------------------
def save_to_google_sheet(riasec_code, riasec_scores, aptitude_scores, user_info=None):
    if not os.path.exists(CREDS_FILE):
        raise FileNotFoundError(f"{CREDS_FILE} not found")

    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
    client = gspread.authorize(creds)

    try:
        sheet = client.open(SHEET_NAME).sheet1
    except Exception as e:
        print("Error opening sheet:", e)
        raise

    # ----------- BUILD ROW ACCORDING TO YOUR SHEET -----------
    row = []

    # A-D Basic info
    row.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    row.append(user_info.get('name', 'Anonymous'))
    row.append(user_info.get('occupation', ''))
    row.append(user_info.get('education', ''))

    # E Final RIASEC Code
    row.append(riasec_code)

    # F-K R, I, A, S, E, C scores
    for code in RIASEC_ORDER:   # ['R','I','A','S','E','C']
        row.append(riasec_scores.get(code, 0))

    # L-W 12 aptitude scores (same order every time)
    ordered_apts = [
        "Logical Reasoning", "Mechanical", "Creative", "Verbal Communication",
        "Numerical", "Social/Helping", "Leadership/Persuasion", "Digital/Computer",
        "Organizing/Structuring", "Writing/Expression", "Scientific", "Spatial/Design"
    ]

    for apt in ordered_apts:
        row.append(aptitude_scores.get(apt, 0))

    # ---------------------------------------------------------

    # FINAL SAFETY CHECK
    if len(row) != 23:
        print("ERROR: Expected 23 columns but got", len(row))
        print("ROW CONTENT:", row)

    # Save in Google Sheet
    sheet.append_row(row)
    return True


# -----------------------------
# Results Page
# -----------------------------
@app.route('/results')
def results():
    if 'answers' not in session or not session['answers']:
        return redirect(url_for('index'))

    riasec_scores, aptitude_scores = calculate_scores()
    riasec_code = resolve_riasec_code(riasec_scores)

    session['last_riasec_code'] = riasec_code
    session['last_riasec_scores'] = riasec_scores
    session['last_aptitude_scores'] = aptitude_scores

    top_riasec = sorted(riasec_scores.items(), key=lambda x:x[1], reverse=True)[:3]
    top_aptitudes = sorted(aptitude_scores.items(), key=lambda x:x[1], reverse=True)[:3]

    return render_template(
        'results.html',
        riasec_code=riasec_code,
        top_riasec=top_riasec,
        top_aptitudes=top_aptitudes,
        all_riasec_scores=riasec_scores,
        all_aptitude_scores=aptitude_scores,
        max_riasec_score=max(riasec_scores.values()) if riasec_scores else 1,
        max_aptitude_score=max(aptitude_scores.values()) if aptitude_scores else 1
    )

@app.route('/save_results', methods=['POST'])
def save_results():
    if 'last_riasec_code' not in session:
        return redirect(url_for('results'))

    try:
        save_to_google_sheet(
            session['last_riasec_code'],
            session['last_riasec_scores'],
            session['last_aptitude_scores'],
            session.get('user_info')
        )
        return jsonify({'success': True, 'msg': 'Results saved successfully!'})
    except FileNotFoundError:
        return jsonify({'success': False, 'msg': 'Service account file missing. Results not saved.'})
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)})

@app.route('/restart')
def restart():
    session.clear()
    return redirect(url_for('basic_info'))

# -----------------------------
# Main
# -----------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
