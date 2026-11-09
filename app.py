from flask import Flask, render_template, request, jsonify
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION ---
DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# Initialize Cloudinary
cloudinary.config( 
  cloud_name = CLOUDINARY_CLOUD_NAME, 
  api_key = CLOUDINARY_API_KEY, 
  api_secret = CLOUDINARY_API_SECRET 
)

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=RealDictCursor)
    return conn

def init_db():
    # Create the shares table if it doesn't exist
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS shares (
            id SERIAL PRIMARY KEY,
            type VARCHAR(10),
            value TEXT,
            filename TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

# Initialize DB on startup
try:
    init_db()
except Exception as e:
    print(f"DB Init Error: {e}")

@app.route('/')
def index():
    url = request.url_root.strip('/')
    return render_template('index.html', url=url)

@app.route('/send', methods=['POST'])
def send():
    content = request.form.get('content')
    type_ = request.form.get('type') 
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        if type_ == 'file':
            file = request.files.get('file')
            if file:
                # Upload file to Cloudinary
                upload_result = cloudinary.uploader.upload(file)
                secure_url = upload_result.get('secure_url')
                filename = file.filename
                cur.execute('INSERT INTO shares (type, value, filename) VALUES (%s, %s, %s)', 
                            ('file', secure_url, filename))
        else:
            if content:
                cur.execute('INSERT INTO shares (type, value) VALUES (%s, %s)', 
                            ('text', content))
        conn.commit()
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'status': 'error'}), 500
    finally:
        cur.close()
        conn.close()
    
    return jsonify({'status': 'success'})

@app.route('/shares')
def get_shares():
    conn = get_db_connection()
    cur = conn.cursor()
    # Fetch last 50 shares
    cur.execute('SELECT * FROM shares ORDER BY created_at DESC LIMIT 50')
    shares = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(shares)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))