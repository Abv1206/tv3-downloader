from flask import Flask, render_template, request, jsonify
import yt_dlp
import threading
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
DOWNLOAD_DIR = '/downloads'

download_state = {
    'is_active': False,
    'is_paused': False,
    'title': 'Preparant...',
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
    
    pause_event.wait()
    
    if d['status'] == 'downloading':
        download_state['title'] = d.get('info_dict', {}).get('title', 'Descarregant...')
        total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
        downloaded = d.get('downloaded_bytes', 0)
        download_state['percent'] = round((downloaded / total) * 100, 1) if total > 0 else 0
        download_state['speed'] = d.get('_speed_str', '0 B/s').strip()
        download_state['current_file'] = d.get('filename', '')

def cleanup_temp_files():
    """Barre y elimina todos los archivos parciales del directorio"""
    for filename in os.listdir(DOWNLOAD_DIR):
        if filename.endswith('.part') or filename.endswith('.ytdl'):
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, filename))
            except:
                pass

def download_worker(url, quality):
    urls = extract_links_from_season(url)
    format_selector = 'best[height<=720]/bestvideo[height<=720]+bestaudio/best' if quality == 'media' else 'bestvideo+bestaudio/best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'merge_output_format': 'mkv',
        'ignoreerrors': True,
        'nocolor': True,
        'progress_hooks': [progress_hook],
        
        # Subtítulos
        'writesubtitles': True,
        'subtitleslangs': ['ca', 'es', 'en'], 
        'postprocessors': [{'key': 'FFmpegEmbedSubtitle'}],
        
        # Forzar metadatos solo de la pista de audio principal (0) a catalán
        'postprocessor_args': ['-metadata:s:a:0', 'language=cat']
    }

    download_state['is_active'] = True
    download_state['is_paused'] = False
    download_state['percent'] = 0
    pause_event.set()
    stop_event.clear()

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Iteramos uno a uno. Si se pulsa detener, rompemos el bucle limpiamente
            for u in urls:
                if stop_event.is_set():
                    break
                try:
                    ydl.download([u])
                except Exception as e:
                    if str(e) == "STOP_REQUESTED":
                        break
    finally:
        download_state['is_active'] = False
        if stop_event.is_set():
            cleanup_temp_files()
        else:
            download_state['title'] = 'Totes les descàrregues finalitzades'
            download_state['percent'] = 100
        download_state['speed'] = ''

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    if download_state['is_active']:
        return jsonify({"status": "error", "message": "En aquests moments hi ha una descàrrega en curs."})
    
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
        download_state['speed'] = 'Pausat'
    elif action == 'resume':
        pause_event.set()
        download_state['is_paused'] = False
    elif action == 'stop':
        stop_event.set()
        pause_event.set()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    app.run(host='0.0.0.0', port=5000)
