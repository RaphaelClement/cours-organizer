import os
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional

import pypdf
import logging
logging.getLogger("pypdf").setLevel(logging.ERROR)
import docx

from drive_meta import get_gdoc_info_by_filename
import gdrive_auth

def extract_text_from_pdf(path: str, max_pages: int = 2) -> str:
    """Extrait le texte des premières pages d'un PDF de manière rapide et silencieuse."""
    try:
        # Ignorer les très gros fichiers (> 30 Mo) pour la vitesse
        if os.path.getsize(path) > 30 * 1024 * 1024:
            return ""
        reader = pypdf.PdfReader(path)
        chunks = []
        for i, page in enumerate(reader.pages[:max_pages]):
            text = page.extract_text()
            if text:
                chunks.append(text)
        return "\n".join(chunks)
    except Exception as e:
        return f"[Erreur lecture PDF: {e}]"

def extract_text_from_docx(path: str, max_chars: int = 4000) -> str:
    """Extrait le texte d'un document Word .docx."""
    try:
        doc = docx.Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        full_text = "\n".join(paragraphs)
        return full_text[:max_chars]
    except Exception as e:
        return f"[Erreur lecture DOCX: {e}]"

def extract_text_from_odt(path: str, max_chars: int = 4000) -> str:
    """Extrait le texte d'un fichier OpenDocument .odt."""
    try:
        with zipfile.ZipFile(path) as z:
            tree = ET.fromstring(z.read('content.xml'))
            text = " ".join(tree.itertext())
            return text[:max_chars]
    except Exception as e:
        return f"[Erreur lecture ODT: {e}]"

def extract_text_from_pptx(path: str, max_chars: int = 4000) -> str:
    """Extrait le texte d'une présentation PowerPoint .pptx."""
    try:
        with zipfile.ZipFile(path) as z:
            slides = sorted([f for f in z.namelist() if f.startswith('ppt/slides/slide') and f.endswith('.xml')])
            texts = []
            for s in slides[:10]:
                tree = ET.fromstring(z.read(s))
                texts.append(" ".join(tree.itertext()))
            full_text = "\n".join(texts)
            return full_text[:max_chars]
    except Exception as e:
        return f"[Erreur lecture PPTX: {e}]"

def extract_text_from_txt(path: str, max_chars: int = 4000) -> str:
    """Extrait le texte d'un fichier texte brut ou script."""
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read(max_chars)
    except Exception as e:
        return f"[Erreur lecture TXT: {e}]"

def extract_document_sample(file_meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrait un échantillon textuel significatif du fichier pour l'analyse IA.
    Prend en charge PDF, DOCX, ODT, PPTX, TXT, GDOC, GSLIDES.
    """
    path = file_meta.get("path", "")
    ext = file_meta.get("extension", "").lower()
    filename = file_meta.get("name", "")

    result = {
        "text": "",
        "source_type": ext,
        "is_google_workspace": False,
        "doc_id": None,
        "auth_required": False
    }

    if ext == ".pdf":
        result["text"] = extract_text_from_pdf(path)
    elif ext == ".docx":
        result["text"] = extract_text_from_docx(path)
    elif ext == ".odt":
        result["text"] = extract_text_from_odt(path)
    elif ext == ".pptx":
        result["text"] = extract_text_from_pptx(path)
    elif ext in [".txt", ".py"]:
        result["text"] = extract_text_from_txt(path)
    elif ext in [".gdoc", ".gslides", ".gsheet"]:
        result["is_google_workspace"] = True
        ginfo = get_gdoc_info_by_filename(filename)
        if ginfo:
            result["doc_id"] = ginfo["id"]
            if ext == ".gdoc":
                text = gdrive_auth.read_google_doc_text(ginfo["id"])
                if text:
                    result["text"] = text[:4000]
                else:
                    result["auth_required"] = True
                    result["text"] = f"[Google Doc: {filename}] (Contenu nécessite authentification Google Cloud)"
            elif ext == ".gslides":
                text = gdrive_auth.read_google_slides_text(ginfo["id"])
                if text:
                    result["text"] = text[:4000]
                else:
                    result["auth_required"] = True
                    result["text"] = f"[Google Slides: {filename}] (Contenu nécessite authentification Google Cloud)"
            else:
                result["text"] = f"[Google Sheet: {filename}]"
        else:
            result["text"] = f"[Fichier Google non indexé dans DriveFS: {filename}]"

    return result

if __name__ == "__main__":
    print("Extractor module initialized.")
