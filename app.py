from flask import Flask, render_template, request, jsonify
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import cloudinary
import cloudinary.uploader
import cloudinary.utils
from dotenv import load_dotenv

import requests
from flask import Response, stream_with_context, abort

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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shares (
            id SERIAL PRIMARY KEY,
            type VARCHAR(10),
            value TEXT,
            filename TEXT,
            public_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("ALTER TABLE shares ADD COLUMN IF NOT EXISTS public_id TEXT;")
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
            if not file or file.filename == '':
                return jsonify({'status': 'error', 'message': 'No file provided'}), 400

            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext in {'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'tiff', 'heic'}:
                resource_type = 'image'
            elif ext in {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv', 'm4v'}:
                resource_type = 'video'
            else:
                resource_type = 'raw'

            MAX_SIZE = 10 * 1024 * 1024
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)
            if size > MAX_SIZE:
                return jsonify({
                    'status': 'error',
                    'message': f'File too large ({size/1024/1024:.1f} MB). Max is 10 MB.'
                }), 413

            upload_result = cloudinary.uploader.upload(
                file,
                resource_type=resource_type,
                use_filename=True,
                unique_filename=True,
                overwrite=False,
            )
            secure_url = upload_result.get('secure_url')
            public_id = upload_result.get('public_id')
            filename = file.filename

            cur.execute(
                'INSERT INTO shares (type, value, filename, public_id) VALUES (%s, %s, %s, %s)',
                ('file', secure_url, filename, public_id)
            )

        else:
            if not content:
                return jsonify({'status': 'error', 'message': 'Empty content'}), 400
            cur.execute('INSERT INTO shares (type, value) VALUES (%s, %s)',
                        ('text', content))

        conn.commit()
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
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
        cur.execute('SELECT type, value, public_id FROM shares WHERE id = %s', (share_id,))
        share = cur.fetchone()

        if not share:
            return jsonify({'status': 'not found'}), 404

        if share['type'] == 'file' and share['public_id']:
            for rt in ('raw', 'image', 'video'):
                try:
                    res = cloudinary.uploader.destroy(share['public_id'], resource_type=rt)
                    if res.get('result') == 'ok':
                        break
                except Exception as e:
                    print(f"Cloudinary destroy ({rt}) error: {e}")

        cur.execute('DELETE FROM shares WHERE id = %s', (share_id,))
        conn.commit()
        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Delete Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/download/<int:share_id>')
def download_share(share_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT type, value, filename FROM shares WHERE id = %s', (share_id,))
        share = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not share or share['type'] != 'file':
        abort(404)

    # Force Cloudinary to serve as attachment, then proxy so we control filename
    url = share['value'].replace('/upload/', '/upload/fl_attachment/')
    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Download proxy error: {e}")
        abort(502)

    filename = (share['filename'] or 'download').replace('"', '').replace('\n', '').replace('\r', '')

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Type': resp.headers.get('Content-Type', 'application/octet-stream'),
    }
    content_length = resp.headers.get('Content-Length')
    if content_length:
        headers['Content-Length'] = content_length

    return Response(
        stream_with_context(resp.iter_content(chunk_size=8192)),
        headers=headers,
        status=200,
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))