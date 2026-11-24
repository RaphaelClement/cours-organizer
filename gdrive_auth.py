import os
from typing import Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/presentations.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), 'credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')

def get_google_creds() -> Optional[Credentials]:
    """Charge ou initie l'authentification OAuth pour Google Workspace."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                with open(TOKEN_FILE, 'w', encoding='utf-8') as token:
                    token.write(creds.to_json())
            except Exception as e:
                print(f"[Google Auth Error] Échec de connexion : {e}")
                return None

    return creds

def extract_text_from_gdoc_elements(elements) -> str:
    """Extrait le texte brut d'une structure Google Docs."""
    text_chunks = []
    for value in elements:
        if 'paragraph' in value:
            elements = value.get('paragraph', {}).get('elements', [])
            for elem in elements:
                text_run = elem.get('textRun')
                if text_run and 'content' in text_run:
                    text_chunks.append(text_run['content'])
        elif 'table' in value:
            table = value.get('table', {})
            for row in table.get('tableRows', []):
                for cell in row.get('tableCells', []):
                    text_chunks.append(extract_text_from_gdoc_elements(cell.get('content', [])))
        elif 'tableOfContents' in value:
            toc = value.get('tableOfContents', {})
            text_chunks.append(extract_text_from_gdoc_elements(toc.get('content', [])))
    return "".join(text_chunks)

def read_google_doc_text(doc_id: str) -> Optional[str]:
    """Récupère le texte brut d'un Google Doc par son ID."""
    creds = get_google_creds()
    if not creds:
        return None
    try:
        service = build('docs', 'v1', credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()
        content = doc.get('body', {}).get('content', [])
        return extract_text_from_gdoc_elements(content)
    except Exception as e:
        print(f"[Google Doc Read Error] {e}")
        return None

def read_google_slides_text(slide_id: str) -> Optional[str]:
    """Récupère le texte d'une présentation Google Slides par son ID."""
    creds = get_google_creds()
    if not creds:
        return None
    try:
        service = build('slides', 'v1', credentials=creds)
        presentation = service.presentations().get(presentationId=slide_id).execute()
        slides = presentation.get('slides', [])
        texts = []
        for slide in slides:
            for pe in slide.get('pageElements', []):
                shape = pe.get('shape', {})
                text_data = shape.get('text', {})
                for te in text_data.get('textElements', []):
                    tr = te.get('textRun')
                    if tr and 'content' in tr:
                        texts.append(tr['content'])
        return "".join(texts)
    except Exception as e:
        print(f"[Google Slides Read Error] {e}")
        return None

if __name__ == "__main__":
    c = get_google_creds()
    print("Credentials status:", "OK" if c else "Non configuré (credentials.json manquant ou non initialisé)")
