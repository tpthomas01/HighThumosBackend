from flask import Flask, jsonify, request
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
import os
from geopy.extra.rate_limiter import RateLimiter
from gspread.utils import rowcol_to_a1  # NEW
# add these imports if not present
import socket
try:
    import urllib3.util.connection as urllib3_connection
    # Force all HTTP(S) connections to use IPv4 (AF_INET)
    def _allowed_gai_family():
        return socket.AF_INET
    urllib3_connection.allowed_gai_family = _allowed_gai_family
except Exception:
    # If the import layout changes, just skip; app will still run
    pass
# --- Geocoder provider switch
PROVIDER = os.getenv("GEOCODER", "nominatim")  # "geoapify" or "nominatim"
UA = "high-thumos-brotherhood/1.0 (contact: tristanpthomas01@gmail.com)"

class _Result:
    def __init__(self, lat, lon):
        self.latitude = float(lat)
        self.longitude = float(lon)

def make_geocode_callable():
    if PROVIDER == "geoapify":
        KEY = os.environ["GEOAPIFY_KEY"]  # set in Render
        BASE = "https://api.geoapify.com/v1/geocode/search"
        def ga_geocode(query, **kwargs):
            timeout = kwargs.get("timeout", 10)
            r = requests.get(
                BASE,
                params={"text": query, "limit": 1, "apiKey": KEY},
                headers={"User-Agent": UA},
                timeout=timeout,
            )
            r.raise_for_status()
            js = r.json()
            feats = js.get("features") or []
            if feats:
                props = feats[0].get("properties", {})
                lat, lon = props.get("lat"), props.get("lon")
                if lat is not None and lon is not None:
                    return _Result(lat, lon)
            return None
        # Geoapify allows higher RPS, but we’ll stay polite
        return ga_geocode, 1.0
    else:
        from geopy.geocoders import Nominatim
        g = Nominatim(user_agent=UA)
        return g.geocode, 1.5

