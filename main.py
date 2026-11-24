import os
import sys
import json
import argparse
from tabulate import tabulate

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from scanner import scan_sources, get_default_sources
from extractor import extract_document_sample
from tree_indexer import scan_target_tree
from classifier import identify_document
from mover import execute_moves, AUDIT_LOG_FILE
from config_loader import get_drive_root

PLAN_FILE = os.path.join(os.path.dirname(__file__), "pending_plan.json")

def generate_plan(sources=None) -> list:
    """Scanne les fichiers, extrait le texte et produit le plan de classement."""
    tree = scan_target_tree()
    candidates = scan_sources(sources)

    plan = []
    for cand in candidates:
        sample = extract_document_sample(cand)
        decision = identify_document(cand, sample, tree)
        plan.append(decision)

    return plan

def display_plan(plan: list, only_moves: bool = False):
    """Affiche un tableau clair et lisible du plan d'action."""
    rows = []
    move_count = 0
    ignore_count = 0

    for item in plan:
        action = item.get("action")
        if action == "MOVE":
            move_count += 1
            rows.append([
                "[DEPLACER]",
                item.get("file", "")[:30],
                f"{item.get('semester')}/{item.get('subject')}",
                item.get("teacher", ""),
                item.get("suggested_name", "")[:35],
                item.get("target_dir", "").replace(r"G:\Mon Drive\___UPSSITECH\\", "")
            ])
        else:
            ignore_count += 1
            if not only_moves:
                rows.append([
                    "[IGNORE]",
                    item.get("file", "")[:30],
                    "-",
                    "-",
                    "-",
                    item.get("ignore_reason", "Non-cours")[:40]
                ])

    headers = ["Action", "Fichier", "Semestre / Matiere", "Prof", "Nouveau Nom", "Destination"]
    print("\n" + tabulate(rows, headers=headers, tablefmt="grid"))
    print(f"\n[Resume] : {move_count} cours a ranger, {ignore_count} fichiers ignores (non-cours / proteges).")

def main():
    parser = argparse.ArgumentParser(description="Organisateur et trieur automatique de cours pour UPSSITECH.")
    parser.add_argument("--dry-run", action="store_true", help="Prévisualise les classements sans déplacer de fichiers.")
    parser.add_argument("--apply", action="store_true", help="Applique le plan en attente ou le plan calculé.")
    parser.add_argument("--only-moves", action="store_true", help="N'affiche que les fichiers éligibles au déplacement.")
    parser.add_argument("--json", action="store_true", help="Sortie au format JSON pour intégration Antigravity.")
    parser.add_argument("--undo", action="store_true", help="Annule le dernier lot de déplacements effectués.")
    parser.add_argument("--sources", nargs="*", help="Dossiers sources spécifiques à analyser.")
    parser.add_argument("--rename-notes", action="store_true", help="Renomme contextuellement toutes les prises de notes sur Google Drive.")
    parser.add_argument("--audit-drive", action="store_true", help="Audite et normalise les fichiers existants sur Google Drive.")
    parser.add_argument("--target", type=str, default=get_drive_root(), help="Dossier cible pour l'audit Drive.")
    parser.add_argument("--interactive", "-i", action="store_true", help="Validation interactive fichier par fichier.")

    args = parser.parse_args()

    if args.audit_drive:
        import audit_drive_names
        sys.argv = ["audit_drive_names.py", "--path", args.target]
        if args.apply:
            sys.argv.append("--apply")
        if args.interactive:
            sys.argv.append("--interactive")
        audit_drive_names.main()
        return

    if args.rename_notes:
        import rename_notes
        plan = rename_notes.build_rename_plan()
        if not plan:
            print("Aucune prise de notes à renommer.")
            return
        rows = [[p["rel_dir"], p["old_name"], "➔", p["new_name"]] for p in plan]
        print("\n" + tabulate(rows, headers=["Emplacement", "Nom Actuel", "", "Nouveau Nom Standardisé"], tablefmt="grid"))
        print(f"\n📊 Total : {len(plan)} document(s) identifié(s).")
        if args.apply:
            print(f"\nExécution du renommage de {len(plan)} document(s)...")
            res = rename_notes.execute_rename_plan(plan)
            print(f"✅ {len(res['success'])} document(s) renommé(s) avec succès !")
        else:
            print("\n💡 Pour appliquer, ajoutez l'option --apply.")
        return

    if args.undo:
        if not os.path.exists(AUDIT_LOG_FILE):
            print("Aucun journal d'audit trouvé pour annuler.")
            return
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
        if not logs:
            print("Aucune opération enregistrée à annuler.")
            return

        last_timestamp = logs[-1].get("timestamp")
        last_batch = [l for l in logs if l.get("timestamp") == last_timestamp]
        print(f"Annulation de {len(last_batch)} fichiers déplacés le {last_timestamp}...")
        for item in last_batch:
            src = item.get("destination")
            dst = item.get("source")
            if os.path.exists(src):
                import shutil
                shutil.move(src, dst)
                print(f" - Restauré : {item.get('file')} -> {dst}")
        remaining = [l for l in logs if l.get("timestamp") != last_timestamp]
        with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)
        print("Annulation terminée avec succès.")
        return

    plan = generate_plan(args.sources)

    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False))
        return

    display_plan(plan, only_moves=args.only_moves)

    if args.apply:
        moves = [p for p in plan if p.get("action") == "MOVE"]
        if not moves:
            print("Aucun cours à déplacer.")
            return
        print(f"\nExécution du déplacement de {len(moves)} fichier(s)...")
        results = execute_moves(plan)
        print(f"OK : {len(results['success'])} fichier(s) déplacé(s) avec succès !")
        if results['failed']:
            print(f"ERREUR : {len(results['failed'])} échec(s) :")
            for err in results['failed']:
                print(f"   - {err['file']}: {err['error']}")

if __name__ == "__main__":
    main()
