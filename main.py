"""
Backend "sujo" (sem estrutura chique) só pra testar o fluxo real na prática:

  usuário cola o link -> POST /meetings -> cria bot no Attendee.dev
       -> thread em background fica checando o status do bot
       -> quando a reunião termina, baixa a gravação (mp4)
       -> extrai o áudio com ffmpeg
       -> transcreve local com faster-whisper (gratuito)
       -> frontend faz polling em GET /meetings/{bot_id} até aparecer o transcript

Rodar:
    uvicorn main:app --reload --port 8080

Precisa de:
  - uma API key do Attendee (grátis em https://app.attendee.dev)
  - ffmpeg instalado no sistema (extração de áudio do mp4)
"""

import os
import time
import threading
import tempfile
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from faster_whisper import WhisperModel

PROJECT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = PROJECT_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")  # lê o arquivo .env se existir (veja .env.example)
except ImportError:
    pass  # se não tiver python-dotenv instalado, só usa variáveis de ambiente do sistema mesmo

ATTENDEE_BASE_URL = os.environ.get("ATTENDEE_BASE_URL", "https://app.attendee.dev")
ATTENDEE_API_KEY = os.environ.get("ATTENDEE_API_KEY", "")

app = FastAPI()

# "banco de dados" na unha, só em memória, pra testar. Some quando reinicia o servidor.
MEETINGS = {}  # bot_id -> {"meeting_url":..., "status":..., "transcript":...}

WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
WHISPER_CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", "4"))

print(f"[main] carregando modelo Whisper '{WHISPER_MODEL_NAME}' em português...")
MODEL = WhisperModel(
    WHISPER_MODEL_NAME,
    device="cpu",
    compute_type="int8",
    cpu_threads=WHISPER_CPU_THREADS,
    num_workers=1,
)


def attendee_headers():
    return {
        "Authorization": f"Token {ATTENDEE_API_KEY}",
        "Content-Type": "application/json",
    }


@app.get("/", response_class=HTMLResponse)
def index():
    with open(PROJECT_DIR / "index.html", encoding="utf-8") as f:
        return f.read()


@app.post("/meetings")
def create_meeting(payload: dict):
    meeting_url = payload.get("meeting_url", "").strip()
    if not meeting_url:
        return JSONResponse({"error": "meeting_url é obrigatório"}, status_code=400)
    if not ATTENDEE_API_KEY.strip():
        return JSONResponse(
            {"error": "ATTENDEE_API_KEY não configurada. Crie um arquivo .env na raiz do projeto."},
            status_code=503,
        )

    body = {"meeting_url": meeting_url, "bot_name": "ToNaCall"}
    resp = requests.post(f"{ATTENDEE_BASE_URL}/api/v1/bots", json=body, headers=attendee_headers())
    if resp.status_code >= 400:
        return JSONResponse({"error": resp.text}, status_code=resp.status_code)

    data = resp.json()
    bot_id = data["id"]
    MEETINGS[bot_id] = {"meeting_url": meeting_url, "status": data.get("state", "joining"), "transcript": None}

    threading.Thread(target=poll_until_ended, args=(bot_id,), daemon=True).start()
    return {"bot_id": bot_id, "status": MEETINGS[bot_id]["status"]}


@app.get("/meetings/{bot_id}")
def get_meeting(bot_id: str):
    info = MEETINGS.get(bot_id)
    if not info:
        return JSONResponse({"error": "não encontrado"}, status_code=404)
    response = {key: value for key, value in info.items() if key != "audio_path"}
    if info.get("audio_path"):
        response["audio_url"] = f"/meetings/{bot_id}/audio"
    return response


@app.get("/meetings/{bot_id}/audio")
def get_audio(bot_id: str):
    info = MEETINGS.get(bot_id)
    if not info or not info.get("audio_path"):
        return JSONResponse({"error": "áudio ainda não disponível"}, status_code=404)

    audio_path = Path(info["audio_path"]).resolve()
    if audio_path.parent != RECORDINGS_DIR.resolve() or not audio_path.is_file():
        return JSONResponse({"error": "arquivo de áudio não encontrado"}, status_code=404)
    return FileResponse(audio_path, media_type="audio/wav", filename=f"meet-{bot_id}.wav")


def poll_until_ended(bot_id, interval_seconds=15, timeout_seconds=4 * 3600):
    """Fica checando o status do bot até a reunião terminar (versão simples, sem webhook)."""
    elapsed = 0
    while elapsed < timeout_seconds:
        time.sleep(interval_seconds)
        elapsed += interval_seconds
        try:
            resp = requests.get(f"{ATTENDEE_BASE_URL}/api/v1/bots/{bot_id}", headers=attendee_headers())
            resp.raise_for_status()
            state = resp.json().get("state")
        except Exception as e:
            print(f"[poll] erro checando bot {bot_id}: {e}")
            continue

        MEETINGS[bot_id]["status"] = state
        print(f"[poll] bot {bot_id} -> {state}")

        if state == "ended":
            process_recording(bot_id)
            return
        if state in ("fatal_error", "could_not_join"):
            MEETINGS[bot_id]["status"] = f"erro: {state}"
            return


def process_recording(bot_id):
    try:
        MEETINGS[bot_id]["status"] = "baixando_gravacao"
        resp = requests.get(f"{ATTENDEE_BASE_URL}/api/v1/bots/{bot_id}/recording", headers=attendee_headers())
        resp.raise_for_status()
        video_url = resp.json()["url"]

        audio_path = RECORDINGS_DIR / f"{bot_id}.wav"
        with tempfile.TemporaryDirectory() as tmp:
            mp4_path = os.path.join(tmp, "gravacao.mp4")

            with requests.get(video_url, stream=True) as r:
                r.raise_for_status()
                with open(mp4_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            MEETINGS[bot_id]["status"] = "extraindo_audio"
            import subprocess
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", mp4_path, "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(audio_path),
                ],
                check=True, capture_output=True,
            )

            MEETINGS[bot_id]["status"] = "transcrevendo"
            segments, _ = MODEL.transcribe(
                str(audio_path),
                language="pt",
                task="transcribe",
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
                condition_on_previous_text=False,
                initial_prompt=(
                    "Português brasileiro. Reunião profissional."
                ),
            )
            texto = "\n".join(f"[{s.start:.1f}s] {s.text.strip()}" for s in segments if s.text.strip())

            MEETINGS[bot_id]["audio_path"] = str(audio_path)
            MEETINGS[bot_id]["transcript"] = texto or "(nenhuma fala detectada)"
            MEETINGS[bot_id]["status"] = "concluido"
    except Exception as e:
        MEETINGS[bot_id]["status"] = f"erro: {e}"
        print(f"[process_recording] erro no bot {bot_id}: {e}")