app = Flask(__name__)
CORS(app)
# -----------------------------
# Helpers for Google Sheets
# -----------------------------
def open_sheet():
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds_info = json.loads(os.environ["GOOGLE_CREDS_JSON"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
    client = gspread.authorize(creds)
    return client.open("High Thumos Brotherhood Map (Responses)").sheet1

def ensure_col(header: list, sheet, name: str) -> int:
    """Ensures a column exists; returns 1-based index."""
    if name not in header:
        sheet.update_cell(1, len(header) + 1, name)
        header.append(name)
    return header.index(name) + 1

from datetime import datetime, timezone, timedelta

RETRY_AFTER_HOURS = int(os.getenv("GEOCODE_RETRY_HOURS", "12"))
RETRY_STATUSES = {"FAILED_RECENTLY", "BAN_COOLDOWN"}

def build_location(row: dict) -> str:
    combo = (row.get("City, State, Country") or "").strip()
    if combo:
        return combo
    parts = [
        (row.get("City") or "").strip(),
        (row.get("State") or "").strip(),
        (row.get("Country") or "").strip(),
    ]
    return ", ".join(p for p in parts if p)

def parse_dt(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None

# Discord OAuth2 Credentials
CLIENT_ID = '1390022823929581660'
CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
REDIRECT_URI = 'https://tpthomas01.github.io/HighThumosMap/oauth-callback.html'


@app.route('/api/discord/token', methods=['POST'])
def get_discord_token():
    json_data = request.get_json()
    if not json_data or 'code' not in json_data:
        return jsonify({'error': 'Missing authorization code'}), 400
    if not CLIENT_SECRET:
        return jsonify({'error': 'Server misconfigured: missing DISCORD_CLIENT_SECRET'}), 500

    code = json_data['code']
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }

    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        token_response = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
        token_response.raise_for_status()
        return jsonify(token_response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/discord/user', methods=['POST'])
def get_discord_user_info():
    access_token = request.json.get('access_token')
    if not access_token:
        return jsonify({'error': 'Missing access token'}), 400

    headers = {
        'Authorization': f'Bearer {access_token}'
    }

    try:
        user_response = requests.get("https://discord.com/api/users/@me", headers=headers)
        user_response.raise_for_status()
        user_data = user_response.json()

        # Build avatar URL (if they have one)
        avatar_url = None
        if user_data.get("avatar"):
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png"
        else:
            # stable generic avatar (no discriminator needed)
            avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

        return jsonify({
            'id': user_data['id'],
            'username': user_data['username'],
            'avatar_url': avatar_url
        })
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/link-discord', methods=['POST'])
def link_discord_to_marker():
    try:
        payload = request.get_json()
        selected_marker = payload.get("selectedMarker")
        discord_id = payload.get("discord_id")
        discord_username = payload.get("username", "").split('#')[0]
        avatar_url = payload.get("avatar_url")

        if not (selected_marker and discord_id and discord_username):
            return jsonify({"error": "Missing required fields"}), 400

        # Access the sheet
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("High Thumos Brotherhood Map (Responses)").sheet1

        headers = [h.strip() for h in sheet.row_values(1)]

        # Ensure Discord columns exist
        discord_id_col = None
        discord_username_col = None
        avatar_url_col = None

        if "Discord ID" not in headers:
            sheet.update_cell(1, len(headers) + 1, "Discord ID")
            headers.append("Discord ID")
        if "Discord Username" not in headers:
            sheet.update_cell(1, len(headers) + 1, "Discord Username")
            headers.append("Discord Username")
        if "Discord Avatar URL" not in headers:
            sheet.update_cell(1, len(headers) + 1, "Discord Avatar URL")
            headers.append("Discord Avatar URL")

        # Get column indices (1-based)
        discord_id_col = headers.index("Discord ID") + 1
        discord_username_col = headers.index("Discord Username") + 1
        avatar_url_col = headers.index("Discord Avatar URL") + 1

        # Find matching row
        all_records = sheet.get_all_records(head=1)
        for i, row in enumerate(all_records):
            marker_label = f"{row.get('Name')} — {row.get('City, State, Country')}"
            if marker_label == selected_marker:
                row_num = i + 2  # account for header row
                sheet.update_cell(row_num, discord_id_col, discord_id)
                sheet.update_cell(row_num, discord_username_col, discord_username)
                sheet.update_cell(row_num, avatar_url_col, avatar_url)
                return jsonify({"success": True})

        return jsonify({"error": "Marker not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_sheet_data():
    sheet = open_sheet()
    return sheet.get_all_records(head=1)

# import socket

# def outbound_ok(host="nominatim.openstreetmap.org", port=443, timeout=4.0):
#     try:
#         with socket.create_connection((host, port), timeout=timeout):
#             return True
#     except Exception:
#         return False

from threading import Lock
job_lock = Lock()

@app.route('/geocode-missing')
def geocode_missing():
    if not job_lock.acquire(False):
        return jsonify({"skipped":"geocode job already running"}), 200
    try:
        limit = int(request.args.get('limit', 60))
        force = request.args.get('force') == '1'   # <— add this
        sheet = open_sheet()

        header = [h.strip() for h in sheet.row_values(1)]
        lat_col    = ensure_col(header, sheet, "Latitude")
        lon_col    = ensure_col(header, sheet, "Longitude")
        status_col = ensure_col(header, sheet, "Geocode Status")
        at_col     = ensure_col(header, sheet, "Geocoded At")
        attempt_col = ensure_col(header, sheet, "Geocode Last Attempt")  # NEW

        data = sheet.get_all_records(head=1)

        geocode_callable, min_delay = make_geocode_callable()
        geocode = RateLimiter(
            geocode_callable,
            min_delay_seconds=min_delay,   # 1.0 for Geoapify, 1.5 for Nominatim
            max_retries=1,
            error_wait_seconds=8.0
        )
        seen = {}

        value_updates = []
        processed = 0
        now = datetime.now(timezone.utc)

        counters = {
            "eligible": 0, "skipped_already_geocoded": 0, "skipped_no_location": 0,
            "skipped_cooldown": 0, "attempted": 0, "ok": 0,
            "failed_empty_result": 0, "failed_exception": 0
        }

        for row_idx, row in enumerate(data, start=2):
            if processed >= limit:
                break

            lat = (row.get("Latitude") or "")
            lon = (row.get("Longitude") or "")
            status = (row.get("Geocode Status") or "").strip()
            last_attempt_s = (row.get("Geocode Last Attempt") or "").strip()

            if str(lat).strip() and str(lon).strip():
                counters["skipped_already_geocoded"] += 1
                continue

            location = build_location(row)
            if not location:
                counters["skipped_no_location"] += 1
                continue

            if status in RETRY_STATUSES and not force:
                last_attempt_dt = parse_dt(last_attempt_s)
                if last_attempt_dt and (now - last_attempt_dt) < timedelta(hours=RETRY_AFTER_HOURS):
                    counters["skipped_cooldown"] += 1
                    continue

            counters["eligible"] += 1

            try:
                 # Use in-run cache first
                if location in seen:
                    res = seen[location]
                else:
                    res = geocode(location, timeout=10)  # our wrapper ignores extra args
                    seen[location] = res

                attempt_a1 = rowcol_to_a1(row_idx, attempt_col)
                value_updates.append({"range": attempt_a1, "values": [[now.strftime("%Y-%m-%d %H:%M:%S")]]})

                if res:
                    lat_a1    = rowcol_to_a1(row_idx, lat_col)
                    lon_a1    = rowcol_to_a1(row_idx, lon_col)
                    status_a1 = rowcol_to_a1(row_idx, status_col)
                    at_a1     = rowcol_to_a1(row_idx, at_col)

                    value_updates += [
                        {"range": lat_a1,    "values": [[res.latitude]]},
                        {"range": lon_a1,    "values": [[res.longitude]]},
                        {"range": status_a1, "values": [["OK"]]},
                        {"range": at_a1,     "values": [[now.strftime("%Y-%m-%d %H:%M:%S")]]},
                    ]
                    counters["ok"] += 1
                else:
                    status_a1 = rowcol_to_a1(row_idx, status_col)
                    value_updates.append({"range": status_a1, "values": [["FAILED_RECENTLY"]]})
                    counters["failed_empty_result"] += 1

                processed += 1
                counters["attempted"] += 1

            except Exception as ex:
                app.logger.exception(f"Geocode error for '{location}': {type(ex).__name__}: {ex}")
                status_a1  = rowcol_to_a1(row_idx, status_col)
                attempt_a1 = rowcol_to_a1(row_idx, attempt_col)
                value_updates += [
                    {"range": status_a1,  "values": [["BAN_COOLDOWN"]]},
                    {"range": attempt_a1, "values": [[now.strftime("%Y-%m-%d %H:%M:%S")]]},
                ]
                counters["failed_exception"] += 1
                break

        if value_updates:
            sheet.batch_update(value_updates, value_input_option='RAW')

        return jsonify({"processed_rows": processed, "counters": counters})

    except Exception as e:
        app.logger.exception("Geocode error")
        return jsonify({"error": str(e)}), 500
    finally:
        job_lock.release()


@app.route("/diag")
def diag():
    out = {"geocoder_provider": PROVIDER}
    UA = "high-thumos-brotherhood/1.0 (contact: tristanpthomas01@gmail.com)"

    try:
        if PROVIDER == "geoapify":
            key = os.getenv("GEOAPIFY_KEY", "")
            r = requests.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": "Zurich, Switzerland", "limit": 1, "apiKey": key},
                headers={"User-Agent": UA},
                timeout=10,
            )
            out["search_http"] = r.status_code
            ct = r.headers.get("content-type", "")
            out["search_body"] = r.json() if "application/json" in ct else r.text[:200]
        else:
            # Nominatim status
            s = requests.get(
                "https://nominatim.openstreetmap.org/status.php",
                headers={"User-Agent": UA},
                timeout=8,
            )
            out["nominatim_status_http"] = s.status_code
            out["nominatim_status_snippet"] = s.text[:200]
            # Nominatim sample search
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": "Zurich, Switzerland", "format": "jsonv2", "limit": 1},
                headers={"User-Agent": UA},
                timeout=10,
            )
            out["search_http"] = r.status_code
            ct = r.headers.get("content-type", "")
            out["search_body"] = r.json() if "application/json" in ct else r.text[:200]
    except Exception as e:
        out["search_error"] = str(e)

    try:
        out["egress_ip"] = requests.get("https://api.ipify.org", timeout=5).text
    except Exception as e:
        out["ip_error"] = str(e)

    return jsonify(out), 200


@app.route('/data')
def serve_data():
    try:
        sheet = open_sheet()
        records = sheet.get_all_records(head=1)
        return jsonify(records)
    except Exception as e:
        print("Server error:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/")
def health():
    return jsonify({"ok": True, "service": "discord-map-api"}), 200

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
