FROM python:3.11-slim

# System deps: ffmpeg, imagemagick, piper
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        imagemagick \
        curl \
        wget \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-eng \
        libreoffice-core \
        libreoffice-common \
        libreoffice-writer \
        libreoffice-impress \
        libreoffice-draw \
        default-jre-headless \
        djvulibre-bin \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install piper TTS binary (Piper: x86_64/aarch64/armv7l; dpkg: amd64/arm64/armhf)
RUN mkdir -p /opt/piper && \
    DPKG_ARCH=$(dpkg --print-architecture) && \
    if [ "$DPKG_ARCH" = "amd64" ]; then PIPER_ARCH=x86_64; elif [ "$DPKG_ARCH" = "arm64" ]; then PIPER_ARCH=aarch64; elif [ "$DPKG_ARCH" = "armhf" ]; then PIPER_ARCH=armv7l; else PIPER_ARCH=x86_64; fi && \
    wget -qO /tmp/piper.tar.gz "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_${PIPER_ARCH}.tar.gz" && \
    tar -xzf /tmp/piper.tar.gz -C /opt/piper --strip-components=1 && \
    rm /tmp/piper.tar.gz && \
    ln -s /opt/piper/piper /usr/local/bin/piper

# Piper voices: 4 Russian (medium) + 1 English (each needs .onnx + .onnx.json config)
ENV PIPER_VOICES_DIR=/opt/piper-voices
RUN mkdir -p ${PIPER_VOICES_DIR} && \
    BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-denis-medium.onnx "${BASE}/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-denis-medium.onnx.json "${BASE}/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx.json" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-irina-medium.onnx "${BASE}/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-irina-medium.onnx.json "${BASE}/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-dmitri-medium.onnx "${BASE}/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-dmitri-medium.onnx.json "${BASE}/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-ruslan-medium.onnx "${BASE}/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" && \
    wget -qO ${PIPER_VOICES_DIR}/ru_RU-ruslan-medium.onnx.json "${BASE}/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json" && \
    wget -qO ${PIPER_VOICES_DIR}/en_US-lessac-medium.onnx "${BASE}/en/en_US/lessac/medium/en_US-lessac-medium.onnx" && \
    wget -qO ${PIPER_VOICES_DIR}/en_US-lessac-medium.onnx.json "${BASE}/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"

# ImageMagick policy: allow text rendering
RUN if [ -f /etc/ImageMagick-6/policy.xml ]; then \
        sed -i 's/rights="none" pattern="@\*"/rights="read|write" pattern="@*"/' /etc/ImageMagick-6/policy.xml; \
    fi

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Preload sentence-transformers embedding model into the image so runtime is fully offline.
ENV HF_HOME=/opt/hf-home
ENV TRANSFORMERS_CACHE=/opt/hf-home/hub
ENV SENTENCE_TRANSFORMERS_HOME=/opt/sentence-transformers
ENV EMBEDDING_MODEL=/opt/sentence-transformers/all-MiniLM-L6-v2
ENV FASTER_WHISPER_DOWNLOAD_ROOT=/opt/faster-whisper-models
ENV FASTER_WHISPER_MODEL=small
RUN mkdir -p "${HF_HOME}" "${SENTENCE_TRANSFORMERS_HOME}" "${EMBEDDING_MODEL}" && \
    python -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/opt/sentence-transformers'); m.save('/opt/sentence-transformers/all-MiniLM-L6-v2')"
RUN mkdir -p "${FASTER_WHISPER_DOWNLOAD_ROOT}" && \
    python -c "import os; from faster_whisper.utils import download_model; root=os.environ['FASTER_WHISPER_DOWNLOAD_ROOT']; models=['tiny','base','small']; [download_model(m, output_dir=f'{root}/{m}', local_files_only=False) for m in models]"
ENV VOSK_MODELS_DIR=/opt/vosk-models
ENV VOSK_MODEL_PATH=/opt/vosk-models/vosk-model-small-ru-0.22
RUN mkdir -p "${VOSK_MODELS_DIR}" && \
    wget -qO /tmp/vosk-ru-small.zip "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip" && \
    python -c "import zipfile; zipfile.ZipFile('/tmp/vosk-ru-small.zip').extractall('/opt/vosk-models')" && \
    rm -f /tmp/vosk-ru-small.zip
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV HF_DATASETS_OFFLINE=1
ENV HF_HUB_DISABLE_TELEMETRY=1

# Pre-download Silero TTS Russian v5 (direct URL; no GitHub/torch.hub during build)
ENV TORCH_HOME=/opt/silero-models
ENV SILERO_MODELS_DIR=/opt/silero-models
RUN mkdir -p ${SILERO_MODELS_DIR} && \
    wget -qO ${SILERO_MODELS_DIR}/v5_ru.pt "https://models.silero.ai/models/tts/ru/v5_ru.pt"

# Offline music assets for podcast post-processing (intro/background/outro)
ENV MUSIC_ASSETS_DIR=/opt/audio-assets
RUN mkdir -p ${MUSIC_ASSETS_DIR} && \
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=740:duration=1.0" \
      -af "volume=0.25,afade=t=in:st=0:d=0.05,afade=t=out:st=0.85:d=0.15" \
      -codec:a libmp3lame -b:a 192k ${MUSIC_ASSETS_DIR}/intro.mp3 && \
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=180:duration=20" \
      -af "volume=0.04,lowpass=f=600" \
      -codec:a libmp3lame -b:a 192k ${MUSIC_ASSETS_DIR}/background.mp3 && \
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=620:duration=1.4" \
      -af "volume=0.22,afade=t=in:st=0:d=0.08,afade=t=out:st=1.1:d=0.3" \
      -codec:a libmp3lame -b:a 192k ${MUSIC_ASSETS_DIR}/outro.mp3

# Copy local optional audio assets from repository media/audio/.
# Keep generated defaults too; if target custom track exists, make it default for intro/background/outro.
COPY media/audio/ ${MUSIC_ASSETS_DIR}/
RUN if [ -f "${MUSIC_ASSETS_DIR}/zvuk-sovershennogo-volshebstva-1149.mp3" ]; then \
      cp "${MUSIC_ASSETS_DIR}/zvuk-sovershennogo-volshebstva-1149.mp3" "${MUSIC_ASSETS_DIR}/intro.mp3" && \
      cp "${MUSIC_ASSETS_DIR}/zvuk-sovershennogo-volshebstva-1149.mp3" "${MUSIC_ASSETS_DIR}/background.mp3" && \
      cp "${MUSIC_ASSETS_DIR}/zvuk-sovershennogo-volshebstva-1149.mp3" "${MUSIC_ASSETS_DIR}/outro.mp3" ; \
    fi

COPY backend/ .

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
