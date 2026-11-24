import os
import sys
import re
import json
import datetime
import argparse
from typing import List, Dict, Any, Optional
from tabulate import tabulate

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from extractor import extract_document_sample
from document_namer import smart_clean_document_name, get_gemini_api_key
from classifier import normalize_text
from config_loader import get_drive_root

AUDIT_LOG_FILE = os.path.join(os.path.dirname(__file__), "audit_log.json")
DEFAULT_UPSSITECH_ROOT = get_drive_root()


def get_unique_path(path: str) -> str:
    """Évite d'écraser un fichier si le nouveau nom existe déjà."""
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


def is_already_well_named(filename: str) -> bool:
    """
    Vérifie si un fichier respecte déjà parfaitement la convention standard :
    - 'Cours [N] - [Thème].ext'
    - 'Cours [X] et [Y] - [Thème].ext'
    - 'TD[N] - [Thème].ext'
    - 'TP[N] - [Thème].ext'
    - 'DM[N] - [Thème].ext'
    - 'Annale [Année] - [Thème].ext' ou 'Annale - [Thème].ext'
    - 'Notes - [Semestre] - [Matière]...ext'
    Et ne contient AUCUN terme parasite ou mot-clé erroné.
    """
    f_norm = normalize_text(filename)

    # Termes parasites évidents qui justifient une correction
    noise_patterns = [
        r'\bfichier\b', r'\benonce\b', r'\bénoncé\b', r'\bexemple controle\b',
        r'\bcours\s*-\s*cours\b', r'\btd\s*-\s*td\b', r'\btp\s*-\s*tp\b',
        r'\bcours\s*-\s*exercice\b', r'\btd[0-9]+\s*-\s*cours\b',
        r'\btd[0-9]+\s*-\s*et\s*[0-9]+\b'
    ]
    for np in noise_patterns:
        if re.search(np, f_norm):
            return False

    # Motifs stricts de noms parfaits
    standard_patterns = [
        r'^cours\s+[0-9]+(\s+et\s+[0-9]+)?\s+-\s+.+',
        r'^cours\s+-\s+.+',
        r'^td[0-9]+\s+-\s+.+',
        r'^tp[0-9]+\s+-\s+.+',
        r'^dm[0-9]*\s+-\s+.+',
        r'^annale(\s+20[12][0-9])?\s+-\s+.+',
        r'^correction\s+(cours|td[0-9]+|tp[0-9]+|dm[0-9]*|annale)\s+-\s+.+',
        r'^notes\s+-\s+s[1-9]\s+-\s+.+'
    ]
    name_no_ext = os.path.splitext(filename)[0].strip()
    name_norm = normalize_text(name_no_ext)

    for sp in standard_patterns:
        if re.match(sp, name_norm):
            return True

    return False


def detect_naming_issue(old_name: str, proposed_name: str) -> Optional[str]:
    """Détecte la raison d'un renommage si un problème réel est corrigé."""
    if old_name == proposed_name:
        return None

    old_norm = normalize_text(old_name)
    prop_norm = normalize_text(proposed_name)

    # 1. Bogue d'import précédent corrigé (ex: TD... - Cours ... ou TD2 - Et 3)
    if re.search(r'\btd[0-9]+\s*-\s*cours\b', old_norm):
        return "Correction confusion TD / Cours"
    if re.search(r'\btd[0-9]+\s*-\s*et\s*[0-9]+\b', old_norm):
        return "Correction fausse détection TD multi-cours"
    if "exemple controle" in old_norm or "controle de l'annee" in old_norm:
        return "Standardisation Annale / Examen"
    if "cours - exercice" in old_norm:
        return "Détection TD réel au lieu d'un cours générique"
    if "fichier" in old_norm or "énoncé" in old_norm or "enonce" in old_norm:
        return "Nettoyage termes parasites Moodle"

    # 2. Fichier brut sans type ni numéro (ex: Diapo 1, TD1, Annale 2023)
    if re.match(r'^(diapo|diapositive)\s*[0-9]+', old_norm):
        return "Standardisation en 'Cours [N]'"
    if re.match(r'^(td|tp)[0-9]+\.[a-z0-9]+$', old_norm):
        return "Ajout du thème manquant"
    if re.match(r'^annale\s*(20[12][0-9])?\.[a-z0-9]+$', old_norm):
        return "Ajout du thème manquant"
    if old_norm.startswith("cours.") or old_norm.startswith("cours "):
        return "Ajout du thème / numérotation"
    if old_norm.startswith("cc.") or old_norm.startswith("cc "):
        return "Standardisation Annale CC"

    # 3. Mauvaise convention de casse ou d'espace (ex: "Cours 2 & 3" -> "Cours 2 et 3")
    if " & " in old_name and " et " in proposed_name:
        return "Normalisation esperluette '&' -> 'et'"

    # 4. Si l'ancien nom n'était pas standardisé du tout
    if not is_already_well_named(old_name):
        return "Application de la charte de nommage"

    return None


