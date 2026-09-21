# 3Cat/TV3 Recursive Downloader

A lightweight, Dockerized web application designed to automatically and recursively download episodes and seasons from the 3Cat (TV3) platform. Built with Python, Flask, and `yt-dlp`, this tool is optimized for NAS environments like OpenMediaVault.

## Features

* **Recursive Downloading:** Process entire seasons by providing a single URL.
* **Accurate Naming:** Extracts official episode titles directly from the platform's metadata, avoiding generic alphanumeric filenames.
* **Quality Selection:** Choose between medium (up to 720p) and high (maximum available) resolution.
* **Automatic Track Merging:** Built-in `ffmpeg` seamlessly merges separated HLS/m3u8 audio and video streams into standard MP4 files.
* **Web Interface:** Clean, user-friendly UI to queue downloads without terminal access.

## Deployment (Docker Compose)

Modify the `volumes` path to match your local download directory and update the `image` URL with your GitHub username if using a custom GitHub Container Registry build.

```yaml
version: "3.8"
services:
  tv3_downloader:
    container_name: tv3_downloader
    image: ghcr.io/YOUR_GITHUB_USERNAME/tv3-downloader:latest
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - /path/to/your/downloads:/downloads
