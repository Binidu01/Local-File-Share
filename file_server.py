import os
import signal
import socket
import qrcode
import webbrowser
import threading
import time
import tempfile
import io
import base64
from queue import Queue
from flask import (
    Flask, request, send_from_directory,
    redirect, url_for, render_template_string, Response
)
from urllib.parse import quote, unquote
from tkinter import Tk, Label, ttk
import sys

# ========== Configuration ==========
PORT = 8000

if getattr(sys, 'frozen', False):
    APP_PATH = os.path.dirname(sys.executable)
else:
    APP_PATH = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(APP_PATH, 'uploads')
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'fileshare_temp')
CHUNKS_DIR = os.path.join(TEMP_DIR, "chunks")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(CHUNKS_DIR, exist_ok=True)

# Flask settings for large files
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE

# Globals for SSE
connected_clients = []
last_state = set()
server_shutdown = False

# ========== Utility Functions ==========
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def generate_qr_data_uri(url: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = base64.b64encode(buf.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{data}"

# ========== Splash Screen ==========
def show_splash():
    splash = Tk()
    splash.overrideredirect(True)
    splash.configure(bg="#121212")
    w, h = 400, 200
    sw, sh = splash.winfo_screenwidth(), splash.winfo_screenheight()
    splash.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    Label(
        splash,
        text="Starting FileShare...",
        font=("Segoe UI", 16),
        bg="#121212", fg="#00FFAA"
    ).pack(pady=30)

    progress = ttk.Progressbar(splash, mode='indeterminate', length=300)
    progress.pack(pady=10)
    progress.start(10)

    def close_after_delay():
        time.sleep(2.5)
        splash.destroy()

    threading.Thread(target=close_after_delay, daemon=True).start()
    splash.mainloop()

if not hasattr(sys, '_called_from_test') and __name__ == '__main__':
    show_splash()

# ========== File Watching & SSE ==========
def broadcast_to_clients(msg: str):
    for q in connected_clients.copy():
        try:
            q.put(msg)
        except:
            connected_clients.remove(q)

def file_watcher():
    global last_state
    last_state = set(os.listdir(UPLOAD_FOLDER))
    while not server_shutdown:
        time.sleep(1)
        current = set(os.listdir(UPLOAD_FOLDER))
        if current != last_state:
            broadcast_to_clients("reload")
            last_state = current

@app.route('/events')
def sse_events():
    def gen(q: Queue):
        try:
            while True:
                msg = q.get()
                yield f"data: {msg}\n\n"
                if msg == 'shutdown':
                    time.sleep(0.5)
        except GeneratorExit:
            if q in connected_clients:
                connected_clients.remove(q)

    q = Queue()
    connected_clients.append(q)
    return Response(gen(q), mimetype='text/event-stream')

# ========== Flask Routes ==========
@app.route('/', methods=['GET'])
def index():
    ip = get_local_ip()
    server_url = f"http://{ip}:{PORT}"
    qr_code = generate_qr_data_uri(server_url)
    files = sorted(os.listdir(UPLOAD_FOLDER))
    return render_template_string(
        HTML, files=files, qr_code=qr_code, server_url=server_url, quote=quote
    )

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    file_id = request.form['file_id']
    chunk_index = int(request.form['chunk_index'])
    total_chunks = int(request.form['total_chunks'])
    filename = request.form['filename']
    chunk = request.files['chunk']

    file_dir = os.path.join(CHUNKS_DIR, file_id)
    os.makedirs(file_dir, exist_ok=True)
    chunk_path = os.path.join(file_dir, f"{chunk_index}.part")
    chunk.save(chunk_path)

    uploaded = len([f for f in os.listdir(file_dir) if f.endswith('.part')])
    if uploaded == total_chunks:
        dest_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(dest_path, 'wb') as f_out:
            for i in range(total_chunks):
                part_path = os.path.join(file_dir, f"{i}.part")
                with open(part_path, 'rb') as f_in:
                    f_out.write(f_in.read())
        for f in os.listdir(file_dir):
            os.remove(os.path.join(file_dir, f))
        os.rmdir(file_dir)
    return ('', 204)

@app.route('/uploads/<path:filename>')
def download_file(filename):
    filename = unquote(filename)
    safe_path = os.path.join(UPLOAD_FOLDER, os.path.basename(filename))
    if not os.path.isfile(safe_path):
        return "File not found.", 404
    return send_from_directory(UPLOAD_FOLDER, os.path.basename(filename), as_attachment=True)

@app.route('/view/<path:filename>')
def view_file(filename):
    filename = unquote(filename)
    safe_path = os.path.join(UPLOAD_FOLDER, os.path.basename(filename))
    if not os.path.isfile(safe_path):
        return "File not found.", 404
    return send_from_directory(UPLOAD_FOLDER, os.path.basename(filename))

@app.route('/delete/<path:filename>', methods=['POST'])
def delete_file(filename):
    try:
        filename = unquote(filename)
        safe_path = os.path.join(UPLOAD_FOLDER, os.path.basename(filename))
        if os.path.exists(safe_path):
            os.remove(safe_path)
            broadcast_to_clients("reload")
            return ('', 204)
        else:
            return "File not found", 404
    except Exception as e:
        return f"Error deleting file: {str(e)}", 500

@app.route('/shutdown', methods=['POST'])
def shutdown():
    global server_shutdown
    server_shutdown = True
    broadcast_to_clients('shutdown')
    time.sleep(1)
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        threading.Thread(target=lambda: (time.sleep(2), func()), daemon=True).start()
    else:
        threading.Thread(
            target=lambda: (time.sleep(2), os.kill(os.getpid(), signal.SIGINT)),
            daemon=True
        ).start()
    return 'Shutting down...'

# ========== HTML Template ==========
HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>FileShare</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link 
href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" 
rel="stylesheet">
<style>
body { background-color: #121212; color: #e0e0e0; }
.navbar { background-color: #1f1f1f; }
.container { max-width: 900px; margin-top: 20px; }
.upload-area { border: 2px dashed #333; padding: 30px; border-radius: 12px; text-align: center; cursor: pointer; }
.upload-area.dragover { background-color: #1e1e1e; }
.file-card { background-color: #1e1e1e; border: none; border-radius: 8px; margin-bottom: 15px; }
.file-card .card-body { display: flex; justify-content: space-between; align-items: center; }
.qr-section { text-align: center; margin-top: 30px; }
.qr-section img { max-width: 150px; }
#shutdown-overlay {
  position: fixed; top:0; left:0; right:0; bottom:0;
  background-color: rgba(0,0,0,0.95);
  display: none; justify-content: center; align-items: center;
  flex-direction: column; z-index:9999;
}
.spinner {
  border: 8px solid #f3f3f3;
  border-top: 8px solid #00FFAA;
  border-radius: 50%;
  width: 60px; height: 60px;
  animation: spin 1s linear infinite;
  margin-bottom: 15px;
}
@keyframes spin { 0%{transform:rotate(0deg);}100%{transform:rotate(360deg);} }
.progress { height: 20px; margin-top: 10px; }
.progress-bar { transition: width 0.2s; }
@media (max-width:576px){
  .upload-area{padding:20px;}
  .file-card .card-body{flex-direction:column;gap:10px;align-items:flex-start;}
}
</style>
</head>
<body>

<div id="shutdown-overlay">
  <div class="spinner"></div>
  <h3>Shutting down server...</h3>
</div>

<nav class="navbar navbar-expand-lg">
  <div class="container">
    <a class="navbar-brand text-light">📁 FileShare</a>
    <button class="btn btn-danger ms-auto" onclick="stopServer()">
      Stop Server
    </button>
  </div>
</nav>

<div class="container mt-4">
  <div id="uploadArea" class="upload-area mb-4">
    <p>Drag &amp; Drop or click to upload</p>
    <div id="progressContainer" class="progress" style="display:none;">
      <div id="progressBar" class="progress-bar progress-bar-striped progress-bar-animated" style="width:0%">0%</div>
    </div>
    <input id="fileInput" type="file" multiple class="d-none">
  </div>

  <div id="fileList">
    {% for file in files %}
      <div class="card file-card text-light">
        <div class="card-body">
          <span class="text-truncate" style="max-width:70%">{{ file }}</span>
          <div>
            <a href="{{ url_for('view_file', filename=quote(file)) }}" target="_blank"
               class="btn btn-sm btn-info me-2">View</a>
            <a href="{{ url_for('download_file', filename=quote(file)) }}"
               class="btn btn-sm btn-outline-light me-2">Download</a>
            <button onclick="deleteFile('{{ quote(file) }}')" class="btn btn-sm btn-danger">Delete</button>
          </div>
        </div>
      </div>
    {% else %}
      <p class="text-center text-muted">No files uploaded yet.</p>
    {% endfor %}
  </div>

  <div class="qr-section">
    <h6>Open on Another Device</h6>
    <img src="{{ qr_code }}" alt="QR Code" class="rounded shadow-sm">
    <p class="text-muted small">{{ server_url }}</p>
  </div>
</div>

<script>
const uploadArea = document.getElementById('uploadArea');
const fileInput  = document.getElementById('fileInput');
const CHUNK_SIZE = 10 * 1024 * 1024; // 10MB

uploadArea.addEventListener('click', () => fileInput.click());

async function uploadFile(file) {
  const fileId = Date.now().toString(36) + Math.random().toString(36).substr(2);
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

  const progressContainer = document.getElementById('progressContainer');
  const progressBar = document.getElementById('progressBar');
  progressContainer.style.display = 'block';
  progressBar.style.width = '0%';
  progressBar.innerText = '0%';

  for (let i = 0; i < totalChunks; i++) {
    const chunk = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
    const formData = new FormData();
    formData.append("file_id", fileId);
    formData.append("chunk_index", i);
    formData.append("total_chunks", totalChunks);
    formData.append("filename", file.name);
    formData.append("chunk", chunk);
    await fetch("/upload_chunk", { method: "POST", body: formData });

    const percent = Math.round(((i + 1) / totalChunks) * 100);
    progressBar.style.width = percent + '%';
    progressBar.innerText = percent + '%';
  }

  setTimeout(() => {
    progressContainer.style.display = 'none';
    location.reload();
  }, 500);
}

fileInput.addEventListener("change", () => {
  for (const file of fileInput.files) uploadFile(file);
});

uploadArea.addEventListener("dragover", e => {
  e.preventDefault(); uploadArea.classList.add('dragover');
});
uploadArea.addEventListener("dragleave", () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener("drop", e => {
  e.preventDefault(); uploadArea.classList.remove('dragover');
  for (const file of e.dataTransfer.files) uploadFile(file);
});

function deleteFile(fn) {
  fetch(`/delete/${fn}`, { method:'POST' }).then(() => location.reload());
}

function stopServer() {
  document.getElementById('shutdown-overlay').style.display = 'flex';
  fetch('/shutdown', { method:'POST' });
}

const evt = new EventSource('/events');
evt.onmessage = e => {
  if (e.data === 'reload') location.reload();
  if (e.data === 'shutdown') {
    document.getElementById('shutdown-overlay').style.display = 'flex';
    setTimeout(() => { evt.close(); location.reload(); }, 3500);
  }
};
</script>
</body>
</html>
'''

# ========== Startup ==========
if __name__ == '__main__':
    threading.Thread(target=file_watcher, daemon=True).start()
    threading.Timer(
        1.5,
        lambda: webbrowser.open(f"http://{get_local_ip()}:{PORT}")
    ).start()
    print(f"Server running at http://{get_local_ip()}:{PORT}")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
