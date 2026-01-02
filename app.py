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
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import wave
import zipfile

# ==================== Configuration ====================

SUPERTONE_API_BASE = "https://supertoneapi.com/v1"
DEFAULT_RPM = 60

# Sona 모델 버전
SONA_MODELS = {
    "Sona 1": "sona_speech_1",
    "Sona 2": "sona_speech_2",
}

# 카테고리 목록
CATEGORIES = [
    "All", "Meme", "Conversational", "Business", "Narration",
    "Announcement", "Education", "Game", "Storytelling", "Acting",
    "News", "Entertainment", "Humor"
]

# 언어 목록
LANGUAGES_FILTER = ["All", "Korean", "English", "Japanese"]
LANGUAGE_MAP = {"Korean": "ko", "English": "en", "Japanese": "ja", "All": "all"}

# 성별 목록
GENDERS = ["All", "Male", "Female"]

# 연령대 목록
AGE_GROUPS = ["All", "Child", "Young-Adult", "Middle-Aged", "Senior"]

# ==================== Sample Voice Data ====================

SAMPLE_VOICES = [
    {"voice_id": "anderson", "name": "Anderson", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Narration", "Storytelling"], "styles": ["neutral", "calm", "serious"], "description": "따뜻하고 신뢰감 있는 내레이션 음성", "is_new": True},
    {"voice_id": "barbara", "name": "Barbara", "language": "English", "gender": "Female", "age_group": "Middle-Aged", "genres": ["News", "Announcement"], "styles": ["neutral", "professional"], "description": "전문적이고 명확한 아나운서 음성", "is_new": True},
    {"voice_id": "daniel", "name": "Daniel", "language": "English", "gender": "Male", "age_group": "Middle-Aged", "genres": ["News", "Announcement"], "styles": ["neutral", "authoritative"], "description": "권위있고 신뢰감 있는 뉴스 음성", "is_new": True},
    {"voice_id": "flop", "name": "Flop", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Game", "Entertainment"], "styles": ["neutral", "energetic", "playful"], "description": "활기차고 재미있는 게임 캐릭터 음성", "is_new": True},
    {"voice_id": "hyunsook", "name": "Hyunsook", "language": "English", "gender": "Female", "age_group": "Young-Adult", "genres": ["Entertainment", "Conversational"], "styles": ["neutral", "friendly", "cheerful"], "description": "밝고 친근한 엔터테인먼트 음성", "is_new": True},
    {"voice_id": "juho", "name": "Juho", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Conversational", "Education"], "styles": ["neutral", "warm", "gentle"], "description": "따뜻하고 편안한 대화형 음성", "is_new": True},
    {"voice_id": "kan", "name": "Kan", "language": "English", "gender": "Male", "age_group": "Middle-Aged", "genres": ["Game", "Acting"], "styles": ["neutral", "dramatic", "intense"], "description": "드라마틱하고 강렬한 연기 음성", "is_new": True},
    {"voice_id": "mansu", "name": "Mansu", "language": "English", "gender": "Male", "age_group": "Young-Adult", "genres": ["Humor", "Entertainment"], "styles": ["neutral", "funny", "sarcastic"], "description": "유머러스하고 재치있는 음성", "is_new": True},
    {"voice_id": "oksoon", "name": "Oksoon", "language": "English", "gender": "Female", "age_group": "Young-Adult", "genres": ["Entertainment", "Conversational"], "styles": ["neutral", "cute", "bright"], "description": "귀엽고 밝은 여성 음성", "is_new": True},
    {"voice_id": "garret", "name": "Garret", "language": "Korean", "gender": "Male", "age_group": "Young-Adult", "genres": ["Narration", "Business"], "styles": ["neutral", "professional", "calm"], "description": "차분하고 전문적인 한국어 남성 음성", "is_new": False},
    {"voice_id": "minjae", "name": "민재", "language": "Korean", "gender": "Male", "age_group": "Young-Adult", "genres": ["Conversational", "Education"], "styles": ["neutral", "friendly"], "description": "친근하고 자연스러운 한국어 남성 음성", "is_new": False},
    {"voice_id": "sooyoung", "name": "수영", "language": "Korean", "gender": "Female", "age_group": "Young-Adult", "genres": ["Narration", "Entertainment"], "styles": ["neutral", "warm", "elegant"], "description": "우아하고 따뜻한 한국어 여성 음성", "is_new": False},
    {"voice_id": "jiwon", "name": "지원", "language": "Korean", "gender": "Female", "age_group": "Young-Adult", "genres": ["News", "Business"], "styles": ["neutral", "professional", "clear"], "description": "명확하고 전문적인 한국어 여성 음성", "is_new": False},
    {"voice_id": "yuki", "name": "Yuki", "language": "Japanese", "gender": "Female", "age_group": "Young-Adult", "genres": ["Entertainment", "Game"], "styles": ["neutral", "cute", "energetic"], "description": "귀엽고 활기찬 일본어 여성 음성", "is_new": False},
    {"voice_id": "takeshi", "name": "Takeshi", "language": "Japanese", "gender": "Male", "age_group": "Middle-Aged", "genres": ["Narration", "Business"], "styles": ["neutral", "serious", "professional"], "description": "진지하고 전문적인 일본어 남성 음성", "is_new": False},
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
    output_format: str = "wav"


@dataclass
class Voice:
    voice_id: str
    name: str
    language: str
    gender: str
    age_group: str
    genres: List[str]
    styles: List[str]
    description: str = ""
    is_new: bool = False


# ==================== Supertone API Client ====================

class SupertoneClient:
    def __init__(self, api_key: str, rpm: int = DEFAULT_RPM):
        self.api_key = api_key
        self.rpm = rpm
        self.request_interval = 60.0 / rpm
        self.last_request_time = 0

    def get_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-sup-api-key": self.api_key,
        }

    def get_voices(self) -> List[dict]:
        try:
            response = requests.get(
                f"{SUPERTONE_API_BASE}/voices",
                headers=self.get_headers(),
                timeout=30
            )
            if response.status_code == 200:
                return response.json().get("voices", [])
            return []
        except Exception:
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
                payload = {
                    "text": text,
                    "language": settings.language,
                    "style": settings.style,
                    "model": settings.model,
                    "output_format": settings.output_format,
                    "voice_settings": {
                        "pitch_shift": settings.pitch_shift,
                        "pitch_variance": settings.pitch_variance,
                        "speed": settings.speed,
                    }
                }

                url = f"{SUPERTONE_API_BASE}/text-to-speech/{settings.voice_id}/stream"

                async with session.post(
                    url,
                    headers=self.get_headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status == 200:
                        return await response.read(), ""
                    else:
                        error_text = await response.text()
                        return None, f"API 오류: {response.status} - {error_text}"

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
    word_gap_percent: float = 100.0,
    sentence_gap_seconds: float = 0.5,
    output_format: str = "wav"
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

def get_voices_list() -> List[Dict]:
    """보이스 목록 반환 (API 또는 샘플 데이터)"""
    if "api_voices" in st.session_state and st.session_state.api_voices:
        return st.session_state.api_voices
    return SAMPLE_VOICES


def filter_voices(
    voices: List[Dict],
    category: str = "All",
    language: str = "All",
    gender: str = "All",
    age_group: str = "All",
    search_query: str = ""
) -> List[Dict]:
    """보이스 필터링"""
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


def render_voice_card(voice: Dict, col) -> bool:
    """보이스 카드 렌더링 - 선택되면 True 반환"""
    with col:
        # 카드 스타일 컨테이너
        is_selected = st.session_state.get("selected_voice_id") == voice.get("voice_id")

        card_style = "border: 2px solid #00D26A;" if is_selected else "border: 1px solid #444;"

        with st.container():
            # 이름과 NEW 뱃지
            name_col, badge_col = st.columns([3, 1])
            with name_col:
                st.markdown(f"**{voice.get('name', 'Unknown')}**")
            with badge_col:
                if voice.get("is_new"):
                    st.markdown('<span style="background-color: #00D26A; color: black; padding: 2px 6px; border-radius: 4px; font-size: 10px;">NEW</span>', unsafe_allow_html=True)

            # 정보
            st.caption(f"🌐 {voice.get('language', '-')} | 👤 {voice.get('gender', '-')} | 📅 {voice.get('age_group', '-')}")

            # 장르
            genres = voice.get("genres", [])
            if genres:
                st.caption(f"🎭 {', '.join(genres[:2])}{'...' if len(genres) > 2 else ''}")

            # 설명
            desc = voice.get("description", "")
            if desc:
                st.caption(f"_{desc[:40]}{'...' if len(desc) > 40 else ''}_")

            # 선택 버튼
            if st.button("선택", key=f"select_{voice.get('voice_id')}", use_container_width=True):
                return True

    return False


def render_voice_library():
    """보이스 라이브러리 팝업 렌더링"""

    st.subheader("🎙️ 보이스 라이브러리")

    voices = get_voices_list()

    # 검색 및 필터
    search_col, refresh_col = st.columns([4, 1])
    with search_col:
        search_query = st.text_input("🔍 보이스 검색", placeholder="이름 또는 설명으로 검색...", key="voice_search")
    with refresh_col:
        if st.button("🔄", help="API에서 보이스 목록 새로고침"):
            if "api_key" in st.session_state and st.session_state.api_key:
                client = SupertoneClient(st.session_state.api_key)
                api_voices = client.get_voices()
                if api_voices:
                    st.session_state.api_voices = api_voices
                    st.success(f"✅ {len(api_voices)}개 보이스 로드됨")
                    st.rerun()

    # 카테고리 필터 (탭 스타일)
    st.markdown("##### 카테고리")
    category_cols = st.columns(7)
    selected_category = st.session_state.get("filter_category", "All")

    for i, cat in enumerate(CATEGORIES[:7]):
        with category_cols[i]:
            if st.button(cat, key=f"cat_{cat}",
                        type="primary" if selected_category == cat else "secondary",
                        use_container_width=True):
                st.session_state.filter_category = cat
                st.rerun()

    # 더 많은 카테고리
    if len(CATEGORIES) > 7:
        category_cols2 = st.columns(7)
        for i, cat in enumerate(CATEGORIES[7:]):
            with category_cols2[i]:
                if st.button(cat, key=f"cat_{cat}",
                            type="primary" if selected_category == cat else "secondary",
                            use_container_width=True):
                    st.session_state.filter_category = cat
                    st.rerun()

    # 필터 드롭다운
    filter_cols = st.columns(4)
    with filter_cols[0]:
        selected_language = st.selectbox("🌐 언어", LANGUAGES_FILTER, key="filter_language")
    with filter_cols[1]:
        selected_gender = st.selectbox("👤 성별", GENDERS, key="filter_gender")
    with filter_cols[2]:
        selected_age = st.selectbox("📅 연령대", AGE_GROUPS, key="filter_age")
    with filter_cols[3]:
        if st.button("필터 초기화", use_container_width=True):
            st.session_state.filter_category = "All"
            st.session_state.filter_language = "All"
            st.session_state.filter_gender = "All"
            st.session_state.filter_age = "All"
            st.rerun()

    st.divider()

    # 필터링된 보이스 목록
    filtered_voices = filter_voices(
        voices,
        category=st.session_state.get("filter_category", "All"),
        language=selected_language,
        gender=selected_gender,
        age_group=selected_age,
        search_query=search_query
    )

    st.markdown(f"**전체 보이스 ({len(filtered_voices)})**")

    # 보이스 카드 그리드
    if not filtered_voices:
        st.info("조건에 맞는 보이스가 없습니다.")
    else:
        # 3열 그리드
        for i in range(0, len(filtered_voices), 3):
            cols = st.columns(3)
            for j, col in enumerate(cols):
                if i + j < len(filtered_voices):
                    voice = filtered_voices[i + j]
                    if render_voice_card(voice, col):
                        st.session_state.selected_voice = voice
                        st.session_state.selected_voice_id = voice.get("voice_id")
                        st.session_state.show_voice_library = False
                        st.rerun()


# ==================== Streamlit UI ====================

def init_session_state():
    defaults = {
        "blocks": [],
        "processing": False,
        "api_voices": [],
        "selected_blocks": set(),
        "selected_voice": None,
        "selected_voice_id": None,
        "show_voice_library": False,
        "filter_category": "All",
        "api_key": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_voice_selector():
    """보이스 선택 UI"""
    st.markdown("##### 🎤 보이스 선택")

    col1, col2 = st.columns([3, 1])

    with col1:
        if st.session_state.selected_voice:
            voice = st.session_state.selected_voice
            st.success(f"✅ **{voice.get('name')}** ({voice.get('language')}, {voice.get('gender')})")
            st.caption(voice.get("description", ""))
        else:
            st.warning("보이스를 선택해주세요")

    with col2:
        if st.button("🎙️ 보이스 선택", use_container_width=True, type="primary"):
            st.session_state.show_voice_library = True
            st.rerun()


def render_settings_panel() -> Optional[TTSSettings]:
    """설정 패널 렌더링"""

    # 보이스 선택 버튼
    render_voice_selector()

    if not st.session_state.selected_voice:
        return None

    voice = st.session_state.selected_voice

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        # 스타일 선택
        styles = voice.get("styles", ["neutral"])
        style = st.selectbox("🎭 말투 (스타일)", styles, key="voice_style")

        # 모델 선택
        model_name = st.selectbox("🤖 Sona 모델", list(SONA_MODELS.keys()), index=1, key="sona_model")
        model = SONA_MODELS[model_name]

    with col2:
        # RPM 설정
        rpm = st.number_input("⚡ RPM", min_value=1, max_value=1000, value=DEFAULT_RPM, key="rpm_setting")

    st.divider()
    st.markdown("##### 🎛️ 음성 조절")

    col1, col2, col3 = st.columns(3)

    with col1:
        pitch_shift = st.slider("음높이", -24.0, 24.0, 0.0, 0.5, key="pitch_shift", help="Pitch Shift")

    with col2:
        pitch_variance = st.slider("음높이 변화", 0.0, 2.0, 1.0, 0.1, key="pitch_variance", help="Pitch Variance")

    with col3:
        speed = st.slider("속도", 0.5, 2.0, 1.0, 0.1, key="speed", help="Speed")

    # 언어 코드 매핑
    lang_code_map = {"Korean": "ko", "English": "en", "Japanese": "ja"}
    language_code = lang_code_map.get(voice.get("language", "English"), "en")

    return TTSSettings(
        voice_id=voice.get("voice_id"),
        voice_name=voice.get("name"),
        language=language_code,
        style=style,
        model=model,
        pitch_shift=pitch_shift,
        pitch_variance=pitch_variance,
        speed=speed,
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

            completed = [0]
            total = len(blocks)

            def update_progress(block: TTSBlock):
                completed[0] += 1
                progress_bar.progress(completed[0] / total)
                status_text.text(f"처리 중: {completed[0]}/{total}")

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
                st.warning(f"⚠️ 성공: {completed_count}개, 실패: {error_count}개")

            st.rerun()


def render_results():
    """결과 및 다운로드"""

    if not st.session_state.blocks:
        return

    completed_blocks = [b for b in st.session_state.blocks if b.status == "completed"]

    if not completed_blocks:
        return

    st.divider()
    st.markdown("##### 📥 결과 및 다운로드")

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

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**병합 설정**")
        word_gap = st.slider("단어 간격 (%)", 50, 200, 100, 10, key="word_gap")
        sentence_gap = st.slider("문장 간격 (초)", 0.0, 3.0, 0.5, 0.1, key="sentence_gap")

    with col2:
        st.markdown("**다운로드**")

        selected_completed = [b for b in st.session_state.blocks
                             if b.index in st.session_state.selected_blocks and b.status == "completed"]

        st.info(f"선택: {len(st.session_state.selected_blocks)}개 | 완료: {len(selected_completed)}개")

        if selected_completed:
            audio_list = [b.audio_data for b in selected_completed if b.audio_data]

            # 병합 오디오 다운로드
            merged = merge_audio_blocks(audio_list, word_gap, sentence_gap)
            if merged:
                st.download_button("🎵 병합 오디오 다운로드", merged,
                                  f"tts_merged_{int(time.time())}.wav", "audio/wav",
                                  use_container_width=True)

            # ZIP 다운로드
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for block in selected_completed:
                    if block.audio_data:
                        zf.writestr(f"block_{block.index+1:03d}.wav", block.audio_data)

            st.download_button("📦 개별 파일 (ZIP)", zip_buffer.getvalue(),
                              f"tts_blocks_{int(time.time())}.zip", "application/zip",
                              use_container_width=True)

        # 텍스트 다운로드
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
            st.success("✅ API 키 설정됨")
        else:
            st.warning("API 키를 입력해주세요")

        st.divider()
        st.caption("Made with Streamlit")
        st.caption("Supertone API 활용")

    # 메인 컨텐츠
    st.title("🎙️ Supertone TTS 병렬 처리기")

    # 보이스 라이브러리 표시
    if st.session_state.show_voice_library:
        render_voice_library()

        if st.button("← 돌아가기", use_container_width=False):
            st.session_state.show_voice_library = False
            st.rerun()
    else:
        # 일반 UI
        if not st.session_state.api_key:
            st.info("👈 사이드바에서 API 키를 입력해주세요")

            # 샘플 데이터로 보이스 라이브러리 미리보기 가능
            st.divider()
            st.markdown("### 🎙️ 보이스 라이브러리 미리보기")
            st.caption("API 키 없이도 보이스 목록을 확인할 수 있습니다 (샘플 데이터)")

            if st.button("보이스 라이브러리 열기", type="primary"):
                st.session_state.show_voice_library = True
                st.rerun()
            return

        client = SupertoneClient(st.session_state.api_key, st.session_state.get("rpm_setting", DEFAULT_RPM))

        # 설정 패널
        settings = render_settings_panel()

        # 스크립트 입력
        blocks = render_script_input()

        # TTS 생성
        if settings and blocks:
            render_process_section(client, settings, blocks)

        # 결과
        render_results()


if __name__ == "__main__":
    main()
