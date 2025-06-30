from flask import Flask, jsonify
from flask_cors import CORS
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import time

app = Flask(__name__)
CORS(app)

def get_sheet_data():
    # Set up Google Sheets access
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sheet = client.open("High Thumos Brotherhood Map (Responses)").sheet1

    # Get all rows and clean header
    all_values = sheet.get_all_values()
    if not all_values:
        return []

    header = [h.strip() for h in all_values[0]]
    data = sheet.get_all_records(head=1)

    # Ensure Latitude and Longitude columns exist
    if "Latitude" not in header:
        sheet.update_cell(1, len(header) + 1, "Latitude")
        header.append("Latitude")
    if "Longitude" not in header:
        sheet.update_cell(1, len(header) + 1 if "Longitude" not in header else header.index("Longitude") + 1, "Longitude")
        header = sheet.row_values(1)

    # Column indexes (1-based)
    location_idx = header.index("City, State, Country") + 1
    lat_idx = header.index("Latitude") + 1
    lon_idx = header.index("Longitude") + 1

    # Geolocate any rows with missing coordinates
    geolocator = Nominatim(user_agent="high-thumos-map")
    for i, row in enumerate(data):
        row_num = i + 2  # account for header

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
                        time.sleep(1)  # Respect Nominatim rate limit
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
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
