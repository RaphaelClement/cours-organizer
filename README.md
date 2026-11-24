# 📚 Cours-Organizer — Synchronisation Moodle & Rangement Automatique sur Google Drive

Outil Python permettant de :
1. **Synchroniser automatiquement ses cours Moodle** (ex: Moodle STRI / UPSSITECH) vers son Google Drive local, **sans jamais télécharger de doublons** (vérification par empreinte SHA-256, même si les fichiers ont déjà été renommés sur le Drive).
2. **Renommer intelligemment les documents** en lisant le contenu réel des premières pages (`.pdf`, `.docx`, `.odt`, `.pptx`) avec une charte propre (`Cours 1 - ...`, `TD1 - ...`, `TP1 - ...`, `Annale 2024 - ...`) et un fallback IA Gemini optionnel.
3. **Auditer et nettoyer** les fichiers déjà présents sur le Drive (`--audit-drive`) avec validation interactive et annulation (`--undo`).

---

## ⚙️ Installation (5 minutes)

### 1. Prérequis
- **Python 3.10+** installé
- **Google Drive pour ordinateur** installé et monté en lecteur local (ex: `G:\Mon Drive\...`)

### 2. Cloner le projet et installer les dépendances
```powershell
git clone <URL_DU_DEPOT>
cd cours-organizer
pip install -r requirements.txt
```

### 3. Configurer ton dossier Drive (`config.json`)
Copie le fichier `config.example.json` en `config.json` et adapte `drive_root` vers le dossier racine de tes cours sur ton Google Drive :

```json
{
  "drive_root": "G:\\Mon Drive\\UPSSITECH",
  "moodle_url": "https://www.stri.fr/eformation",
  "gemini_api_key": "",
  "student_names": []
}
```

- `drive_root` : chemin vers ton arborescence de cours (`{Année}\{Semestre}\{Matière}\{Initiales_Prof}\`).
- `moodle_url` : URL racine de ton Moodle (par défaut STRI).
- `gemini_api_key` *(optionnel)* : clé API Gemini si tu veux activer l'aide IA pour les noms ambigus (fonctionne 100% hors-ligne sans clé).
- `student_names` *(optionnel)* : ton nom/prénom pour le retirer automatiquement des noms de fichiers rendus si besoin.

> **Sécurité :** `config.json`, `course_mappings.json`, `audit_log.json`, `credentials.json` et `token.json` sont dans `.gitignore` et restent strictement en local sur ta machine.

---

## 🎓 Synchroniser tes cours depuis Moodle

### Synchroniser tous les cours du tableau de bord
```powershell
python moodle_downloader.py --all --username ton.identifiant --apply
```
*(Ton mot de passe est demandé de manière masquée dans le terminal et n'est jamais enregistré.)*

### Synchroniser un ou plusieurs cours précis (par ID Moodle)
```powershell
python moodle_downloader.py --course 77 --username ton.identifiant --apply
python moodle_downloader.py --course 88 90 --username ton.identifiant --apply
```

### Fonctionnement interactif
- **Premier passage sur un cours** : l'outil devine le dossier cible et te demande confirmation (`[O]ui / [c]hanger dossier / [r]efuser ce cours / [p]asser`). Ton choix est mémorisé localement dans `course_mappings.json`.
- **Dédoublonnage automatique** : si tu as déjà le fichier sur ton Drive (même sous un autre nom), il est détecté par hash SHA-256 et ignoré.
- **Validation des noms** : avant de déplacer les nouveaux fichiers, l'outil affiche les noms proposés et te permet de les valider (`O`), de modifier un nom à la volée (`m`) ou d'annuler (`n`).
- **Réactiver un cours refusé** : `python moodle_downloader.py --unignore <ID>`

---

## 🔍 Auditer et renommer des fichiers déjà présents sur le Drive

```powershell
# Prévisualiser les anomalies sur un semestre (aucun fichier modifié) :
python main.py --audit-drive --target "G:\Mon Drive\UPSSITECH\5A\S9"

# Valider ou modifier les renommages fichier par fichier :
python main.py --audit-drive --target "G:\Mon Drive\UPSSITECH\5A\S9" --interactive

# Standardiser les prises de notes anonymes ("Prise de notes", "Document sans titre") :
python main.py --rename-notes --apply

# Annuler le dernier lot de déplacements ou renommages :
python main.py --undo
```
