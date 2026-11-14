"""
patch.py — applies:
  * requests import + /download/<id> proxy route
  * raw file-type support (.py, .js, .sh, .zip, etc.)
  * public_id column + safe Cloudinary delete
  * 10 MB size check + clear error messages
  * frontend: better sendFile() error handling
to app.py, requirements.txt, templates/index.html.

Usage:
    python patch.py

Safe to run more than once: idempotent per transform.
"""

import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak):
        write(bak, read(path))
        print(f"  (backup created: {os.path.basename(bak)})")


def patch_file(name, transforms):
    path = os.path.join(ROOT, name)
    print(f"  path: {path}")
    if not os.path.exists(path):
        print(f"[skip] {name} not found")
        return False

    content = read(path)
    changed = False

    for label, fn in transforms:
        new = fn(content)
        if new == content:
            print(f"  [no change] {label}")
        else:
            content = new
            changed = True
            print(f"  [patched]  {label}")

    if changed:
        backup(path)
        write(path, content)
        print(f"[ok] {name} updated")
    else:
        print(f"[no-op] {name} already up to date")
    return changed


def replace_block(src, start_marker, end_marker, new_block, label):
    """Replace src[start_marker ... end_marker] (inclusive) with new_block."""
    start = src.find(start_marker)
    if start == -1:
        print(f"  [skip] {label}: start marker not found")
        return src
    end = src.find(end_marker, start)
    if end == -1:
        print(f"  [skip] {label}: end marker not found")
        return src
    end += len(end_marker)
    if src[start:end] == new_block:
        return src
    return src[:start] + new_block + src[end:]


# ---------------------------------------------------------------------------
# app.py — imports
# ---------------------------------------------------------------------------
def app_add_imports(src):
    if "from flask import Response, stream_with_context, abort" in src:
        return src
    marker = "from dotenv import load_dotenv\n"
    if marker not in src:
        raise SystemExit("app.py: missing load_dotenv import")
    return src.replace(
        marker,
        marker + "\nimport requests\nfrom flask import Response, stream_with_context, abort\n",
        1,
    )


# ---------------------------------------------------------------------------
# app.py — init_db (adds public_id + idempotent ALTER)
# ---------------------------------------------------------------------------
NEW_INIT_DB = '''def init_db():
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
'''


def app_replace_init_db(src):
    if "ADD COLUMN IF NOT EXISTS public_id" in src:
        return src
    # Match "def init_db():" through "conn.close()" followed by blank line
    return replace_block(
        src,
        "def init_db():",
        "conn.close()\n",
        NEW_INIT_DB,
        "init_db",
    )


# ---------------------------------------------------------------------------
# app.py — /send route
# ---------------------------------------------------------------------------
NEW_SEND = '''@app.route('/send', methods=['POST'])
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
'''


def app_replace_send(src):
    if "resource_type=resource_type" in src:
        return src
    return replace_block(
        src,
        "@app.route('/send', methods=['POST'])",
        "return jsonify({'status': 'success'})\n",
        NEW_SEND,
        "/send",
    )


# ---------------------------------------------------------------------------
# app.py — /delete route
# ---------------------------------------------------------------------------
NEW_DELETE = '''@app.route('/delete/<int:share_id>', methods=['DELETE'])
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
'''


def app_replace_delete(src):
    if "for rt in ('raw', 'image', 'video')" in src:
        return src
    return replace_block(
        src,
        "@app.route('/delete/<int:share_id>', methods=['DELETE'])",
        "conn.close()\n",
        NEW_DELETE,
        "/delete",
    )


# ---------------------------------------------------------------------------
# app.py — /download route (keep existing if already present)
# ---------------------------------------------------------------------------
DOWNLOAD_ROUTE = '''
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

    url = share['value'].replace('/upload/', '/upload/fl_attachment/')
    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Download proxy error: {e}")
        abort(502)

    filename = (share['filename'] or 'download').replace('"', '').replace('\\n', '').replace('\\r', '')

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


'''


def app_add_download_route(src):
    if "def download_share(" in src:
        return src
    marker = "if __name__ == '__main__':"
    if marker not in src:
        raise SystemExit("app.py: missing __main__ guard")
    return src.replace(marker, DOWNLOAD_ROUTE + marker, 1)


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------
def reqs_add_requests(src):
    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    if "requests" in lines:
        return src
    if not src.endswith("\n"):
        src += "\n"
    return src + "requests\n"


# ---------------------------------------------------------------------------
# templates/index.html — new sendFile()
# ---------------------------------------------------------------------------
NEW_SEND_FILE = '''        async function sendFile() {
            const fileInput = document.getElementById('fileInput');
            if (fileInput.files.length === 0) return;
            const file = fileInput.files[0];

            if (file.size > 10 * 1024 * 1024) {
                alert(`File too large (${(file.size/1024/1024).toFixed(1)} MB). Max is 10 MB.`);
                fileInput.value = '';
                return;
            }

            const formData = new FormData();
            formData.append('file', file);
            formData.append('type', 'file');

            const btn = document.querySelector('.drop-zone');
            const originalHTML = btn.innerHTML;
            btn.innerHTML = `Uploading ${escapeHtml(file.name)}... ⏳`;

            try {
                const res = await fetch('/send', { method: 'POST', body: formData });
                const json = await res.json();
                if (!res.ok || json.status !== 'success') {
                    throw new Error(json.message || `Upload failed (HTTP ${res.status})`);
                }
            } catch (e) {
                alert('Upload failed: ' + e.message);
            } finally {
                btn.innerHTML = originalHTML;
                fileInput.value = '';
                loadShares();
            }
        }
'''


def html_replace_send_file(src):
    if "Max is 10 MB" in src and "json.message" in src:
        return src
    return replace_block(
        src,
        "        async function sendFile() {",
        "        }\n",
        NEW_SEND_FILE,
        "sendFile()",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("== LocalDrop patch ==")

    print("\n[1/4] app.py")
    patch_file("app.py", [
        ("imports (requests + Response)", app_add_imports),
        ("init_db() with public_id",       app_replace_init_db),
        ("/send with raw support",         app_replace_send),
        ("/delete with public_id",         app_replace_delete),
        ("/download proxy",                app_add_download_route),
    ])

    print("\n[2/4] requirements.txt")
    patch_file("requirements.txt", [("ensure requests", reqs_add_requests)])

    print("\n[3/4] templates/index.html")
    patch_file(os.path.join("templates", "index.html"), [
        ("sendFile() error handling", html_replace_send_file),
    ])

    print("\n[4/4] done.")
    print("\nNext steps:")
    print("  pip install -r requirements.txt")
    print("  restart the server")
    print("  hard-refresh browser (Ctrl+Shift+R)")
    print("  the DB schema auto-updates on next boot (ADD COLUMN IF NOT EXISTS)")


if __name__ == "__main__":
    main()