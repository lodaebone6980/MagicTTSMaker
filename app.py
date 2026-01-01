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
from dataclasses import dataclass
from typing import List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import wave
import struct
from pydub import AudioSegment

# ==================== Configuration ====================

# 슈퍼톤 API 설정
SUPERTONE_API_BASE = "https://supertoneapi.com/v1"
DEFAULT_RPM = 60  # 분당 요청 제한 (기본값)

# 지원 언어
LANGUAGES = {
    "한국어": "ko",
    "English": "en",
    "日本語": "ja",
}

# Sona 모델 버전
SONA_MODELS = {
    "Sona 1": "sona_speech_1",
    "Sona 2": "sona_speech_2",
}

# 출력 포맷
OUTPUT_FORMATS = {
    "WAV": "wav",
    "MP3": "mp3",
}

# ==================== Data Classes ====================

@dataclass
class TTSBlock:
    """TTS 생성을 위한 텍스트 블록"""
    index: int
    text: str
    audio_data: Optional[bytes] = None
    status: str = "pending"  # pending, processing, completed, error
    error_message: str = ""


@dataclass
class TTSSettings:
    """TTS 설정"""
    voice_id: str
    language: str
    style: str
    model: str
    pitch_shift: float
    pitch_variance: float
    speed: float
    output_format: str


# ==================== Supertone API Client ====================

