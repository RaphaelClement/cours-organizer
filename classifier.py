import re
import os
import unicodedata
from typing import Dict, Any, Optional, Tuple, List

from tree_indexer import scan_target_tree

NON_COURSE_KEYWORDS = [
    "lettre de motivation", "lettre de motiv", "motiv", "curriculum vitae", "relevé de notes", "releve de notes",
    "reinscription", "réinscription", "recette", "menu course", "habitude tracker",
    "thune", "banque", "facture", "fiche de paie", "bulletin de paie", "stage_1404",
    "parsec", "capture lite", "cisco packet tracer", "antigravity", "gestionnaire de mots de passe",
    "capgemini", "internship agreement", "convention de stage", "relevé_notes", "releve_notes",
    "notice-maj", "candidature", "bulletin", "attestation", "recapitulatif"
]

SUBJECT_ALIASES = {
    "Sécurité": ["securite", "sécurité", "cyber", "cybersécurité", "cybersecurite", "dmsécu", "dmsecu"],
    "SI": ["système d'information", "systèmes d'information", "algebre relationnelle", "optimisation logique", "fragmentation"],
    "TR": ["traitements répartis", "traitements repartis", "rabbitmq", "amqp", "rpc"],
    "ESE": ["gérer les coûts", "gestion des coûts", "couts", "coûts"],
    "FSQTEL": ["fsqtel", "qualité télécoms"],
    "Ing. Liaison": ["ing. liaison", "liaison", "liaisons optiques", "faisceaux"],
    "Management ITIL": ["itil", "management itil", "gouvernance itil"],
    "Anglais": ["anglais", "english", "toeic"],
    "Espagnol": ["espagnol", "spanish", "gramática"],
    "Adm Système Linux": ["adm système linux", "linux", "iscsi", "nfs", "systemd"],
    "Adm Système Windows": ["adm système windows", "windows server", "active directory", "wsus", "authentifications chez microsoft"],
    "Informatique Python": ["python", "algo python", "tp python"],
    "Interconnexion": ["interconnexion", "routage", "ospf", "bgp"],
    "Gestion Réseaux": ["gestion de réseaux", "gestion de reseaux", "gestion réseaux", "gestion reseaux", "snmp"]
}

def normalize_text(text: str) -> str:
    """Normalise une chaîne (sans accents, minuscules, espaces propres)."""
    nfkd = unicodedata.normalize('NFKD', text)
    cleaned = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return cleaned.lower()

def clean_theme_title(text: str) -> str:
    """Nettoie un texte pour en faire un thème propre pour le nom de fichier."""
    text = re.sub(r'[\\/*?:"<>|]', '', text)
    text = text.replace('\n', ' ').strip()
    words = text.split()
    return " ".join(words[:6])

