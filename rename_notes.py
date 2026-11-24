import os
import sys
import json
import datetime
import argparse
from tabulate import tabulate

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

AUDIT_LOG_FILE = os.path.join(os.path.dirname(__file__), "audit_log.json")
DEFAULT_ROOTS = [r"G:\Mon Drive\___UPSSITECH", r"G:\Mon Drive\__CUPGE"]

def get_unique_path(path: str) -> str:
    """Évite d'écraser un fichier existant si le nouveau nom existe déjà."""
    if not os.path.exists(path):
        return path
    base_dir = os.path.dirname(path)
    filename = os.path.basename(path)
    name, ext = os.path.splitext(filename)
    counter = 2
    while True:
        candidate = os.path.join(base_dir, f"{name} ({counter}){ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1

def build_rename_plan(roots: list = None) -> list:
    """Parcourt les arborescences de cours (UPSSITECH et CUPGE) et génère le plan de renommage contextuel."""
    if roots is None:
        roots = DEFAULT_ROOTS

    plan = []

    for root_dir in roots:
        if not os.path.exists(root_dir):
            continue

        for r, dirs, files in os.walk(root_dir):
            for f in files:
                f_lower = f.lower()
                is_note = 'prise de notes' in f_lower or 'notes cours' in f_lower
                is_untitled_doc = 'document sans titre' in f_lower
                is_untitled_slides = 'présentation sans titre' in f_lower or 'presentation sans titre' in f_lower

                if not (is_note or is_untitled_doc or is_untitled_slides):
                    continue

                rel = os.path.relpath(r, root_dir)
                parts = rel.split(os.sep)

                # Extraction contextuelle : Année / Semestre / Matière / Prof
                if len(parts) >= 3:
                    year = parts[0]
                    sem = parts[1]
                    subject = parts[2]
                    teacher = parts[3] if len(parts) >= 4 else None
                elif len(parts) == 2:
                    year = parts[0]
                    sem = parts[1]
                    subject = "Général"
                    teacher = None
                else:
                    continue

                _, ext = os.path.splitext(f)

                # Normaliser les espaces dans le nom de matière et de prof
                subject_clean = " ".join(subject.split())

                # Cas spécifique pour les "Notes Cours [Sous-titre]"
                theme_suffix = ""
                if 'notes cours' in f_lower:
                    raw_theme = f[len("Notes Cours"):].split('.')[0].strip(' -_')
                    sub_theme = " ".join(raw_theme.split())
                    if sub_theme:
                        theme_suffix = f" - {sub_theme}"

                # Déterminer le préfixe
                prefix = "Présentation" if is_untitled_slides else "Notes"

                # Filtre pour ignorer les faux sous-dossiers qui ne sont pas des profs
                if teacher and teacher.lower() not in ['tp', 'td', 'cours', 'dm', 'projet', 'annales', 'be']:
                    teacher_clean = " ".join(teacher.split())
                    new_name = f"{prefix} - {sem} - {subject_clean} - {teacher_clean}{theme_suffix}{ext}"
                else:
                    new_name = f"{prefix} - {sem} - {subject_clean}{theme_suffix}{ext}"

                # Si le fichier a déjà ce nom exact, rien à faire
                if f == new_name:
                    continue

                src_path = os.path.join(r, f)
                dst_path = os.path.join(r, new_name)

                plan.append({
                    "src": src_path,
                    "dst": dst_path,
                    "old_name": f,
                    "new_name": new_name,
                    "rel_dir": f"{os.path.basename(root_dir)}\\{rel}",
                    "semester": sem,
                    "subject": subject_clean,
                    "teacher": teacher or "-"
                })

    return plan

def execute_rename_plan(plan: list) -> dict:
    """Exécute les renommages et enregistre l'audit log."""
    results = {"success": [], "failed": []}
    timestamp = datetime.datetime.now().isoformat()
    log_entries = []

    for item in plan:
        src = item["src"]
        dst = item["dst"]
        old_name = item["old_name"]

        if not os.path.exists(src):
            results["failed"].append({"file": old_name, "error": "Fichier source introuvable"})
            continue

        try:
            # Gestion des collisions si le nom existe déjà
            final_dst = get_unique_path(dst)
            os.rename(src, final_dst)

            record = {
                "timestamp": timestamp,
                "action": "RENAME_NOTE",
                "old_name": old_name,
                "new_name": os.path.basename(final_dst),
                "source": src,
                "destination": final_dst
            }
            results["success"].append(record)
            log_entries.append(record)
        except Exception as e:
            results["failed"].append({"file": old_name, "error": str(e)})

    # Enregistrement dans audit_log.json
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

def main():
    parser = argparse.ArgumentParser(description="Renommage standardisé des Prises de Notes sur UPSSITECH.")
    parser.add_argument("--apply", action="store_true", help="Applique directement les renommages.")
    args = parser.parse_args()

    plan = build_rename_plan()
    if not plan:
        print("Aucune prise de notes ou document sans titre à renommer.")
        return

    rows = []
    for p in plan:
        rows.append([
            p["rel_dir"],
            p["old_name"],
            "➔",
            p["new_name"]
        ])

    headers = ["Emplacement", "Nom Actuel", "", "Nouveau Nom Standardisé"]
    print("\n" + tabulate(rows, headers=headers, tablefmt="grid"))
    print(f"\n📊 Total : {len(plan)} document(s) identifié(s).")

    if args.apply:
        print(f"\nExécution du renommage de {len(plan)} document(s)...")
        res = execute_rename_plan(plan)
        print(f"✅ {len(res['success'])} document(s) renommé(s) avec succès !")
        if res["failed"]:
            print(f"❌ {len(res['failed'])} échec(s) :")
            for err in res["failed"]:
                print(f"   - {err['file']}: {err['error']}")
    else:
        print("\n💡 Pour appliquer, lancez avec l'option --apply.")

if __name__ == "__main__":
    main()
