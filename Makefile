.PHONY: up down build logs backend-dev frontend-dev clean install-deps amd64-build amd64-export amd64-load-up amd64-up amd64-target-up rag-smoke api-smoke turn-json-smoke project-notebook-smoke frontend-smoke

# ---- Установка зависимостей (для генерации аудио) ----

install-deps:
	brew install ffmpeg espeak-ng
	@echo "Готово. Piper уже в backend/piper_bin/. На Mac ARM при ошибке архитектуры используйте: docker compose up"

# ---- Docker Compose ----

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

amd64-build:
	docker compose -f docker-compose.yml -f docker-compose.amd64.yml build

amd64-export:
	docker buildx build --platform linux/amd64 -t open-notebooklm-backend:amd64 --load -f backend/Dockerfile .
	docker buildx build --platform linux/amd64 -t open-notebooklm-frontend:amd64 --load -f frontend/Dockerfile frontend
	CHROMA_AMD64_REF=$$(docker buildx imagetools inspect chromadb/chroma:latest | awk '/^  Name:/{name=$$2} /^  Platform:[[:space:]]+linux\/amd64/{print name}'); \
	docker pull "$$CHROMA_AMD64_REF"; \
	docker tag "$$CHROMA_AMD64_REF" open-notebooklm-chroma:amd64
	docker save -o open-notebooklm-amd64-images.tar \
		open-notebooklm-backend:amd64 \
		open-notebooklm-frontend:amd64 \
		open-notebooklm-chroma:amd64

amd64-load-up:
	docker load -i open-notebooklm-amd64-images.tar
	docker compose -f docker-compose.yml -f docker-compose.amd64.yml up -d

amd64-target-up:
	docker load -i open-notebooklm-amd64-images.tar
	docker compose -f docker-compose.target.yml up -d

amd64-up:
	docker compose -f docker-compose.yml -f docker-compose.amd64.yml up -d --build

logs:
	docker compose logs -f

# ---- Local development (without Docker) ----

backend-dev:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

frontend-dev:
	cd frontend && npm install && npm run dev

# ---- Utilities ----

clean:
	rm -rf data/outputs/*
	rm -rf data/index/*
	rm -f data/jobs.json
	rm -f data/jobs_artifacts.json
	@echo "Cleaned output and index data."

rag-smoke:
	cd backend && python3 -m unittest -q tests.test_rag_hybrid

api-smoke:
	cd backend && python3 -m unittest -q tests.test_api_smoke_pipeline

turn-json-smoke:
	cd backend && python3 -m unittest -q tests.test_turn_taking_json_stability

project-notebook-smoke:
	cd backend && python3 -m unittest -q tests.test_project_notebook_api

frontend-smoke:
	cd frontend && npm test
