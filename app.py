from flask import Flask, render_template, request, jsonify
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import cloudinary
import cloudinary.uploader
import cloudinary.utils
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
    cur.execute('SELECT * FROM shares ORDER BY created_at DESC LIMIT 50')
    shares = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(shares)

@app.route('/delete/<int:share_id>', methods=['DELETE'])
def delete_share(share_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Find the share to see if it's a file (to delete from Cloudinary)
        cur.execute('SELECT type, value FROM shares WHERE id = %s', (share_id,))
        share = cur.fetchone()
        
        if not share:
            return jsonify({'status': 'not found'}), 404
            
        if share['type'] == 'file':
            # Extract the public_id from the Cloudinary URL
            # Example URL: https://res.cloudinary.com/demo/image/upload/v1234/folder/sample.jpg
            url = share['value']
            # Split by /upload/ and then remove extension from the last part
            try:
                parts = url.split('/upload/')
                if len(parts) > 1:
                    path_with_version = parts[1].split('/')
                    # Remove the 'v1234567' version prefix if exists
                    version_and_path = path_with_version[0].split('/') 
                    # This is tricky, simpler way: use cloudinary.utils.asset_id_from_url
                    public_id = cloudinary.utils.asset_id_from_url(url)
                    cloudinary.uploader.destroy(public_id)
            except Exception as e:
                print(f"Cloudinary delete error: {e}")

        # 2. Delete from Neon DB
        cur.execute('DELETE FROM shares WHERE id = %s', (share_id,))
        conn.commit()
        return jsonify({'status': 'success'})
        
    except Exception as e:
        print(f"Delete Error: {e}")
        return jsonify({'status': 'error'}), 500
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))