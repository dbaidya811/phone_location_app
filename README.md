# Phone Number Location Finder

Flask web app to find approximate location from a phone number, with enhanced support for Indian mobile number circle detection and consent-based live location sharing.

## Features
- Phone number parsing and validation (`phonenumbers`)
- Geocoding to approximate location (`geopy` + Folium map)
- India-specific mobile prefix to circle/state lookup (`data/india_mobile_prefixes.csv`)
- Interactive map embedded via iframe (`static/map.html`)
- Live location sharing:
  - Share link: sender grants browser GPS permission and streams location
  - Live map link: viewer sees real-time updates
  - In-memory storage of latest coordinates per token

## Project structure
```
phone_location_app/
├─ app.py
├─ requirements.txt
├─ data/
│  └─ india_mobile_prefixes.csv
├─ static/
│  ├─ css/
│  │  └─ style.css
│  ├─ favicon.svg
│  └─ map.html   # auto-generated at runtime
└─ templates/
   ├─ base.html
   ├─ index.html
   ├─ result.html
   ├─ share.html
   └─ live.html
```

## Prerequisites
- Python 3.9+
- pip

## Installation
```bash
# From the project root (the folder that contains phone_location_app/)
cd phone_location_app
pip install -r requirements.txt
```

If you plan to deploy, also install Gunicorn (add to requirements.txt):
```bash
pip install gunicorn
```
Then add this line to `requirements.txt` if not present:
```
gunicorn
```

## Running locally
```bash
# from phone_location_app/
python app.py
```
- App runs at: http://127.0.0.1:5000
- For LAN testing (share links with a phone on the same Wi‑Fi), the app binds to `0.0.0.0` in debug mode.
- If you need to test outside your network, use a tunnel (ngrok):
  ```bash
  ngrok http 5000
  # use the https URL that ngrok prints
  ```

## Usage
1. Open the homepage and enter a phone number (with or without +/00). Example: `+918888888888` or `918888888888`.
2. The result page shows:
   - Country, service area/description, derived Indian circle (if applicable)
   - Folium map approximate location (iframe)
   - Live location links:
     - Share link: open on the sender’s phone, allow GPS, keep page open
     - Live map link: open to view updates in near real-time

## Live Location Internals
- Endpoints
  - `GET /share/<token>`: sender page requests geolocation and `POST`s updates to server
  - `GET /live/<token>`: viewer page displaying a Leaflet map; polls `/api/loc/<token>`
  - `GET|POST /api/loc/<token>`: backend to store/fetch latest `{lat, lng, ts}` in memory
- Storage
  - In-memory dict `LIVE_LOCATIONS` inside `app.py` (temporary, cleared on restart)
- Privacy
  - Location sharing requires explicit consent on the sender device; close the tab to stop sharing

## India Circle Detection
- CSV `data/india_mobile_prefixes.csv` maps Indian mobile prefixes to telecom circles/states
- The app detects circle by matching the normalized number’s prefix, then geocodes the circle name for better map centering
- You can extend the CSV with more prefixes for better coverage

## Deployment (Render.com recommended)
1. Push the project to GitHub (the repo root should contain the `phone_location_app/` folder and `requirements.txt`).
2. Ensure `requirements.txt` includes:
   - `Flask`, `phonenumbers`, `folium`, `geopy`, `pycountry`, `python-dotenv` (optional), `gunicorn`
3. On Render:
   - New → Web Service → Connect your repo
   - Environment: Python 3.x
   - Build command: (empty, Render auto-installs from `requirements.txt`)
   - Start command:
     ```
     gunicorn -w 2 -k gthread -b 0.0.0.0:$PORT phone_location_app.app:app
     ```
   - Click Deploy
4. Optional improvements for production:
   - Use environment variable for `SECRET_KEY` in `app.py`:
     ```python
     app.secret_key = os.getenv('SECRET_KEY', 'dev-secret')
     ```
   - Save the Folium map using `app.static_folder` for portability:
     ```python
     os.makedirs(app.static_folder, exist_ok=True)
     map_path = os.path.join(app.static_folder, 'map.html')
     ```

## Troubleshooting
- Map not visible:
  - Check `static/map.html` exists after a query
  - Browser cache: hard refresh (Ctrl+F5)
- Live map not updating:
  - Sender must have Share page open with location permission granted
  - Check network reachability between devices; use ngrok if on different networks
- Import errors on deploy:
  - Confirm `requirements.txt` includes all packages listed above

## License
MIT (or your preferred license)
