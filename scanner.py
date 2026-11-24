import os
from typing import List, Dict, Any

IGNORED_EXTENSIONS = {
    '.lnk', '.ini', '.exe', '.bat', '.cmd', '.ps1', '.tmp', '.crdownload', 
    '.part', '.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.apk', '.msi'
}

ELIGIBLE_EXTENSIONS = {
    '.pdf', '.docx', '.doc', '.odt', '.pptx', '.ppt', '.txt', '.py',
    '.gdoc', '.gslides', '.gsheet'
}

def get_default_sources() -> List[str]:
    """Retourne la liste des dossiers sources configurés par défaut."""
    user_home = os.path.expanduser("~")
    # Par défaut : uniquement le Bureau et la racine de Google Drive (Downloads strictement exclu)
    sources = [
        os.path.join(user_home, "Desktop"),
        r"G:\Mon Drive"
    ]
    return [s for s in sources if os.path.exists(s)]

def scan_sources(sources: List[str] = None) -> List[Dict[str, Any]]:
    """
    Scanne les dossiers sources et retourne les métadonnées de base des fichiers éligibles.
    Ne scanne que les fichiers au premier niveau (pas les sous-dossiers).
    """
    if sources is None:
        sources = get_default_sources()

    candidates = []

    for src in sources:
        if not os.path.exists(src):
            continue

        try:
            entries = os.listdir(src)
        except Exception as e:
            print(f"[Scanner Error] Impossible de lire {src}: {e}")
            continue

        for entry in entries:
            full_path = os.path.join(src, entry)

            # Ignorer les sous-dossiers et fichiers cachés / temporaires
            if os.path.isdir(full_path) or entry.startswith('.') or entry.startswith('~$'):
                continue

            _, ext = os.path.splitext(entry)
            ext_lower = ext.lower()

            if ext_lower in IGNORED_EXTENSIONS:
                continue

            if ext_lower in ELIGIBLE_EXTENSIONS:
                try:
                    stat = os.stat(full_path)
                    candidates.append({
                        "name": entry,
                        "path": full_path,
                        "dir": src,
                        "extension": ext_lower,
                        "size": stat.st_size,
                        "modified": stat.st_mtime
                    })
                except Exception:
                    # En cas d'erreur de stat (ex: fichier virtuel G: non ouvert)
                    candidates.append({
                        "name": entry,
                        "path": full_path,
                        "dir": src,
                        "extension": ext_lower,
                        "size": 0,
                        "modified": 0
                    })

    return candidates

if __name__ == "__main__":
    files = scan_sources()
    print(f"Trouvé {len(files)} fichiers candidats :")
    for f in files[:10]:
        print(f" - [{f['extension']}] {f['name']} ({f['dir']})")
