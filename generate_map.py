from flask import Flask, jsonify, request
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import requests
import time
import os

app = Flask(__name__)
CORS(app)

# Discord OAuth2 Credentials
CLIENT_ID = '1390022823929581660'
CLIENT_SECRET = 'DeWv6gfS03P2km2PKzsy41ZPN2qnNfoS'
REDIRECT_URI = 'https://tpthomas01.github.io/HighThumosMap/oauth-callback.html'


@app.route('/api/discord/token', methods=['POST'])
def get_discord_token():
    json_data = request.get_json()
    if not json_data or 'code' not in json_data:
        return jsonify({'error': 'Missing authorization code'}), 400

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
            avatar_url = f"https://cdn.discordapp.com/embed/avatars/{int(user_data['discriminator']) % 5}.png"

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
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sheet = client.open("High Thumos Brotherhood Map (Responses)").sheet1

    all_values = sheet.get_all_values()
    if not all_values:
        return []

    header = [h.strip() for h in all_values[0]]
    data = sheet.get_all_records(head=1)

    if "Latitude" not in header:
        sheet.update_cell(1, len(header) + 1, "Latitude")
        header.append("Latitude")
    if "Longitude" not in header:
        sheet.update_cell(1, len(header) + 1 if "Longitude" not in header else header.index("Longitude") + 1, "Longitude")
        header = sheet.row_values(1)

    location_idx = header.index("City, State, Country") + 1
    lat_idx = header.index("Latitude") + 1
    lon_idx = header.index("Longitude") + 1

    geolocator = Nominatim(user_agent="high-thumos-map")
    for i, row in enumerate(data):
        row_num = i + 2
        lat = row.get("Latitude")
        lon = row.get("Longitude")
        location = row.get("City, State, Country")

        if (not lat or str(lat).strip() == "") or (not lon or str(lon).strip() == ""):
            if location:
                try:
                    geo = geolocator.geocode(location, timeout=10)
                    if geo:
                        sheet.update_cell(row_num, lat_idx, geo.latitude)
                        sheet.update_cell(row_num, lon_idx, geo.longitude)
                        print(f"✅ Row {row_num}: {location} → ({geo.latitude}, {geo.longitude})")
                        time.sleep(1)
                    else:
                        print(f"⚠️  Could not geocode: {location}")
                except GeocoderTimedOut:
                    print(f"⏱️  Timeout geocoding row {row_num}: {location}")
                    continue
                except Exception as e:
                    print(f"❌ Error geocoding row {row_num}: {e}")
                    continue

    return sheet.get_all_records(head=1)


@app.route('/data')
def serve_data():
    try:
        data = get_sheet_data()
        return jsonify(data)
    except Exception as e:
        print("Server error:", e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
