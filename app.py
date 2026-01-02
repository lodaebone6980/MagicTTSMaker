"""
Supertone TTS Parallel Processor - Streamlit App
병렬 처리로 슈퍼톤 TTS를 빠르게 생성하는 웹 앱
"""

import streamlit as st
import requests
import asyncio
import aiohttp
import io
import time
import re
import json
import base64
import os
import threading
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict
import wave
import zipfile

# ==================== File Server for Large Downloads ====================

FILE_SERVER_PORT = 8501  # Streamlit usually runs on 8501, we'll use 8502
DOWNLOAD_SERVER_PORT = 8502

class QuietHandler(SimpleHTTPRequestHandler):
    """조용한 HTTP 핸들러 (로그 최소화)"""
    def log_message(self, format, *args):
        pass  # 로그 출력 안 함

    def end_headers(self):
        # CORS 허용 및 다운로드 강제
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

def find_available_port(start_port: int = 8502, max_tries: int = 10) -> int:
    """사용 가능한 포트 찾기"""
    for port in range(start_port, start_port + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    return start_port

def start_file_server(directory: Path, port: int) -> int:
    """백그라운드에서 파일 서버 시작"""
    directory.mkdir(exist_ok=True)

    handler = partial(QuietHandler, directory=str(directory))

    try:
        server = HTTPServer(('0.0.0.0', port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return port
    except OSError:
        # 포트가 이미 사용 중이면 다른 포트 시도
        new_port = find_available_port(port + 1)
        server = HTTPServer(('0.0.0.0', new_port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return new_port

def get_download_url(filename: str, port: int) -> str:
    """다운로드 URL 생성"""
    return f"http://localhost:{port}/{filename}"

# ==================== Persistent Storage ====================

DATA_DIR = Path(__file__).parent / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
SETTINGS_FILE = DATA_DIR / "settings.json"


def ensure_data_dir():
    """데이터 디렉토리 생성"""
    DATA_DIR.mkdir(exist_ok=True)


def load_global_settings() -> Dict:
    """전역 설정 로드"""
    ensure_data_dir()
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # JSON에서 로드 시 키가 문자열로 변환되므로 정수로 복원
                if "voice_favorites" in data:
                    new_favorites = {}
                    for voice_id, favs in data["voice_favorites"].items():
                        new_favorites[voice_id] = {}
                        for k, v in favs.items():
                            new_favorites[voice_id][int(k)] = v
                    data["voice_favorites"] = new_favorites
                return data
        except Exception:
            pass
    return {}


def save_global_settings(settings: Dict):
    """전역 설정 저장"""
    ensure_data_dir()
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"설정 저장 실패: {e}")


def load_projects() -> List[Dict]:
    """프로젝트 목록 로드"""
    ensure_data_dir()
    if PROJECTS_FILE.exists():
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_projects(projects: List[Dict]):
    """프로젝트 목록 저장"""
    ensure_data_dir()
    try:
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"프로젝트 저장 실패: {e}")


def get_project_by_id(project_id: str) -> Optional[Dict]:
    """ID로 프로젝트 찾기"""
    projects = load_projects()
    for p in projects:
        if p.get("id") == project_id:
            return p
    return None


def save_current_project():
    """현재 프로젝트 저장"""
    project_id = st.session_state.get("current_project_id")
    if not project_id:
        return

    projects = load_projects()
    project_data = {
        "id": project_id,
        "name": st.session_state.get("project_name", "새 프로젝트"),
        "updated_at": int(time.time()),
        "selected_voice": st.session_state.get("selected_voice"),
        "selected_voice_id": st.session_state.get("selected_voice_id"),
        "script_input": st.session_state.get("script_input", ""),
        "max_chars": st.session_state.get("max_chars", 300),
    }

    # 기존 프로젝트 업데이트 또는 새로 추가
    found = False
    for i, p in enumerate(projects):
        if p.get("id") == project_id:
            projects[i] = project_data
            found = True
            break

    if not found:
        projects.append(project_data)

    save_projects(projects)


def create_new_project(name: str) -> str:
    """새 프로젝트 생성"""
    project_id = f"proj_{int(time.time())}_{os.urandom(4).hex()}"
    projects = load_projects()
    projects.append({
        "id": project_id,
        "name": name,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "selected_voice": None,
        "selected_voice_id": None,
        "script_input": "",
        "max_chars": 300,
    })
    save_projects(projects)
    return project_id


def delete_project(project_id: str):
    """프로젝트 삭제"""
    projects = load_projects()
    projects = [p for p in projects if p.get("id") != project_id]
    save_projects(projects)


def load_project_to_session(project: Dict):
    """프로젝트를 세션에 로드"""
    st.session_state.current_project_id = project.get("id")
    st.session_state.project_name = project.get("name", "새 프로젝트")
    st.session_state.selected_voice = project.get("selected_voice")
    st.session_state.selected_voice_id = project.get("selected_voice_id")
    st.session_state.script_input = project.get("script_input", "")
    st.session_state.max_chars = project.get("max_chars", 300)

# ==================== Configuration ====================

SUPERTONE_API_BASE = "https://supertoneapi.com/v1"
DEFAULT_RPM = 60

SONA_MODELS = {
    "Sona 1": "sona_speech_1",
    "Sona 2": "sona_speech_2",
    "Supertonic API 1": "supertonic_api_1",
}

# 모델별 지원 언어
MODEL_LANGUAGES = {
    "sona_speech_1": ["en", "ko", "ja"],
    "supertonic_api_1": ["en", "ko", "ja", "es", "pt"],
    "sona_speech_2": ["en", "ko", "ja", "bg", "cs", "da", "el", "es", "et", "fi", "hu", "it", "nl", "pl", "pt", "ro", "ar", "de", "fr", "hi", "id", "ru", "vi"],
}

# 모델별 지원 음성 설정
MODEL_VOICE_SETTINGS = {
    "sona_speech_1": ["pitch_shift", "pitch_variance", "speed", "duration", "similarity", "text_guidance", "subharmonic_amplitude_control"],
    "supertonic_api_1": ["speed"],  # speed만 지원
    "sona_speech_2": ["pitch_shift", "pitch_variance", "speed"],
}

CATEGORIES = [
    "All", "Meme", "Conversational", "Business", "Narration",
    "Announcement", "Education", "Game", "Storytelling", "Acting",
    "News", "Entertainment", "Humor"
]

LANGUAGES_FILTER = ["All", "Korean", "English", "Japanese"]
GENDERS = ["All", "Male", "Female"]
AGE_GROUPS = ["All", "Child", "Young-Adult", "Middle-Aged", "Senior"]

# 캐릭터 이미지 (DiceBear Avatars API 사용)
def get_avatar_url(name: str, gender: str = "Male") -> str:
    style = "adventurer" if gender == "Male" else "adventurer"
    return f"https://api.dicebear.com/7.x/{style}/svg?seed={name}&backgroundColor=b6e3f4,c0aede,d1d4f9"

# ==================== Sample Voice Data ====================

SAMPLE_VOICES = [
    {"voice_id": "preset_anderson", "name": "Anderson", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Narration", "Storytelling"], "styles": ["neutral", "calm", "serious"], "description": "따뜻하고 신뢰감 있는 내레이션 음성", "is_new": True},
    {"voice_id": "preset_barbara", "name": "Barbara", "language": "English", "gender": "Female", "age_group": "Middle-Aged", "genres": ["News", "Announcement"], "styles": ["neutral", "professional"], "description": "전문적이고 명확한 아나운서 음성", "is_new": True},
    {"voice_id": "preset_daniel", "name": "Daniel", "language": "English", "gender": "Male", "age_group": "Middle-Aged", "genres": ["News", "Announcement"], "styles": ["neutral", "authoritative"], "description": "권위있고 신뢰감 있는 뉴스 음성", "is_new": True},
    {"voice_id": "preset_flop", "name": "Flop", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Game", "Entertainment"], "styles": ["neutral", "energetic", "playful"], "description": "활기차고 재미있는 게임 캐릭터 음성", "is_new": True},
    {"voice_id": "preset_hyunsook", "name": "Hyunsook", "language": "English", "gender": "Female", "age_group": "Young-Adult", "genres": ["Entertainment", "Conversational"], "styles": ["neutral", "friendly", "cheerful"], "description": "밝고 친근한 엔터테인먼트 음성", "is_new": True},
    {"voice_id": "preset_juho", "name": "Juho", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Conversational", "Education"], "styles": ["neutral", "warm", "gentle"], "description": "따뜻하고 편안한 대화형 음성", "is_new": True},
    {"voice_id": "preset_kan", "name": "Kan", "language": "English", "gender": "Male", "age_group": "Middle-Aged", "genres": ["Game", "Acting"], "styles": ["neutral", "dramatic", "intense"], "description": "드라마틱하고 강렬한 연기 음성", "is_new": True},
    {"voice_id": "preset_mansu", "name": "Mansu", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Humor", "Entertainment"], "styles": ["neutral", "funny", "sarcastic"], "description": "유머러스하고 재치있는 음성", "is_new": True},
    {"voice_id": "preset_oksoon", "name": "Oksoon", "language": "English", "gender": "Female", "age_group": "Young-Adult", "genres": ["Entertainment", "Conversational"], "styles": ["neutral", "cute", "bright"], "description": "귀엽고 밝은 여성 음성", "is_new": True},
    {"voice_id": "preset_garret", "name": "Garret", "language": "Korean", "gender": "Male", "age_group": "Young-Adult", "genres": ["Narration", "Business"], "styles": ["neutral", "professional", "calm"], "description": "차분하고 전문적인 한국어 남성 음성", "is_new": False},
    {"voice_id": "preset_minjae", "name": "민재", "language": "Korean", "gender": "Male", "age_group": "Young-Adult", "genres": ["Conversational", "Education"], "styles": ["neutral", "friendly"], "description": "친근하고 자연스러운 한국어 남성 음성", "is_new": False},
    {"voice_id": "preset_sooyoung", "name": "수영", "language": "Korean", "gender": "Female", "age_group": "Young-Adult", "genres": ["Narration", "Entertainment"], "styles": ["neutral", "warm", "elegant"], "description": "우아하고 따뜻한 한국어 여성 음성", "is_new": False},
    {"voice_id": "preset_jiwon", "name": "지원", "language": "Korean", "gender": "Female", "age_group": "Young-Adult", "genres": ["News", "Business"], "styles": ["neutral", "professional", "clear"], "description": "명확하고 전문적인 한국어 여성 음성", "is_new": False},
    {"voice_id": "preset_yuki", "name": "Yuki", "language": "Japanese", "gender": "Female", "age_group": "Young-Adult", "genres": ["Entertainment", "Game"], "styles": ["neutral", "cute", "energetic"], "description": "귀엽고 활기찬 일본어 여성 음성", "is_new": False},
    {"voice_id": "preset_takeshi", "name": "Takeshi", "language": "Japanese", "gender": "Male", "age_group": "Middle-Aged", "genres": ["Narration", "Business"], "styles": ["neutral", "serious", "professional"], "description": "진지하고 전문적인 일본어 남성 음성", "is_new": False},
]

# ==================== Data Classes ====================

@dataclass
class TTSBlock:
    index: int
    text: str
    audio_data: Optional[bytes] = None
    status: str = "pending"
    error_message: str = ""


@dataclass
class TTSSettings:
    voice_id: str
    voice_name: str
    language: str
    style: str
    model: str
    pitch_shift: float
    pitch_variance: float
    speed: float
    duration: float = 0.0
    similarity: float = 3.0
    text_guidance: float = 1.0
    subharmonic_amplitude_control: float = 1.0
    output_format: str = "wav"


# ==================== Supertone API Client ====================

class SupertoneClient:
    def __init__(self, api_key: str, rpm: int = DEFAULT_RPM):
        self.api_key = api_key
        self.rpm = rpm

    def get_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-sup-api-key": self.api_key,
        }

    def get_credits(self) -> Optional[float]:
        """크레딧 잔액 조회"""
        try:
            response = requests.get(
                f"{SUPERTONE_API_BASE}/credits",
                headers=self.get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("balance")
            return None
        except Exception:
            return None

    def get_custom_voices(self) -> List[dict]:
        """커스텀(클론) 보이스 목록 가져오기"""
        all_voices = []
        next_page_token = None
        page_count = 0

        try:
            while True:
                page_count += 1
                params = {"page_size": 100}
                if next_page_token:
                    params["next_page_token"] = next_page_token

                response = requests.get(
                    f"{SUPERTONE_API_BASE}/custom-voices",
                    headers=self.get_headers(),
                    params=params,
                    timeout=30
                )

                if response.status_code != 200:
                    break

                data = response.json()
                voices = data.get("items", [])
                all_voices.extend(voices)

                next_page_token = data.get("next_page_token")
                total = data.get("total", 0)

                if total and len(all_voices) >= total:
                    break
                if not next_page_token:
                    break
                if page_count > 10:
                    break

            # 커스텀 보이스 형식 변환
            formatted = []
            for v in all_voices:
                formatted.append({
                    "voice_id": v.get("voice_id", ""),
                    "name": v.get("name", "Unknown"),
                    "language": "Custom",
                    "gender": "Custom",
                    "age_group": "Custom",
                    "genres": ["Custom"],
                    "styles": ["default"],
                    "description": v.get("description", "커스텀 클론 보이스"),
                    "is_new": False,
                    "is_custom": True,
                    "image_url": "",
                })
            return formatted

        except Exception:
            return []

    def predict_duration(self, text: str, voice_id: str, language: str, style: str = None, model: str = "sona_speech_1") -> Optional[float]:
        """TTS 길이 예측 (크레딧 소모 없음)"""
        try:
            payload = {
                "text": text,
                "language": language,
                "model": model,
            }
            if style:
                payload["style"] = style

            response = requests.post(
                f"{SUPERTONE_API_BASE}/predict-duration/{voice_id}",
                headers=self.get_headers(),
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("duration")
            return None
        except Exception:
            return None

    def get_voices(self) -> List[dict]:
        """API에서 보이스 목록 가져오기 (페이지네이션 처리)"""
        all_voices = []
        next_page_token = None
        page_count = 0

        try:
            while True:
                page_count += 1
                # 페이지네이션 파라미터 (Supertone API 문서 기준)
                params = {"page_size": 100}  # max 100, snake_case
                if next_page_token:
                    params["next_page_token"] = next_page_token

                response = requests.get(
                    f"{SUPERTONE_API_BASE}/voices",  # /voices 엔드포인트 (전체 목록)
                    headers=self.get_headers(),
                    params=params,
                    timeout=30
                )

                if response.status_code != 200:
                    st.error(f"보이스 목록 조회 실패: {response.status_code} - {response.text[:200]}")
                    break

                data = response.json()
                voices = data.get("items", [])  # items 배열
                all_voices.extend(voices)

                # 다음 페이지 토큰 확인
                next_page_token = data.get("next_page_token")

                # 총 개수 확인
                total = data.get("total", 0)
                if total and len(all_voices) >= total:
                    break

                if not next_page_token:
                    break

                # 무한 루프 방지
                if page_count > 10:
                    break

            # API 응답 형식에 맞게 변환
            formatted = []
            for v in all_voices:
                # language 필드 처리 (배열일 수 있음)
                lang = v.get("language", ["en"])
                if isinstance(lang, list):
                    lang = lang[0] if lang else "en"
                lang_display = {"ko": "Korean", "en": "English", "ja": "Japanese"}.get(lang, lang)

                # use_cases를 genres로 사용
                genres = v.get("use_cases", v.get("genres", v.get("tags", [])))

                formatted.append({
                    "voice_id": v.get("voice_id", v.get("id", "")),
                    "name": v.get("name", "Unknown"),
                    "language": lang_display,
                    "gender": v.get("gender", "Unknown").capitalize(),
                    "age_group": v.get("age", v.get("age_group", "Unknown")).replace("-", " ").title().replace(" ", "-"),
                    "genres": [g.capitalize() for g in genres] if genres else [],
                    "styles": v.get("styles", ["neutral"]),
                    "description": v.get("description", ""),
                    "is_new": v.get("is_new", False),
                    # thumbnail_image_url 필드 사용
                    "image_url": v.get("thumbnail_image_url", v.get("thumbnail", v.get("image_url", ""))),
                })
            return formatted

        except Exception as e:
            st.error(f"API 연결 오류: {str(e)}")
            return []

    async def generate_tts_async(
        self,
        session: aiohttp.ClientSession,
        text: str,
        settings: TTSSettings,
        semaphore: asyncio.Semaphore
    ) -> Tuple[Optional[bytes], str]:
        async with semaphore:
            try:
                # 모델별 지원 설정에 따라 voice_settings 구성
                supported_settings = MODEL_VOICE_SETTINGS.get(settings.model, ["speed"])
                voice_settings = {}

                if "pitch_shift" in supported_settings:
                    voice_settings["pitch_shift"] = settings.pitch_shift
                if "pitch_variance" in supported_settings:
                    voice_settings["pitch_variance"] = settings.pitch_variance
                if "speed" in supported_settings:
                    voice_settings["speed"] = settings.speed
                if "duration" in supported_settings and settings.duration > 0:
                    voice_settings["duration"] = settings.duration
                if "similarity" in supported_settings:
                    voice_settings["similarity"] = settings.similarity
                if "text_guidance" in supported_settings:
                    voice_settings["text_guidance"] = settings.text_guidance
                if "subharmonic_amplitude_control" in supported_settings:
                    voice_settings["subharmonic_amplitude_control"] = settings.subharmonic_amplitude_control

                payload = {
                    "text": text,
                    "language": settings.language,
                    "style": settings.style,
                    "model": settings.model,
                    "output_format": settings.output_format,
                    "voice_settings": voice_settings
                }

                url = f"{SUPERTONE_API_BASE}/text-to-speech/{settings.voice_id}/stream"

                async with session.post(
                    url,
                    headers=self.get_headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    if response.status == 200:
                        return await response.read(), ""
                    else:
                        error_text = await response.text()
                        return None, f"[{response.status}] {error_text[:200]}"

            except asyncio.TimeoutError:
                return None, "요청 시간 초과 (120초)"
            except Exception as e:
                return None, f"요청 실패: {str(e)}"


# ==================== Text Processing ====================

def split_text_into_blocks(text: str, max_chars: int = 300) -> List[str]:
    if not text.strip():
        return []

    sentence_pattern = r'(?<=[.!?。！？])\s*'
    sentences = re.split(sentence_pattern, text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    blocks = []
    current_block = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            if current_block:
                blocks.append(current_block.strip())
                current_block = ""
            words = sentence.split()
            temp_block = ""
            for word in words:
                if len(temp_block) + len(word) + 1 <= max_chars:
                    temp_block += " " + word if temp_block else word
                else:
                    if temp_block:
                        blocks.append(temp_block.strip())
                    temp_block = word
            if temp_block:
                blocks.append(temp_block.strip())
        else:
            if len(current_block) + len(sentence) + 1 <= max_chars:
                current_block += " " + sentence if current_block else sentence
            else:
                if current_block:
                    blocks.append(current_block.strip())
                current_block = sentence

    if current_block:
        blocks.append(current_block.strip())

    return blocks


# ==================== Audio Processing ====================

def read_wav_data(wav_bytes: bytes) -> Tuple[Optional[bytes], int, int, int]:
    try:
        with io.BytesIO(wav_bytes) as wav_buffer:
            with wave.open(wav_buffer, 'rb') as wav_file:
                params = wav_file.getparams()
                frames = wav_file.readframes(params.nframes)
                return frames, params.framerate, params.nchannels, params.sampwidth
    except Exception:
        return None, 0, 0, 0


def create_silence_bytes(duration_seconds: float, sample_rate: int, channels: int, sample_width: int) -> bytes:
    num_frames = int(duration_seconds * sample_rate)
    return b'\x00' * (num_frames * channels * sample_width)


def merge_audio_blocks(
    audio_blocks: List[bytes],
    sentence_gap_seconds: float = 0.5,
    word_gap_seconds: float = 0.0
) -> bytes:
    if not audio_blocks:
        return b""

    valid_blocks = [b for b in audio_blocks if b is not None]
    if not valid_blocks:
        return b""

    first_frames, sample_rate, channels, sample_width = read_wav_data(valid_blocks[0])
    if first_frames is None:
        return b""

    all_frames = []

    for i, audio_data in enumerate(valid_blocks):
        frames, sr, ch, sw = read_wav_data(audio_data)
        if frames is None:
            continue
        all_frames.append(frames)
        if i < len(valid_blocks) - 1 and sentence_gap_seconds > 0:
            silence = create_silence_bytes(sentence_gap_seconds, sample_rate, channels, sample_width)
            all_frames.append(silence)

    if not all_frames:
        return b""

    combined_frames = b''.join(all_frames)

    output_buffer = io.BytesIO()
    with wave.open(output_buffer, 'wb') as wav_out:
        wav_out.setnchannels(channels)
        wav_out.setsampwidth(sample_width)
        wav_out.setframerate(sample_rate)
        wav_out.writeframes(combined_frames)

    return output_buffer.getvalue()


# ==================== Parallel Processing ====================

async def process_blocks_parallel(
    client: SupertoneClient,
    blocks: List[TTSBlock],
    settings: TTSSettings,
    progress_callback=None
) -> List[TTSBlock]:
    max_concurrent = min(len(blocks), max(1, client.rpm // 10))
    semaphore = asyncio.Semaphore(max_concurrent)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for block in blocks:
            task = process_single_block(client, session, block, settings, semaphore, progress_callback)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                blocks[i].status = "error"
                blocks[i].error_message = str(result)

    return blocks


async def process_single_block(
    client: SupertoneClient,
    session: aiohttp.ClientSession,
    block: TTSBlock,
    settings: TTSSettings,
    semaphore: asyncio.Semaphore,
    progress_callback=None
) -> TTSBlock:
    block.status = "processing"

    audio_data, error = await client.generate_tts_async(session, block.text, settings, semaphore)

    if audio_data:
        block.audio_data = audio_data
        block.status = "completed"
    else:
        block.status = "error"
        block.error_message = error

    if progress_callback:
        progress_callback(block)

    return block


# ==================== Voice Library UI ====================

def get_voices_list(include_custom: bool = True) -> List[Dict]:
    """보이스 목록 가져오기 (기본 + 커스텀)"""
    voices = []

    # API 보이스
    if "api_voices" in st.session_state and st.session_state.api_voices:
        voices.extend(st.session_state.api_voices)
    else:
        voices.extend(SAMPLE_VOICES)

    # 커스텀 보이스 추가
    if include_custom and "custom_voices" in st.session_state and st.session_state.custom_voices:
        voices.extend(st.session_state.custom_voices)

    return voices


def filter_voices(
    voices: List[Dict],
    category: str = "All",
    language: str = "All",
    gender: str = "All",
    age_group: str = "All",
    search_query: str = ""
) -> List[Dict]:
    filtered = voices

    if category != "All":
        filtered = [v for v in filtered if category in v.get("genres", [])]
    if language != "All":
        filtered = [v for v in filtered if v.get("language") == language]
    if gender != "All":
        filtered = [v for v in filtered if v.get("gender") == gender]
    if age_group != "All":
        filtered = [v for v in filtered if v.get("age_group") == age_group]
    if search_query:
        query = search_query.lower()
        filtered = [v for v in filtered if
                   query in v.get("name", "").lower() or
                   query in v.get("description", "").lower()]

    return filtered


def render_voice_row_dialog(voice: Dict, index, show_fav_btn: bool = True) -> Tuple[bool, bool]:
    """다이얼로그용 보이스 행 렌더링. Returns (selected, fav_toggled)"""
    voice_id = voice.get("voice_id", "")
    is_fav = is_favorite_voice(voice_id)

    cols = st.columns([0.5, 0.6, 2, 0.8, 0.8, 1.2, 0.8])

    fav_toggled = False
    with cols[0]:
        fav_icon = "⭐" if is_fav else "☆"
        if show_fav_btn and st.button(fav_icon, key=f"fav_{index}_{voice_id}", help="즐겨찾기"):
            toggle_favorite_voice(voice_id)
            fav_toggled = True

    with cols[1]:
        img_url = voice.get("image_url") or get_avatar_url(voice.get("name", "User"), voice.get("gender", "Male"))
        st.image(img_url, width=40)

    with cols[2]:
        name = voice.get("name", "Unknown")
        if voice.get("is_new"):
            st.markdown(f"**{name}** <span style='background:#00D26A;color:#000;padding:1px 4px;border-radius:3px;font-size:10px;'>NEW</span>", unsafe_allow_html=True)
        elif voice.get("is_custom"):
            st.markdown(f"**{name}** <span style='background:#FF6B6B;color:#fff;padding:1px 4px;border-radius:3px;font-size:10px;'>CUSTOM</span>", unsafe_allow_html=True)
        else:
            st.markdown(f"**{name}**")

    with cols[3]:
        st.caption(voice.get("language", "-"))

    with cols[4]:
        st.caption(voice.get("gender", "-"))

    with cols[5]:
        genres = voice.get("genres", [])
        st.caption(", ".join(genres[:2]) + ("..." if len(genres) > 2 else ""))

    with cols[6]:
        if st.button("선택", key=f"dlg_sel_{index}_{voice_id}", type="primary"):
            return True, fav_toggled

    return False, fav_toggled


@st.dialog("📥 다운로드 설정", width="small")
def download_settings_dialog():
    """병합 오디오 다운로드 설정 팝업"""

    st.markdown("#### 오디오 간격 설정")

    # 기본값 불러오기
    default_word_gap = st.session_state.get("default_word_gap", 0.0)
    default_sentence_gap = st.session_state.get("default_sentence_gap", 0.5)

    word_gap = st.slider(
        "📝 단어 사이 간격 (초)",
        0.0, 2.0, default_word_gap, 0.05,
        key="dlg_word_gap",
        help="각 단어 사이에 추가되는 무음 구간"
    )

    sentence_gap = st.slider(
        "📄 문장 사이 간격 (초)",
        0.0, 3.0, default_sentence_gap, 0.1,
        key="dlg_sentence_gap",
        help="각 블록(문장) 사이에 추가되는 무음 구간"
    )

    st.divider()

    # 선택된 완료 블록 가져오기
    selected_completed = [b for b in st.session_state.blocks
                         if b.index in st.session_state.selected_blocks and b.status == "completed"]

    if not selected_completed:
        st.warning("선택된 오디오가 없습니다.")
        return

    st.caption(f"📊 {len(selected_completed)}개 블록 병합")

    # 기본값 저장 버튼
    col_save, col_merge = st.columns(2)

    with col_save:
        if st.button("💾 기본값으로 저장", use_container_width=True):
            st.session_state.default_word_gap = word_gap
            st.session_state.default_sentence_gap = sentence_gap
            save_settings_to_file()
            st.success("✅ 기본값 저장됨!")

    with col_merge:
        if st.button("🔄 병합하기", use_container_width=True, type="primary"):
            try:
                with st.spinner(f"🔄 {len(selected_completed)}개 블록 병합 중..."):
                    audio_list = [b.audio_data for b in selected_completed if b.audio_data]
                    merged = merge_audio_blocks(audio_list, sentence_gap, word_gap)

                    if merged:
                        # 대용량 파일은 디스크에 저장
                        ensure_data_dir()
                        output_filename = f"tts_merged_{int(time.time())}.wav"
                        output_path = DATA_DIR / output_filename

                        with open(output_path, "wb") as f:
                            f.write(merged)

                        st.session_state.merged_audio_path = str(output_path)
                        st.session_state.merged_audio_filename = output_filename
                        st.session_state.merged_audio_size = len(merged)

                        # 메모리에서 제거 (대용량 파일)
                        if len(merged) > 50 * 1024 * 1024:  # 50MB 이상
                            st.session_state.merged_audio = None
                        else:
                            st.session_state.merged_audio = merged

                        st.success(f"✅ 병합 완료! ({len(merged) / 1024 / 1024:.2f} MB)")
                        st.rerun()
                    else:
                        st.error("❌ 병합 실패: 오디오 데이터가 없습니다.")
            except MemoryError:
                st.error("❌ 메모리 부족! 블록 수를 줄여주세요.")
            except Exception as e:
                st.error(f"❌ 병합 오류: {str(e)}")

    # 병합된 오디오가 있으면 다운로드 버튼 표시
    merged_path = st.session_state.get("merged_audio_path")
    merged_size = st.session_state.get("merged_audio_size", 0)

    if merged_path and Path(merged_path).exists():
        st.divider()
        st.success(f"📁 병합된 파일: {merged_size / 1024 / 1024:.2f} MB")

        filename = st.session_state.get("merged_audio_filename", "merged.wav")
        port = st.session_state.get("file_server_port", DOWNLOAD_SERVER_PORT)

        # 30MB 이하는 Streamlit 다운로드 버튼
        if merged_size <= 30 * 1024 * 1024:
            if st.session_state.get("merged_audio"):
                st.download_button(
                    "🎵 다운로드",
                    st.session_state.merged_audio,
                    filename,
                    "audio/wav",
                    use_container_width=True,
                    type="primary"
                )
            else:
                try:
                    with open(merged_path, "rb") as f:
                        st.download_button(
                            "🎵 다운로드",
                            f.read(),
                            filename,
                            "audio/wav",
                            use_container_width=True,
                            type="primary"
                        )
                except Exception as e:
                    st.error(f"파일 읽기 오류: {e}")
        else:
            # 30MB 초과 - 파일 서버 다운로드 링크
            download_url = get_download_url(filename, port)
            st.markdown(f"""
            ### 🎵 대용량 파일 다운로드
            아래 링크를 클릭하면 다운로드가 시작됩니다:
            """)
            st.markdown(f'<a href="{download_url}" download="{filename}" target="_blank" style="display: inline-block; padding: 0.5rem 1rem; background-color: #FF4B4B; color: white; text-decoration: none; border-radius: 0.5rem; font-weight: bold;">📥 다운로드 ({merged_size / 1024 / 1024:.1f} MB)</a>', unsafe_allow_html=True)
            st.caption(f"💡 다운로드 URL: `{download_url}`")

        # 미리듣기 (10MB 미만만)
        if merged_size < 10 * 1024 * 1024 and st.session_state.get("merged_audio"):
            st.audio(st.session_state.merged_audio, format="audio/wav")


@st.dialog("🎙️ 보이스 라이브러리", width="large")
def voice_library_dialog():
    """보이스 라이브러리 팝업 다이얼로그"""

    voices = get_voices_list()

    # 즐겨찾기 보이스
    favorite_ids = st.session_state.get("favorite_voice_ids", [])
    favorite_voices = [v for v in voices if v.get("voice_id") in favorite_ids]
    if favorite_voices:
        st.markdown("##### ⭐ 즐겨찾기 보이스")
        for i, voice in enumerate(favorite_voices[:5]):
            selected, fav_toggled = render_voice_row_dialog(voice, f"fav_{i}")
            if fav_toggled:
                st.rerun()
            if selected:
                st.session_state.selected_voice = voice
                st.session_state.selected_voice_id = voice.get("voice_id")
                add_to_recent_voices(voice)
                st.rerun()
        st.divider()

    # 최근 사용한 보이스
    recent = st.session_state.get("recent_voices", [])
    if recent:
        st.markdown("##### ⏱️ 최근 사용한 보이스")
        for i, voice in enumerate(recent[:3]):
            selected, fav_toggled = render_voice_row_dialog(voice, f"recent_{i}")
            if fav_toggled:
                st.rerun()
            if selected:
                st.session_state.selected_voice = voice
                st.session_state.selected_voice_id = voice.get("voice_id")
                add_to_recent_voices(voice)
                st.rerun()
        st.divider()

    # API 키 없을 때만 샘플 데이터 경고
    if not st.session_state.get("api_key"):
        st.warning("⚠️ **샘플 데이터** - 사이드바에서 API 키를 입력하세요")

    # 검색 및 필터 (모두 selectbox/input 사용 - rerun 없이 자동 반영)
    col1, col2 = st.columns([3, 2])
    with col1:
        search_query = st.text_input("🔍 검색", placeholder="이름으로 검색...", key="dlg_search", label_visibility="collapsed")
    with col2:
        selected_category = st.selectbox("카테고리", CATEGORIES, key="dlg_cat", label_visibility="collapsed")

    col3, col4, col5 = st.columns(3)
    with col3:
        selected_language = st.selectbox("🌐 언어", LANGUAGES_FILTER, key="dlg_lang", label_visibility="collapsed")
    with col4:
        selected_gender = st.selectbox("👤 성별", GENDERS, key="dlg_gender", label_visibility="collapsed")
    with col5:
        selected_age = st.selectbox("📅 연령대", AGE_GROUPS, key="dlg_age", label_visibility="collapsed")

    # 필터링
    filtered_voices = filter_voices(
        voices,
        category=selected_category,
        language=selected_language,
        gender=selected_gender,
        age_group=selected_age,
        search_query=search_query
    )

    # 페이지네이션 설정
    items_per_page = 25
    total_pages = max(1, (len(filtered_voices) + items_per_page - 1) // items_per_page)

    # 페이지 선택 (slider 사용 - rerun 없이 자동 반영)
    col_info, col_page = st.columns([1, 2])
    with col_info:
        st.caption(f"**{len(filtered_voices)}개 보이스**")
    with col_page:
        if total_pages > 1:
            current_page = st.slider(
                "페이지",
                1, total_pages, 1,
                key="dlg_page_slider",
                label_visibility="collapsed"
            )
        else:
            current_page = 1

    # 보이스 목록
    if not filtered_voices:
        st.info("조건에 맞는 보이스가 없습니다.")
    else:
        start_idx = (current_page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        page_voices = filtered_voices[start_idx:end_idx]

        st.caption(f"페이지 {current_page}/{total_pages} ({start_idx+1}-{min(end_idx, len(filtered_voices))})")

        for i, voice in enumerate(page_voices):
            global_idx = start_idx + i
            selected, fav_toggled = render_voice_row_dialog(voice, global_idx)
            if fav_toggled:
                st.rerun()
            if selected:
                st.session_state.selected_voice = voice
                st.session_state.selected_voice_id = voice.get("voice_id")
                add_to_recent_voices(voice)
                st.rerun()  # 선택 시에만 rerun (팝업 닫기)

            if i < len(page_voices) - 1:
                st.markdown("<hr style='margin: 3px 0; border: none; border-top: 1px solid #eee;'>", unsafe_allow_html=True)


# ==================== Streamlit UI ====================

def init_session_state():
    # 파일 서버 시작 (한 번만)
    if "file_server_port" not in st.session_state:
        ensure_data_dir()
        port = start_file_server(DATA_DIR, DOWNLOAD_SERVER_PORT)
        st.session_state.file_server_port = port

    # 파일에서 전역 설정 로드
    if "settings_loaded" not in st.session_state:
        saved_settings = load_global_settings()
        st.session_state.voice_favorites = saved_settings.get("voice_favorites", {})
        st.session_state.favorite_voice_ids = saved_settings.get("favorite_voice_ids", [])
        st.session_state.default_word_gap = saved_settings.get("default_word_gap", 0.0)
        st.session_state.default_sentence_gap = saved_settings.get("default_sentence_gap", 0.5)
        st.session_state.recent_voices = saved_settings.get("recent_voices", [])
        st.session_state.settings_loaded = True

    defaults = {
        "blocks": [],
        "processing": False,
        "api_voices": [],
        "custom_voices": [],  # 커스텀(클론) 보이스
        "selected_blocks": set(),
        "selected_voice": None,
        "selected_voice_id": None,
        "api_key": "",
        "recent_voices": [],  # 최근 사용한 보이스 목록 (최대 10개)
        "credits": None,  # 크레딧 잔액
        # 즐겨찾기 설정 (보이스별)
        "voice_favorites": {},  # {voice_id: {1: {pitch, variance, speed}, 2: {...}, 3: {...}}}
        # 기본 간격 설정
        "default_word_gap": 0.0,  # 단어 사이 간격 (초)
        "default_sentence_gap": 0.5,  # 문장 사이 간격 (초)
        # 즐겨찾기 보이스 목록
        "favorite_voice_ids": [],  # [voice_id, ...]
        # 프로젝트 설정
        "project_name": "새 프로젝트",
        "current_project_id": None,
        # 보이스 변경 추적
        "last_selected_voice_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def save_settings_to_file():
    """현재 설정을 파일에 저장"""
    settings = {
        "voice_favorites": st.session_state.get("voice_favorites", {}),
        "favorite_voice_ids": st.session_state.get("favorite_voice_ids", []),
        "default_word_gap": st.session_state.get("default_word_gap", 0.0),
        "default_sentence_gap": st.session_state.get("default_sentence_gap", 0.5),
        "recent_voices": st.session_state.get("recent_voices", []),
    }
    save_global_settings(settings)


def add_to_recent_voices(voice: Dict):
    """보이스를 최근 사용 목록에 추가"""
    if not voice:
        return

    voice_id = voice.get("voice_id")
    if not voice_id:
        return

    # 기존 목록에서 같은 보이스 제거
    st.session_state.recent_voices = [
        v for v in st.session_state.recent_voices
        if v.get("voice_id") != voice_id
    ]

    # 맨 앞에 추가
    st.session_state.recent_voices.insert(0, voice)

    # 최대 10개 유지
    st.session_state.recent_voices = st.session_state.recent_voices[:10]


def toggle_favorite_voice(voice_id: str):
    """보이스 즐겨찾기 토글"""
    if voice_id in st.session_state.favorite_voice_ids:
        st.session_state.favorite_voice_ids.remove(voice_id)
    else:
        st.session_state.favorite_voice_ids.append(voice_id)
    save_settings_to_file()  # 파일에 저장


def is_favorite_voice(voice_id: str) -> bool:
    """보이스가 즐겨찾기인지 확인"""
    return voice_id in st.session_state.favorite_voice_ids


# ==================== Project Save/Load ====================

def save_project_to_json() -> str:
    """현재 프로젝트를 JSON으로 저장"""
    project_data = {
        "version": "1.0",
        "project_name": st.session_state.get("project_name", "새 프로젝트"),
        "selected_voice": st.session_state.get("selected_voice"),
        "selected_voice_id": st.session_state.get("selected_voice_id"),
        "script_input": st.session_state.get("script_input", ""),
        "max_chars": st.session_state.get("max_chars", 300),
        "voice_style": st.session_state.get("voice_style", "neutral"),
        "sona_model": st.session_state.get("sona_model", "Sona 1"),
        "tts_language": st.session_state.get("tts_language", "Korean"),
        "rpm_setting": st.session_state.get("rpm_setting", 60),
        "pitch_shift": st.session_state.get("pitch_shift", 0.0),
        "pitch_variance": st.session_state.get("pitch_variance", 1.0),
        "speed": st.session_state.get("speed", 1.0),
        "duration": st.session_state.get("duration", 0.0),
        "similarity": st.session_state.get("similarity", 3.0),
        "text_guidance": st.session_state.get("text_guidance", 1.0),
        "subharmonic": st.session_state.get("subharmonic", 1.0),
        "voice_favorites": st.session_state.get("voice_favorites", {}),
        "favorite_voice_ids": st.session_state.get("favorite_voice_ids", []),
        "default_word_gap": st.session_state.get("default_word_gap", 0.0),
        "default_sentence_gap": st.session_state.get("default_sentence_gap", 0.5),
        "recent_voices": st.session_state.get("recent_voices", []),
    }
    return json.dumps(project_data, ensure_ascii=False, indent=2)


def load_project_from_json(json_str: str) -> bool:
    """JSON에서 프로젝트 로드"""
    try:
        project_data = json.loads(json_str)

        # 기본 설정 로드
        if "project_name" in project_data:
            st.session_state.project_name = project_data["project_name"]
        if "selected_voice" in project_data:
            st.session_state.selected_voice = project_data["selected_voice"]
        if "selected_voice_id" in project_data:
            st.session_state.selected_voice_id = project_data["selected_voice_id"]
        if "script_input" in project_data:
            st.session_state.script_input = project_data["script_input"]
        if "max_chars" in project_data:
            st.session_state.max_chars = project_data["max_chars"]
        if "voice_favorites" in project_data:
            st.session_state.voice_favorites = project_data["voice_favorites"]
        if "favorite_voice_ids" in project_data:
            st.session_state.favorite_voice_ids = project_data["favorite_voice_ids"]
        if "default_word_gap" in project_data:
            st.session_state.default_word_gap = project_data["default_word_gap"]
        if "default_sentence_gap" in project_data:
            st.session_state.default_sentence_gap = project_data["default_sentence_gap"]
        if "recent_voices" in project_data:
            st.session_state.recent_voices = project_data["recent_voices"]

        return True
    except Exception as e:
        st.error(f"프로젝트 로드 실패: {str(e)}")
        return False


@st.dialog("💾 프로젝트 저장/불러오기", width="small")
def project_dialog():
    """프로젝트 저장/불러오기 다이얼로그"""

    tab1, tab2 = st.tabs(["💾 저장", "📂 불러오기"])

    with tab1:
        project_name = st.text_input("프로젝트 이름", value=st.session_state.get("project_name", "새 프로젝트"))
        st.session_state.project_name = project_name

        if st.button("📥 프로젝트 파일 다운로드", use_container_width=True, type="primary"):
            json_data = save_project_to_json()
            st.download_button(
                "💾 다운로드",
                json_data,
                f"{project_name}_{int(time.time())}.stproj",
                "application/json",
                use_container_width=True
            )

    with tab2:
        uploaded_file = st.file_uploader("프로젝트 파일 선택", type=["stproj", "json"])
        if uploaded_file:
            if st.button("📂 프로젝트 불러오기", use_container_width=True, type="primary"):
                json_str = uploaded_file.read().decode("utf-8")
                if load_project_from_json(json_str):
                    st.success("✅ 프로젝트를 불러왔습니다!")
                    st.rerun()


# ==================== Voice Cloning ====================

@st.dialog("🎤 보이스 클로닝", width="small")
def voice_cloning_dialog():
    """보이스 클로닝 다이얼로그"""

    st.markdown("#### 커스텀 보이스 생성")
    st.info("음성 파일을 업로드하여 나만의 보이스를 만드세요!")

    voice_name = st.text_input("보이스 이름", placeholder="예: 나의 목소리")
    description = st.text_area("설명 (선택)", placeholder="이 보이스에 대한 설명...")

    uploaded_files = st.file_uploader(
        "음성 파일 업로드",
        type=["wav", "mp3", "m4a", "ogg"],
        accept_multiple_files=True,
        help="고품질 음성 파일을 업로드하세요 (최소 30초 권장)"
    )

    if uploaded_files:
        st.caption(f"📁 {len(uploaded_files)}개 파일 선택됨")
        total_size = sum(f.size for f in uploaded_files)
        st.caption(f"📊 총 크기: {total_size / 1024 / 1024:.2f} MB")

    st.divider()

    if st.button("🚀 보이스 생성", use_container_width=True, type="primary",
                disabled=not voice_name or not uploaded_files):
        if not st.session_state.get("api_key"):
            st.error("API 키를 먼저 입력해주세요!")
            return

        with st.spinner("보이스 생성 중... (몇 분 소요될 수 있습니다)"):
            try:
                # 파일을 multipart/form-data로 전송
                files = []
                for f in uploaded_files:
                    files.append(("files", (f.name, f.read(), f.type or "audio/wav")))

                data = {
                    "name": voice_name,
                }
                if description:
                    data["description"] = description

                response = requests.post(
                    f"{SUPERTONE_API_BASE}/custom-voices",
                    headers={"x-sup-api-key": st.session_state.api_key},
                    data=data,
                    files=files,
                    timeout=300
                )

                if response.status_code in [200, 201]:
                    result = response.json()
                    st.success(f"✅ 보이스 '{voice_name}' 생성 완료!")
                    st.json(result)
                    # 커스텀 보이스 목록 새로고침
                    st.session_state.custom_voices = []
                    st.rerun()
                else:
                    st.error(f"❌ 생성 실패: {response.status_code}")
                    st.text(response.text[:500])

            except Exception as e:
                st.error(f"❌ 오류: {str(e)}")

    st.divider()
    st.caption("💡 팁: 깨끗한 환경에서 녹음된 음성이 좋은 결과를 만듭니다.")


def render_voice_selector():
    """보이스 선택 UI"""
    st.markdown("##### 🎤 보이스 선택")

    col1, col2 = st.columns([3, 1])

    with col1:
        if st.session_state.selected_voice:
            voice = st.session_state.selected_voice
            # 샘플 데이터 경고
            if voice.get("voice_id", "").startswith("preset_"):
                st.error("⚠️ 샘플 보이스입니다. 팝업에서 'API에서 보이스 불러오기' 버튼을 눌러 실제 보이스를 선택해주세요.")
            else:
                # 이미지와 정보 함께 표시
                img_col, info_col = st.columns([0.15, 0.85])
                with img_col:
                    img_url = voice.get("image_url") or get_avatar_url(voice.get("name", ""), voice.get("gender", "Male"))
                    st.image(img_url, width=60)
                with info_col:
                    st.success(f"**{voice.get('name')}** ({voice.get('language')}, {voice.get('gender')})")
                    st.caption(voice.get("description", ""))
        else:
            st.warning("보이스를 선택해주세요")

    with col2:
        if st.button("🎙️ 보이스 선택", use_container_width=True, type="primary"):
            voice_library_dialog()


def render_settings_panel() -> Optional[TTSSettings]:
    """설정 패널"""

    render_voice_selector()

    if not st.session_state.selected_voice:
        return None

    voice = st.session_state.selected_voice
    voice_id = voice.get("voice_id", "")

    # 샘플 보이스인 경우 TTS 생성 불가
    if voice_id.startswith("preset_"):
        return None

    st.divider()

    # 모델 및 스타일 선택
    col1, col2, col3 = st.columns(3)

    with col1:
        styles = voice.get("styles", ["neutral"])
        style = st.selectbox("🎭 말투 (스타일)", styles, key="voice_style")

    with col2:
        model_name = st.selectbox("🤖 모델", list(SONA_MODELS.keys()), index=0, key="sona_model")
        model = SONA_MODELS[model_name]

    with col3:
        rpm = st.number_input("⚡ RPM", min_value=1, max_value=1000, value=DEFAULT_RPM, key="rpm_setting")

    # 언어 선택 (모델별 지원 언어) - 기본값 Korean
    supported_langs = MODEL_LANGUAGES.get(model, ["en", "ko", "ja"])
    lang_display_map = {
        "en": "English", "ko": "Korean", "ja": "Japanese",
        "es": "Spanish", "pt": "Portuguese", "de": "German",
        "fr": "French", "it": "Italian", "ru": "Russian",
        "bg": "Bulgarian", "cs": "Czech", "da": "Danish",
        "el": "Greek", "et": "Estonian", "fi": "Finnish",
        "hu": "Hungarian", "nl": "Dutch", "pl": "Polish",
        "ro": "Romanian", "ar": "Arabic", "hi": "Hindi",
        "id": "Indonesian", "vi": "Vietnamese"
    }
    lang_options = [lang_display_map.get(l, l) for l in supported_langs]
    lang_code_reverse = {v: k for k, v in lang_display_map.items()}

    # 기본 언어를 Korean으로 설정 (지원되는 경우)
    default_lang_idx = 0
    if "Korean" in lang_options:
        default_lang_idx = lang_options.index("Korean")

    col_lang, col_rpm2 = st.columns([2, 1])
    with col_lang:
        selected_lang_display = st.selectbox("🌐 언어", lang_options, index=default_lang_idx, key="tts_language")
        language_code = lang_code_reverse.get(selected_lang_display, "ko")

    # 모델별 지원 설정 가져오기
    supported_settings = MODEL_VOICE_SETTINGS.get(model, ["speed"])

    st.divider()
    st.markdown("##### 🎛️ 음성 조절")

    # 즐겨찾기 불러오기/저장
    favorites = st.session_state.voice_favorites.get(voice_id, {})

    # 보이스가 변경되면 자동으로 ⭐1 로드
    voice_changed = st.session_state.get("last_selected_voice_id") != voice_id
    if voice_changed:
        st.session_state.last_selected_voice_id = voice_id
        if 1 in favorites:
            st.session_state.fav_load = favorites[1]
            st.info(f"⭐1 기본 설정 자동 로드됨")

    fav_col1, fav_col2, fav_col3, fav_col4 = st.columns([1, 1, 1, 2])
    with fav_col1:
        fav1_label = "⭐1 (기본)" if 1 in favorites else "⭐1"
        if st.button(fav1_label, use_container_width=True, disabled=1 not in favorites):
            if 1 in favorites:
                st.session_state.fav_load = favorites[1]
                st.rerun()
    with fav_col2:
        if st.button("⭐2", use_container_width=True, disabled=2 not in favorites):
            if 2 in favorites:
                st.session_state.fav_load = favorites[2]
                st.rerun()
    with fav_col3:
        if st.button("⭐3", use_container_width=True, disabled=3 not in favorites):
            if 3 in favorites:
                st.session_state.fav_load = favorites[3]
                st.rerun()
    with fav_col4:
        # 저장된 즐겨찾기 표시
        saved_favs = [str(k) for k in sorted(favorites.keys())]
        if saved_favs:
            st.caption(f"저장됨: ⭐{', ⭐'.join(saved_favs)}")

    # 즐겨찾기에서 로드된 값 사용
    fav_loaded = st.session_state.pop("fav_load", None)
    default_pitch = fav_loaded["pitch_shift"] if fav_loaded else 0.0
    default_variance = fav_loaded["pitch_variance"] if fav_loaded else 1.0
    default_speed = fav_loaded["speed"] if fav_loaded else 1.0

    # 기본 설정 (항상 표시)
    col1, col2, col3 = st.columns(3)

    with col1:
        if "pitch_shift" in supported_settings:
            pitch_shift = st.slider("음높이", -24.0, 24.0, default_pitch, 0.5, key="pitch_shift")
        else:
            pitch_shift = 0.0
            st.caption("음높이: 미지원")

    with col2:
        if "pitch_variance" in supported_settings:
            pitch_variance = st.slider("음높이 변화", 0.0, 2.0, default_variance, 0.1, key="pitch_variance")
        else:
            pitch_variance = 1.0
            st.caption("음높이 변화: 미지원")

    with col3:
        speed = st.slider("속도", 0.5, 2.0, default_speed, 0.1, key="speed")

    # 즐겨찾기 저장 버튼
    save_col1, save_col2, save_col3 = st.columns(3)
    with save_col1:
        if st.button("⭐1 저장 (기본)", use_container_width=True, type="primary"):
            if voice_id not in st.session_state.voice_favorites:
                st.session_state.voice_favorites[voice_id] = {}
            st.session_state.voice_favorites[voice_id][1] = {
                "pitch_shift": pitch_shift, "pitch_variance": pitch_variance, "speed": speed
            }
            save_settings_to_file()  # 파일에 저장
            st.success("⭐1 저장됨! (기본값)")
            st.rerun()
    with save_col2:
        if st.button("⭐2 저장", use_container_width=True):
            if voice_id not in st.session_state.voice_favorites:
                st.session_state.voice_favorites[voice_id] = {}
            st.session_state.voice_favorites[voice_id][2] = {
                "pitch_shift": pitch_shift, "pitch_variance": pitch_variance, "speed": speed
            }
            save_settings_to_file()  # 파일에 저장
            st.success("⭐2 저장됨!")
            st.rerun()
    with save_col3:
        if st.button("⭐3 저장", use_container_width=True):
            if voice_id not in st.session_state.voice_favorites:
                st.session_state.voice_favorites[voice_id] = {}
            st.session_state.voice_favorites[voice_id][3] = {
                "pitch_shift": pitch_shift, "pitch_variance": pitch_variance, "speed": speed
            }
            save_settings_to_file()  # 파일에 저장
            st.success("⭐3 저장됨!")
            st.rerun()

    # 고급 설정 (Sona 1 전용)
    duration = 0.0
    similarity = 3.0
    text_guidance = 1.0
    subharmonic = 1.0

    if model == "sona_speech_1":
        with st.expander("🔧 고급 설정 (Sona 1 전용)", expanded=False):
            adv_col1, adv_col2 = st.columns(2)
            with adv_col1:
                duration = st.slider("목표 길이 (초)", 0.0, 60.0, 0.0, 0.5, key="duration",
                                    help="0이면 자동, 설정 시 해당 길이에 맞춤")
                similarity = st.slider("유사도", 1.0, 5.0, 3.0, 0.5, key="similarity",
                                      help="원본 캐릭터 목소리와의 유사도")
            with adv_col2:
                text_guidance = st.slider("텍스트 반응도", 0.0, 4.0, 1.0, 0.5, key="text_guidance",
                                         help="텍스트 내용에 대한 음성 반응 정도")
                subharmonic = st.slider("하모닉 진폭", 0.0, 2.0, 1.0, 0.1, key="subharmonic",
                                       help="생성된 음성의 하모닉 진폭 조절")

    return TTSSettings(
        voice_id=voice.get("voice_id"),
        voice_name=voice.get("name"),
        language=language_code,
        style=style,
        model=model,
        pitch_shift=pitch_shift,
        pitch_variance=pitch_variance,
        speed=speed,
        duration=duration,
        similarity=similarity,
        text_guidance=text_guidance,
        subharmonic_amplitude_control=subharmonic,
        output_format="wav"
    )


def render_script_input():
    """스크립트 입력"""
    st.markdown("##### 📝 스크립트 입력")

    col1, col2 = st.columns([4, 1])
    with col1:
        script = st.text_area(
            "스크립트",
            height=150,
            placeholder="여기에 텍스트를 입력하세요. 지정된 글자 수 단위로 자동 분리됩니다.",
            key="script_input",
            label_visibility="collapsed"
        )
    with col2:
        max_chars = st.number_input("블록 크기", 50, 1000, 300, 50, key="max_chars")
        if script:
            st.metric("글자 수", len(script))

    if script:
        blocks = split_text_into_blocks(script, max_chars)

        with st.expander(f"📦 분리된 블록 미리보기 ({len(blocks)}개)", expanded=False):
            for i, block_text in enumerate(blocks):
                st.text(f"[{i+1}] ({len(block_text)}자) {block_text[:80]}...")

        return blocks

    return []


def render_process_section(client: SupertoneClient, settings: TTSSettings, blocks: List[str]):
    """TTS 생성 섹션"""

    st.divider()

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown(f"**선택된 보이스:** {settings.voice_name} | **블록 수:** {len(blocks)}")

        if st.button("🚀 TTS 생성 시작", type="primary", use_container_width=True,
                    disabled=st.session_state.processing):

            st.session_state.processing = True
            st.session_state.blocks = [TTSBlock(index=i, text=text) for i, text in enumerate(blocks)]

            progress_bar = st.progress(0)
            status_text = st.empty()
            error_container = st.empty()

            completed = [0]
            errors = []
            total = len(blocks)

            def update_progress(block: TTSBlock):
                completed[0] += 1
                progress_bar.progress(completed[0] / total)
                status_text.text(f"처리 중: {completed[0]}/{total}")
                if block.status == "error":
                    errors.append(f"블록 {block.index + 1}: {block.error_message}")

            with st.spinner(f"🔄 {len(blocks)}개 블록 병렬 처리 중..."):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    st.session_state.blocks = loop.run_until_complete(
                        process_blocks_parallel(client, st.session_state.blocks, settings, update_progress)
                    )
                finally:
                    loop.close()

            st.session_state.processing = False

            completed_count = sum(1 for b in st.session_state.blocks if b.status == "completed")
            error_count = sum(1 for b in st.session_state.blocks if b.status == "error")

            if error_count == 0:
                st.success(f"✅ 모든 블록 처리 완료! ({completed_count}개)")
            else:
                st.error(f"⚠️ 성공: {completed_count}개, 실패: {error_count}개")
                # 에러 상세 표시
                with st.expander("❌ 에러 상세 보기"):
                    for b in st.session_state.blocks:
                        if b.status == "error":
                            st.error(f"**블록 {b.index + 1}**: {b.error_message}")

            st.rerun()


def render_results():
    """결과 및 다운로드"""

    if not st.session_state.blocks:
        return

    completed_blocks = [b for b in st.session_state.blocks if b.status == "completed"]
    error_blocks = [b for b in st.session_state.blocks if b.status == "error"]

    if not completed_blocks and not error_blocks:
        return

    st.divider()
    st.markdown("##### 📥 결과 및 다운로드")

    # 에러가 있으면 표시
    if error_blocks:
        with st.expander(f"❌ 실패한 블록 ({len(error_blocks)}개)", expanded=False):
            for b in error_blocks:
                st.error(f"블록 {b.index + 1}: {b.error_message}")

    if not completed_blocks:
        st.warning("성공적으로 생성된 오디오가 없습니다.")
        return

    # 선택 컨트롤
    select_all = st.checkbox("모두 선택", value=True, key="select_all")

    if select_all:
        st.session_state.selected_blocks = set(range(len(st.session_state.blocks)))

    # 블록 목록
    for block in st.session_state.blocks:
        col1, col2, col3 = st.columns([0.3, 3, 1])

        with col1:
            is_selected = st.checkbox("", value=block.index in st.session_state.selected_blocks,
                                     key=f"sel_{block.index}", disabled=select_all)
            if not select_all:
                if is_selected:
                    st.session_state.selected_blocks.add(block.index)
                else:
                    st.session_state.selected_blocks.discard(block.index)

        with col2:
            status_emoji = {"completed": "✅", "error": "❌", "processing": "🔄", "pending": "⏳"}
            st.text(f"{status_emoji.get(block.status, '❓')} [{block.index+1}] {block.text[:60]}...")

        with col3:
            if block.status == "completed" and block.audio_data:
                st.audio(block.audio_data, format="audio/wav")

    # 다운로드 옵션
    st.divider()

    selected_completed = [b for b in st.session_state.blocks
                         if b.index in st.session_state.selected_blocks and b.status == "completed"]

    st.markdown("**📥 다운로드**")
    st.info(f"선택: {len(st.session_state.selected_blocks)}개 | 완료: {len(selected_completed)}개")

    col1, col2, col3 = st.columns(3)

    with col1:
        if selected_completed:
            if st.button("🎵 병합 오디오 다운로드", use_container_width=True, type="primary"):
                download_settings_dialog()
        else:
            st.button("🎵 병합 오디오 다운로드", use_container_width=True, disabled=True)

    with col2:
        if selected_completed:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for block in selected_completed:
                    if block.audio_data:
                        zf.writestr(f"block_{block.index+1:03d}.wav", block.audio_data)

            st.download_button("📦 개별 파일 (ZIP)", zip_buffer.getvalue(),
                              f"tts_blocks_{int(time.time())}.zip", "application/zip",
                              use_container_width=True)

    with col3:
        if st.session_state.selected_blocks:
            texts = [st.session_state.blocks[i].text for i in sorted(st.session_state.selected_blocks)
                    if i < len(st.session_state.blocks)]
            text_content = "\n\n---\n\n".join([f"[{i+1}]\n{t}" for i, t in enumerate(texts)])

            st.download_button("📄 텍스트 다운로드", text_content,
                              f"script_{int(time.time())}.txt", "text/plain",
                              use_container_width=True)


def main():
    st.set_page_config(
        page_title="Supertone TTS Processor",
        page_icon="🎙️",
        layout="wide"
    )

    init_session_state()

    # 사이드바 - API 키
    with st.sidebar:
        st.header("⚙️ 설정")
        api_key = st.text_input("Supertone API Key", type="password", key="api_key_input")

        if api_key:
            st.session_state.api_key = api_key
            client = SupertoneClient(api_key)

            # 크레딧 잔액 표시
            if st.session_state.get("credits") is None:
                credits = client.get_credits()
                if credits is not None:
                    st.session_state.credits = credits

            if st.session_state.get("credits") is not None:
                st.metric("💰 크레딧", f"{st.session_state.credits:,.0f}")

            # API 키가 있고 보이스가 없으면 자동 로드
            if not st.session_state.get("api_voices"):
                with st.spinner("보이스 로딩 중..."):
                    api_voices = client.get_voices()
                    custom_voices = client.get_custom_voices()
                    if api_voices:
                        st.session_state.api_voices = api_voices
                    if custom_voices:
                        st.session_state.custom_voices = custom_voices
                    st.rerun()

            # 로드된 보이스 수 표시
            voice_count = len(st.session_state.get("api_voices", []))
            custom_count = len(st.session_state.get("custom_voices", []))

            col_v1, col_v2 = st.columns(2)
            with col_v1:
                st.info(f"🎙️ {voice_count}개")
            with col_v2:
                if custom_count > 0:
                    st.success(f"🎤 커스텀 {custom_count}개")

            # 새로고침 버튼
            if st.button("🔄 새로고침", use_container_width=True):
                with st.spinner("새로고침 중..."):
                    api_voices = client.get_voices()
                    custom_voices = client.get_custom_voices()
                    credits = client.get_credits()
                    if api_voices:
                        st.session_state.api_voices = api_voices
                    if custom_voices:
                        st.session_state.custom_voices = custom_voices
                    if credits is not None:
                        st.session_state.credits = credits
                    st.success(f"✅ {len(api_voices)}개 + 커스텀 {len(custom_voices)}개")
                    st.rerun()

            st.divider()

            # 보이스 클로닝
            if st.button("🎤 보이스 클로닝", use_container_width=True):
                voice_cloning_dialog()

        else:
            st.warning("API 키를 입력해주세요")

        st.divider()

        # 프로젝트 관리
        st.markdown("##### 📁 프로젝트")
        projects = load_projects()

        # 현재 프로젝트 표시
        current_id = st.session_state.get("current_project_id")
        current_name = st.session_state.get("project_name", "새 프로젝트")

        if current_id:
            st.success(f"📂 {current_name}")
            if st.button("💾 저장", use_container_width=True):
                save_current_project()
                st.success("저장됨!")
                st.rerun()
        else:
            st.info("프로젝트를 선택하세요")

        # 프로젝트 목록
        if projects:
            project_names = ["(새 프로젝트)"] + [p.get("name", "이름없음") for p in projects]
            project_ids = [None] + [p.get("id") for p in projects]

            selected_idx = st.selectbox(
                "프로젝트 선택",
                range(len(project_names)),
                format_func=lambda i: project_names[i],
                key="project_selector",
                label_visibility="collapsed"
            )

            if selected_idx > 0:
                selected_project = projects[selected_idx - 1]
                if st.session_state.get("current_project_id") != selected_project.get("id"):
                    if st.button("📂 불러오기", use_container_width=True, type="primary"):
                        load_project_to_session(selected_project)
                        st.success(f"'{selected_project.get('name')}' 로드됨!")
                        st.rerun()

        # 새 프로젝트 생성
        with st.expander("➕ 새 프로젝트 생성"):
            new_name = st.text_input("프로젝트 이름", placeholder="새 프로젝트", key="new_project_name")
            if st.button("생성", use_container_width=True, disabled=not new_name):
                new_id = create_new_project(new_name)
                st.session_state.current_project_id = new_id
                st.session_state.project_name = new_name
                st.session_state.selected_voice = None
                st.session_state.selected_voice_id = None
                st.session_state.script_input = ""
                st.success(f"'{new_name}' 생성됨!")
                st.rerun()

        st.divider()
        st.caption("Made with Streamlit")
        st.caption("Supertone API 활용")

    # 메인 컨텐츠
    st.title("🎙️ Supertone TTS 병렬 처리기")

    if not st.session_state.api_key:
        st.info("👈 사이드바에서 API 키를 입력해주세요")

        st.divider()
        st.markdown("### 🎙️ 보이스 미리보기")
        st.caption("API 키 없이도 보이스 목록을 확인할 수 있습니다 (샘플 데이터)")

        if st.button("🎙️ 보이스 라이브러리 열기", type="primary"):
            voice_library_dialog()
        return

    client = SupertoneClient(st.session_state.api_key, st.session_state.get("rpm_setting", DEFAULT_RPM))

    settings = render_settings_panel()
    blocks = render_script_input()

    if settings and blocks:
        render_process_section(client, settings, blocks)

    render_results()


if __name__ == "__main__":
    main()
