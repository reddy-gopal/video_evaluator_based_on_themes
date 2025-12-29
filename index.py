import os
import re
import json
import tempfile
import io
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone

import cv2

from google import genai
from google.genai import types

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


# =========================
# CONFIG
# =========================
GEMINI_API_KEY = "AIzaSyABjMXV9tnPZVrYorXVc2Zcd5-XEiOFWUY"
MODEL_NAME = "gemini-2.5-flash"

GOOGLE_SHEET_URL = input("Enter the Google Sheet URL: ")
CLIENT_SECRETS_FILE = "client_secret.json"
TOKEN_FILE = "token.json"

BATCH_SIZE = 5
EXTRACT_EVERY_SEC = 1.0
MAX_FRAMES_TO_SEND = 10
RESIZE_MAX_SIDE = 768


# Use THESE themes (instead of reading theme from Google Sheet)
THEMES = [
    "Transform famous brand logos into unexpected objects - Nike swoosh → flying bird, Apple logo → real apple transforming, McDonald's M → golden mountains.",
    "Create hilarious animations with your own photos - turn yourself into a cartoon character and animate characters, create relatable school/college moments with funny effects.",
    "Trending transition effects - smooth scene changes, ordinary objects turning extraordinary, before/after reveals, or trending challenges with your own twist."
]


# =========================
# GEMINI SETUP
# =========================
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# GOOGLE SHEETS & DRIVE SETUP (OAuth)
# =========================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly"
]

def load_oauth_credentials():
    """
    Load OAuth credentials using InstalledAppFlow.
    Handles token refresh automatically.
    """
    creds = None
    
    # Check if token.json exists (stored credentials)
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"⚠️  Warning: Could not load token.json: {e}")
            print("   Will request new authorization...")
            creds = None
    
    # If there are no (valid) credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh expired token
            try:
                print("🔄 Refreshing expired token...")
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️  Token refresh failed: {e}")
                print("   Will request new authorization...")
                creds = None
        
        if not creds:
            # Check if client_secret.json exists
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(
                    f"❌ OAuth client secrets file not found: {CLIENT_SECRETS_FILE}\n\n"
                    f"📋 SETUP INSTRUCTIONS:\n\n"
                    f"1. Go to Google Cloud Console: https://console.cloud.google.com/\n"
                    f"2. Select your project (or create a new one)\n"
                    f"3. Go to 'APIs & Services' > 'Credentials'\n"
                    f"4. Click '+ CREATE CREDENTIALS' > 'OAuth client ID'\n"
                    f"5. If prompted, configure OAuth consent screen:\n"
                    f"   - User Type: External (or Internal if using Google Workspace)\n"
                    f"   - App name: Video Theme Matcher (or any name)\n"
                    f"   - User support email: Your email\n"
                    f"   - Add your email to test users\n"
                    f"   - Scopes: Add '.../auth/spreadsheets' and '.../auth/drive.readonly'\n"
                    f"6. Application type: 'Desktop app'\n"
                    f"7. Name: 'Video Theme Matcher Client'\n"
                    f"8. Click 'Create'\n"
                    f"9. Click 'Download JSON'\n"
                    f"10. Rename the downloaded file to: {CLIENT_SECRETS_FILE}\n"
                    f"11. Place it in the project root directory\n\n"
                    f"After downloading, run the script again."
                )
            
            # Request authorization
            try:
                print(f"🔐 Starting OAuth authorization flow...")
                print(f"   A browser window will open for you to sign in with your Google account.")
                print(f"   Make sure you're signed in to the Gmail account that has access to the Drive files.\n")
                
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                error_msg = str(e)
                if "invalid_client" in error_msg.lower() or "client_secret" in error_msg.lower():
                    raise RuntimeError(
                        f"❌ Invalid OAuth client configuration.\n\n"
                        f"Please verify that {CLIENT_SECRETS_FILE} is the correct file downloaded from Google Cloud Console.\n"
                        f"If you've updated the OAuth client, delete {TOKEN_FILE} and try again.\n\n"
                        f"Original error: {error_msg}"
                    )
                elif "access_denied" in error_msg.lower() or "consent" in error_msg.lower():
                    raise RuntimeError(
                        f"❌ OAuth consent denied or failed.\n\n"
                        f"SOLUTION:\n"
                        f"1. Delete the token file: {TOKEN_FILE}\n"
                        f"2. Make sure your OAuth consent screen is properly configured\n"
                        f"3. Add your email as a test user in OAuth consent screen (if app is in testing mode)\n"
                        f"4. Run the script again\n\n"
                        f"Original error: {error_msg}"
                    )
                else:
                    raise RuntimeError(
                        f"❌ OAuth authorization failed: {error_msg}\n\n"
                        f"If this persists, try:\n"
                        f"1. Delete {TOKEN_FILE} and run again\n"
                        f"2. Verify {CLIENT_SECRETS_FILE} is correct\n"
                        f"3. Check that required APIs are enabled in Google Cloud Console"
                    )
        
        # Save the credentials for the next run
        try:
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
            print(f"✅ Authorization successful! Token saved to {TOKEN_FILE}\n")
        except Exception as e:
            print(f"⚠️  Warning: Could not save token to {TOKEN_FILE}: {e}")
            print("   You may need to authorize again on next run.")
    
    return creds

