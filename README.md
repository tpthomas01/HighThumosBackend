# Discord Member Map Backend

Flask API for the High Thumos Brotherhood map project.

This service:

- reads member rows from Google Sheets
- geocodes missing locations into latitude/longitude
- serves map data to the frontend
- handles Discord OAuth token exchange and user lookup
- links a Discord account to a member marker in the sheet

## Stack

- Python + Flask
- `gspread` + Google Sheets API (service account)
- Discord OAuth2 (server-side token exchange)
- `geopy` (Nominatim) or Geoapify for geocoding
- Render (deployment target used by the frontend)

## Main File

- `generate_map.py`

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies.
3. Set environment variables.
4. Run the Flask app.

Example (PowerShell):

```powershell
cd .\discord-map-backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DISCORD_CLIENT_SECRET = "your_discord_client_secret"
$env:GOOGLE_CREDS_JSON = (Get-Content .\credentials.json -Raw)

# Optional geocoder settings
$env:GEOCODER = "nominatim"   # or "geoapify"
# $env:GEOAPIFY_KEY = "your_geoapify_key"
# $env:GEOCODE_RETRY_HOURS = "12"

python .\generate_map.py
```

The API runs on `http://localhost:5000` by default.

## Environment Variables

Required:

- `DISCORD_CLIENT_SECRET`: Discord OAuth app client secret
- `GOOGLE_CREDS_JSON`: Google service account JSON as a string

Optional:

- `PORT`: Flask port (Render sets this automatically)
- `GEOCODER`: `nominatim` (default) or `geoapify`
- `GEOAPIFY_KEY`: required when `GEOCODER=geoapify`
- `GEOCODE_RETRY_HOURS`: cooldown before retrying failed geocodes (default `12`)

## Google Sheets Data

The backend reads from the spreadsheet:

- `High Thumos Brotherhood Map (Responses)`

Expected member/location fields (used by the app):

- `Name`
- `City, State, Country` (preferred combined field)
- or separate `City`, `State`, `Country`

Auto-managed columns (created when needed):

- `Latitude`
- `Longitude`
- `Geocode Status`
- `Geocoded At`
- `Geocode Last Attempt`
- `Discord ID`
- `Discord Username`
- `Discord Avatar URL`

## API Endpoints

### `GET /`

Health check.

Returns:

```json
{ "ok": true, "service": "discord-map-api" }
```

### `GET /data`

Returns all rows from the Google Sheet as JSON for the frontend map.

### `POST /api/discord/token`

Exchanges a Discord OAuth authorization code for a token.

Request body:

```json
{ "code": "discord_oauth_code" }
```

### `POST /api/discord/user`

Fetches the authenticated Discord user using an access token.

Request body:

```json
{ "access_token": "discord_access_token" }
```

Returns a simplified payload including:

- `id`
- `username`
- `avatar_url`

### `POST /api/link-discord`

Links a Discord user to a selected marker row in the sheet.

Request body:

```json
{
  "selectedMarker": "Name - City, State, Country",
  "discord_id": "1234567890",
  "username": "exampleuser",
  "avatar_url": "https://cdn.discordapp.com/..."
}
```

Note:

- This route currently reads a local `credentials.json` file directly.
- Other routes use `GOOGLE_CREDS_JSON`.
- For production consistency, this route should eventually be refactored to reuse `open_sheet()`.

### `GET /geocode-missing`

Geocodes rows missing `Latitude`/`Longitude`.

Query params:

- `limit` (default `60`): max rows to process this run
- `force=1`: bypass retry cooldown for failed rows

Behavior:

- rate-limits geocoder requests
- caches repeated locations during a run
- writes results in batch updates to Sheets
- tracks retry/cooldown status
- prevents concurrent runs with an in-process lock

### `GET /diag`

Diagnostic endpoint for geocoder/provider connectivity and outbound IP checks.

Useful for debugging deployment/network issues on Render.

## Deployment Notes

- Frontend is configured to call the deployed backend at `https://discord-map-api.onrender.com`.
- Discord OAuth redirect URI is hardcoded in `generate_map.py` and must match the frontend callback page and Discord app settings.
- CORS is enabled for frontend requests.

## Security Notes

- Do not commit real Discord secrets or Google service account credentials.
- Prefer environment variables for all secrets in production.
- Restrict who can trigger maintenance endpoints like `/geocode-missing` if you later make this public.

## Common Improvements (Future)

- Move `/api/link-discord` to the same credential-loading path (`GOOGLE_CREDS_JSON`) as the rest of the app
- Add auth/protection for admin/maintenance endpoints
- Add request logging and error monitoring
- Add tests for OAuth and sheet update flows
- Pin package versions after confirming a known-good deploy/build
