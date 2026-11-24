import glob
import os
import sqlite3
from typing import Optional, Tuple, Dict, Any

def get_drivefs_db_path() -> Optional[str]:
    """Trouve le chemin de la base de métadonnées SQLite active de Google Drive Desktop."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return None
    pattern = os.path.join(local_app_data, "Google", "DriveFS", "*", "metadata_sqlite_db")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

def get_gdoc_info_by_filename(filename: str, parent_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Recherche un fichier Google Workspace (.gdoc, .gsheet, .gslides) dans la base SQLite locale DriveFS.
    Retourne {id, local_title, mime_type, stable_id, parent_title}
    """
    db_path = get_drivefs_db_path()
    if not db_path or not os.path.exists(db_path):
        return None

    uri = f"file:{db_path}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        cursor = conn.cursor()
        
        query = """
            SELECT i.id, i.local_title, i.mime_type, i.stable_id, p.local_title as parent_title
            FROM items i
            LEFT JOIN stable_parents sp ON i.stable_id = sp.item_stable_id
            LEFT JOIN items p ON sp.parent_stable_id = p.stable_id
            WHERE i.local_title = ? AND i.trashed = 0
        """
        params = [filename]
        rows = cursor.execute(query, params).fetchall()
        conn.close()

        if not rows:
            return None

        # Si un parent_name est spécifié, filtrer avec
        if parent_name:
            for r in rows:
                if r[4] and (parent_name.lower() in r[4].lower() or r[4].replace('\xa0', ' ').lower() in parent_name.lower()):
                    return {
                        "id": r[0],
                        "local_title": r[1],
                        "mime_type": r[2],
                        "stable_id": r[3],
                        "parent_title": r[4]
                    }

        # Sinon renvoyer le premier actif
        first = rows[0]
        return {
            "id": first[0],
            "local_title": first[1],
            "mime_type": first[2],
            "stable_id": first[3],
            "parent_title": first[4]
        }
    except Exception as e:
        print(f"[DriveFS Meta Error] {e}")
        return None

if __name__ == "__main__":
    test = get_gdoc_info_by_filename("MENU - COURSE.gdoc")
    print("Test MENU - COURSE:", test)