class SupertoneClient:
    """슈퍼톤 API 클라이언트"""

    def __init__(self, api_key: str, rpm: int = DEFAULT_RPM):
        self.api_key = api_key
        self.rpm = rpm
        self.request_interval = 60.0 / rpm  # 요청 간격 (초)
        self.last_request_time = 0

    def get_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-sup-api-key": self.api_key,
        }

    def get_voices(self) -> List[dict]:
        """음성 목록 가져오기"""
        try:
            response = requests.get(
                f"{SUPERTONE_API_BASE}/voices",
                headers=self.get_headers(),
                timeout=30
            )
            if response.status_code == 200:
                return response.json().get("voices", [])
            else:
                st.error(f"음성 목록 조회 실패: {response.status_code}")
                return []
        except Exception as e:
            st.error(f"API 오류: {str(e)}")
            return []

    def generate_tts(self, text: str, settings: TTSSettings) -> Tuple[Optional[bytes], str]:
        """TTS 생성 (동기)"""
        try:
            # Rate limiting
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.request_interval:
                time.sleep(self.request_interval - time_since_last)

            self.last_request_time = time.time()

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

            response = requests.post(
                f"{SUPERTONE_API_BASE}/text-to-speech/{settings.voice_id}/stream",
                headers=self.get_headers(),
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                return response.content, ""
            else:
                return None, f"API 오류: {response.status_code} - {response.text}"

        except Exception as e:
            return None, f"요청 실패: {str(e)}"

    async def generate_tts_async(
        self,
        session: aiohttp.ClientSession,
        text: str,
        settings: TTSSettings,
        semaphore: asyncio.Semaphore
    ) -> Tuple[Optional[bytes], str]:
        """TTS 생성 (비동기)"""
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
                        text = await response.text()
                        return None, f"API 오류: {response.status} - {text}"

            except Exception as e:
                return None, f"요청 실패: {str(e)}"


# ==================== Text Processing ====================

def split_text_into_blocks(text: str, max_chars: int = 300) -> List[str]:
    """
    텍스트를 최대 글자 수에 맞게 블록으로 분리
    문장 단위로 끊어서 자연스럽게 분리
    """
    if not text.strip():
        return []

    # 문장 분리 패턴 (한국어/영어/일본어 지원)
    sentence_pattern = r'(?<=[.!?。！？])\s*'
    sentences = re.split(sentence_pattern, text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    blocks = []
    current_block = ""

    for sentence in sentences:
        # 문장 자체가 max_chars보다 길면 단어 단위로 분리
        if len(sentence) > max_chars:
            if current_block:
                blocks.append(current_block.strip())
                current_block = ""

            # 단어 단위로 분리
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
            # 현재 블록에 문장 추가 가능한지 확인
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

def create_silence(duration_ms: int, sample_rate: int = 44100) -> AudioSegment:
    """무음 오디오 생성"""
    return AudioSegment.silent(duration=duration_ms, frame_rate=sample_rate)


def merge_audio_blocks(
    audio_blocks: List[bytes],
    word_gap_percent: float = 100.0,
    sentence_gap_seconds: float = 0.5,
    output_format: str = "wav"
) -> bytes:
    """
    오디오 블록들을 하나로 병합

    Args:
        audio_blocks: 오디오 바이트 리스트
        word_gap_percent: 단어 사이 간격 (100% = 원본 유지)
        sentence_gap_seconds: 문장 사이 간격 (초)
        output_format: 출력 포맷 (wav/mp3)
    """
    if not audio_blocks:
        return b""

    combined = None
    sentence_gap_ms = int(sentence_gap_seconds * 1000)

    for i, audio_data in enumerate(audio_blocks):
        if audio_data is None:
            continue

        try:
            # 오디오 데이터를 AudioSegment로 변환
            audio = AudioSegment.from_file(io.BytesIO(audio_data), format=output_format)

            # 단어 간격 조정 (속도 조절로 구현)
            if word_gap_percent != 100.0:
                speed_factor = 100.0 / word_gap_percent
                audio = audio._spawn(
                    audio.raw_data,
                    overrides={"frame_rate": int(audio.frame_rate * speed_factor)}
                ).set_frame_rate(audio.frame_rate)

            if combined is None:
                combined = audio
            else:
                # 문장 사이 간격 추가
                silence = create_silence(sentence_gap_ms, audio.frame_rate)
                combined = combined + silence + audio

        except Exception as e:
            st.warning(f"블록 {i+1} 오디오 처리 실패: {str(e)}")
            continue

    if combined is None:
        return b""

    # 출력 포맷으로 변환
    output_buffer = io.BytesIO()
    combined.export(output_buffer, format=output_format)
    return output_buffer.getvalue()


# ==================== Parallel Processing ====================

async def process_blocks_parallel(
    client: SupertoneClient,
    blocks: List[TTSBlock],
    settings: TTSSettings,
    progress_callback=None
) -> List[TTSBlock]:
    """블록들을 병렬로 처리"""

    # RPM에 맞춰 동시 요청 수 제한
    max_concurrent = min(len(blocks), client.rpm // 10)  # RPM의 1/10로 동시 요청
    max_concurrent = max(1, max_concurrent)

    semaphore = asyncio.Semaphore(max_concurrent)

    async with aiohttp.ClientSession() as session:
        tasks = []

        for block in blocks:
            task = process_single_block(
                client, session, block, settings, semaphore, progress_callback
            )
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
    """단일 블록 처리"""
    block.status = "processing"

    audio_data, error = await client.generate_tts_async(
        session, block.text, settings, semaphore
    )

    if audio_data:
        block.audio_data = audio_data
        block.status = "completed"
    else:
        block.status = "error"
        block.error_message = error

    if progress_callback:
        progress_callback(block)

    return block


# ==================== Streamlit UI ====================

def init_session_state():
    """세션 상태 초기화"""
    if "blocks" not in st.session_state:
        st.session_state.blocks = []
    if "processing" not in st.session_state:
        st.session_state.processing = False
    if "voices" not in st.session_state:
        st.session_state.voices = []
    if "selected_blocks" not in st.session_state:
        st.session_state.selected_blocks = set()


def render_sidebar() -> Tuple[Optional[SupertoneClient], Optional[TTSSettings]]:
    """사이드바 렌더링 - TTS 설정"""

    st.sidebar.header("🎙️ TTS 설정")

    # API 키 입력
    api_key = st.sidebar.text_input(
        "Supertone API Key",
        type="password",
        help="슈퍼톤 API 키를 입력하세요"
    )

    if not api_key:
        st.sidebar.warning("API 키를 입력해주세요")
        return None, None

    # RPM 설정
    rpm = st.sidebar.number_input(
        "RPM (분당 요청 수)",
        min_value=1,
        max_value=1000,
        value=DEFAULT_RPM,
        help="API Rate Limit에 맞춰 설정하세요"
    )

    client = SupertoneClient(api_key, rpm)

    # 음성 목록 로드
    if st.sidebar.button("🔄 음성 목록 새로고침"):
        with st.spinner("음성 목록 로딩 중..."):
            st.session_state.voices = client.get_voices()

    st.sidebar.divider()

    # 음성 선택 (직접 입력 또는 선택)
    voice_input_method = st.sidebar.radio(
        "음성 선택 방식",
        ["직접 입력", "목록에서 선택"],
        horizontal=True
    )

    voice_id = ""
    style = "neutral"

    if voice_input_method == "직접 입력":
        voice_id = st.sidebar.text_input(
            "Voice ID",
            value="",
            help="슈퍼톤 Voice ID를 입력하세요"
        )
        style = st.sidebar.text_input(
            "스타일",
            value="neutral",
            help="예: neutral, happy, sad, angry"
        )
    else:
        if st.session_state.voices:
            voice_names = [v.get("name", v.get("voice_id", "Unknown")) for v in st.session_state.voices]
            selected_idx = st.sidebar.selectbox(
                "음성 선택",
                range(len(voice_names)),
                format_func=lambda x: voice_names[x]
            )
            if selected_idx is not None:
                selected_voice = st.session_state.voices[selected_idx]
                voice_id = selected_voice.get("voice_id", "")

                # 스타일 선택
                styles = selected_voice.get("styles", ["neutral"])
                style = st.sidebar.selectbox("스타일", styles)
        else:
            st.sidebar.info("음성 목록을 로드해주세요")
            voice_id = st.sidebar.text_input("Voice ID", value="")
            style = st.sidebar.text_input("스타일", value="neutral")

    st.sidebar.divider()

    # 언어 선택
    language_name = st.sidebar.selectbox(
        "언어",
        list(LANGUAGES.keys()),
        index=0
    )
    language = LANGUAGES[language_name]

    # Sona 모델 선택
    model_name = st.sidebar.selectbox(
        "Sona 모델",
        list(SONA_MODELS.keys()),
        index=1,  # 기본값: Sona 2
        help="Sona 2가 더 자연스러운 음성을 생성합니다"
    )
    model = SONA_MODELS[model_name]

    st.sidebar.divider()
    st.sidebar.subheader("🎛️ 음성 조절")

    # 음높이
    pitch_shift = st.sidebar.slider(
        "음높이 (Pitch)",
        min_value=-24.0,
        max_value=24.0,
        value=0.0,
        step=0.5,
        help="음높이 조절 (-24 ~ +24)"
    )

    # 음높이 변화
    pitch_variance = st.sidebar.slider(
        "음높이 변화 (Pitch Variance)",
        min_value=0.0,
        max_value=2.0,
        value=1.0,
        step=0.1,
        help="음높이 변화량 (0 ~ 2)"
    )

    # 속도
    speed = st.sidebar.slider(
        "속도 (Speed)",
        min_value=0.5,
        max_value=2.0,
        value=1.0,
        step=0.1,
        help="말하기 속도 (0.5x ~ 2.0x)"
    )

    st.sidebar.divider()

    # 출력 포맷
    format_name = st.sidebar.selectbox(
        "출력 포맷",
        list(OUTPUT_FORMATS.keys()),
        index=0
    )
    output_format = OUTPUT_FORMATS[format_name]

    if not voice_id:
        return client, None

    settings = TTSSettings(
        voice_id=voice_id,
        language=language,
        style=style,
        model=model,
        pitch_shift=pitch_shift,
        pitch_variance=pitch_variance,
        speed=speed,
        output_format=output_format
    )

    return client, settings


def render_script_input():
    """스크립트 입력 영역 렌더링"""

    st.header("📝 스크립트 입력")

    # 최대 글자 수 설정
    col1, col2 = st.columns([3, 1])
    with col1:
        script = st.text_area(
            "스크립트를 입력하세요",
            height=200,
            placeholder="여기에 텍스트를 입력하세요. 300자 단위로 자동 분리됩니다.",
            key="script_input"
        )
    with col2:
        max_chars = st.number_input(
            "블록 최대 글자 수",
            min_value=50,
            max_value=1000,
            value=300,
            step=50
        )

        if script:
            st.metric("총 글자 수", len(script))

    # 블록 분리 미리보기
    if script:
        blocks = split_text_into_blocks(script, max_chars)

        st.subheader(f"📦 분리된 블록 ({len(blocks)}개)")

        for i, block_text in enumerate(blocks):
            with st.expander(f"블록 {i+1} ({len(block_text)}자)", expanded=False):
                st.text(block_text)

        return blocks

    return []


def render_process_button(client: SupertoneClient, settings: TTSSettings, blocks: List[str]):
    """처리 버튼 및 진행 상태"""

    st.divider()

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        if st.button(
            "🚀 TTS 생성 시작",
            type="primary",
            use_container_width=True,
            disabled=not blocks or st.session_state.processing
        ):
            st.session_state.processing = True
            st.session_state.blocks = [
                TTSBlock(index=i, text=text)
                for i, text in enumerate(blocks)
            ]

            # 진행 상황 표시
            progress_bar = st.progress(0)
            status_text = st.empty()

            completed = [0]
            total = len(blocks)

            def update_progress(block: TTSBlock):
                completed[0] += 1
                progress = completed[0] / total
                progress_bar.progress(progress)
                status_text.text(f"처리 중: {completed[0]}/{total} ({block.status})")

            # 비동기 처리 실행
            with st.spinner(f"🔄 {len(blocks)}개 블록 병렬 처리 중..."):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    st.session_state.blocks = loop.run_until_complete(
                        process_blocks_parallel(
                            client,
                            st.session_state.blocks,
                            settings,
                            update_progress
                        )
                    )
                finally:
                    loop.close()

            st.session_state.processing = False
            progress_bar.progress(1.0)

            # 결과 요약
            completed_count = sum(1 for b in st.session_state.blocks if b.status == "completed")
            error_count = sum(1 for b in st.session_state.blocks if b.status == "error")

            if error_count == 0:
                st.success(f"✅ 모든 블록 처리 완료! ({completed_count}개)")
            else:
                st.warning(f"⚠️ 처리 완료: 성공 {completed_count}개, 실패 {error_count}개")

            st.rerun()


def render_results():
    """결과 및 다운로드 영역"""

    if not st.session_state.blocks:
        return

    completed_blocks = [b for b in st.session_state.blocks if b.status == "completed"]

    if not completed_blocks:
        return

    st.header("📥 결과 및 다운로드")

    # 선택 컨트롤
    col1, col2 = st.columns(2)
    with col1:
        select_all = st.checkbox("모두 선택", value=True, key="select_all")

    if select_all:
        st.session_state.selected_blocks = set(range(len(st.session_state.blocks)))

    # 개별 블록 표시 및 선택
    st.subheader("📋 블록 목록")

    for block in st.session_state.blocks:
        col1, col2, col3 = st.columns([0.5, 3, 1])

        with col1:
            is_selected = st.checkbox(
                "",
                value=block.index in st.session_state.selected_blocks,
                key=f"select_{block.index}",
                disabled=select_all
            )
            if is_selected and not select_all:
                st.session_state.selected_blocks.add(block.index)
            elif not is_selected and not select_all:
                st.session_state.selected_blocks.discard(block.index)

        with col2:
            status_emoji = {
                "completed": "✅",
                "error": "❌",
                "processing": "🔄",
                "pending": "⏳"
            }
            st.text(f"{status_emoji.get(block.status, '❓')} 블록 {block.index + 1}: {block.text[:50]}...")

        with col3:
            if block.status == "completed" and block.audio_data:
                st.audio(block.audio_data, format="audio/wav")
            elif block.status == "error":
                st.error(block.error_message[:30])

    st.divider()

    # 다운로드 옵션
    st.subheader("⬇️ 다운로드 옵션")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**오디오 병합 설정**")
        word_gap = st.slider(
            "단어 사이 간격 (%)",
            min_value=50,
            max_value=200,
            value=100,
            step=10,
            help="100% = 원본 유지"
        )
        sentence_gap = st.slider(
            "문장(블록) 사이 간격 (초)",
            min_value=0.0,
            max_value=3.0,
            value=0.5,
            step=0.1,
            help="블록 사이의 무음 시간"
        )

    with col2:
        st.markdown("**다운로드**")

        selected_indices = sorted(st.session_state.selected_blocks)
        selected_completed = [
            b for b in st.session_state.blocks
            if b.index in selected_indices and b.status == "completed"
        ]

        st.info(f"선택된 블록: {len(selected_indices)}개 (완료: {len(selected_completed)}개)")

        # 오디오 다운로드
        if selected_completed:
            audio_data_list = [b.audio_data for b in selected_completed if b.audio_data]

            if st.button("🎵 오디오 다운로드 (병합)", use_container_width=True):
                with st.spinner("오디오 병합 중..."):
                    # 출력 포맷 확인
                    output_format = "wav"  # 기본값

                    merged_audio = merge_audio_blocks(
                        audio_data_list,
                        word_gap_percent=float(word_gap),
                        sentence_gap_seconds=float(sentence_gap),
                        output_format=output_format
                    )

                    if merged_audio:
                        st.download_button(
                            label="📥 병합된 오디오 저장",
                            data=merged_audio,
                            file_name=f"tts_merged_{int(time.time())}.{output_format}",
                            mime=f"audio/{output_format}"
                        )
                        st.success("병합 완료!")
                    else:
                        st.error("오디오 병합 실패")

            # 개별 오디오 다운로드
            if st.button("🎵 개별 오디오 다운로드 (ZIP)", use_container_width=True):
                import zipfile

                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for block in selected_completed:
                        if block.audio_data:
                            zf.writestr(
                                f"block_{block.index + 1:03d}.wav",
                                block.audio_data
                            )

                st.download_button(
                    label="📥 ZIP 파일 저장",
                    data=zip_buffer.getvalue(),
                    file_name=f"tts_blocks_{int(time.time())}.zip",
                    mime="application/zip"
                )

        # 텍스트 다운로드
        if selected_indices:
            selected_texts = [
                st.session_state.blocks[i].text
                for i in selected_indices
                if i < len(st.session_state.blocks)
            ]

            if st.button("📄 텍스트 다운로드", use_container_width=True):
                text_content = "\n\n---\n\n".join([
                    f"[블록 {i+1}]\n{text}"
                    for i, text in zip(selected_indices, selected_texts)
                ])

                st.download_button(
                    label="📥 텍스트 파일 저장",
                    data=text_content,
                    file_name=f"tts_script_{int(time.time())}.txt",
                    mime="text/plain"
                )


def main():
    """메인 함수"""

    st.set_page_config(
        page_title="Supertone TTS Processor",
        page_icon="🎙️",
        layout="wide"
    )

    st.title("🎙️ Supertone TTS 병렬 처리기")
    st.caption("슈퍼톤 API를 활용한 대량 TTS 생성 도구")

    # 세션 상태 초기화
    init_session_state()

    # 사이드바 - TTS 설정
    client, settings = render_sidebar()

    if not client:
        st.info("👈 사이드바에서 API 키를 입력해주세요")
        return

    if not settings:
        st.warning("👈 사이드바에서 Voice ID를 입력해주세요")

    # 스크립트 입력
    blocks = render_script_input()

    # 처리 버튼
    if settings and blocks:
        render_process_button(client, settings, blocks)

    # 결과 표시
    render_results()

    # 푸터
    st.divider()
    st.caption("Made with Streamlit & Supertone API")


if __name__ == "__main__":
    main()