def scan_and_audit_drive(
    root_path: str = DEFAULT_UPSSITECH_ROOT,
    all_files: bool = False
) -> List[Dict[str, Any]]:
    """
    Parcourt le Drive et identifie les fichiers ayant un problème de nommage
    ou pouvant être améliorés selon la charte intelligente.
    """
    if not os.path.exists(root_path):
        print(f"[ERREUR] Le chemin cible n'existe pas : {root_path}")
        return []

    plan = []
    supported_exts = {".pdf", ".docx", ".odt", ".pptx"}

    for r, dirs, files in os.walk(root_path):
        # Ignorer les dossiers système, cache et corbeille Drive
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith(".tmp")]

        rel = os.path.relpath(r, root_path)
        parts = rel.split(os.sep)

        # Déduire le contexte académique depuis l'arborescence (ex: 5A / S9 / SI / RM)
        subject_context = "Cours"
        for p in parts:
            if p not in ["3A", "4A", "5A", "S5", "S6", "S7", "S8", "S9", "UPSSITECH", ".", ""]:
                subject_context = p
                break

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in supported_exts:
                continue

            # Si le fichier est déjà parfaitement conforme et qu'on ne force pas tout, on l'ignore
            if not all_files and is_already_well_named(f):
                continue

            full_path = os.path.join(r, f)
            act_title = os.path.splitext(f)[0]

            # Appel de la logique intelligente
            proposed = smart_clean_document_name(
                file_path=full_path,
                activity_title=act_title,
                section_title="Général",
                original_filename=f,
                course_title=subject_context,
                target_dir=r
            )

            reason = detect_naming_issue(f, proposed)

            # Si aucun problème réel n'est identifié et que l'utilisateur n'a pas demandé --all-files
            if not all_files and not reason:
                continue

            if f != proposed:
                plan.append({
                    "src": full_path,
                    "dst": os.path.join(r, proposed),
                    "old_name": f,
                    "new_name": proposed,
                    "folder": rel,
                    "reason": reason or "Optimisation du nom"
                })

    return plan


