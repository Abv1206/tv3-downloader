from flask import Flask, render_template, request, jsonify
import yt_dlp
import threading
import os

app = Flask(__name__)
DOWNLOAD_DIR = '/downloads'

def download_video(url, quality):
    # Definir formato según la calidad elegida
    if quality == 'media':
        format_selector = 'best[height<=720]/bestvideo[height<=720]+bestaudio/best'
    else:
        format_selector = 'bestvideo+bestaudio/best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'noplaylist': False, # Permite descargar listas/temporadas enteras si la URL lo soporta
        'extract_flat': False
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"Error en la descarga: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    url = request.form.get('url')
    quality = request.form.get('quality')
    
    if not url:
        return jsonify({"status": "error", "message": "URL no proporcionada"}), 400

    # Ejecutar en un hilo separado para no bloquear la interfaz web
    thread = threading.Thread(target=download_video, args=(url, quality))
    thread.start()
    
    return jsonify({"status": "success", "message": "Descarga iniciada en segundo plano."})

if __name__ == '__main__':
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    app.run(host='0.0.0.0', port=5000)
