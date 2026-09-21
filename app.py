from flask import Flask, render_template, request, jsonify
import yt_dlp
import threading
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import uuid
import time

app = Flask(__name__)
DOWNLOAD_DIR = '/downloads'

# Nuevo sistema de estado global
tasks = {}
queue_lock = threading.Lock()
max_concurrent = 1

pause_event = threading.Event()
pause_event.set()
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

def cleanup_temp_files():
    for root, dirs, files in os.walk(DOWNLOAD_DIR):
        for filename in files:
            if filename.endswith('.part') or filename.endswith('.ytdl'):
                try:
                    os.remove(os.path.join(root, filename))
                except:
                    pass

def get_folder_name(url):
    try:
        clean_url = url.rstrip('/')
        parts = clean_url.split('/')
        if 'videos' in parts:
            idx = parts.index('videos')
            series = parts[idx-1].replace('-', ' ').title()
            season = parts[idx+1].replace('-', ' ').capitalize()
            return f"{series} - {season}"
        elif 'video' in parts:
            idx = parts.index('video')
            series = parts[idx-1].replace('-', ' ').title()
            return series
    except Exception:
        pass
    return "Descàrregues 3Cat"

# Gestor de cola en segundo plano
def queue_manager():
    while True:
        time.sleep(1)
        if stop_event.is_set():
            continue
        pause_event.wait()
        
        with queue_lock:
            active_count = sum(1 for t in tasks.values() if t['status'] == 'downloading')
            pending_tasks = [t for t in tasks.values() if t['status'] == 'pending']
            
            while active_count < max_concurrent and pending_tasks:
                task = pending_tasks.pop(0)
                task['status'] = 'downloading'
                threading.Thread(target=download_worker, args=(task['id'],)).start()
                active_count += 1

threading.Thread(target=queue_manager, daemon=True).start()

def get_progress_hook(task_id):
    def hook(d):
        if stop_event.is_set():
            raise Exception("STOP_REQUESTED")
        pause_event.wait()
        
        task = tasks.get(task_id)
        if not task: return
        
        if d['status'] == 'downloading':
            task['title'] = d.get('info_dict', {}).get('title', task['title'])
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            task['percent'] = round((downloaded / total) * 100, 1) if total > 0 else 0
            task['speed'] = d.get('_speed_str', '0 B/s').strip()
    return hook

def download_worker(task_id):
    task = tasks.get(task_id)
    if not task: return
    
    target_dir = os.path.join(DOWNLOAD_DIR, get_folder_name(task['url']))
    format_selector = 'bestvideo[height<=540]+bestaudio/best[height<=540]/bestvideo[height<720]+bestaudio/best' if task['quality'] == 'media' else 'bestvideo+bestaudio/best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(target_dir, '%(title)s.%(ext)s'),
        'merge_output_format': 'mkv',
        'ignoreerrors': True,
        'nocolor': True,
        'progress_hooks': [get_progress_hook(task_id)],
        'writesubtitles': True,
        'subtitleslangs': ['ca', 'es', 'en'], 
        'postprocessors': [
            {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mkv'},
            {'key': 'FFmpegEmbedSubtitle'}
        ],
        'postprocessor_args': {
            'VideoRemuxer': ['-metadata:s:a:0', 'language=cat'],
            'EmbedSubtitle': ['-metadata:s:a:0', 'language=cat']
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([task['url']])
        
        if stop_event.is_set():
            task['status'] = 'failed'
        else:
            task['status'] = 'finished'
            task['percent'] = 100
    except Exception:
        task['status'] = 'failed'

def extract_and_queue(url, quality, concurrent):
    global max_concurrent
    max_concurrent = int(concurrent)
    
    urls = extract_links_from_season(url)
    with queue_lock:
        for u in urls:
            task_id = str(uuid.uuid4())
            # Nombre temporal extraído de la URL hasta que yt-dlp lea el metadato real
            temp_title = u.strip('/').split('/')[-1].replace('-', ' ').title()
            tasks[task_id] = {
                'id': task_id,
                'title': temp_title,
                'url': u,
                'status': 'pending',
                'percent': 0,
                'speed': '',
                'quality': quality
            }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    url = request.form.get('url')
    quality = request.form.get('quality')
    concurrent = request.form.get('concurrent', 1)
    
    if not url:
        return jsonify({"status": "error", "message": "URL no proporcionada."})

    threading.Thread(target=extract_and_queue, args=(url, quality, concurrent)).start()
    return jsonify({"status": "success"})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'is_paused': not pause_event.is_set(),
        'tasks': list(tasks.values())
    })

@app.route('/action', methods=['POST'])
def handle_action():
    action = request.json.get('action')
    if action == 'pause':
        pause_event.clear()
    elif action == 'resume':
        pause_event.set()
    elif action == 'stop':
        stop_event.set()
        pause_event.set()
        with queue_lock:
            for t in tasks.values():
                if t['status'] in ['pending', 'downloading']:
                    t['status'] = 'failed'
        cleanup_temp_files()
        threading.Timer(2.0, stop_event.clear).start() # Restablece para futuras descargas
    return jsonify({"status": "success"})

if __name__ == '__main__':
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    app.run(host='0.0.0.0', port=5000)
