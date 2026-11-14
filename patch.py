"""
patch.py — applies the copy-button / download-button / XSS fixes to
app.py, requirements.txt, and templates/index.html.

Usage:
    python patch.py

Safe to run more than once: it detects already-applied changes and skips them.
"""

import os

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
        print(f"[skip] {name} not found at {path}")
        return False

    original = read(path)
    content = original
    changed = False

    for label, fn in transforms:
        new = fn(content)
        if new == content:
            print(f"  [already applied / no change] {label}")
        else:
            content = new
            changed = True
            print(f"  [patched] {label}")

    if changed:
        backup(path)
        write(path, content)
        print(f"[ok] {name} updated")
    else:
        print(f"[no-op] {name} already up to date")
    return changed


# ---------------------------------------------------------------------------
# app.py transforms
# ---------------------------------------------------------------------------
def app_add_imports(src):
    if "from flask import Response, stream_with_context, abort" in src:
        return src
    marker = "from dotenv import load_dotenv\n"
    if marker not in src:
        raise SystemExit("app.py: could not find 'from dotenv import load_dotenv'")
    addition = (
        marker
        + "\nimport requests\n"
        + "from flask import Response, stream_with_context, abort\n"
    )
    return src.replace(marker, addition, 1)


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
        raise SystemExit("app.py: could not find '__main__' guard")
    return src.replace(marker, DOWNLOAD_ROUTE + marker, 1)


# ---------------------------------------------------------------------------
# requirements.txt transform
# ---------------------------------------------------------------------------
def reqs_add_requests(src):
    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    if "requests" in lines:
        return src
    if not src.endswith("\n"):
        src += "\n"
    return src + "requests\n"


# ---------------------------------------------------------------------------
# templates/index.html transform
# ---------------------------------------------------------------------------
NEW_SCRIPT = """<script>
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        }[c]));
    }
    function escapeAttr(s) { return escapeHtml(s); }

    async function copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (_) { /* fall through */ }
        }
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-9999px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
        document.body.removeChild(ta);
        return ok;
    }

    async function loadShares() {
        try {
            const res = await fetch('/shares');
            const data = await res.json();
            const list = document.getElementById('sharesList');
            list.innerHTML = '';
            if (data.length === 0) {
                list.innerHTML = '<div style="color:var(--text-muted); text-align:center">No drops yet. Be the first!</div>';
                return;
            }
            data.forEach(item => {
                const div = document.createElement('div');
                div.className = 'share-item';

                if (item.type === 'text') {
                    const isUrl = item.value.startsWith('http');
                    const displayContent = isUrl
                        ? `<a href="${escapeAttr(item.value)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:none;word-break:break-all;">${escapeHtml(item.value)}</a>`
                        : escapeHtml(item.value);

                    div.innerHTML = `
                        <div class="share-header">
                            <span class="type-tag">Text</span>
                            <div style="display:flex; gap:10px; align-items:center;">
                                <button class="copy-text-btn" data-copy="${escapeAttr(item.value)}">📋 Copy</button>
                                <button class="delete-btn" data-delete="${item.id}" title="Remove this drop">🗑️</button>
                            </div>
                        </div>
                        <div class="content-text">${displayContent}</div>
                    `;
                } else {
                    div.innerHTML = `
                        <div class="share-header">
                            <span class="type-tag">File</span>
                            <div style="display:flex; gap:10px; align-items:center;">
                                <a href="/download/${item.id}" class="download-btn">⬇️ Download</a>
                                <button class="delete-btn" data-delete="${item.id}" title="Remove this file">🗑️</button>
                            </div>
                        </div>
                        <div class="content-text" style="color:var(--text-muted)">${escapeHtml(item.filename || '')}</div>
                    `;
                }
                list.appendChild(div);
            });
        } catch (e) { console.error("Error loading shares", e); }
    }

    document.getElementById('sharesList').addEventListener('click', async (e) => {
        const copyBtn = e.target.closest('[data-copy]');
        if (copyBtn) {
            const ok = await copyToClipboard(copyBtn.dataset.copy);
            const original = copyBtn.textContent;
            copyBtn.textContent = ok ? '✅ Copied!' : '❌ Failed';
            copyBtn.classList.toggle('success', ok);
            setTimeout(() => {
                copyBtn.textContent = original;
                copyBtn.classList.remove('success');
            }, 1500);
            return;
        }

        const delBtn = e.target.closest('[data-delete]');
        if (delBtn) {
            deleteShare(Number(delBtn.dataset.delete));
        }
    });

    async function deleteShare(id) {
        if (!confirm("Are you sure you want to wipe this drop?")) return;
        try {
            await fetch(`/delete/${id}`, { method: 'DELETE' });
            loadShares();
        } catch (e) {
            alert("Error deleting share");
        }
    }

    async function sendText() {
        const content = document.getElementById('textContent').value;
        if (!content) return;
        const formData = new FormData();
        formData.append('content', content);
        formData.append('type', 'text');
        await fetch('/send', { method: 'POST', body: formData });
        document.getElementById('textContent').value = '';
        loadShares();
    }

    async function sendFile() {
        const fileInput = document.getElementById('fileInput');
        if (fileInput.files.length === 0) return;
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('type', 'file');

        const btn = document.querySelector('.drop-zone');
        const originalHTML = btn.innerHTML;
        btn.innerHTML = "Uploading... ⏳";

        try {
            await fetch('/send', { method: 'POST', body: formData });
        } catch (e) {
            alert("Upload failed");
        } finally {
            btn.innerHTML = originalHTML;
            fileInput.value = '';
            loadShares();
        }
    }

    setInterval(loadShares, 5000);
    loadShares();
</script>
"""


def html_replace_script(src):
    if "function escapeAttr(s)" in src:
        return src  # already patched

    start = src.find("<script>")
    if start == -1:
        raise SystemExit("index.html: no <script> block found")
    end = src.find("</script>", start)
    if end == -1:
        raise SystemExit("index.html: no closing </script> found")
    end += len("</script>")

    return src[:start] + NEW_SCRIPT + src[end:]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("== LocalDrop patch ==")

    print("\n[1/3] app.py")
    patch_file("app.py", [
        ("add requests + Response imports", app_add_imports),
        ("add /download/<id> route", app_add_download_route),
    ])

    print("\n[2/3] requirements.txt")
    patch_file("requirements.txt", [
        ("ensure 'requests' listed", reqs_add_requests),
    ])

    print("\n[3/3] templates/index.html")
    patch_file(os.path.join("templates", "index.html"), [
        ("replace <script> block", html_replace_script),
    ])

    print("\nDone. Restart the server and hard-refresh the browser (Ctrl+Shift+R).")


if __name__ == "__main__":
    main()