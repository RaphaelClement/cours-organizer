import os
import sys
import re
import json
import time
import getpass
import hashlib
import urllib.parse
from email.message import EmailMessage
from typing import Dict, Any, List, Optional, Tuple, Set
import requests
from bs4 import BeautifulSoup

from tree_indexer import scan_target_tree
from classifier import normalize_text, clean_theme_title, SUBJECT_ALIASES
from extractor import extract_document_sample
from mover import safe_move_file, get_unique_target_path
from document_namer import smart_clean_document_name
from config_loader import get_drive_root, get_moodle_url

BASE_URL = get_moodle_url()
STAGING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moodle_staging")
DEFAULT_DRIVE_ROOT = get_drive_root()
MAPPINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "course_mappings.json")
MAX_FILE_SIZE_MB = 100
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 45


def load_course_mappings() -> Dict[str, Any]:
    """Charge les correspondances manuelles/mémorisées de cours vers les dossiers du Drive."""
    if os.path.exists(MAPPINGS_FILE):
        try:
            with open(MAPPINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_course_mapping(course_id: str, mapping_data: Dict[str, Any]):
    """Enregistre une correspondance ou un refus de cours dans course_mappings.json."""
    mappings = load_course_mappings()
    mappings[str(course_id)] = mapping_data
    try:
        with open(MAPPINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] Impossible de sauvegarder course_mappings.json: {e}")


class MoodleSessionManager:
    """Gère la session HTTP et l'authentification Moodle."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
        })
        self.is_logged_in = False
        self.user_fullname = None
        self.user_id = None
        self.sesskey = None

    def login_with_credentials(self, username: str, password: str) -> bool:
        login_url = f"{BASE_URL}/login/index.php"
        try:
            r = self.session.get(login_url, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"[ERREUR] Impossible de joindre la page de connexion Moodle: {e}")
            return False

        soup = BeautifulSoup(r.text, 'html.parser')
        token_input = soup.find('input', {'name': 'logintoken'})
        if not token_input or not token_input.get('value'):
            print("[ERREUR] Impossible de trouver le jeton de sécurité logintoken sur la page.")
            return False

        logintoken = token_input['value']

        payload = {
            'anchor': '',
            'logintoken': logintoken,
            'username': username,
            'password': password,
            'rememberusername': 1
        }

        try:
            post_res = self.session.post(login_url, data=payload, timeout=20, allow_redirects=True)
            post_res.raise_for_status()
        except Exception as e:
            print(f"[ERREUR] Échec de la requête de connexion: {e}")
            return False

        if "login/index.php" in post_res.url and ("errormsg" in post_res.text or "loginerrors" in post_res.text or "La connexion a échoué" in post_res.text):
            err_soup = BeautifulSoup(post_res.text, 'html.parser')
            err_div = err_soup.find(class_=lambda c: c and ('alert-danger' in c or 'loginerrors' in c))
            err_msg = err_div.get_text(strip=True) if err_div else "Nom d'utilisateur ou mot de passe incorrect."
            print(f"[ERREUR CONNEXION] {err_msg}")
            return False

        self.is_logged_in = True
        self._extract_session_meta(post_res.text)
        return True

    def login_with_cookie(self, moodle_session: str) -> bool:
        self.session.cookies.set('MoodleSession', moodle_session, domain='www.stri.fr', path='/')
        try:
            r = self.session.get(f"{BASE_URL}/my/index.php", timeout=15)
            if r.status_code != 200 or "login/index.php" in r.url or "Une erreur est survenue" in r.text:
                print("[ERREUR] Le cookie MoodleSession fourni est expiré ou invalide.")
                return False
            self.is_logged_in = True
            self._extract_session_meta(r.text)
            return True
        except Exception as e:
            print(f"[ERREUR] Impossible de valider le cookie de session: {e}")
            return False

    def _extract_session_meta(self, html_content: str):
        soup = BeautifulSoup(html_content, 'html.parser')
        user_menu = soup.find('div', {'class': 'usermenu'}) or soup.find('span', {'class': 'usertext'})
        if user_menu:
            self.user_fullname = user_menu.get_text(strip=True)

        m_sk = re.search(r'"sesskey":\s*"([^"]+)"', html_content)
        if m_sk:
            self.sesskey = m_sk.group(1)

        m_uid = re.search(r'"userId":\s*(\d+)', html_content) or re.search(r'"USER":\s*\{\s*"id":\s*(\d+)', html_content) or re.search(r'/user/profile\.php\?id=(\d+)', html_content)
        if m_uid:
            self.user_id = m_uid.group(1)


class DriveHashIndexer:
    """Indexe les fichiers du Drive par hash SHA-256 et taille."""

    def __init__(self, root_dir: str = DEFAULT_DRIVE_ROOT):
        self.root_dir = root_dir
        self.hash_to_path: Dict[str, str] = {}
        self.size_to_paths: Dict[int, List[str]] = {}
        self.indexed_folders = set()

    def index_target_scope(self, specific_folder: Optional[str] = None):
        """Indexe un dossier spécifique ou tout le Drive UPSSITECH."""
        scan_path = specific_folder or self.root_dir
        if not os.path.exists(scan_path):
            return

        if scan_path in self.indexed_folders:
            return

        ignored_exts = {'.gdoc', '.gsheet', '.gslides', '.lnk', '.ini', '.tmp', '.db'}
        for root, dirs, files in os.walk(scan_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in ignored_exts:
                    continue
                full_p = os.path.join(root, f)
                try:
                    sz = os.path.getsize(full_p)
                    if sz > 150 * 1024 * 1024:
                        continue
                    if sz not in self.size_to_paths:
                        self.size_to_paths[sz] = []
                    self.size_to_paths[sz].append(full_p)

                    h = hashlib.sha256()
                    with open(full_p, 'rb') as fp:
                        while chunk := fp.read(65536):
                            h.update(chunk)
                    self.hash_to_path[h.hexdigest()] = full_p
                except Exception:
                    pass

        self.indexed_folders.add(scan_path)

    def find_duplicate(self, file_path: str) -> Optional[Tuple[str, str]]:
        if not os.path.exists(file_path):
            return None

        h = hashlib.sha256()
        with open(file_path, 'rb') as fp:
            while chunk := fp.read(65536):
                h.update(chunk)
        file_hash = h.hexdigest()

        if file_hash in self.hash_to_path:
            return (self.hash_to_path[file_hash], "EXACT_HASH")

        return None


def clean_moodle_filename(activity_title: str, section_title: str, original_filename: str, course_title: str, target_dir: str, file_path: str = "") -> str:
    """Rétrocompatibilité : redirige vers le moteur intelligent de document_namer."""
    return smart_clean_document_name(
        file_path=file_path,
        activity_title=activity_title,
        section_title=section_title,
        original_filename=original_filename,
        course_title=course_title,
        target_dir=target_dir
    )


class MoodleCourseDownloader:
    """Télécharge et analyse les cours Moodle, vérifie les doublons et prépare le rangement."""

    def __init__(self, session_mgr: MoodleSessionManager, drive_indexer: DriveHashIndexer):
        self.session = session_mgr.session
        self.session_mgr = session_mgr
        self.drive_indexer = drive_indexer
        self.tree = scan_target_tree(drive_indexer.root_dir)

        if not os.path.exists(STAGING_DIR):
            os.makedirs(STAGING_DIR, exist_ok=True)

    def discover_courses(self) -> List[Dict[str, Any]]:
        """
        Découvre TOUS les cours accessibles par l'étudiant via plusieurs mécanismes :
        1. WebService AJAX Moodle (core_course_get_enrolled_courses_by_timeline_classification avec 'all', 'inprogress', 'future', 'past')
        2. WebService AJAX Moodle (core_enrol_get_users_courses)
        3. Page des notes (/grade/report/overview/index.php) - liste TOUS les cours sans JS
        4. Profil utilisateur (/user/profile.php)
        5. Tableau de bord (/my/index.php) et Mes cours (/my/courses.php)
        6. Tous les cours déjà répertoriés dans course_mappings.json
        """
        courses = []
        seen_ids: Set[str] = {"1"}

        def add_course(cid: str, title: str):
            cid = str(cid).strip()
            if not cid or cid in seen_ids or not cid.isdigit():
                return
            clean_title = re.sub(r'^(Accéder au cours|Aller au cours|Favori|Options du cours)[\s:]*', '', title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r'[\s:]*(Accéder au cours|Aller au cours)$', '', clean_title, flags=re.IGNORECASE).strip()
            if not clean_title or len(clean_title) < 3 or clean_title.lower() in ["accueil", "tableau de bord", "mes cours", "site home"]:
                clean_title = f"Cours_{cid}"
            seen_ids.add(cid)
            courses.append({
                "id": cid,
                "title": clean_title,
                "url": f"{BASE_URL}/course/view.php?id={cid}"
            })

        sesskey = self.session_mgr.sesskey
        user_id = self.session_mgr.user_id

        # Si sesskey n'a pas été capturé lors du login, essayer de le lire depuis /my/index.php
        if not sesskey:
            try:
                r_my = self.session.get(f"{BASE_URL}/my/index.php", timeout=15)
                if r_my.status_code == 200:
                    m_sk = re.search(r'"sesskey":\s*"([^"]+)"', r_my.text)
                    if m_sk:
                        sesskey = m_sk.group(1)
                    m_uid = re.search(r'"userId":\s*(\d+)', r_my.text) or re.search(r'"USER":\s*\{\s*"id":\s*(\d+)', r_my.text) or re.search(r'/user/profile\.php\?id=(\d+)', r_my.text)
                    if m_uid:
                        user_id = m_uid.group(1)
            except Exception:
                pass

        # --- 1. WebService AJAX Moodle (Extrêmement complet) ---
        if sesskey:
            ajax_url = f"{BASE_URL}/lib/ajax/service.php?sesskey={sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
            for classification in ["all", "inprogress", "future", "past"]:
                try:
                    payload = [{
                        "index": 0,
                        "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                        "args": {
                            "offset": 0,
                            "limit": 0,
                            "classification": classification,
                            "sort": "fullname"
                        }
                    }]
                    resp = self.session.post(ajax_url, json=payload, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0 and not data[0].get("error"):
                            course_list = data[0].get("data", {}).get("courses", [])
                            for c in course_list:
                                add_course(str(c.get("id")), c.get("fullname") or c.get("shortname") or "")
                except Exception:
                    pass

            if user_id:
                try:
                    ajax_url_enrol = f"{BASE_URL}/lib/ajax/service.php?sesskey={sesskey}&info=core_enrol_get_users_courses"
                    payload_enrol = [{
                        "index": 0,
                        "methodname": "core_enrol_get_users_courses",
                        "args": {"userid": int(user_id)}
                    }]
                    resp = self.session.post(ajax_url_enrol, json=payload_enrol, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0 and not data[0].get("error"):
                            for c in data[0].get("data", []):
                                add_course(str(c.get("id")), c.get("fullname") or c.get("shortname") or "")
                except Exception:
                    pass

        # --- 2. Scraping HTML multi-pages (Relevé de notes, Profil, Tableau de bord) ---
        overview_urls = [
            f"{BASE_URL}/grade/report/overview/index.php",
            f"{BASE_URL}/user/profile.php",
            f"{BASE_URL}/my/courses.php",
            f"{BASE_URL}/my/index.php"
        ]
        for page_url in overview_urls:
            try:
                r = self.session.get(page_url, timeout=15)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a', href=re.compile(r'/course/view\.php\?id=(\d+)')):
                    href = a['href']
                    m = re.search(r'id=(\d+)', href)
                    if not m:
                        continue
                    cid = m.group(1)
                    title = a.get_text(strip=True)
                    if not title or len(title) < 3:
                        parent = a.find_parent(class_=lambda c: c and any(k in c for k in ['course-info', 'card-body', 'dashboard-card', 'coursebox']))
                        if parent:
                            title_elem = parent.find(class_=lambda c: c and any(k in c for k in ['coursename', 'title'])) or parent.find(['h5', 'h4', 'h3'])
                            if title_elem:
                                title = title_elem.get_text(strip=True)
                    add_course(cid, title)
            except Exception:
                pass

        # --- 3. Tous les cours mémorisés dans course_mappings.json ---
        mappings = load_course_mappings()
        for cid, m_data in mappings.items():
            if cid.isdigit() and cid not in seen_ids:
                add_course(cid, m_data.get("course_title", f"Cours_{cid}"))

        return courses

    def get_course_details(self, course_id: str) -> Dict[str, Any]:
        url = f"{BASE_URL}/course/view.php?id={course_id}"
        r = self.session.get(url, timeout=15)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, 'html.parser')

        title = ""
        h1 = soup.find('div', {'class': 'page-header-headings'})
        if h1:
            title = h1.get_text(strip=True)
        if not title:
            t = soup.find('title')
            title = t.get_text(strip=True).split('|')[0].strip() if t else f"Cours_{course_id}"

        resources = []
        sections = soup.find_all('li', class_=lambda c: c and ('section' in c and 'main' in c or 'section-item' in c))
        if not sections:
            sections = soup.find_all('div', class_=lambda c: c and 'course-section' in c)

        if sections:
            for s in sections:
                sec_title_elem = (
                    s.find(class_=lambda c: c and any(k in c for k in ['sectionname', 'contentheader', 'section-title']))
                    or s.find(['h3', 'h4'])
                )
                sec_name = sec_title_elem.get_text(strip=True) if sec_title_elem else "Général"

                for a in s.find_all('a', href=re.compile(r'/mod/resource/view\.php\?id=(\d+)')):
                    inst = a.find(class_=lambda c: c and 'instancename' in c)
                    if inst:
                        for hidden in inst.find_all(class_='accesshide'):
                            hidden.decompose()
                        raw_name = inst.get_text(" ", strip=True)
                    else:
                        raw_name = a.get_text(" ", strip=True)
                    clean_res_name = re.sub(r'(?:\b|(?<=[0-9a-z]))(Fichier|Document|PDF)\b', '', raw_name, flags=re.IGNORECASE).strip()
                    resources.append({
                        "type": "resource",
                        "section": sec_name,
                        "title": clean_res_name or raw_name,
                        "url": a['href']
                    })

                for a in s.find_all('a', href=re.compile(r'/mod/folder/view\.php\?id=(\d+)')):
                    inst = a.find(class_=lambda c: c and 'instancename' in c)
                    if inst:
                        for hidden in inst.find_all(class_='accesshide'):
                            hidden.decompose()
                        raw_name = inst.get_text(" ", strip=True)
                    else:
                        raw_name = a.get_text(" ", strip=True)
                    resources.append({
                        "type": "folder",
                        "section": sec_name,
                        "title": raw_name,
                        "url": a['href']
                    })
        else:
            for a in soup.find_all('a', href=re.compile(r'/mod/resource/view\.php\?id=(\d+)')):
                resources.append({
                    "type": "resource",
                    "section": "Ressources",
                    "title": a.get_text(strip=True),
                    "url": a['href']
                })

        return {
            "id": course_id,
            "title": title,
            "url": url,
            "resources": resources
        }

    def _extract_filename_from_response(self, response: requests.Response, default_title: str) -> str:
        cd = response.headers.get('Content-Disposition', '')
        filename = None
        if cd:
            m_utf8 = re.search(r"filename\*\s*=\s*utf-8''([^;]+)", cd, re.IGNORECASE)
            if m_utf8:
                filename = urllib.parse.unquote(m_utf8.group(1))
            else:
                m_plain = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
                if not m_plain:
                    m_plain = re.search(r'filename\s*=\s*([^;]+)', cd, re.IGNORECASE)
                if m_plain:
                    filename = m_plain.group(1).strip().strip('"\'')

        if not filename:
            path = urllib.parse.urlparse(response.url).path
            base = os.path.basename(path)
            if '.' in base and not base.endswith('.php'):
                filename = urllib.parse.unquote(base)

        if not filename:
            ct = response.headers.get('Content-Type', '').lower()
            ext = ".pdf"
            if "powerpoint" in ct or "presentation" in ct:
                ext = ".pptx"
            elif "word" in ct:
                ext = ".docx"
            elif "zip" in ct:
                ext = ".zip"
            filename = f"{clean_theme_title(default_title)}{ext}"

        filename = re.sub(r'[\\/*?:"<>|]', '_', filename)
        return filename

    def _stream_response_to_file(self, response: requests.Response, activity_title: str, dest_dir: str) -> Tuple[str, str]:
        content_len = response.headers.get('Content-Length')
        if content_len and content_len.isdigit():
            size_bytes = int(content_len)
            if size_bytes > MAX_FILE_SIZE_BYTES:
                size_mb = size_bytes / (1024 * 1024)
                raise ValueError(f"Fichier trop volumineux ({size_mb:.1f} Mo > {MAX_FILE_SIZE_MB} Mo), ignoré.")

        filename = self._extract_filename_from_response(response, activity_title)
        staging_path = os.path.join(dest_dir, filename)
        start_time = time.monotonic()
        downloaded = 0

        try:
            with open(staging_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_FILE_SIZE_BYTES:
                        raise ValueError(f"Fichier trop volumineux (> {MAX_FILE_SIZE_MB} Mo), téléchargement interrompu.")
                    if time.monotonic() - start_time > MAX_DOWNLOAD_SECONDS:
                        raise TimeoutError(f"Téléchargement trop lent (> {MAX_DOWNLOAD_SECONDS}s), interrompu.")
                    f.write(chunk)
        except Exception:
            if os.path.exists(staging_path):
                try:
                    os.remove(staging_path)
                except Exception:
                    pass
            raise

        return filename, staging_path

    def _download_resource_file(self, res_url: str, activity_title: str, dest_dir: str) -> Tuple[Optional[str], Optional[str]]:
        with self.session.get(res_url, stream=True, timeout=(10, 20)) as r:
            r.raise_for_status()
            content_type = r.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                html_bytes = b""
                for chunk in r.iter_content(chunk_size=65536):
                    html_bytes += chunk
                    if len(html_bytes) > 2 * 1024 * 1024:
                        break
                html_text = html_bytes.decode(r.encoding or 'utf-8', errors='replace')
                soup = BeautifulSoup(html_text, 'html.parser')
                file_link = None
                obj = soup.find(['object', 'iframe', 'embed'], src=True) or soup.find(['object', 'iframe', 'embed'], data=True)
                if obj:
                    file_link = obj.get('src') or obj.get('data')

                if not file_link:
                    res_div = soup.find(class_=lambda c: c and ('resourcecontent' in c or 'resourceworkaround' in c))
                    if res_div:
                        a_tag = res_div.find('a', href=re.compile(r'pluginfile\.php'))
                        if a_tag:
                            file_link = a_tag['href']

                if not file_link:
                    a_tag = soup.find('a', href=re.compile(r'pluginfile\.php'))
                    if a_tag:
                        file_link = a_tag['href']

                if file_link:
                    with self.session.get(file_link, stream=True, timeout=(10, 20)) as file_r:
                        file_r.raise_for_status()
                        return self._stream_response_to_file(file_r, activity_title, dest_dir)
                else:
                    return None, None
            else:
                return self._stream_response_to_file(r, activity_title, dest_dir)

    def infer_and_confirm_destination(self, course_id: str, course_title: str) -> Optional[str]:
        """
        Détermine interactivement le dossier cible pour un NOUVEAU cours :
        1. Devine par initiales d'enseignant dans le titre (ex: R. Mokadem -> RM)
        2. Devine par mots-clés de matière
        3. Propose à l'utilisateur : [O]ui / [c]hanger / [r]efuser définitivement / [p]asser
        Enregistre le choix dans course_mappings.json pour ne plus redemander !
        """
        guessed_year = None
        guessed_sem = None
        guessed_sub = None
        guessed_teacher = None

        # Heuristique 1 : Recherche des initiales de prof dans le titre
        m_prof = re.findall(r'\b([A-Z])\.\s*([A-Za-z]+)\b', course_title)
        initials_candidates = []
        for p1, p2 in m_prof:
            init = f"{p1}{p2[0].upper()}"
            initials_candidates.append(init)

        for cand_init in initials_candidates:
            if cand_init in self.tree.get("all_teachers", []):
                guessed_teacher = cand_init
                for s_code, s_data in self.tree.get("semesters", {}).items():
                    for sub_name, sub_data in s_data.get("subjects", {}).items():
                        if cand_init in sub_data.get("teachers", []):
                            guessed_sem = s_code
                            guessed_year = s_data.get("year")
                            guessed_sub = sub_name
                            break
                    if guessed_sub:
                        break
            if guessed_sub:
                break

        # Heuristique 2 : Matière par alias
        if not guessed_sub:
            norm_title = normalize_text(course_title)
            for std_name, aliases in SUBJECT_ALIASES.items():
                for al in aliases:
                    if normalize_text(al) in norm_title:
                        cands = self.tree.get("all_subjects", {}).get(std_name, [])
                        if cands:
                            chosen = cands[0]
                            for c in cands:
                                if c["semester"] == "S9":
                                    chosen = c
                                    break
                            guessed_sem = chosen["semester"]
                            guessed_year = chosen["year"]
                            guessed_sub = std_name
                            if chosen.get("teachers"):
                                guessed_teacher = chosen["teachers"][0]
                            break
                if guessed_sub:
                    break

        proposed_dir = None
        if guessed_sem and guessed_sub:
            sub_path = self.tree.get("semesters", {}).get(guessed_sem, {}).get("subjects", {}).get(guessed_sub, {}).get("path")
            if sub_path:
                if guessed_teacher:
                    cand_teacher_path = os.path.join(sub_path, guessed_teacher)
                    proposed_dir = cand_teacher_path if os.path.isdir(cand_teacher_path) else sub_path
                else:
                    proposed_dir = sub_path

        # Affichage et demande de confirmation
        print("\n" + "="*70)
        print(f"Nouveau cours détecté : {course_title} (ID: {course_id})")
        if proposed_dir:
            rel = os.path.relpath(proposed_dir, DEFAULT_DRIVE_ROOT)
            print(f"Dossier cible suggéré : {rel}")
            print(f"Chemin complet : {proposed_dir}")
        else:
            print("Aucun dossier cible n'a pu être deviné avec certitude.")
        print("="*70)

        if proposed_dir:
            rep = input("Action ([O]ui / [c]hanger dossier / [r]efuser ce cours / [p]asser cette fois) [défaut: O]: ").strip().lower()
            if rep in ["", "o", "oui", "y", "yes"]:
                save_course_mapping(course_id, {
                    "status": "MAPPED",
                    "year": guessed_year,
                    "semester": guessed_sem,
                    "subject": guessed_sub,
                    "teacher": guessed_teacher,
                    "course_title": course_title,
                    "target_dir": proposed_dir
                })
                return proposed_dir
            elif rep in ["r", "refuser", "ignore", "ignorer"]:
                save_course_mapping(course_id, {
                    "status": "IGNORED",
                    "course_title": course_title,
                    "target_dir": None
                })
                print(f"[REFUSÉ] Cours ID {course_id} marqué comme refusé. Il ne vous sera plus proposé.")
                return None
            elif rep in ["p", "passer", "skip", "s"]:
                print(f"[PASSÉ] Cours ID {course_id} ignoré temporairement.")
                return None
        else:
            rep = input("Action ([c]hoisir un dossier / [r]efuser ce cours / [p]asser) [défaut: c]: ").strip().lower()
            if rep in ["r", "refuser", "ignore", "ignorer"]:
                save_course_mapping(course_id, {
                    "status": "IGNORED",
                    "course_title": course_title,
                    "target_dir": None
                })
                print(f"[REFUSÉ] Cours ID {course_id} marqué comme refusé. Il ne vous sera plus proposé.")
                return None
            elif rep in ["p", "passer", "skip", "s"]:
                print(f"[PASSÉ] Cours ID {course_id} ignoré temporairement.")
                return None

        # Choix manuel
        print("\n--- Sélection manuelle du dossier dans UPSSITECH ---")
        available_sems = list(self.tree.get("semesters", {}).keys())
        print("Semestres disponibles :", ", ".join(available_sems))
        chosen_sem = input(f"Semestre [défaut: {guessed_sem or 'S9'}]: ").strip().upper() or guessed_sem or "S9"

        if chosen_sem not in self.tree.get("semesters", {}):
            print(f"[ERREUR] Semestre '{chosen_sem}' introuvable.")
            return None

        sem_data = self.tree["semesters"][chosen_sem]
        subjects = list(sem_data.get("subjects", {}).keys())
        print(f"\nMatières disponibles en {chosen_sem} :")
        for idx, sub in enumerate(subjects, 1):
            print(f"  {idx}. {sub}")

        sub_choice = input("Numéro ou nom de la matière : ").strip()
        chosen_sub = None
        if sub_choice.isdigit() and 1 <= int(sub_choice) <= len(subjects):
            chosen_sub = subjects[int(sub_choice) - 1]
        elif sub_choice in subjects:
            chosen_sub = sub_choice
        else:
            for s in subjects:
                if normalize_text(sub_choice) in normalize_text(s):
                    chosen_sub = s
                    break

        if not chosen_sub:
            print("[ERREUR] Matière invalide.")
            return None

        sub_info = sem_data["subjects"][chosen_sub]
        sub_path = sub_info["path"]
        teachers = sub_info.get("teachers", [])

        chosen_teacher = None
        if teachers:
            print(f"\nDossiers enseignants disponibles dans {chosen_sub} :")
            for idx, t in enumerate(teachers, 1):
                print(f"  {idx}. {t}")
            print(f"  {len(teachers) + 1}. (Racine de la matière)")
            t_choice = input(f"Choix de l'enseignant [défaut: 1]: ").strip() or "1"
            if t_choice.isdigit():
                t_idx = int(t_choice)
                if 1 <= t_idx <= len(teachers):
                    chosen_teacher = teachers[t_idx - 1]

        final_target = os.path.join(sub_path, chosen_teacher) if chosen_teacher else sub_path
        os.makedirs(final_target, exist_ok=True)

        save_course_mapping(course_id, {
            "status": "MAPPED",
            "year": sem_data.get("year"),
            "semester": chosen_sem,
            "subject": chosen_sub,
            "teacher": chosen_teacher,
            "course_title": course_title,
            "target_dir": final_target
        })
        print(f"[OK] Dossier cible validé : {final_target}\n")
        return final_target

    def process_course_resources(self, course_details: Dict[str, Any], target_dir: str) -> List[Dict[str, Any]]:
        course_title = course_details["title"]
        course_id = course_details["id"]
        results = []

        self.drive_indexer.index_target_scope(target_dir)

        course_staging_dir = os.path.join(STAGING_DIR, f"{course_id}_{clean_theme_title(course_title)}")
        os.makedirs(course_staging_dir, exist_ok=True)

        resource_items = [r for r in course_details["resources"] if r["type"] == "resource"]
        total_res = len(resource_items)
        for idx, res in enumerate(resource_items, 1):
            res_url = res["url"]
            activity_title = res["title"]
            section_title = res["section"]

            print(f"    -> [{idx}/{total_res}] Vérification : {activity_title[:60]}...", end="\r", flush=True)

            try:
                filename, staging_path = self._download_resource_file(res_url, activity_title, course_staging_dir)
                if not filename or not staging_path or not os.path.exists(staging_path):
                    continue

                dup_info = self.drive_indexer.find_duplicate(staging_path)
                if dup_info:
                    drive_existing_path, match_type = dup_info
                    results.append({
                        "file": filename,
                        "activity_title": activity_title,
                        "section": section_title,
                        "staging_path": staging_path,
                        "is_duplicate": True,
                        "existing_drive_path": drive_existing_path,
                        "status": "DÉJÀ SUR LE DRIVE",
                        "action": "SKIP"
                    })
                    continue

                suggested_name = smart_clean_document_name(
                    file_path=staging_path,
                    activity_title=activity_title,
                    section_title=section_title,
                    original_filename=filename,
                    course_title=course_title,
                    target_dir=target_dir
                )

                target_full_path = os.path.join(target_dir, suggested_name)

                results.append({
                    "file": filename,
                    "activity_title": activity_title,
                    "section": section_title,
                    "staging_path": staging_path,
                    "is_duplicate": False,
                    "suggested_name": suggested_name,
                    "target_dir": target_dir,
                    "target_path": target_full_path,
                    "status": "NOUVEAU",
                    "action": "MOVE"
                })

            except Exception as e:
                results.append({
                    "file": activity_title,
                    "activity_title": activity_title,
                    "section": section_title,
                    "status": "ERREUR",
                    "error": str(e),
                    "action": "ERROR"
                })

        if total_res > 0:
            print(" " * 85, end="\r", flush=True)
        return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Synchroniseur Moodle vers Google Drive UPSSITECH")
    parser.add_argument("--course", "--courses", nargs="+", help="ID(s) de cours Moodle à synchroniser (ex: 77, ou 88 90, ou 88,90)")
    parser.add_argument("--all", action="store_true", help="Synchroniser tous les cours du tableau de bord Moodle")
    parser.add_argument("--username", help="Nom d'utilisateur Moodle")
    parser.add_argument("--password", help="Mot de passe Moodle")
    parser.add_argument("--cookie", help="Valeur du cookie MoodleSession")
    parser.add_argument("--apply", action="store_true", help="Déplacer réellement les nouveaux fichiers vers le Drive (sinon dry-run)")
    parser.add_argument("--ignore", nargs="+", help="Marquer des IDs de cours comme refusés/ignorés (ex: --ignore 19 115)")
    parser.add_argument("--unignore", nargs="+", help="Dé-marquer des IDs de cours comme refusés (ex: --unignore 88)")
    parser.add_argument("--include-ignored", action="store_true", help="Inclure aussi les cours précédemment refusés lors de --all")
    parser.add_argument("--reconfigure", action="store_true", help="Forcer la reconfiguration des dossiers même pour les cours déjà mappés")
    args = parser.parse_args()

    # Gestion rapide des commandes d'ignorance sans connexion Moodle si aucun cours à lancer
    if args.ignore:
        mappings = load_course_mappings()
        for item in args.ignore:
            for cid in re.split(r'[,;\s]+', str(item)):
                c_str = cid.strip()
                if c_str.isdigit():
                    mappings[c_str] = {
                        "status": "IGNORED",
                        "course_title": mappings.get(c_str, {}).get("course_title", f"Cours_{c_str}"),
                        "target_dir": None
                    }
                    print(f"[CONFIG] Cours ID {c_str} marqué comme IGNORÉ/REFUSÉ.")
        with open(MAPPINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)

    if args.unignore:
        mappings = load_course_mappings()
        for item in args.unignore:
            for cid in re.split(r'[,;\s]+', str(item)):
                c_str = cid.strip()
                if c_str.isdigit() and c_str in mappings:
                    if mappings[c_str].get("status") == "IGNORED":
                        del mappings[c_str]
                        print(f"[CONFIG] Cours ID {c_str} réactivé (statut IGNORÉ retiré).")
        with open(MAPPINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)

    session_mgr = MoodleSessionManager()

    # 1. Authentification
    cookie = args.cookie or os.environ.get("MOODLE_COOKIE")
    username = args.username or os.environ.get("MOODLE_USER")
    password = args.password or os.environ.get("MOODLE_PASS")

    if cookie:
        print("[AUTH] Connexion via cookie MoodleSession...")
        if not session_mgr.login_with_cookie(cookie):
            sys.exit(1)
    elif username and not password:
        p_val = getpass.getpass(f"Mot de passe Moodle pour '{username}': ").strip()
        if not session_mgr.login_with_credentials(username, p_val):
            sys.exit(1)
    elif username and password:
        print(f"[AUTH] Connexion via identifiant '{username}'...")
        if not session_mgr.login_with_credentials(username, password):
            sys.exit(1)
    else:
        print("=== Authentification Moodle UPSSITECH (STRI) ===")
        print("1. Connexion avec Identifiant et Mot de passe (recommandé)")
        print("2. Connexion avec Cookie MoodleSession")
        choice = input("Choix (1/2) [défaut: 1]: ").strip() or "1"

        if choice == "2":
            c_val = input("Collez votre cookie MoodleSession: ").strip()
            if not session_mgr.login_with_cookie(c_val):
                sys.exit(1)
        else:
            u_val = input("Nom d'utilisateur: ").strip()
            p_val = getpass.getpass("Mot de passe: ").strip()
            if not session_mgr.login_with_credentials(u_val, p_val):
                sys.exit(1)

    user_info = f" ({session_mgr.user_fullname})" if session_mgr.user_fullname else ""
    print(f"[OK] Connecté avec succès sur Moodle{user_info} !\n")

    indexer = DriveHashIndexer(DEFAULT_DRIVE_ROOT)
    downloader = MoodleCourseDownloader(session_mgr, indexer)

    # 2. Sélection des cours
    target_course_ids = []
    if args.course:
        for item in args.course:
            for cid in re.split(r'[,;\s]+', str(item)):
                c_clean = cid.strip()
                if c_clean.isdigit() and c_clean not in target_course_ids:
                    target_course_ids.append(c_clean)
    elif args.all:
        print("[SCAN] Recherche exhaustive de tous les cours accessibles...")
        courses = downloader.discover_courses()
        print(f"Trouvé {len(courses)} cours accessibles sur Moodle.")
        for c in courses:
            target_course_ids.append(c["id"])
    else:
        target_course_ids = ["77"]

    # 3. Traitement cours par cours
    total_courses = len(target_course_ids)
    for c_idx, cid in enumerate(target_course_ids, 1):
        mappings = load_course_mappings()
        stored = mappings.get(str(cid))

        # A. Si le cours a été explicitement REFUSÉ
        if stored and stored.get("status") == "IGNORED" and not args.include_ignored:
            c_title = stored.get("course_title") or f"Cours ID {cid}"
            print(f"[{c_idx}/{total_courses}] [IGNORÉ] {c_title} (ID {cid}) : cours déjà refusé.", flush=True)
            continue

        print(f"[{c_idx}/{total_courses}] [ANALYSE] Lecture du cours ID {cid}...", end="\r", flush=True)
        try:
            details = downloader.get_course_details(cid)
        except Exception as e:
            print(" " * 80, end="\r", flush=True)
            print(f"[{c_idx}/{total_courses}] [ERREUR] Impossible de lire le cours {cid}: {e}", flush=True)
            continue

        course_title = details["title"]
        resources = details.get("resources", [])

        # B. Si aucune ressource dans le cours
        if not resources:
            print(" " * 80, end="\r", flush=True)
            print(f"[{c_idx}/{total_courses}] [VIDE] {course_title} (ID {cid}) : aucune ressource téléchargeable.", flush=True)
            continue

        # C. Détermination du dossier cible (Année/Semestre/Matière/Prof)
        target_dir = None
        if stored and stored.get("target_dir") and stored.get("status") != "IGNORED" and not args.reconfigure:
            # Déjà mappé et validé par l'utilisateur : on utilise directement sans redemander !
            target_dir = stored["target_dir"]
        else:
            print(" " * 80, end="\r", flush=True)
            # Nouveau cours ou reconfiguration demandée :
            target_dir = downloader.infer_and_confirm_destination(cid, course_title)
            if not target_dir:
                # Refusé ou passé
                continue

        print(" " * 80, end="\r", flush=True)
        print(f"[{c_idx}/{total_courses}] [SCAN] {course_title} (ID {cid}) ({len(resources)} ressources)...", flush=True)

        # D. Traitement des ressources et dédoublonnage contre le Drive
        results = downloader.process_course_resources(details, target_dir)

        duplicates = [r for r in results if r.get("is_duplicate")]
        new_files = [r for r in results if not r.get("is_duplicate") and r.get("status") == "NOUVEAU"]
        errors = [r for r in results if r.get("status") == "ERREUR"]

        if errors:
            for err in errors:
                print(f"    [WARN] Ressource ignorée ({err.get('file')}) : {err.get('error')}", flush=True)

        # E. Si le Drive a DÉJÀ TOUS les fichiers (0 nouveau fichier) :
        if not new_files:
            rel_dest = os.path.relpath(target_dir, DEFAULT_DRIVE_ROOT)
            print(f"[À JOUR] {course_title} (ID {cid}) -> {rel_dest} : {len(duplicates)}/{len(results)} fichiers déjà présents sur le Drive. Rien à faire.", flush=True)
            continue

        # F. S'il y a de NOUVEAUX fichiers détectés :
        rel_dest = os.path.relpath(target_dir, DEFAULT_DRIVE_ROOT)
        while True:
            print("\n" + "="*70)
            print(f"[NOUVEAUX FICHIERS DÉTECTÉS] {course_title} (ID {cid})")
            print(f"Destination : {rel_dest}")
            print("="*70)
            for idx, n in enumerate(new_files, 1):
                print(f"  {idx}. Moodle : {n['file']}")
                print(f"     -> Nom Drive : {n['suggested_name']}")

            if not args.apply:
                print("\n[DRY RUN] Aucun fichier n'a été déplacé. Utilisez '--apply' pour exécuter le déplacement.")
                break

            ans = input(f"\nAction ([O]ui pour valider / [m]odifier un nom / [n]on pour annuler) [défaut: O]: ").strip().lower()
            if ans in ["", "o", "oui", "y", "yes"]:
                print("\n[DÉPLACEMENT SÉCURISÉ VERS LE DRIVE]...")
                for n in new_files:
                    src = n["staging_path"]
                    dest = os.path.join(n["target_dir"], n["suggested_name"])
                    res = safe_move_file(src, dest, audit_note=f"Import Moodle Cours {cid} - {course_title}")
                    if res.get("status") == "SUCCESS":
                        print(f"  [OK] {os.path.basename(res['target'])}")
                    else:
                        print(f"  [ERREUR] {res.get('error')}")
                break
            elif ans in ["m", "modifier", "edit", "e"]:
                idx_input = input(f"Numéro du fichier à modifier (1-{len(new_files)}) : ").strip()
                if idx_input.isdigit() and 1 <= int(idx_input) <= len(new_files):
                    target_idx = int(idx_input) - 1
                    cur_name = new_files[target_idx]["suggested_name"]
                    new_val = input(f"Nouveau nom pour ce fichier [actuel: {cur_name}]: ").strip()
                    if new_val:
                        cur_ext = os.path.splitext(cur_name)[1]
                        if not os.path.splitext(new_val)[1]:
                            new_val += cur_ext
                        new_files[target_idx]["suggested_name"] = new_val
                        new_files[target_idx]["target_path"] = os.path.join(target_dir, new_val)
                        print(f"[MODIFIÉ] Nouveau nom pris en compte : {new_val}")
                else:
                    print("[ERREUR] Numéro de fichier invalide.")
            else:
                print("[ANNULATION] Déplacement annulé pour ce cours.")
                break


if __name__ == "__main__":
    main()
