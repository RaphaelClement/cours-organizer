import os
import re
import json
import urllib.parse
from typing import Dict, Any, Optional
import requests

from extractor import extract_document_sample
from classifier import normalize_text, clean_theme_title
from config_loader import get_student_names

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def get_gemini_api_key() -> Optional[str]:
    """Récupère la clé API Gemini depuis les variables d'environnement ou config.json."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key and key.strip():
        return key.strip()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                conf_key = data.get("gemini_api_key", "").strip()
                if conf_key:
                    return conf_key
        except Exception:
            pass
    return None


def ask_gemini_naming(
    file_text_sample: str,
    activity_title: str,
    original_filename: str,
    course_title: str,
    api_key: str
) -> Optional[Dict[str, str]]:
    """Appelle l'API Gemini REST pour suggérer intelligemment le nommage du document."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    prompt = f"""Tu es un assistant universitaire expert en organisation et archivage de cours d'ingénieur.
Analyse les informations suivantes d'un document académique téléchargé depuis Moodle :
- Intitulé Moodle de l'activité : "{activity_title}"
- Nom de fichier d'origine : "{original_filename}"
- Nom de la matière / cours : "{course_title}"
- Extrait du contenu textuel interne du document (pages 1-3) :
\"\"\"
{file_text_sample[:2500]}
\"\"\"

Règles de classification et nommage strictes :
1. Type autorisé : 'Cours', 'TD', 'TP', 'Annale', 'DM'.
   - Un sujet de contrôle, examen, partiel, devoir surveillé, DS est TOUJOURS 'Annale'.
   - Une série d'exercices, travaux dirigés ou feuille de TD est TOUJOURS 'TD'.
   - Un support de cours, diaporama, chapitre ou polycopié est TOUJOURS 'Cours'.
   - Des travaux pratiques, TP sur machine ou labo sont TOUJOURS 'TP'.
