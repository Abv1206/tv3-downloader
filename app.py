from flask import Flask, render_template, request, jsonify
import yt_dlp
import threading
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
DOWNLOAD_DIR = '/downloads'

# Estado global de la descarga
download_state = {
    'is_active': False,
    'is_paused': False,
    'title': 'Preparando...',
    'percent': 0,
    'speed': '0 B/s',
    'current_file': ''
}

pause_event = threading.Event()
stop_event = threading.Event()

def extract_links_from_season(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/video/' in href:
                full_url = urljoin(url, href)
                if full_url not in links and full_url != url:
                    links.append(full_url)
        return links
    except Exception:
        return [url]

def progress_hook(d):
    if stop_event.is_set():
        raise Exception("STOP_REQUESTED")
    
    # Si el evento no está 'set' (Pausado), el hilo se congela aquí hasta que se reanude
    pause_event.wait()
    
    if d['status'] == 'downloading':
        download_state['title'] = d.get('info_dict', {}).get('title', 'Descargando...')
        
        total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
        downloaded = d.get('downloaded_bytes', 0)
        download_state['percent'] = round((downloaded / total) * 100, 1) if total > 0 else 0
        
        download_state['speed'] = d.get('_speed_str', '0 B/s').strip()
        download_state['current_file'] = d.get('filename', '')

def download_worker(url, quality):
    urls = extract_links_from_season(url)
    
    format_selector = 'best[height<=720]/bestvideo[height<=720]+bestaudio/best' if quality == 'media' else 'bestvideo+bestaudio/best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'ignoreerrors': True,
        'nocolor': True, # Facilita la lectura de los metadatos de progreso
        'progress_hooks': [progress_hook]
    }

    download_state['is_active'] = True
    download_state['is_paused'] = False
    download_state['percent'] = 0
    pause_event.set()
    stop_event.clear()

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(urls)
    except Exception as e:
        if str(e) == "STOP_REQUESTED" and download_state['current_file']:
            # Limpieza de archivos a medias (temporales de yt-dlp y ffmpeg)
            base_path = download_state['current_file']
            for ext in ['', '.part', '.ytdl']:
                if os.path.exists(base_path + ext):
                    try:
                        os.remove(base_path + ext)
                    except:
                        pass
    finally:
        download_state['is_active'] = False
        if not stop_event.is_set():
            download_state['title'] = 'Todas las descargas finalizadas'
            download_state['percent'] = 100
        download_state['speed'] = ''

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    if download_state['is_active']:
        return jsonify({"status": "error", "message": "Ya hay una descarga en curso."})
    
    url = request.form.get('url')
    quality = request.form.get('quality')
    
    if not url:
        return jsonify({"status": "error", "message": "URL no proporcionada."})

    threading.Thread(target=download_worker, args=(url, quality)).start()
    return jsonify({"status": "success"})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(download_state)

@app.route('/action', methods=['POST'])
def handle_action():
    action = request.json.get('action')
    if action == 'pause':
        pause_event.clear()
        download_state['is_paused'] = True
        download_state['speed'] = 'Pausado'
    elif action == 'resume':
        pause_event.set()
        download_state['is_paused'] = False
    elif action == 'stop':
        stop_event.set()
        pause_event.set() # Desbloquea forzosamente por si estaba pausado
    return jsonify({"status": "success"})

if __name__ == '__main__':
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    app.run(host='0.0.0.0', port=5000)