def execute_rename_batch(plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Exécute les renommages validés et enregistre le journal d'audit."""
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
            final_dst = get_unique_path(dst)
            os.rename(src, final_dst)

            record = {
                "timestamp": timestamp,
                "action": "RENAME_DRIVE",
                "old_name": old_name,
                "new_name": os.path.basename(final_dst),
                "source": src,
                "destination": final_dst
            }
            results["success"].append(record)
            log_entries.append(record)
            print(f"  [OK] '{old_name}' -> '{os.path.basename(final_dst)}'")
        except Exception as e:
            results["failed"].append({"file": old_name, "error": str(e)})
            print(f"  [ERREUR] '{old_name}' : {e}")

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
    parser = argparse.ArgumentParser(
        description="Audit et renommage intelligent des fichiers déjà présents sur Google Drive."
    )
    parser.add_argument(
        "--path",
        type=str,
        default=DEFAULT_UPSSITECH_ROOT,
        help="Dossier cible à auditer (défaut: tout UPSSITECH, ex: 'G:\\Mon Drive\\___UPSSITECH\\5A\\S9')"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Applique directement les renommages validés."
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Audite absolument tous les fichiers, même ceux déjà conformes."
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Mode interactif pour valider ou modifier chaque fichier un par un."
    )
    args = parser.parse_args()

    target_dir = os.path.abspath(args.path)
    print(f"\n======================================================================")
    print(f"AUDIT INTELLIGENT DES FICHIERS DRIVE")
    print(f"Cible : {target_dir}")
    print(f"======================================================================")

    plan = scan_and_audit_drive(target_dir, all_files=args.all_files)

    if not plan:
        print("\n✅ Tous les fichiers audités sont déjà parfaitement conformes !")
        print("Aucun problème de nommage ni doublon de termes détecté.")
        return

    # Affichage du tableau de synthèse
    rows = []
    for p in plan:
        rows.append([
            p["folder"][:25],
            p["old_name"][:35],
            "➔",
            p["new_name"][:40],
            p["reason"]
        ])

    headers = ["Dossier", "Nom Actuel sur Drive", "", "Nouveau Nom Suggéré", "Problème Résolu"]
    print("\n" + tabulate(rows, headers=headers, tablefmt="grid"))
    print(f"\n📊 Total : {len(plan)} fichier(s) présentant un problème ou une amélioration possible.")

    if not args.apply and not args.interactive:
        print("\n💡 Mode prévisualisation (aucun fichier modifié).")
        print("Pour appliquer ou valider interactivement :")
        print(f"  python audit_drive_names.py --path \"{target_dir}\" --interactive")
        print(f"  python audit_drive_names.py --path \"{target_dir}\" --apply")
        return

    # Mode interactif ou confirmation
    to_rename = []
    if args.interactive:
        print("\n--- VALIDATION INTERACTIVE FICHIER PAR FICHIER ---")
        for idx, item in enumerate(plan, start=1):
            print(f"\n[{idx}/{len(plan)}] Dossier : {item['folder']}")
            print(f"  Nom actuel : {item['old_name']}")
            print(f"  Suggéré    : {item['new_name']}")
            print(f"  Raison     : {item['reason']}")
            
            while True:
                choice = input("Appliquer ce renommage ? ([O]ui / [m]odifier / [p]asser / [q]uitter) [défaut: O] : ").strip().lower()
                if choice in ["", "o", "oui", "y"]:
                    to_rename.append(item)
                    break
                elif choice in ["p", "passer", "n", "non"]:
                    print("  -> Ignoré.")
                    break
                elif choice in ["m", "modifier"]:
                    custom = input(f"  Nouveau nom complet (avec extension) : ").strip()
                    if custom:
                        item["new_name"] = custom
                        item["dst"] = os.path.join(os.path.dirname(item["src"]), custom)
                        to_rename.append(item)
                        print(f"  -> Nom personnalisé : {custom}")
                        break
                elif choice in ["q", "quitter"]:
                    print("Arrêt de l'audit.")
                    return
    else:
        # --apply direct avec confirmation globale
        confirm = input(f"\nConfirmez-vous le renommage de ces {len(plan)} fichier(s) ? ([O]ui / [n]on) [défaut: O] : ").strip().lower()
        if confirm not in ["", "o", "oui", "y"]:
            print("Opération annulée.")
            return
        to_rename = plan

    if to_rename:
        print(f"\n[EXÉCUTION] Renommage de {len(to_rename)} fichier(s)...")
        res = execute_rename_batch(to_rename)
        print(f"\n✅ {len(res['success'])} fichier(s) renommé(s) avec succès !")
        if res["failed"]:
            print(f"❌ {len(res['failed'])} échec(s).")
    else:
        print("\nAucun renommage sélectionné.")


if __name__ == "__main__":
    main()