def identify_document(file_meta: Dict[str, Any], extracted: Dict[str, Any], tree: Dict[str, Any]) -> Dict[str, Any]:
    filename = file_meta.get("name", "")
    ext = file_meta.get("extension", "").lower()
    text = (extracted.get("text", "") or "")
    combined_raw = f"{filename} {text}"
    combined_norm = normalize_text(combined_raw)
    filename_norm = normalize_text(filename)

    # 1. Vérification stricte des filtres non-cours
    if filename_norm.startswith("cv_") or filename_norm.startswith("cv.") or filename_norm.startswith("cv-") or re.search(r'\bcv\b', filename_norm):
        return {
            "file": filename,
            "is_course": False,
            "confidence": "HIGH",
            "ignore_reason": "Curriculum Vitae (CV) personnel",
            "action": "IGNORE"
        }

    for kw in NON_COURSE_KEYWORDS:
        kw_norm = normalize_text(kw)
        if kw_norm in filename_norm or kw_norm in combined_norm[:500]:
            return {
                "file": filename,
                "is_course": False,
                "confidence": "HIGH",
                "ignore_reason": f"Fichier personnel/administratif ou non académique ('{kw}')",
                "action": "IGNORE"
            }

    # 2. Détection du type de document
    doc_type = "Cours"
    num = ""
    theme = ""

    if "annale" in filename_norm or re.search(r'\b(annale|examen|partiel|devoir surveille|ds)\b', combined_norm):
        first_line = (text.split('\n')[0] if text else "").lower()
        if any(w in filename_norm for w in ["reponse", "corrige", "correction", "solution"]) or any(w in first_line for w in ["correction", "corrige", "reponse"]):
            doc_type = "Correction Annale"
        else:
            doc_type = "Annale"
    elif filename_norm.startswith("dm") or re.search(r'\b(dm|devoir maison)\b', combined_norm):
        doc_type = "DM"
    elif re.search(r'\b(td|travaux diriges)\b', combined_norm):
        doc_type = "TD"
        m = re.search(r'\btd\s*([0-9]+)\b', combined_norm)
        if m:
            num = m.group(1)
    elif re.search(r'\b(tp|travaux pratiques)\b', combined_norm):
        doc_type = "TP"
        m = re.search(r'\btp\s*([0-9]+)\b', combined_norm)
        if m:
            num = m.group(1)
    elif ext in [".gdoc", ".odt"] and ("prise de notes" in combined_norm or "document sans titre" in filename_norm or len(text) < 500):
        doc_type = "Prise de notes"
    elif ext in [".pptx", ".gslides"] or "slide" in combined_norm or "presentation" in combined_norm:
        doc_type = "Présentation"
    else:
        m = re.search(r'\bcours\s*([0-9]+)\b', combined_norm)
        if m:
            doc_type = "Cours"
            num = m.group(1)

    # 3. Détection de l'année/semestre explicite dans le texte
    detected_sem = None
    detected_year = None
    if re.search(r'\b(upssitech\s*3a|5a|s9)\b', combined_norm):
        detected_sem = "S9"
        detected_year = "5A"
    elif re.search(r'\b(upssitech\s*2a|4a|s8)\b', combined_norm):
        detected_sem = "S8"
        detected_year = "4A"
    elif re.search(r'\b(s7)\b', combined_norm):
        detected_sem = "S7"
        detected_year = "4A"
    elif re.search(r'\b(upssitech\s*1a|3a|s5)\b', combined_norm):
        detected_sem = "S5"
        detected_year = "3A"
    elif re.search(r'\b(s6)\b', combined_norm):
        detected_sem = "S6"
        detected_year = "3A"

    # 4. Marqueur académique obligatoire (évite les livres, articles, vidéos hors-cours)
    has_academic_marker = any(re.search(rf'\b{w}\b', combined_norm) for w in [
        "upssitech", "cours", "td", "tp", "dm", "examen", "partiel", "devoir", "polycopie",
        "chapitre", "seance", "professeur", "enseignant", "universite", "annale", "stri", "master"
    ])
    if not has_academic_marker and not any(w in filename_norm for w in ["cours", "td", "tp", "dm", "annale", "partiel"]):
        return {
            "file": filename,
            "is_course": False,
            "confidence": "HIGH",
            "ignore_reason": "Aucun marqueur académique (cours/TD/TP/examen/devoir)",
            "action": "IGNORE"
        }

    # 5. Détection de la matière
    matched_subject = None
    best_match_len = 0

    # Test avec les alias explicites d'abord (plus précis)
    for standard_name, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_text(alias)
            if re.search(rf'\b{re.escape(alias_norm)}\b', combined_norm):
                # Trouver où se situe cette matière dans l'arbre
                if standard_name in tree.get("all_subjects", {}):
                    candidates = tree["all_subjects"][standard_name]
                    # Si un semestre a été détecté, privilégier ce semestre
                    chosen = None
                    if detected_sem:
                        for cand in candidates:
                            if cand["semester"] == detected_sem:
                                chosen = cand
                                break
                    if not chosen:
                        chosen = candidates[0]
                    matched_subject = (chosen["semester"], standard_name, chosen["year"])
                    best_match_len = len(alias_norm)
                    break
        if matched_subject:
            break

    # Recherche directe dans tous les sujets réels de l'arbre
    if not matched_subject:
        # Trier par longueur décroissante pour matcher les noms longs d'abord ("Gestion Réseaux" avant "Réseaux")
        sorted_subs = sorted(tree.get("all_subjects", {}).keys(), key=lambda s: len(s), reverse=True)
        for sub_name in sorted_subs:
            sub_norm = normalize_text(sub_name)
            # Pour les noms courts (<= 3 lettres comme SI, TR, ESE), exiger des frontières de mot
            if len(sub_norm) <= 3:
                pattern = rf'\b{re.escape(sub_norm)}\b'
            else:
                pattern = rf'{re.escape(sub_norm)}'
            
            if re.search(pattern, combined_norm):
                candidates = tree["all_subjects"][sub_name]
                chosen = None
                if detected_sem:
                    for cand in candidates:
                        if cand["semester"] == detected_sem:
                            chosen = cand
                            break
                if not chosen:
                    chosen = candidates[0]
                matched_subject = (chosen["semester"], sub_name, chosen["year"])
                break

    if not matched_subject:
        return {
            "file": filename,
            "is_course": False,
            "confidence": "LOW",
            "ignore_reason": "Aucune matière académique connue détectée dans le document",
            "action": "IGNORE"
        }

    sem_code, subject_name, year = matched_subject

    # 5. Détection du professeur (initiales)
    known_teachers = tree.get("semesters", {}).get(sem_code, {}).get("subjects", {}).get(subject_name, {}).get("teachers", [])
    matched_teacher = None

    for t in known_teachers:
        if re.search(rf'\b{re.escape(t)}\b', combined_raw[:1500]):
            matched_teacher = t
            break

    if not matched_teacher and len(known_teachers) == 1:
        matched_teacher = known_teachers[0]

    # 6. Détermination du thème
    name_clean = os.path.splitext(filename)[0]
    name_clean = re.sub(r'^(cours|td|tp|dm|annale)[\s0-9_-]*', '', name_clean, flags=re.IGNORECASE).strip(' -_')
    if name_clean and len(name_clean) > 3 and normalize_text(name_clean) not in ["document sans titre", "prise de notes"]:
        theme = name_clean
    else:
        lines = [line.strip() for line in text.split('\n') if len(line.strip()) > 3]
        for l in lines[:6]:
            l_norm = normalize_text(l)
            if not any(k in l_norm for k in ["upssitech", "universite", "duree", "examen", "cours", "professeur", "page", "mi rt"]):
                theme = clean_theme_title(l)
                break
        if not theme:
            theme = subject_name

    # 7. Nom de fichier standardisé
    if doc_type == "Annale":
        year_match = re.search(r'\b(20[12][0-9])\b', combined_norm)
        year_str = year_match.group(1) if year_match else "2025"
        suggested_filename = f"Annale {year_str} - {subject_name}{ext}"
    elif doc_type == "Correction Annale":
        year_match = re.search(r'\b(20[12][0-9])\b', combined_norm)
        year_str = year_match.group(1) if year_match else "2025"
        suggested_filename = f"Correction Annale {year_str} - {subject_name}{ext}"
    elif doc_type == "DM":
        suggested_filename = f"DM - {theme}{ext}"
    elif doc_type in ["Cours", "TD", "TP"]:
        if num:
            suggested_filename = f"{doc_type} {num} - {theme}{ext}"
        else:
            suggested_filename = f"{doc_type} - {theme}{ext}"
    elif doc_type == "Prise de notes":
        suggested_filename = f"Prise de notes - {theme}{ext}"
    else:
        suggested_filename = f"{doc_type} - {theme}{ext}"

    # Construction du chemin cible
    sub_folder = tree.get("semesters", {}).get(sem_code, {}).get("subjects", {}).get(subject_name, {}).get("path")
    if matched_teacher:
        target_dir = os.path.join(sub_folder, matched_teacher)
    else:
        target_dir = sub_folder

    target_full_path = os.path.join(target_dir, suggested_filename)

    return {
        "file": filename,
        "is_course": True,
        "semester": sem_code,
        "year": year,
        "subject": subject_name,
        "teacher": matched_teacher or "(Racine de matière)",
        "doc_type": doc_type,
        "suggested_name": suggested_filename,
        "source_path": file_meta.get("path"),
        "target_dir": target_dir,
        "target_path": target_full_path,
        "confidence": "HIGH" if matched_teacher else "MEDIUM",
        "action": "MOVE"
    }
