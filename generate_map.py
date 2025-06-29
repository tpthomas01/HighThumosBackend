from flask import Flask, jsonify, render_template
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

def get_sheet_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)

    sheet = client.open("High Thumos Brotherhood Map (Responses)").sheet1
    data = sheet.get_all_records()
    return data

@app.route('/')
def home():
    return render_template('index.html')  # Serves the map

@app.route('/data')
def serve_data():
    try:
        data = get_sheet_data()
        print("Fetched data:", data)
        return jsonify(data)
    except Exception as e:
        print("Error:", e)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))