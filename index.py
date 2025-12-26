import os
import re
import json
import tempfile
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone

import cv2
import gdown

from google import genai
from google.genai import types

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# =========================
# CONFIG
# =========================
GEMINI_API_KEY = "AIzaSyABjMXV9tnPZVrYorXVc2Zcd5-XEiOFWUY"
MODEL_NAME = "gemini-2.5-flash"

GOOGLE_SHEET_URL = input("Enter the Google Sheet URL: ")
SERVICE_ACCOUNT_JSON = "credentials.json"

BATCH_SIZE = 5
EXTRACT_EVERY_SEC = 1.0
MAX_FRAMES_TO_SEND = 10
RESIZE_MAX_SIDE = 768


# =========================
# GEMINI SETUP
# =========================
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# GOOGLE SHEETS SETUP
# =========================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_JSON, scopes=SCOPES)
sheets_service = build("sheets", "v4", credentials=creds)


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
    """
    Returns dict: normalized_header -> column_index (0-based)
    """
    return {normalize_header(h): idx for idx, h in enumerate(headers) if h.strip()}

def col_to_a1(col_idx_0: int) -> str:
    """
    0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA ...
    """
    n = col_idx_0 + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# =========================
# VIDEO + IMAGE HELPERS
# =========================
def download_drive_video(drive_url: str, out_path: str) -> str:
    downloaded_path = gdown.download(url=drive_url, output=out_path, quiet=False, fuzzy=True)
    if not downloaded_path or not os.path.exists(downloaded_path):
        raise RuntimeError("Google Drive download failed. Ensure the link is public/shared.")
    return downloaded_path


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
# GEMINI ANALYSIS
# =========================
def analyze_frames_with_gemini(theme: str, frame_paths: List[str]) -> Dict[str, Any]:
    img_parts = load_images_for_gemini(frame_paths)

    prompt = f"""
You will see multiple frames from a single video.

Return ONLY a single JSON object with exactly these keys:
- "match_score": integer 0 to 100 indicating how well the video matches the theme "{theme}"
- "feedback": a short 1–2 line feedback explaining the score

No extra keys. No markdown. No commentary outside JSON.
Example:
{{"match_score": 72, "feedback": "Mostly matches the theme, but some frames are unrelated."}}
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

    return {
        "match_score": int(data.get("match_score", 0)),
        "feedback": str(data.get("feedback", "")).strip(),
    }


# =========================
# SHEETS IO
# =========================
def read_all_rows(spreadsheet_id: str, sheet_name: str) -> List[List[str]]:
    # Read all columns (A:Z) from row 2 onwards. (Adjust if you might go beyond Z.)
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
    """
    Returns dict mapping sheet_row_number -> id_to_set for rows missing Id.
    Ids are assigned from 1..n in row order, skipping already-used IDs.
    """
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
def process_one_video(drive_link: str, theme: str) -> Tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="video_theme_") as tmpdir:
        video_path = os.path.join(tmpdir, "input_video.mp4")
        frames_dir = os.path.join(tmpdir, "frames")

        download_drive_video(drive_link, video_path)

        all_frames = video_to_frames(video_path, frames_dir, every_sec=EXTRACT_EVERY_SEC)
        if not all_frames:
            return 0, "No frames extracted from video."

        selected = pick_frames_evenly(all_frames, MAX_FRAMES_TO_SEND)
        for fp in selected:
            resize_in_place(fp, RESIZE_MAX_SIDE)

        result = analyze_frames_with_gemini(theme, selected)
        return result["match_score"], result["feedback"]


def main():
    spreadsheet_id, gid = parse_sheet_url(GOOGLE_SHEET_URL)
    sheet_name = gid_to_sheet_name(spreadsheet_id, gid)

    headers = read_header(spreadsheet_id, sheet_name)
    if not headers:
        raise RuntimeError("Header row (Row 1) is empty. Add headers like: Id, drive_link, theme, ...")

    hmap = build_header_map(headers)

    # Required headers (normalized)
    required = ["id", "drive_link", "theme", "match_score", "feedback", "status", "processed_at"]
    missing = [r for r in required if r not in hmap]
    if missing:
        raise RuntimeError(f"Missing required headers in Row 1: {missing}")

    id_col = hmap["id"]
    drive_col = hmap["drive_link"]
    theme_col = hmap["theme"]
    score_col = hmap["match_score"]
    feedback_col = hmap["feedback"]
    status_col = hmap["status"]
    ts_col = hmap["processed_at"]

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
        # re-read rows after ID fill (optional but safer)
        rows = read_all_rows(spreadsheet_id, sheet_name)

    # 2) Process rows in batches using header-based columns
    processed_count = 0

    for sheet_row_num, r in enumerate(rows, start=2):
        if processed_count >= BATCH_SIZE:
            break

        # Get values safely
        row_id = safe_int(r[id_col]) if len(r) > id_col else None
        drive_link = r[drive_col].strip() if len(r) > drive_col else ""
        theme = r[theme_col].strip() if len(r) > theme_col else ""
        existing_score = r[score_col].strip() if len(r) > score_col else ""

        if not drive_link or not theme:
            continue
        if existing_score:
            continue

        # Set status = PROCESSING
        status_a1 = col_to_a1(status_col)
        updates.append({"range": f"{sheet_name}!{status_a1}{sheet_row_num}:{status_a1}{sheet_row_num}",
                        "values": [["PROCESSING"]]})

        try:
            score, feedback = process_one_video(drive_link, theme)
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

            print(f"DONE | Id={row_id} | score={score}")

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
