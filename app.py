from flask import Flask, request, jsonify, redirect, send_file, render_template_string
from flask_cors import CORS
from pymongo import MongoClient
import qrcode
import os
import random
import string
from datetime import datetime
from config import MONGO_URI, BASE_URL
import user_agents

# -------------------- Setup --------------------
app = Flask(__name__)
CORS(app)

# MongoDB setup
client = MongoClient(MONGO_URI)
db = client.url_db
urls = db.urls

# QR code folder
QR_DIR = "qrcodes"
os.makedirs(QR_DIR, exist_ok=True)

# -------------------- Helpers --------------------
def generate_short_id(num_chars=6):
    """Generate a random short ID"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=num_chars))

def is_expired(url_data):
    """Check if URL has expired based on expiry date or max clicks"""
    expiry = url_data.get("expiry_date")
    if expiry:
        expiry_dt = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
        if datetime.now() > expiry_dt:
            return True
    max_clicks = url_data.get("max_clicks")
    if max_clicks and url_data.get("clicks", 0) >= max_clicks:
        return True
    return False

def get_country_redirect(url_data, country_code):
    """Return country-specific redirect if available"""
    country_data = url_data.get("country_redirect")  # stored as dict
    if country_data and country_code in country_data:
        return country_data[country_code]
    return None

# -------------------- Routes --------------------

# Home page
@app.route("/", methods=["GET"])
def home():
    return """
    <h2>URL Shortener</h2>
    <p>Use the <a href="/shorten">/shorten</a> endpoint via POST (JSON) or the form at /shorten.</p>
    """

# Shorten URL
@app.route("/shorten", methods=["GET", "POST"])
def shorten():
    # GET → show HTML form
    if request.method == "GET":
        return """
        <h2>URL Shortener with Optional Premium Features</h2>
        <form method="POST">
            Long URL: <input type="text" name="long_url" size="50" required><br><br>
            Custom ID (optional): <input type="text" name="custom_id" size="20"><br><br>
            Password (optional): <input type="text" name="password" size="20"><br><br>
            Expiry Date (YYYY-MM-DD HH:MM:SS, optional): <input type="text" name="expiry_date" size="25"><br><br>
            Max Clicks (optional): <input type="number" name="max_clicks" min="1"><br><br>
            Mobile Redirect URL (optional): <input type="text" name="mobile_url" size="50"><br><br>
            Desktop Redirect URL (optional): <input type="text" name="desktop_url" size="50"><br><br>
            Country Redirect (format: US=https://us.site.com;IN=https://in.site.com): <input type="text" name="country_redirect" size="60"><br><br>
            <input type="submit" value="Shorten URL">
        </form>
        """

    # POST → handle form or JSON
    if request.is_json:
        data = request.get_json()
        long_url = data.get("long_url")
        custom_id = data.get("custom_id")
        password = data.get("password")
        expiry_date = data.get("expiry_date")
        max_clicks = data.get("max_clicks")
        mobile_url = data.get("mobile_url")
        desktop_url = data.get("desktop_url")
        country_redirect = data.get("country_redirect")
    else:
        long_url = request.form.get("long_url")
        custom_id = request.form.get("custom_id")
        password = request.form.get("password")
        expiry_date = request.form.get("expiry_date")
        max_clicks = request.form.get("max_clicks")
        mobile_url = request.form.get("mobile_url")
        desktop_url = request.form.get("desktop_url")
        country_redirect = request.form.get("country_redirect")

    if not long_url:
        return jsonify({"error": "Missing URL"}), 400

    # Handle custom ID
    if custom_id:
        if urls.find_one({"short_id": custom_id}):
            return jsonify({"error": "Custom ID already taken"}), 400
        short_id = custom_id
    else:
        short_id = generate_short_id()
        while urls.find_one({"short_id": short_id}):
            short_id = generate_short_id()

    # Parse country redirect into dict
    country_dict = None
    if country_redirect:
        country_dict = {}
        for pair in country_redirect.split(";"):
            if "=" in pair:
                code, url = pair.split("=", 1)
                country_dict[code.strip().upper()] = url.strip()

    # Save to DB
    url_entry = {
        "short_id": short_id,
        "long_url": long_url,
        "clicks": 0,
        "password": password or None,
        "expiry_date": expiry_date or None,
        "max_clicks": int(max_clicks) if max_clicks else None,
        "mobile_url": mobile_url or None,
        "desktop_url": desktop_url or None,
        "country_redirect": country_dict
    }
    urls.insert_one(url_entry)

    # Generate QR code
    short_url = BASE_URL + short_id
    qr_img = qrcode.make(short_url)
    qr_path = os.path.join(QR_DIR, f"{short_id}.png")
    qr_img.save(qr_path)

    # Return response
    if request.is_json:
        return jsonify({"short_url": short_url, "qr_code": f"{BASE_URL}qr/{short_id}"})
    else:
        return render_template_string("""
            <h3>Short URL created!</h3>
            <p>Short URL: <a href="{{ short_url }}" target="_blank">{{ short_url }}</a></p>
            <p>QR Code:</p>
            <img src="{{ qr_url }}" alt="QR Code">
            <br><br>
            <a href="/shorten">Create another</a>
        """, short_url=short_url, qr_url=f"/qr/{short_id}")

# Redirect short URL
@app.route("/<short_id>", methods=["GET", "POST"])
def redirect_short_url(short_id):
    url_data = urls.find_one({"short_id": short_id})
    if not url_data:
        return "<h3>Error: URL not found</h3>", 404

    # Check expiry / max clicks
    if is_expired(url_data):
        return "<h3>Error: Link expired or max clicks reached</h3>", 403

    # Check password
    if url_data.get("password"):
        if request.method == "POST":
            input_pwd = request.form.get("password") or (request.json.get("password") if request.is_json else None)
            if input_pwd != url_data["password"]:
                return "<h3>Error: Incorrect password</h3>", 403
        else:
            return f"""
                <h3>Password required</h3>
                <form method="POST">
                  Password: <input type="text" name="password">
                  <input type="submit" value="Submit">
                </form>
            """

    # Device-based redirect
    ua_string = request.headers.get("User-Agent")
    ua = user_agents.parse(ua_string)
    if ua.is_mobile and url_data.get("mobile_url"):
        target_url = url_data["mobile_url"]
    elif ua.is_pc and url_data.get("desktop_url"):
        target_url = url_data["desktop_url"]
    else:
        target_url = url_data["long_url"]

    # Country-based redirect (if X-Country header sent)
    country_code = request.headers.get("X-Country", "").upper()
    country_redirect_url = get_country_redirect(url_data, country_code)
    if country_redirect_url:
        target_url = country_redirect_url

    # Increment clicks
    urls.update_one({"short_id": short_id}, {"$inc": {"clicks": 1}})
    return redirect(target_url)

# Serve QR code
@app.route("/qr/<short_id>")
def get_qr(short_id):
    qr_path = os.path.join(QR_DIR, f"{short_id}.png")
    if os.path.exists(qr_path):
        return send_file(qr_path, mimetype="image/png")
    return "<h3>Error: QR code not found</h3>", 404

# Get click stats
@app.route("/stats/<short_id>")
def stats(short_id):
    url_data = urls.find_one({"short_id": short_id})
    if url_data:
        return jsonify({
            "short_url": BASE_URL + short_id,
            "long_url": url_data["long_url"],
            "clicks": url_data["clicks"],
            "expiry_date": url_data.get("expiry_date"),
            "max_clicks": url_data.get("max_clicks"),
            "password_protected": bool(url_data.get("password")),
            "mobile_url": url_data.get("mobile_url"),
            "desktop_url": url_data.get("desktop_url"),
            "country_redirect": url_data.get("country_redirect")
        })
    return jsonify({"error": "URL not found"}), 404

# -------------------- Update URL options --------------------# -------------------- Update URL options --------------------
@app.route("/update/<short_id>", methods=["PATCH", "POST"])
def update_url(short_id):
    url_data = urls.find_one({"short_id": short_id})
    if not url_data:
        return jsonify({"error": "URL not found"}), 404

    if not request.is_json:
        return jsonify({"error": "Send JSON data"}), 400
    data = request.get_json()

    update_fields = {}

    # Only set fields if key exists
    for field in ["password", "expiry_date", "max_clicks", "mobile_url", "desktop_url", "country_redirect"]:
        if field in data:
            value = data[field]
            if value == "" or value is None:
                update_fields[field] = None
            else:
                if field == "max_clicks":
                    update_fields[field] = int(value)
                elif field == "country_redirect":
                    # Convert country_redirect string to dict
                    country_dict = {}
                    for pair in value.split(";"):
                        if "=" in pair:
                            code, url = pair.split("=", 1)
                            country_dict[code.strip().upper()] = url.strip()
                    update_fields[field] = country_dict
                else:
                    update_fields[field] = value

    if not update_fields:
        return jsonify({"message": "No updates provided"}), 400

    urls.update_one({"short_id": short_id}, {"$set": update_fields})
    return jsonify({"message": "URL updated successfully", "updated_fields": update_fields})

# Custom 405 handler
@app.errorhandler(405)
def method_not_allowed(e):
    return """
    <h1>405 Method Not Allowed</h1>
    <p>Use POST request to create short URLs (form or JSON).</p>
    <a href="/">Go back</a>
    """, 405

# -------------------- Run App --------------------
if __name__ == "__main__":
    app.run(debug=True)