2. Formatage précis des noms :
   - Pour Cours : 'Cours [N] - [Thème]' (ex: 'Cours 1 - Introduction', 'Cours 2 et 3 - XML DTD' si multi-séances).
   - Pour TD : 'TD[N] - [Thème]' (sans espace après TD, ex: 'TD1 - Modélisation').
   - Pour TP : 'TP[N] - [Thème]' (sans espace après TP, ex: 'TP1 - Routage BGP').
   - Pour Annale : 'Annale [Année] - [Thème]' (ex: 'Annale 2023 - XML', ou 'Annale - Exemple Contrôle XML' si pas d'année).
3. Thème : Concis (2 à 5 mots pertinents), première lettre majuscule, débarrassé de termes parasites comme 'Fichier', 'Document', 'Enoncé'.

Réponds UNIQUEMENT par un objet JSON valide sans markdown supplémentaire :
{{
  "doc_type": "Cours|TD|TP|Annale|DM",
  "num": "numéro ou vide (ex: '1', ou '2 et 3')",
  "year": "année ou vide (ex: '2023')",
  "theme": "thème concis",
  "suggested_filename": "nom complet avec extension .pdf"
}}
"""
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=8)
        if r.status_code == 200:
            res_data = r.json()
            candidates = res_data.get("candidates", [])
            if candidates:
                raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                parsed = json.loads(raw_text)
                return parsed
    except Exception:
        pass
    return None


def smart_clean_document_name(
    file_path: str,
    activity_title: str,
    section_title: str,
    original_filename: str,
    course_title: str,
    target_dir: str
) -> str:
    """
    Détermine de façon robuste et intelligente le nom standardisé d'un fichier :
    1. Ouvre et extrait le contenu textuel des 2 à 3 premières pages du document.
    2. Applique des règles de détection affinées (frontières de mots, exclusion d'acronymes DTD/TCP).
    3. Si une clé Gemini est disponible et qu'une hésitation subsiste, consulte l'IA.
    4. Garantit la charte exacte (Cours 1, TD1, TP1, Annale 2023).
    """
    ext = os.path.splitext(original_filename)[1].lower() or ".pdf"

    # 1. Extraction du texte réel du document
    doc_text = ""
    if file_path and os.path.exists(file_path):
        try:
            meta = {
                "name": os.path.basename(file_path),
                "path": file_path,
                "extension": ext
            }
            sample = extract_document_sample(meta)
            doc_text = sample.get("text", "") or ""
        except Exception:
            doc_text = ""

    first_lines = "\n".join([l.strip() for l in doc_text.split("\n") if l.strip()][:8])

    combined_meta = f"{activity_title} {original_filename} {section_title}"
    meta_norm = normalize_text(combined_meta)
    text_norm = normalize_text(first_lines)

    # Protection contre les acronymes techniques (DTD, XSLT, HTML, etc.)
    # Empêche "XML DTD" d'être pris pour un "TD"
    sanitized_meta = re.sub(r'\b(dtd|xslt|html|http|tcp|udp|ftp|smtp|ntp|stp|ltd)\b', '', meta_norm)
    sanitized_text = re.sub(r'\b(dtd|xslt|html|http|tcp|udp|ftp|smtp|ntp|stp|ltd)\b', '', text_norm)
    sanitized_all = f"{sanitized_meta} {sanitized_text}"

    # 2. Détection du type
    is_correction = bool(re.search(r'\b(corrige|corrigee?|correction|solution)\b', sanitized_all))

    # A. Annales / Contrôles / Examens
    is_exam = bool(re.search(r'\b(contr[oô]le|examen|partiel|annale|devoir\s+surveill[eé]|ds)\b', sanitized_meta))

    # B. DM (Devoir Maison)
    is_dm = bool(re.search(r'(?:\b|_)(dm[0-9]*|devoir\s+maison)(?:\b|_)', sanitized_meta))

    # C. TP (Travaux Pratiques)
    is_tp = bool(re.search(r'(?:\b|_)(tp[0-9]*|travaux\s+pratiques)(?:\b|_)', sanitized_meta))
    if not is_tp and re.search(r'\b(tp[0-9]*|travaux\s+pratiques)\b', sanitized_text):
        is_tp = True

    # D. TD (Travaux Dirigés / Exercices)
    is_td = bool(re.search(r'(?:\b|_)(td[0-9]*|travaux\s+dirig[eé]s)(?:\b|_)', sanitized_meta))
    if not is_td and re.search(r'\b(td[0-9]*|travaux\s+dirig[eé]s)\b', sanitized_text):
        is_td = True

    # Si pas encore exam, vérifier les en-têtes d'examen spécifiques dans le texte (éviter faux positifs sur "contrôle d'accès", "contrôle de flux")
    if not is_exam and not is_td and not is_tp and not is_dm:
        if re.search(r'\b(sujet\s+d[ue]\s+contr[oô]le|contr[oô]le\s+continu|contr[oô]le\s+de\s+connaissances|examen\s+terminal|examen\s+final|partiel|annale)\b', sanitized_text) or re.match(r'^(contr[oô]le|examen|partiel|annale)\b', text_norm):
            is_exam = True

    # Si "exercice" apparaît dans le titre et qu'il n'est pas dans un support de cours
    if not is_td and not is_exam and not is_tp:
        if re.search(r'\bexercice[s]?\b', sanitized_meta):
            if "td" in sanitized_text or "travaux dirig" in sanitized_text or "exercice" in sanitized_text:
                is_td = True

    if is_exam:
        real_type = "Annale"
    elif is_dm:
        real_type = "DM"
    elif is_tp:
        real_type = "TP"
    elif is_td:
        real_type = "TD"
    else:
        real_type = "Cours"

    # 3. Extraction du numéro
    num = ""
    # Cas multi-séances de cours (ex: "Cours 2 et 3 XML DTD" ou "Cours 2 & 3")
    m_multi = re.search(r'\bcours\s*([0-9]+)\s*(?:et|&|-|a|à)\s*([0-9]+)\b', meta_norm)
    if m_multi and real_type == "Cours":
        num = f"{m_multi.group(1)} et {m_multi.group(2)}"
    elif real_type == "Cours":
        m_c = re.search(r'\b(?:cours|chapitre|diapo)\s*([0-9]+)\b', meta_norm)
        if m_c:
            num = m_c.group(1)
    elif real_type in ["TD", "TP", "DM"]:
        m_t = re.search(rf'(?:\b|_){real_type.lower()}\s*([0-9]+)(?:\b|_)', sanitized_meta)
        if not m_t:
            # Chercher dans le texte interne du document (ex: "Premier exercice (TD1)")
            m_t = re.search(rf'\b{real_type.lower()}\s*([0-9]+)\b', sanitized_text)
        if not m_t and real_type == "TD":
            # Si "exercice 1"
            m_ex = re.search(r'\bexercice[s]?\s*([0-9]+)\b', sanitized_meta) or re.search(r'\bexercice[s]?\s*([0-9]+)\b', sanitized_text)
            if m_ex:
                num = m_ex.group(1)
        if m_t:
            num = m_t.group(1)

    # 4. Extraction de l'année pour les Annales
    year = ""
    if real_type == "Annale":
        # Chercher d'abord dans les métadonnées (titre d'activité ou nom de fichier)
        m_yr = re.search(r'\b(20[12][0-9])\b', combined_meta)
        if not m_yr and "exemple" not in meta_norm:
            # Chercher uniquement dans la première ligne du texte
            first_line = (first_lines.split("\n")[0] if first_lines else "")
            m_yr = re.search(r'\b(20[12][0-9])\b', first_line)
        if m_yr:
            year = m_yr.group(1)

    # 5. Extraction du thème
    raw_title = activity_title or os.path.splitext(original_filename)[0]
    # Retirer les suffixes Moodle collés sans espace (ex: "TP1Fichier" -> "TP1")
    raw_title = re.sub(r'(?:Fichier|Document|Dossier|URL|Page)$', '', raw_title).strip()
    for noise in ["Fichier", "Document", "Dossier", "URL", "Page", "Devoir", "Enoncé", "énoncé", "sujet"]:
        raw_title = re.sub(rf'\b{noise}\b', '', raw_title, flags=re.IGNORECASE).strip()

    # Nettoyer les noms d'auteurs ou binômes éventuels (configurés dans config.json)
    student_names = get_student_names()
    if student_names:
        escaped_names = "|".join(re.escape(n) for n in student_names)
        raw_title = re.sub(rf'^(?:(?:{escaped_names})(?:\s+et\s+)?[\s_&\-]*)+', '', raw_title, flags=re.IGNORECASE).strip(' -_:')

    # Retirer le préfixe de type
    theme_cand = re.sub(r'^(cours|td|tp|projet|annale|annales|controle|contrôle|examen|dm)(?:\s+et\s+)?[\s0-9_&:\-]*', '', raw_title, flags=re.IGNORECASE).strip(' -_:')

    # Retirer un sous-préfixe résiduel (ex: "TD2 - Les conteneurs chiffrés")
    theme_cand = re.sub(r'^(cours|td|tp|projet|annale|annales|dm)[0-9]*[\s_:\-]*', '', theme_cand, flags=re.IGNORECASE).strip(' -_:')

    # Si c'est un TD ou TP et qu'il commence par "exercice [N]" ou "cours", nettoyer
    if real_type in ["TD", "TP"]:
        theme_cand = re.sub(r'^(exercice|exercices)[\s0-9_:-]*', '', theme_cand, flags=re.IGNORECASE).strip(' -_:')
        theme_cand = re.sub(r'^(cours|support de cours|cours :)[\s0-9_:-]*', '', theme_cand, flags=re.IGNORECASE).strip(' -_:')

    if real_type == "Annale":
        if year:
            theme_cand = re.sub(rf'\b{year}\b', '', theme_cand).strip(' -_:')
        # Si le thème est juste "XML" ou "Contrôle" ou vide
        if not theme_cand or theme_cand.lower() in ["xml", "examen", "controle", "contrôle", "sujet", ""]:
            c_name = re.sub(r'^cours\s*:\s*', '', course_title, flags=re.IGNORECASE).strip()
            theme_cand = c_name

    if not theme_cand or len(theme_cand) <= 2 or theme_cand.isdigit():
        c_name = re.sub(r'^cours\s*:\s*', '', course_title, flags=re.IGNORECASE).strip()
        theme_cand = c_name

    theme = theme_cand.strip(' -_:')
    if theme:
        theme = theme[0].upper() + theme[1:]

    # 6. Fallback IA Gemini si clé configurée et besoin de confirmation
    api_key = get_gemini_api_key()
    if api_key and (not theme or not num or real_type == "Cours" and not num):
        gemini_result = ask_gemini_naming(doc_text, activity_title, original_filename, course_title, api_key)
        if gemini_result and gemini_result.get("suggested_filename"):
            g_filename = re.sub(r'[\\/*?:"<>|]', '', gemini_result["suggested_filename"].strip())
            g_filename = re.sub(r'\s+', ' ', g_filename).strip()
            if g_filename.endswith(ext):
                return g_filename
            return f"{g_filename}{ext}"

    # 7. Formatage standardisé final
    prefix = "Correction " if is_correction else ""

    if real_type == "Annale":
        if year:
            final_name = f"{prefix}Annale {year} - {theme}{ext}" if theme and theme != year else f"{prefix}Annale {year}{ext}"
        else:
            final_name = f"{prefix}Annale - {theme}{ext}" if theme else f"{prefix}Annale{ext}"
    elif real_type == "Cours":
        if num:
            final_name = f"{prefix}Cours {num} - {theme}{ext}"
        else:
            final_name = f"{prefix}Cours - {theme}{ext}"
    elif real_type in ["TD", "TP"]:
        if num:
            final_name = f"{prefix}{real_type}{num} - {theme}{ext}"
        else:
            final_name = f"{prefix}{real_type} - {theme}{ext}"
    else:
        final_name = f"{prefix}{real_type} - {theme}{ext}"

    # Assainir les caractères interdits sous Windows (ex: ':')
    final_name = re.sub(r'[\\/*?:"<>|]', '', final_name)
    final_name = re.sub(r'\s+', ' ', final_name).strip()
    return final_name
