from flask import Flask, render_template, request, jsonify
import yt_dlp
import threading
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import uuid
import time
import re

app = Flask(__name__)
DOWNLOAD_DIR = '/downloads'

# Nuevo sistema de estado global
tasks = {}
queue_lock = threading.Lock()
max_concurrent = 1
extracting_count = 0

pause_event = threading.Event()
pause_event.set()
stop_event = threading.Event()

def extract_links_from_season(url):
    # Si la URL es de un capítulo suelto, devolvemos solo ese enlace
    if '/video/' in url and '/videos/' not in url:
        return [url]
        
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        
        # 1. Extraemos los vídeos de la página principal estática
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/video/' in href:
                full_url = urljoin(url, href)
                if full_url not in links and full_url != url:
                    links.append(full_url)
                    
        # 2. Cazamos el ID del programa para la paginación dinámica usando Regex
        # Buscamos un patrón tipo: contenidorVideosStandAloneDefault/2/120086583
        match = re.search(r'contenidorVideosStandAloneDefault/\d+/(\d+)', response.text)
        
        if match:
            program_id = match.group(1)
            pagina = 2 # La petición inicial equivale a la página 1
            
            while True:
                # Construimos la URL secreta de la API
                api_url = f"https://www.3cat.cat/Comu/standalone/tv3_sx3_item_fitxa-programa_videos/contenidor/contenidorVideosStandAloneDefault/{pagina}/{program_id}/"
                api_res = requests.get(api_url, headers=headers)
                
                # Si la página da error 404, hemos llegado al final
                if api_res.status_code != 200:
                    break
                    
                # Parseamos el fragmento de HTML devuelto
                api_soup = BeautifulSoup(api_res.text, 'html.parser')
                nuevos_enlaces = 0
                
                for a in api_soup.find_all('a', href=True):
                    href = a['href']
                    if '/video/' in href:
                        full_url = urljoin(url, href)
                        if full_url not in links and full_url != url:
                            links.append(full_url)
                            nuevos_enlaces += 1
                            
                # Si el fragmento HTML no contiene vídeos nuevos, detenemos el bucle
                if nuevos_enlaces == 0:
                    break
                    
                pagina += 1 # Avanzamos silenciosamente a la siguiente página
                
        return links
        
    except Exception as e:
        print(f"Error procesando enlaces: {e}")
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
        
        # 1. Rutas de vistas generales o temporadas enteras
        if 'videos' in parts:
            idx = parts.index('videos')
            series = parts[idx-1].replace('-', ' ').title()
            
            # Si hay algo después de /videos/ (ej: temporada-1), lo añadimos
            if idx + 1 < len(parts):
                season = parts[idx+1].replace('-', ' ').capitalize()
                return f"{series} - {season}"
            else:
                # Si no hay temporada (ej: /pokemon/videos), raspeamos el pgm_titol
                headers = {'User-Agent': 'Mozilla/5.0'}
                res = requests.get(url, headers=headers, timeout=10)
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # Buscamos por clase o ID ignorando mayúsculas/minúsculas
                titol_elem = soup.find(class_=re.compile(r'pgm_titol', re.IGNORECASE))
                if not titol_elem:
                    titol_elem = soup.find(id=re.compile(r'pgm_titol', re.IGNORECASE))
                    
                if titol_elem and titol_elem.text.strip():
                    return titol_elem.text.strip()
                
                # Si no encuentra la etiqueta, devuelve el nombre deducido de la URL (Ej: "Pokemon")
                return series
                
        # 2. Rutas de un capítulo suelto
        elif 'video' in parts:
            idx = parts.index('video')
            series = parts[idx-1].replace('-', ' ').title()
            
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            texts_to_search = []
            if soup.title: 
                texts_to_search.append(soup.title.string)
            for meta in soup.find_all('meta'):
                if meta.get('content'): 
                    texts_to_search.append(meta.get('content'))
            
            for text in texts_to_search:
                match = re.search(r'(Temporada\s+\d+)', text, re.IGNORECASE)
                if match:
                    return f"{series} - {match.group(1).capitalize()}"
                
                match_t = re.search(r'\bT(\d+)\b', text, re.IGNORECASE)
                if match_t:
                    return f"{series} - Temporada {match_t.group(1)}"
            
            return series
            
    except Exception as e:
        print(f"Error obteniendo carpeta principal: {e}")
        
    # 3. SALVAVIDAS FINAL: Si la URL es rarísima y falla todo, buscamos el pgm_titol directamente
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        titol_elem = soup.find(class_=re.compile(r'pgm_titol', re.IGNORECASE))
        if not titol_elem:
            titol_elem = soup.find(id=re.compile(r'pgm_titol', re.IGNORECASE))
        if titol_elem and titol_elem.text.strip():
            return titol_elem.text.strip()
    except:
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
    
    # Leemos la carpeta que se le asignó al añadirlo a la cola, no calculamos nada nuevo
    target_dir = os.path.join(DOWNLOAD_DIR, task['folder'])
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


def extract_and_queue(url, quality):
    global extracting_count
    extracting_count += 1  # Activamos el indicador de carga
    try:
        base_folder = get_folder_name(url)
        urls = extract_links_from_season(url)
        
        with queue_lock:
            for u in urls:
                task_id = str(uuid.uuid4())
                temp_title = u.strip('/').split('/')[-1].replace('-', ' ').title()
                tasks[task_id] = {
                    'id': task_id,
                    'title': temp_title,
                    'url': u,
                    'status': 'pending',
                    'percent': 0,
                    'speed': '',
                    'quality': quality,
                    'folder': base_folder
                }
    finally:
        extracting_count -= 1  # Lo desactivamos pase lo que pase (incluso si hay error)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    url = request.form.get('url')
    quality = request.form.get('quality')
    
    if not url:
        return jsonify({"status": "error", "message": "URL no proporcionada."})

    threading.Thread(target=extract_and_queue, args=(url, quality)).start()
    return jsonify({"status": "success"})

# Nueva ruta para actualizar el límite en tiempo real
@app.route('/set_concurrent', methods=['POST'])
def set_concurrent():
    global max_concurrent
    max_concurrent = int(request.json.get('concurrent', 1))
    return jsonify({"status": "success", "max_concurrent": max_concurrent})

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'is_paused': not pause_event.is_set(),
        'tasks': list(tasks.values()),
        'max_concurrent': max_concurrent,
        'is_extracting': extracting_count > 0  # Informamos a la web si estamos buscando enlaces
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
        cleanup_temp_files()
        with queue_lock:
            tasks.clear() # Al cancelar, vaciamos el tablero de inmediato
        threading.Timer(2.0, stop_event.clear).start()
    elif action == 'clear':
        with queue_lock:
            tasks.clear() # Limpieza manual del historial cuando finalizan las descargas
    return jsonify({"status": "success"})

if __name__ == '__main__':
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    app.run(host='0.0.0.0', port=5000)