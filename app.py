# app.py
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from collections import defaultdict, Counter
import os
import re

from config import config
from questions.main_questions import QUESTIONS
from questions.tie_breaker_questions import TIE_BREAKER_QUESTIONS

# -----------------------------
# App Initialization
# -----------------------------
def create_app():
    app = Flask(__name__)
    env = os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])
    return app

app = create_app()

# -----------------------------
# New aptitude set (target)
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
    session['total_questions'] = len(QUESTIONS)

# -----------------------------
# Utilities: text enrichment
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

# -----------------------------
# FIXED: Score Calculation
# -----------------------------
def calculate_scores(use_text_enrichment=False):
    """
    Returns (riasec_scores, aptitude_scores)
    Tie-breaker questions ALWAYS update RIASEC.
    Aptitudes ONLY from main questions.
    """
    riasec_scores = {'R':0,'I':0,'A':0,'S':0,'E':0,'C':0}
    aptitude_scores = Counter({a: 0 for a in NEW_APTITUDES})
    main_total = len(QUESTIONS)

    for qnum_key, selected in session.get('answers', {}).items():
        try:
            qnum = int(qnum_key)
        except:
            continue

        # Identify question
        if qnum <= main_total:
            question = next((q for q in QUESTIONS if q['number'] == qnum), None)
        else:
            question = next((q for q in TIE_BREAKER_QUESTIONS if q['number'] == qnum), None)

        if not question:
            continue
        if selected not in question.get('options', {}):
            continue

        option = question['options'][selected]

        # -------------------------
        # ALWAYS add RIASEC points
        # -------------------------
        riasec_code = option.get('riasec')
        if riasec_code in riasec_scores:
            riasec_scores[riasec_code] += 1

        # ----------------------------------------------------
        # Aptitudes ONLY for main questions (no tie-breakers)
        # ----------------------------------------------------
        if qnum <= main_total:
            option_apts = option.get('aptitudes', {}) or {}
            for old_key, score in option_apts.items():
                if old_key in OLD_TO_NEW_APT_MAP:
                    for new_key in OLD_TO_NEW_APT_MAP[old_key]:
                        aptitude_scores[new_key] += int(score)
                else:
                    if old_key in NEW_APTITUDES:
                        aptitude_scores[old_key] += int(score)

            # Optional text enrichment (main questions only)
            if use_text_enrichment:
                for field in ('explain','hint','job_text'):
                    if field in question:
                        boosts = enrich_from_text(question[field])
                        for k,v in boosts.items():
                            aptitude_scores[k] += v

    return riasec_scores, dict(aptitude_scores)

# -----------------------------
# Tie-breaker logic (unchanged)
# -----------------------------
def identify_tie_pairs(riasec_scores, delta):
    sorted_by_score = sorted(riasec_scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_by_score) < 3:
        return set()

    codes_order = [c for c,_ in sorted_by_score]
    scores = [s for _,s in sorted_by_score]

    pairs = set()
    first_score, second_score, third_score = scores[0], scores[1], scores[2]
    first_code, second_code, third_code = codes_order[0], codes_order[1], codes_order[2]

    if (first_score - second_score) < delta:
        pairs.add(f"{min(first_code,second_code)}-{max(first_code,second_code)}")

    if (second_score - third_score) < delta:
        pairs.add(f"{min(second_code,third_code)}-{max(second_code,third_code)}")

    third_place_codes = [c for c,s in sorted_by_score if s == third_score]
    if len(third_place_codes) > 1:
        for i in range(len(third_place_codes)):
            for j in range(i+1, len(third_place_codes)):
                a,b = sorted([third_place_codes[i], third_place_codes[j]])
                pairs.add(f"{a}-{b}")

    return pairs

def get_questions_for_pairs(pairs, already_asked):
    new_qs = []
    for pair in pairs:
        if pair in already_asked:
            continue
        matched = [q for q in TIE_BREAKER_QUESTIONS if q.get('pair') == pair]
        if matched:
            new_qs.extend(matched[:2])
    return new_qs

def needs_tie_breaker(riasec_scores):
    delta = app.config.get('TIE_BREAKER_DELTA', 1)
    pairs = identify_tie_pairs(riasec_scores, delta)
    return len(pairs) > 0

# -----------------------------
# Routes (fixed rendering for tie-breaker display index)
# -----------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_assessment():
    initialize_session()
    return redirect(url_for('assessment'))

