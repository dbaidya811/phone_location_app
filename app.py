from flask import Flask, render_template, request, flash, redirect, url_for
import phonenumbers
from phonenumbers import geocoder, carrier, PhoneNumberFormat
import folium
import os
import sys
import csv
import pycountry
from geopy.geocoders import Nominatim
import secrets
import time

# Load India prefix->circle mapping (if available)
INDIA_PREFIX_CIRCLES = {}
DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'india_mobile_prefixes.csv')
if os.path.exists(DATA_PATH):
    try:
        with open(DATA_PATH, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                p = row.get('prefix', '').strip()
                c = row.get('circle', '').strip()
                if p and c:
                    INDIA_PREFIX_CIRCLES[p] = c
    except Exception as _:
        INDIA_PREFIX_CIRCLES = {}

def get_india_circle(national_number: str):
    # Try longest prefixes first (supports 6..3 digits)
    s = str(national_number)
    for length in (6, 5, 4, 3):
        if len(s) >= length:
            pref = s[:length]
            if pref in INDIA_PREFIX_CIRCLES:
                return INDIA_PREFIX_CIRCLES[pref]
    return None

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Required for flash messages

# In-memory live location store: { token: {"lat": float, "lng": float, "ts": epoch} }
LIVE_LOCATIONS = {}

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        phone_number = request.form.get('phone_number', '').strip()
        
        # Normalize input: keep digits and '+', convert leading '00' to '+'
        normalized = ''.join(ch for ch in phone_number if ch.isdigit() or ch == '+')
        if normalized.startswith('00'):
            normalized = '+' + normalized[2:]
        
        if not phone_number:
            flash('Please enter a phone number', 'error')
            return redirect(url_for('index'))
            
        try:
            # Parse the phone number
            default_region = 'BD'  # Fallback region when '+' not provided
            region = None if normalized.startswith('+') else default_region
            parsed_number = phonenumbers.parse(normalized, region)
            
            # Basic validation
            if not phonenumbers.is_valid_number(parsed_number):
                flash('Invalid phone number. Please include country code (e.g., +880 for Bangladesh)', 'error')
                return redirect(url_for('index'))
            
            # Get country code and national number
            country_code = parsed_number.country_code
            national_number = parsed_number.national_number
            
            # Get location (country/region)
            try:
                region_code = phonenumbers.region_code_for_country_code(country_code)
                location = geocoder.description_for_number(parsed_number, "en") or f"Country Code: +{country_code}"
            except:
                location = f"Country Code: +{country_code}"
            
            # Get service provider if available
            try:
                service_provider = carrier.name_for_number(parsed_number, "en") or "Unknown Carrier"
            except:
                service_provider = "Unknown Carrier"
            
            # Determine coordinates by geocoding the best available place string
            geolocator = Nominatim(user_agent="phone_locator_app")
            zoom = 5
            coordinates = None
            derived_circle = None
            try:
                # Country name from code
                region_code = phonenumbers.region_code_for_country_code(country_code)
                country = pycountry.countries.get(alpha_2=region_code)
                country_name = country.name if country else f"+{country_code}"

                # Try detailed location first (e.g., state/city for some countries)
                query = None
                # For India, try circle from prefix database first
                if country_code == 91 and national_number:
                    derived_circle = get_india_circle(str(national_number))
                    if derived_circle:
                        query = f"{derived_circle}, India"
                # If no circle or not India, try phonenumbers description
                if not query and location and location.strip() and location.strip().lower() not in {"unknown", ""}:
                    query = f"{location}, {country_name}"

                if query:
                    loc = geolocator.geocode(query, exactly_one=True, timeout=10)
                    if loc:
                        coordinates = {'lat': loc.latitude, 'lng': loc.longitude}
                        zoom = 9

                # Fallback to country centroid
                if coordinates is None and country_name:
                    loc = geolocator.geocode(country_name, exactly_one=True, timeout=10)
                    if loc:
                        coordinates = {'lat': loc.latitude, 'lng': loc.longitude}
                        zoom = 5

            except Exception as _:
                pass

            # Final fallback: Dhaka center
            if coordinates is None:
                coordinates = {'lat': 23.8103, 'lng': 90.4125}
                zoom = 5
            
            # Format number for display
            phone_display = phonenumbers.format_number(parsed_number, PhoneNumberFormat.INTERNATIONAL)

            # Create a simple map
            m = folium.Map(location=[coordinates['lat'], coordinates['lng']], zoom_start=zoom)
            folium.Marker(
                [coordinates['lat'], coordinates['lng']],
                popup=f"{phone_display}\n{location}",
                icon=folium.Icon(color='red')
            ).add_to(m)
            
            # Save the map to the static directory so it can be embedded via iframe
            os.makedirs('static', exist_ok=True)
            map_path = os.path.join('static', 'map.html')
            m.save(map_path)
            
            # Generate shareable live tracking links
            token = secrets.token_urlsafe(8)
            share_url = url_for('share_location', token=token, _external=True)
            live_url = url_for('live_view', token=token, _external=True)

            return render_template('result.html',
                                phone_number=phone_display,
                                location=location,
                                service_provider=service_provider,
                                country_code=country_code,
                                national_number=national_number,
                                derived_circle=derived_circle,
                                share_url=share_url,
                                live_url=live_url,
                                token=token)
            
        except Exception as e:
            print(f"Error: {str(e)}", file=sys.stderr)
            flash(f'Error processing phone number: {str(e)}', 'error')
            return redirect(url_for('index'))
    
    return render_template('index.html')

# ---------------------- Live location endpoints ----------------------

@app.route('/share/<token>')
def share_location(token):
    # Page that asks user to share GPS and posts it back
    return render_template('share.html', token=token)


@app.route('/live/<token>')
def live_view(token):
    # Viewer page that polls for latest location and shows on a map
    return render_template('live.html', token=token)


@app.route('/api/loc/<token>', methods=['GET', 'POST'])
def api_location(token):
    if request.method == 'POST':
        try:
            data = request.get_json(force=True, silent=True) or {}
            lat = float(data.get('lat'))
            lng = float(data.get('lng'))
            LIVE_LOCATIONS[token] = {"lat": lat, "lng": lng, "ts": time.time()}
            return {"ok": True}
        except Exception as _:
            return {"ok": False, "error": "invalid_payload"}, 400
    else:
        loc = LIVE_LOCATIONS.get(token)
        if not loc:
            return {"ok": True, "loc": None}
        return {"ok": True, "loc": loc}

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    # Bind to all interfaces so phones on same Wi‑Fi can open the share/live links
    app.run(debug=True, host='0.0.0.0')
