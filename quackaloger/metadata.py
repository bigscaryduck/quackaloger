"""Audio metadata reading and writing (tags + processing markers).

Supports MP3 (ID3), M4B/M4A (MP4), FLAC, and OGG (Vorbis Comments).
"""

import json
import os
from typing import Optional

from quackaloger.constants import MARKER_TAG_NAME

try:
    import mutagen
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3, TXXX
except ImportError:
    mutagen = None
    MP4 = None
    ID3 = None
    TXXX = None


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------

def read_tags(filepath: str, verbose: bool = False) -> dict:
    """Read all accessible tags from an audio file. Returns a flat dict."""
    if mutagen is None:
        return {}

    tags = {}
    try:
        audio = mutagen.File(filepath, easy=True)
        if audio is None:
            if verbose:
                from quackaloger.ui import ui
                ui.warn(f"mutagen returned None for: {filepath}")
            return tags

        for key in audio.keys():
            val = audio.get(key)
            if val:
                if isinstance(val, list):
                    tags[key.lower()] = val[0] if len(val) == 1 else "; ".join(str(v) for v in val)
                else:
                    tags[key.lower()] = str(val)

        ext = os.path.splitext(filepath)[1].lower()

        if ext == ".mp3":
            _read_mp3_custom_frames(filepath, tags)

        if ext in (".m4b", ".m4a"):
            _read_mp4_custom_atoms(filepath, tags)

    except Exception as e:
        if verbose:
            from quackaloger.ui import ui
            ui.error(f"Failed to read tags from {filepath}: {e}")
    return tags


def _read_mp3_custom_frames(filepath: str, tags: dict):
    """Extract custom TXXX/MVNM/MVIN frames from MP3 ID3 tags."""
    try:
        raw = ID3(filepath)
        for frame_id in [
            "TXXX:SERIES", "TXXX:series", "MVNM",
            "TXXX:ASIN", "TXXX:asin", "TXXX:audible_asin", "TXXX:AUDIBLE_ASIN",
            "MVIN", "TXXX:SERIES-PART", "TXXX:series-part",
        ]:
            frame = raw.getall(frame_id)
            if frame:
                val = str(frame[0])
                key = frame_id.split(":")[-1].lower() if ":" in frame_id else frame_id.lower()
                tags[key] = val
    except Exception:
        pass


def _read_mp4_custom_atoms(filepath: str, tags: dict):
    """Extract custom iTunes freeform atoms from M4B/M4A files."""
    try:
        raw = MP4(filepath)
        atom_map = {
            "----:com.apple.iTunes:SERIES": "series",
            "----:com.apple.iTunes:series": "series",
            "----:com.apple.iTunes:SERIES-PART": "series-part",
            "----:com.apple.iTunes:series-part": "series-part",
            "----:com.apple.iTunes:ASIN": "asin",
            "----:com.apple.iTunes:asin": "asin",
            "----:com.apple.iTunes:AUDIBLE_ASIN": "asin",
            "----:com.apple.iTunes:audible_asin": "asin",
            "\xa9mvn": "series",
            "sonm": "series-part",
        }
        if raw.tags:
            for atom, key in atom_map.items():
                if atom in raw.tags:
                    val = raw.tags[atom]
                    if isinstance(val, list):
                        val = val[0]
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", errors="replace")
                    tags[key] = str(val).strip()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Processing marker read/write
# ---------------------------------------------------------------------------

def read_marker(filepath: str) -> Optional[dict]:
    """Read the AUDIOBOOK_ORGANIZER marker from a file, if present."""
    if mutagen is None:
        return None
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".mp3":
            return _read_marker_mp3(filepath)
        elif ext in (".m4b", ".m4a"):
            return _read_marker_mp4(filepath)
        else:
            return _read_marker_vorbis(filepath)
    except Exception:
        return None


def write_marker(filepath: str, data: dict) -> bool:
    """Embed the AUDIOBOOK_ORGANIZER marker into a file's metadata. Returns True on success."""
    if mutagen is None:
        return False
    json_str = json.dumps(data, ensure_ascii=False)
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".mp3":
            return _write_marker_mp3(filepath, json_str)
        elif ext in (".m4b", ".m4a"):
            return _write_marker_mp4(filepath, json_str)
        else:
            return _write_marker_vorbis(filepath, json_str)
    except Exception:
        return False


def clear_marker(filepath: str) -> bool:
    """Remove the AUDIOBOOK_ORGANIZER marker from a file. Returns True on success."""
    if mutagen is None:
        return False
    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".mp3":
            return _clear_marker_mp3(filepath)
        elif ext in (".m4b", ".m4a"):
            return _clear_marker_mp4(filepath)
        else:
            return _clear_marker_vorbis(filepath)
    except Exception:
        return False


# --- MP3 (ID3) ---

def _read_marker_mp3(filepath: str) -> Optional[dict]:
    raw = ID3(filepath)
    for frame in raw.getall(f"TXXX:{MARKER_TAG_NAME}"):
        text = str(frame)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _write_marker_mp3(filepath: str, json_str: str) -> bool:
    raw = ID3(filepath)
    raw.add(TXXX(encoding=3, desc=MARKER_TAG_NAME, text=[json_str]))
    raw.save(filepath)
    return True


def _clear_marker_mp3(filepath: str) -> bool:
    raw = ID3(filepath)
    raw.delall(f"TXXX:{MARKER_TAG_NAME}")
    raw.save(filepath)
    return True


# --- M4B / M4A (MP4) ---

_MP4_ATOM = f"----:com.apple.iTunes:{MARKER_TAG_NAME}"


def _read_marker_mp4(filepath: str) -> Optional[dict]:
    raw = MP4(filepath)
    if raw.tags and _MP4_ATOM in raw.tags:
        val = raw.tags[_MP4_ATOM]
        if isinstance(val, list):
            val = val[0]
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        try:
            return json.loads(str(val))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _write_marker_mp4(filepath: str, json_str: str) -> bool:
    raw = MP4(filepath)
    if raw.tags is None:
        raw.add_tags()
    raw.tags[_MP4_ATOM] = [json_str.encode("utf-8")]
    raw.save(filepath)
    return True


def _clear_marker_mp4(filepath: str) -> bool:
    raw = MP4(filepath)
    if raw.tags and _MP4_ATOM in raw.tags:
        del raw.tags[_MP4_ATOM]
        raw.save(filepath)
    return True


# --- FLAC / OGG (Vorbis Comments) ---

def _read_marker_vorbis(filepath: str) -> Optional[dict]:
    audio = mutagen.File(filepath)
    if audio and audio.tags:
        vals = audio.tags.get(MARKER_TAG_NAME)
        if vals:
            val = vals[0] if isinstance(vals, list) else str(vals)
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return None


def _write_marker_vorbis(filepath: str, json_str: str) -> bool:
    audio = mutagen.File(filepath)
    if audio is None:
        return False
    if audio.tags is None:
        audio.add_tags()
    audio.tags[MARKER_TAG_NAME] = [json_str]
    audio.save()
    return True


def _clear_marker_vorbis(filepath: str) -> bool:
    audio = mutagen.File(filepath)
    if audio and audio.tags and MARKER_TAG_NAME in audio.tags:
        del audio.tags[MARKER_TAG_NAME]
        audio.save()
    return True
