import os
import signal
import socket
import qrcode
import webbrowser
import threading
import time
from flask import Flask, request, send_from_directory, redirect, url_for, render_template_string
from tkinter import Tk, Label, ttk
import sys

# ========== Splash Screen ==========
def show_splash():
    splash = Tk()
    splash.overrideredirect(True)
    splash.configure(bg="#121212")
    width, height = 400, 200
    screen_width = splash.winfo_screenwidth()
    screen_height = splash.winfo_screenheight()
    x = (screen_width // 2) - (width // 2)
    y = (screen_height // 2) - (height // 2)
    splash.geometry(f"{width}x{height}+{x}+{y}")

    label = Label(splash, text="Starting FileShare...", font=("Segoe UI", 16), bg="#121212", fg="#00FFAA")
    label.pack(pady=30)

    progress = ttk.Progressbar(splash, mode='indeterminate', length=300)
    progress.pack(pady=10)
    progress.start(10)

    splash.update()
    def close_after_delay():
        time.sleep(2.5)
        splash.destroy()
    threading.Thread(target=close_after_delay, daemon=True).start()
    splash.mainloop()

if not hasattr(sys, '_called_from_test') and __name__ == '__main__':
    show_splash()

# ========== Flask Setup ==========
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
PORT = 8000

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP

# ========== HTML Template ==========
HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local File Sharing</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
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
    #shutdown-overlay, #startup-overlay {
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background-color: rgba(0, 0, 0, 0.95);
      display: flex; justify-content: center; align-items: center;
      flex-direction: column; z-index: 9999;
    }
    .spinner {
      border: 8px solid #f3f3f3;
      border-top: 8px solid #00FFAA;
      border-radius: 50%;
      width: 60px; height: 60px;
      animation: spin 1s linear infinite;
      margin-bottom: 15px;
    }
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    @media (max-width: 576px) {
      .upload-area { padding: 20px; }
      .file-card .card-body { flex-direction: column; gap: 10px; align-items: flex-start; }
    }
  </style>
</head>
<body>

<!-- Shutdown overlay (hidden by default) -->
<div id="shutdown-overlay" style="display: none;">
  <div class="spinner"></div>
  <h3>Shutting down server...</h3>
</div>

<nav class="navbar navbar-expand-lg">
  <div class="container">
    <a class="navbar-brand text-light" href="#">📁 FileShare</a>
    <button class="btn btn-danger ms-auto" onclick="stopServer()">Stop Server</button>
  </div>
</nav>
<div class="container">
  <div id="uploadArea" class="upload-area mb-4">
    <p class="mb-1">Drag & Drop files here or click to upload</p>
    <small class="text-muted">Supported: Any file type</small>
    <form id="uploadForm" method="POST" enctype="multipart/form-data" class="d-none">
      <input id="fileInput" type="file" name="file" multiple>
    </form>
  </div>

  <div id="fileList">
    {% for file in files %}
      <div class="card file-card text-light">
        <div class="card-body">
          <span class="text-truncate" style="max-width: 70%">{{ file }}</span>
          <div>
            <a href="{{ url_for('download_file', filename=file) }}" class="btn btn-sm btn-outline-light me-2">Download</a>
            <button onclick="deleteFile('{{ file }}')" class="btn btn-sm btn-danger">Delete</button>
          </div>
        </div>
      </div>
    {% else %}
      <p class="text-center text-muted">No files uploaded yet.</p>
    {% endfor %}
  </div>

  <div class="qr-section">
    <h6>Open on Another Device</h6>
    <img src="/qrcode" alt="QR Code" class="rounded shadow-sm" />
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
  // Hide startup screen after load
  window.onload = function () {
    document.getElementById('startup-overlay').style.display = 'none';
  };

  const uploadArea = document.getElementById('uploadArea');
  const fileInput = document.getElementById('fileInput');
  const uploadForm = document.getElementById('uploadForm');

  uploadArea.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => uploadForm.submit());

  uploadArea.addEventListener('dragover', e => { e.preventDefault(); uploadArea.classList.add('dragover'); });
  uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
  uploadArea.addEventListener('drop', e => {
    e.preventDefault(); uploadArea.classList.remove('dragover');
    fileInput.files = e.dataTransfer.files; uploadForm.submit();
  });

  function deleteFile(filename) {
    fetch(`/delete/${filename}`, { method: 'POST' }).then(() => location.reload());
  }

function stopServer() {
    document.getElementById('shutdown-overlay').style.display = 'flex';
    fetch('/shutdown', { method: 'POST' })
        .then(() => {
            // Refresh immediately after shutdown request
            location.reload(); 
        })
        .catch(() => {
            // Force refresh even if server is unreachable
            location.reload();
        });
}
</script>
</body>
</html>
'''

# ========== Routes ==========
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        files = request.files.getlist('file')
        for f in files:
            f.save(os.path.join(UPLOAD_FOLDER, f.filename))
        return redirect(url_for('upload_file'))
    files = os.listdir(UPLOAD_FOLDER)
    return render_template_string(HTML, files=files)

@app.route('/uploads/<path:filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/qrcode')
def qr():
    ip = get_ip()
    url = f"http://{ip}:{PORT}"
    img = qrcode.make(url)
    qr_path = os.path.join('.', 'qrcode.png')
    img.save(qr_path)
    def delete_qr():
        time.sleep(10)
        if os.path.exists(qr_path):
            os.remove(qr_path)
    threading.Thread(target=delete_qr, daemon=True).start()
    return send_from_directory('.', 'qrcode.png')

@app.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(path):
        os.remove(path)
    return ('', 204)

@app.route('/shutdown', methods=['POST'])
def shutdown():
    # If the app is running with Werkzeug, this will shut it down.
    shutdown_func = request.environ.get('werkzeug.server.shutdown')

    if shutdown_func:
        # Shutdown the server gracefully with a delay
        threading.Thread(target=lambda: (time.sleep(1), shutdown_func())).start()
        return 'Server shutting down...'
    # If Werkzeug shutdown function is not available, try using SIGINT
    os.kill(os.getpid(), signal.SIGINT)  # This will stop the server by sending SIGINT
    return 'Server shutting down...'


# ========== Main ==========
if __name__ == '__main__':
    ip = get_ip()
    print(f"Server running at: http://{ip}:{PORT}")
    threading.Timer(1.5, lambda: webbrowser.open(f"http://{ip}:{PORT}")).start()
    app.run(host='0.0.0.0', port=PORT, debug=False)
