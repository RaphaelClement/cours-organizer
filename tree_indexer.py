import os
import re
import json
from typing import Dict, Any, List
from config_loader import get_drive_root

DEFAULT_ROOT = get_drive_root()

def scan_target_tree(root_dir: str = DEFAULT_ROOT) -> Dict[str, Any]:
    """
    Scanne l'arborescence UPSSITECH sur Google Drive pour cartographier
    les années, semestres, matières et initiales de professeurs existants.
    """
    tree = {
        "root": root_dir,
        "semesters": {}, # "S9": {"year": "5A", "path": "...", "subjects": {...}}
        "all_subjects": {}, # "Informatique Python": [...]
        "all_teachers": set()
    }

    if not os.path.exists(root_dir):
        return tree

    try:
        years = [y for y in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, y)) and not y.startswith('.')]
    except Exception:
        return tree

    for y in sorted(years):
        y_path = os.path.join(root_dir, y)
        try:
            sems = [s for s in os.listdir(y_path) if os.path.isdir(os.path.join(y_path, s)) and not s.startswith('.')]
        except Exception:
            continue

        for s in sorted(sems):
            s_path = os.path.join(y_path, s)
            tree["semesters"][s] = {
                "year": y,
                "path": s_path,
                "subjects": {}
            }

            try:
                subjects = [sub for sub in os.listdir(s_path) if os.path.isdir(os.path.join(s_path, sub)) and not sub.startswith('.')]
            except Exception:
                continue

            for sub in sorted(subjects):
                sub_path = os.path.join(s_path, sub)
                teachers = []
                try:
                    # Sous-dossiers de professeurs (généralement 2 à 4 lettres majuscules comme AnAn, RM, PT, BC)
                    sub_entries = [e for e in os.listdir(sub_path) if os.path.isdir(os.path.join(sub_path, e)) and not e.startswith('.')]
                    for e in sub_entries:
                        # Un dossier d'enseignant est strictement composé de 2 à 4 lettres (ex: RM, AnAn, PT, BC, JGT)
                        if re.match(r'^[A-Za-z]{2,4}$', e) and e.lower() not in ['tp', 'td', 'dm', 'ds', 'exam']:
                            teachers.append(e)
                            tree["all_teachers"].add(e)
                except Exception:
                    pass

                tree["semesters"][s]["subjects"][sub] = {
                    "path": sub_path,
                    "teachers": teachers
                }

                if sub not in tree["all_subjects"]:
                    tree["all_subjects"][sub] = []
                tree["all_subjects"][sub].append({
                    "year": y,
                    "semester": s,
                    "path": sub_path,
                    "teachers": teachers
                })

    tree["all_teachers"] = sorted(list(tree["all_teachers"]))
    return tree

if __name__ == "__main__":
    t = scan_target_tree()
    print("Semestres découverts :", list(t["semesters"].keys()))
    print("Nombre total de matières uniques :", len(t["all_subjects"]))
    print("Initiales de professeurs découvertes :", t["all_teachers"])
    if "S9" in t["semesters"]:
        print("Matières du S9 :", list(t["semesters"]["S9"]["subjects"].keys()))