@app.route('/assessment')
def assessment():
    if 'current_question' not in session:
        return redirect(url_for('index'))

    # MAIN PHASE
    if not session.get('tie_breaker_phase', False):
        if session['current_question'] <= len(QUESTIONS):
            q = QUESTIONS[session['current_question'] - 1]
            # main questions: display index and total are both plain main counts
            return render_template('assessment.html', question=q, phase="main",
                                   total_questions=len(QUESTIONS),
                                   current_question=session['current_question'])
        else:
            # main finished; evaluate tie-breakers
            riasec_scores,_ = calculate_scores()
            delta = app.config.get('TIE_BREAKER_DELTA', 1)
            pairs_needed = identify_tie_pairs(riasec_scores, delta)
            if pairs_needed:
                # Enter tie-breaker phase
                session['tie_breaker_phase'] = True
                already = set(session.get('tie_breaker_pairs_asked', []))
                new_qs = get_questions_for_pairs(pairs_needed, already)
                if not new_qs:
                    return redirect(url_for('submit_all_answers'))

                # assign tie questions and mark those pairs as asked
                session['tie_breaker_questions'] = new_qs
                to_mark = [p for p in pairs_needed if p not in already]
                session['tie_breaker_pairs_asked'] = list(set(session.get('tie_breaker_pairs_asked', [])) | set(to_mark))
                session['tie_breaker_answered'] = 0
                # total_questions is the COUNT of questions the user will answer (main + assigned tie-breakers)
                session['total_questions'] = len(QUESTIONS) + len(session['tie_breaker_questions'])
                return redirect(url_for('assessment'))
            else:
                return redirect(url_for('submit_all_answers'))

    # TIE-BREAKER PHASE
    tie_qs = session.get('tie_breaker_questions', [])
    answered = session.get('tie_breaker_answered', 0)
    if answered < len(tie_qs):
        q = tie_qs[answered]
        # IMPORTANT: display_index must be the sequence position (main_count + index within tie-breakers)
        display_index = len(QUESTIONS) + answered + 1
        return render_template('assessment.html', question=q, phase="tie_breaker",
                               total_questions=session.get('total_questions'),
                               current_question=display_index)
    else:
        # all assigned tie-breakers answered; check if further pairs need resolution
        riasec_scores,_ = calculate_scores()
        delta = app.config.get('TIE_BREAKER_DELTA', 1)
        pairs_needed = identify_tie_pairs(riasec_scores, delta)

        already = set(session.get('tie_breaker_pairs_asked', []))
        remaining = set(pairs_needed) - already

        if remaining:
            new_qs = get_questions_for_pairs(remaining, already)
            if not new_qs:
                return redirect(url_for('submit_all_answers'))
            session['tie_breaker_questions'].extend(new_qs)
            to_mark = [p for p in remaining if p not in already]
            session['tie_breaker_pairs_asked'] = list(already | set(to_mark))
            session['total_questions'] += len(new_qs)
            return redirect(url_for('assessment'))
        else:
            return redirect(url_for('submit_all_answers'))

@app.route('/save_answer', methods=['POST'])
def save_answer():
    if 'current_question' not in session:
        return jsonify({'success': False, 'redirect': url_for('index')})

    data = request.get_json(force=True)
    qnum = data.get('question_number')
    ans = data.get('answer')

    if qnum is None or ans is None:
        return jsonify({'success': False, 'msg': 'missing payload'})

    session['answers'][str(qnum)] = ans
    session.modified = True

    main_total = len(QUESTIONS)
    if qnum > main_total:
        qobj = next((q for q in TIE_BREAKER_QUESTIONS if q['number'] == qnum), None)
        if qobj:
            pair = qobj.get('pair')
            if pair and pair not in session.get('tie_breaker_pairs_asked', []):
                session['tie_breaker_pairs_asked'].append(pair)

    if not session.get('tie_breaker_phase', False):
        session['current_question'] = session.get('current_question', 1) + 1
    else:
        session['tie_breaker_answered'] = session.get('tie_breaker_answered', 0) + 1
        # update session current_question as sequence index (not the question.id)
        session['current_question'] = len(QUESTIONS) + session['tie_breaker_answered'] + 1

    return jsonify({'success': True, 'redirect': url_for('assessment')})

@app.route('/submit_all_answers')
def submit_all_answers():
    if 'answers' not in session or not session['answers']:
        return redirect(url_for('index'))
    return redirect(url_for('results'))

def resolve_riasec_code(riasec_scores):
    ordered = sorted(riasec_scores.items(), key=lambda x: (-x[1], x[0]))
    top3 = ordered[:3]
    return ''.join([c for c,_ in top3])

@app.route('/results')
def results():
    if 'answers' not in session or not session['answers']:
        return redirect(url_for('index'))

    riasec_scores, aptitude_scores = calculate_scores()

    top_riasec = sorted(riasec_scores.items(), key=lambda x:x[1], reverse=True)[:3]
    top_aptitudes = sorted(aptitude_scores.items(), key=lambda x:x[1], reverse=True)[:3]

    max_riasec_score = max(riasec_scores.values(), default=1)
    max_aptitude_score = max(aptitude_scores.values(), default=1)

    riasec_code = resolve_riasec_code(riasec_scores)

    return render_template('results.html',
        riasec_code=riasec_code,
        top_riasec=top_riasec,
        top_aptitudes=top_aptitudes,
        all_riasec_scores=riasec_scores,
        all_aptitude_scores=aptitude_scores,
        max_riasec_score=max_riasec_score,
        max_aptitude_score=max_aptitude_score
    )

@app.route('/restart')
def restart():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    if app.config.get('DEBUG', False):
        app.run(debug=True)
    else:
        port = int(os.getenv('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