# Load OAuth credentials
creds = load_oauth_credentials()
sheets_service = build("sheets", "v4", credentials=creds)
drive_service = build("drive", "v3", credentials=creds)


# =========================
# SHEET URL PARSER
# =========================
def parse_sheet_url(url: str) -> Tuple[str, Optional[int]]:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise ValueError("Invalid Google Sheets URL: Could not extract spreadsheetId.")
    spreadsheet_id = m.group(1)

    gid = None
    m2 = re.search(r"gid=(\d+)", url)
    if m2:
        gid = int(m2.group(1))

    return spreadsheet_id, gid


def gid_to_sheet_name(spreadsheet_id: str, gid: Optional[int]) -> str:
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = meta.get("sheets", [])
    if not sheets:
        raise RuntimeError("No sheets found in spreadsheet.")

    if gid is None:
        return sheets[0]["properties"]["title"]

    for s in sheets:
        props = s.get("properties", {})
        if props.get("sheetId") == gid:
            return props.get("title")

    return sheets[0]["properties"]["title"]


# =========================
# HEADER-BASED COLUMN MAP
# =========================
def normalize_header(h: str) -> str:
    return re.sub(r"\s+", "_", h.strip().lower())

def read_header(spreadsheet_id: str, sheet_name: str) -> List[str]:
    rng = f"{sheet_name}!1:1"
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=rng
    ).execute()
    values = resp.get("values", [])
    return values[0] if values else []

def build_header_map(headers: List[str]) -> Dict[str, int]:
    return {normalize_header(h): idx for idx, h in enumerate(headers) if h.strip()}

def col_to_a1(col_idx_0: int) -> str:
    n = col_idx_0 + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# =========================
# VIDEO + IMAGE HELPERS
# =========================
def extract_file_id_from_url(drive_url: str) -> str:
    """Extract file ID from various Google Drive URL formats."""
    # Pattern 1: https://drive.google.com/file/d/FILE_ID/view
    # Pattern 2: https://drive.google.com/open?id=FILE_ID
    # Pattern 3: https://drive.google.com/uc?id=FILE_ID
    # Pattern 4: FILE_ID (if just the ID is provided)
    
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
        r"/uc\?id=([a-zA-Z0-9_-]+)",
        r"^([a-zA-Z0-9_-]+)$"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, drive_url)
        if match:
            return match.group(1)
    
    raise ValueError(f"Could not extract file ID from URL: {drive_url}")

