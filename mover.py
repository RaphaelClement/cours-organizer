import os
import shutil
import json
import datetime
from typing import List, Dict, Any, Tuple

AUDIT_LOG_FILE = os.path.join(os.path.dirname(__file__), "audit_log.json")

def get_unique_target_path(target_path: str) -> str:
    """Évite d'écraser un fichier existant en ajoutant un suffixe si nécessaire."""
    if not os.path.exists(target_path):
        return target_path

    base_dir = os.path.dirname(target_path)
    filename = os.path.basename(target_path)
    name, ext = os.path.splitext(filename)

    counter = 2
    while True:
        candidate = os.path.join(base_dir, f"{name} ({counter}){ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1

def execute_moves(plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Exécute le déplacement des fichiers validés par l'utilisateur et consigne chaque action
    dans audit_log.json.
    """
    results = {
        "success": [],
        "failed": [],
        "skipped": []
    }

    log_entries = []
    timestamp = datetime.datetime.now().isoformat()

    for item in plan:
        action = item.get("action", "")
        if action != "MOVE":
            results["skipped"].append(item.get("file", ""))
            continue

        src = item.get("source_path")
        target_dir = item.get("target_dir")
        target_path = item.get("target_path")

        if not src or not os.path.exists(src):
            results["failed"].append({
                "file": item.get("file", ""),
                "error": "Fichier source introuvable"
            })
            continue

        try:
            # 1. Création sécurisée du dossier cible (ex: dossier d'initiales prof)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            # 2. Gestion des collisions de noms
            final_target = get_unique_target_path(target_path)

            # 3. Déplacement du fichier (fonctionne pour fichiers locaux et G: Drive)
            shutil.move(src, final_target)

            record = {
                "timestamp": timestamp,
                "file": item.get("file"),
                "source": src,
                "destination": final_target,
                "subject": item.get("subject"),
                "teacher": item.get("teacher"),
                "semester": item.get("semester")
            }
            results["success"].append(record)
            log_entries.append(record)
        except Exception as e:
            results["failed"].append({
                "file": item.get("file"),
                "error": str(e)
            })

    # Mise à jour du fichier d'audit
    existing_logs = []
    if os.path.exists(AUDIT_LOG_FILE):
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                existing_logs = json.load(f)
        except Exception:
            existing_logs = []

    existing_logs.extend(log_entries)
    try:
        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_logs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Audit Log Error] {e}")

    return results

def safe_move_file(src: str, target_path: str, audit_note: str = "") -> Dict[str, Any]:
    """Déplace un fichier de manière sécurisée en évitant les écrasements et en consignant dans audit_log."""
    if not os.path.exists(src):
        return {"status": "ERROR", "error": "Fichier source introuvable"}
    try:
        target_dir = os.path.dirname(target_path)
        os.makedirs(target_dir, exist_ok=True)
        final_target = get_unique_target_path(target_path)
        shutil.move(src, final_target)

        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "source": src,
            "destination": final_target,
            "note": audit_note
        }

        existing_logs = []
        if os.path.exists(AUDIT_LOG_FILE):
            try:
                with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                    existing_logs = json.load(f)
            except Exception:
                existing_logs = []
        existing_logs.append(record)
        try:
            with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(existing_logs, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        return {"status": "SUCCESS", "target": final_target}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}

if __name__ == "__main__":
    print("Mover module initialized. Audit log:", AUDIT_LOG_FILE)
