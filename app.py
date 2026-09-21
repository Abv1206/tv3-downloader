from flask import Flask, render_template, request, jsonify
import yt_dlp
import threading
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
DOWNLOAD_DIR = '/downloads'

def extract_links_from_season(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        
        # Busca cualquier enlace dentro de la página que contenga la palabra 'video'
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/video/' in href:
                full_url = urljoin(url, href)
                # Evita duplicados y no incluye la propia URL raíz de la temporada
                if full_url not in links and full_url != url:
                    links.append(full_url)
        return links
    except Exception as e:
        print(f"Error al analizar la web: {e}")
        return [url] # Si falla, intenta descargar la URL original como último recurso

def download_video(url, quality):
    urls_to_download = extract_links_from_season(url)
    print(f"Iniciando descarga de {len(urls_to_download)} enlaces encontrados...")

    if quality == 'media':
        format_selector = 'best[height<=720]/bestvideo[height<=720]+bestaudio/best'
    else:
        format_selector = 'bestvideo+bestaudio/best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'ignoreerrors': True, # Continúa con el siguiente capítulo si uno da error
        'no_warnings': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(urls_to_download)
    except Exception as e:
        print(f"Error en yt-dlp: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    url = request.form.get('url')
    quality = request.form.get('quality')
    
    if not url:
        return jsonify({"status": "error", "message": "URL no proporcionada"}), 400

    thread = threading.Thread(target=download_video, args=(url, quality))
    thread.start()
    
    return jsonify({"status": "success", "message": "Descarga iniciada. Revisa los logs de OMV para ver el progreso."})

if __name__ == '__main__':
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    app.run(host='0.0.0.0', port=5000)