def download_drive_video(drive_url: str, out_path: str) -> str:
    """Download video from Google Drive using service account credentials."""
    try:
        # Extract file ID from URL
        file_id = extract_file_id_from_url(drive_url)
        
        # Request file metadata to verify access
        try:
            file_metadata = drive_service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
            file_name = file_metadata.get('name', 'video')
            print(f"Downloading: {file_name}")
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "notFound" in error_str:
                raise RuntimeError(
                    f"❌ FILE NOT FOUND (404 Error)\n\n"
                    f"The file cannot be accessed with your current Google account.\n\n"
                    f"📋 POSSIBLE SOLUTIONS:\n\n"
                    f"1. Verify the Drive link is correct\n"
                    f"2. Make sure you're signed in to the Gmail account that has access to this file\n"
                    f"3. If the file is owned by someone else, ask them to share it with your Gmail account\n"
                    f"4. Check that the file hasn't been deleted or moved\n\n"
                    f"File ID: {file_id}\n"
                    f"Original error: {error_str}"
                )
            else:
                raise RuntimeError(
                    f"Cannot access file. Make sure:\n"
                    f"1. You're signed in to the Gmail account that has access to this file\n"
                    f"2. The file is shared with your Gmail account (if owned by someone else)\n"
                    f"3. The Drive link is correct\n"
                    f"Original error: {error_str}"
                )
        
        # Download the file
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.FileIO(out_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Download progress: {int(status.progress() * 100)}%")
        
        fh.close()
        
        if not os.path.exists(out_path):
            raise RuntimeError("Download completed but file not found at output path.")
        
        return out_path
        
    except Exception as e:
        if "Cannot access file" in str(e) or "PERMISSION_DENIED" in str(e):
            raise RuntimeError(
                f"Cannot access Google Drive file.\n"
                f"Make sure:\n"
                f"1. You're signed in to the Gmail account that has access to this file\n"
                f"2. The file is shared with your Gmail account (if owned by someone else)\n"
                f"3. The Drive link is correct\n"
                f"Original error: {str(e)}"
            )
        raise RuntimeError(f"Failed to download video from Google Drive: {str(e)}")

def video_to_frames(video_path: str, output_dir: str, every_sec: float) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        cap.release()
        raise RuntimeError("Could not read FPS from video.")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = frame_count / fps if fps else 0

    saved_paths = []
    t = 0.0
    idx = 0

    while t <= duration_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break

        out_path = os.path.join(output_dir, f"frame_{idx:06d}_t{int(t):04d}s.jpg")
        cv2.imwrite(out_path, frame)
        saved_paths.append(out_path)

        idx += 1
        t += every_sec

    cap.release()
    return saved_paths

def resize_in_place(image_path: str, max_side: int) -> None:
    img = cv2.imread(image_path)
    if img is None:
        return
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return

    scale = max_side / float(longest)
    new_w = int(w * scale)
    new_h = int(h * scale)

    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    cv2.imwrite(image_path, resized, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

def pick_frames_evenly(frame_paths: List[str], max_frames: int) -> List[str]:
    if len(frame_paths) <= max_frames:
        return frame_paths

    step = (len(frame_paths) - 1) / float(max_frames - 1)
    picks = []
    for i in range(max_frames):
        idx = round(i * step)
        picks.append(frame_paths[int(idx)])
    return picks

def load_images_for_gemini(frame_paths: List[str]) -> List[types.Part]:
    parts: List[types.Part] = []
    for p in frame_paths:
        with open(p, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/jpeg"))
    return parts


# =========================
# GEMINI ANALYSIS (AUTO-THEME)
# =========================
def analyze_frames_with_gemini(frame_paths: List[str]) -> Dict[str, Any]:
    img_parts = load_images_for_gemini(frame_paths)

    themes_block = "\n".join([f"- {t}" for t in THEMES])

    prompt = f"""
You will see multiple frames from a single video.
Your job: pick the BEST matching theme from the list and score the match.

Themes (choose exactly ONE):
{themes_block}

Return ONLY a single JSON object with exactly these keys:
- "best_theme": string, must be exactly one of the themes above
- "match_score": integer 0 to 100 (overall score: how well the video matches the chosen theme)
- "feedback": a short 1–2 line feedback explaining the overall score
- "evaluation_criteria": object with these keys (each integer 0 to 100):
  - "relevance": how well the video content matches the chosen theme
  - "visual_quality": clarity, resolution, and visual appeal of the video
  - "creativity": originality and creative execution of the concept
  - "technical_execution": quality of editing, transitions, and technical aspects
  - "engagement_potential": how likely the video is to engage and captivate viewers

No extra keys. No markdown. No commentary outside JSON.

Example:
{{"best_theme":"Transform famous brand logos into unexpected objects - Nike swoosh → flying bird, Apple logo → real apple transforming, McDonald's M → golden mountains.","match_score":72,"feedback":"Mostly matches the theme, but some frames are unrelated.","evaluation_criteria":{{"relevance":75,"visual_quality":70,"creativity":80,"technical_execution":65,"engagement_potential":70}}}}
""".strip()

    contents = img_parts + [prompt]

    resp = gemini_client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    raw = resp.text or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise RuntimeError(f"Gemini did not return valid JSON. Raw: {raw}")
        data = json.loads(m.group(0))

    best_theme = str(data.get("best_theme", "")).strip()
    if best_theme not in THEMES:
        # hard fail to avoid writing garbage into sheet
        raise RuntimeError(f'Invalid best_theme returned: "{best_theme}"')

    criteria = data.get("evaluation_criteria", {})
    if not isinstance(criteria, dict):
        criteria = {}

    return {
        "best_theme": best_theme,
        "match_score": int(data.get("match_score", 0)),
        "feedback": str(data.get("feedback", "")).strip(),
        "evaluation_criteria": {
            "relevance": int(criteria.get("relevance", 0)),
            "visual_quality": int(criteria.get("visual_quality", 0)),
            "creativity": int(criteria.get("creativity", 0)),
            "technical_execution": int(criteria.get("technical_execution", 0)),
            "engagement_potential": int(criteria.get("engagement_potential", 0)),
        }
    }


# =========================
# SHEETS IO
# =========================
def read_all_rows(spreadsheet_id: str, sheet_name: str) -> List[List[str]]:
    rng = f"{sheet_name}!A2:Z"
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=rng
    ).execute()
    return resp.get("values", [])

def batch_update(spreadsheet_id: str, updates: List[Dict[str, Any]]) -> None:
    body = {"valueInputOption": "RAW", "data": updates}
    sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body=body
    ).execute()


# =========================
# ID HELPERS
# =========================
def safe_int(x: str) -> Optional[int]:
    try:
        return int(str(x).strip())
    except Exception:
        return None

def next_missing_ids(rows: List[List[str]], id_col: int) -> Dict[int, int]:
    used = set()
    for r in rows:
        if len(r) > id_col:
            v = safe_int(r[id_col])
            if v is not None:
                used.add(v)

    next_id = 1
    mapping = {}
    for sheet_row_num, r in enumerate(rows, start=2):
        existing = safe_int(r[id_col]) if len(r) > id_col else None
        if existing is None:
            while next_id in used:
                next_id += 1
            mapping[sheet_row_num] = next_id
            used.add(next_id)
            next_id += 1

    return mapping


# =========================
# MAIN LOOP
# =========================
def process_one_video(drive_link: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="video_theme_") as tmpdir:
        video_path = os.path.join(tmpdir, "input_video.mp4")
        frames_dir = os.path.join(tmpdir, "frames")

        download_drive_video(drive_link, video_path)

        all_frames = video_to_frames(video_path, frames_dir, every_sec=EXTRACT_EVERY_SEC)
        if not all_frames:
            return {
                "best_theme": "",
                "match_score": 0,
                "feedback": "No frames extracted from video.",
                "evaluation_criteria": {
                    "relevance": 0,
                    "visual_quality": 0,
                    "creativity": 0,
                    "technical_execution": 0,
                    "engagement_potential": 0,
                }
            }

        selected = pick_frames_evenly(all_frames, MAX_FRAMES_TO_SEND)
        for fp in selected:
            resize_in_place(fp, RESIZE_MAX_SIDE)

        result = analyze_frames_with_gemini(selected)
        return result


def main():
    print(f"✅ Using OAuth authentication with your Google account")
    print(f"   Make sure your Google Sheet is accessible to the account you signed in with\n")
    
    spreadsheet_id, gid = parse_sheet_url(GOOGLE_SHEET_URL)
    sheet_name = gid_to_sheet_name(spreadsheet_id, gid)

    # Test credentials by trying to read headers
    try:
        headers = read_header(spreadsheet_id, sheet_name)
    except Exception as e:
        error_msg = str(e)
        if "PERMISSION_DENIED" in error_msg or "access" in error_msg.lower():
            raise RuntimeError(
                f"Permission denied accessing Google Sheet.\n"
                f"This usually means:\n"
                f"1. The service account email doesn't have access to the sheet\n"
                f"2. The credentials.json file is incorrect or outdated\n"
                f"3. The sheet hasn't been shared with the service account\n\n"
                f"To fix:\n"
                f"- Share your Google Sheet with the service account email (Editor access)\n"
                f"- Verify credentials.json is the correct file\n"
                f"- Original error: {error_msg}"
            )
        else:
            raise RuntimeError(f"Failed to read Google Sheet: {error_msg}")
    
    if not headers:
        raise RuntimeError("Header row (Row 1) is empty. Add headers like: Id, Drive Link, match_score, feedback, status, processed_at")

    hmap = build_header_map(headers)
    
    # Show what headers were found (for debugging)
    found_headers = list(hmap.keys())
    print(f"Found headers: {', '.join(found_headers)}")

    # Required headers (theme is OPTIONAL now; if present, we'll write best_theme into it)
    required = ["id", "drive_link", "match_score", "feedback", "status", "processed_at"]
    missing = [r for r in required if r not in hmap]
    if missing:
        raise RuntimeError(
            f"Missing required headers in Row 1: {missing}\n\n"
            f"Found headers: {', '.join(found_headers)}\n"
            f"Required headers: {', '.join(required)}\n\n"
            f"Note: Headers are case-insensitive. 'Drive Link' and 'drive_link' are both valid.\n"
            f"Please add the missing headers to Row 1 of your Google Sheet."
        )

    id_col = hmap["id"]
    drive_col = hmap["drive_link"]
    score_col = hmap["match_score"]
    feedback_col = hmap["feedback"]
    status_col = hmap["status"]
    ts_col = hmap["processed_at"]

    # We'll write best_theme into "theme" column if it exists; else require "best_theme"
    theme_out_col = None
    if "theme" in hmap:
        theme_out_col = hmap["theme"]
    elif "best_theme" in hmap:
        theme_out_col = hmap["best_theme"]
    else:
        # If you want best theme written somewhere, add a column header "theme" or "best_theme"
        theme_out_col = None

    rows = read_all_rows(spreadsheet_id, sheet_name)

    # 1) Ensure Ids exist (auto-fill missing)
    id_assignments = next_missing_ids(rows, id_col)
    updates: List[Dict[str, Any]] = []

    for sheet_row_num, new_id in id_assignments.items():
        a1 = col_to_a1(id_col)
        updates.append({"range": f"{sheet_name}!{a1}{sheet_row_num}:{a1}{sheet_row_num}", "values": [[new_id]]})

    if updates:
        batch_update(spreadsheet_id, updates)
        updates = []
        rows = read_all_rows(spreadsheet_id, sheet_name)

    # 2) Process rows in batches
    processed_count = 0

    for sheet_row_num, r in enumerate(rows, start=2):
        if processed_count >= BATCH_SIZE:
            break

        row_id = safe_int(r[id_col]) if len(r) > id_col else None
        drive_link = r[drive_col].strip() if len(r) > drive_col else ""

        # Skip rows without an Id
        if row_id is None:
            continue
        
        # Skip rows with empty drive links
        if not drive_link:
            continue

        status_a1 = col_to_a1(status_col)
        updates.append({"range": f"{sheet_name}!{status_a1}{sheet_row_num}:{status_a1}{sheet_row_num}",
                        "values": [["PROCESSING"]]})

        try:
            result = process_one_video(drive_link)
            best_theme = result["best_theme"]
            score = result["match_score"]
            feedback = result["feedback"]
            criteria = result.get("evaluation_criteria", {})
            
            ts = datetime.now(timezone.utc).isoformat()

            score_a1 = col_to_a1(score_col)
            feedback_a1 = col_to_a1(feedback_col)
            ts_a1 = col_to_a1(ts_col)

            updates.extend([
                {"range": f"{sheet_name}!{score_a1}{sheet_row_num}:{score_a1}{sheet_row_num}", "values": [[score]]},
                {"range": f"{sheet_name}!{feedback_a1}{sheet_row_num}:{feedback_a1}{sheet_row_num}", "values": [[feedback]]},
                {"range": f"{sheet_name}!{status_a1}{sheet_row_num}:{status_a1}{sheet_row_num}", "values": [["DONE"]]},
                {"range": f"{sheet_name}!{ts_a1}{sheet_row_num}:{ts_a1}{sheet_row_num}", "values": [[ts]]},
            ])

            # Write best theme if we have a column for it
            if theme_out_col is not None:
                theme_a1 = col_to_a1(theme_out_col)
                updates.append({"range": f"{sheet_name}!{theme_a1}{sheet_row_num}:{theme_a1}{sheet_row_num}",
                                "values": [[best_theme]]})

            # Write evaluation criteria scores if columns exist
            criteria_columns = {
                "relevance": "relevance_score",
                "visual_quality": "visual_quality_score",
                "creativity": "creativity_score",
                "technical_execution": "technical_execution_score",
                "engagement_potential": "engagement_potential_score",
            }
            
            for criteria_key, column_name in criteria_columns.items():
                if column_name in hmap:
                    criteria_a1 = col_to_a1(hmap[column_name])
                    criteria_value = criteria.get(criteria_key, 0)
                    updates.append({
                        "range": f"{sheet_name}!{criteria_a1}{sheet_row_num}:{criteria_a1}{sheet_row_num}",
                        "values": [[criteria_value]]
                    })

            print(f"DONE | Id={row_id} | theme={best_theme} | score={score}")

        except Exception as e:
            ts = datetime.now(timezone.utc).isoformat()
            feedback_a1 = col_to_a1(feedback_col)
            ts_a1 = col_to_a1(ts_col)
            status_a1 = col_to_a1(status_col)

            updates.extend([
                {"range": f"{sheet_name}!{feedback_a1}{sheet_row_num}:{feedback_a1}{sheet_row_num}",
                 "values": [[f"Error: {str(e)[:180]}"]]},
                {"range": f"{sheet_name}!{status_a1}{sheet_row_num}:{status_a1}{sheet_row_num}",
                 "values": [["ERROR"]]},
                {"range": f"{sheet_name}!{ts_a1}{sheet_row_num}:{ts_a1}{sheet_row_num}",
                 "values": [[ts]]},
            ])

            print(f"ERROR | Id={row_id} | {e}")

        processed_count += 1

        if len(updates) >= 20:
            batch_update(spreadsheet_id, updates)
            updates = []

    if updates:
        batch_update(spreadsheet_id, updates)

    print(f"Processed {processed_count} row(s).")


if __name__ == "__main__":
    main()
