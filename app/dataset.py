from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.crypto import encrypt_bytes
from app.db import SessionLocal
from app.models import Call, Operator

log = logging.getLogger("rechkontrol.dataset")

MANIFEST_NAME = "manifest.json"
DATASET_ID = "PolyAI/minds14"
DATASET_CONFIG = "ru-RU"
DEFAULT_LIMIT = 24

_state_lock = threading.Lock()
_state: dict = {
    "status": "idle",
    "message": "",
    "progress": 0,
    "clips": 0,
}


def dataset_ready() -> bool:
    return (settings.dataset_dir / MANIFEST_NAME).exists()


def load_manifest() -> dict:
    path = settings.dataset_dir / MANIFEST_NAME
    empty = {"source": DATASET_ID, "config": DATASET_CONFIG, "clips": []}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty
    if not isinstance(data, dict):
        return empty
    clips = [item for item in data.get("clips") or [] if isinstance(item, dict)]
    data["clips"] = clips
    return data


def clip_file(name: str) -> Path:
    root = settings.dataset_dir.resolve()
    path = (root / str(name or "")).resolve()
    if path.suffix.lower() != ".wav" or not path.is_relative_to(root):
        raise RuntimeError("Некорректный файл датасета")
    return path


def status() -> dict:
    with _state_lock:
        live = dict(_state)
    man = load_manifest()
    clips = man.get("clips") or []
    live.update(
        {
            "ready": bool(clips),
            "source": man.get("source", DATASET_ID),
            "config": man.get("config", DATASET_CONFIG),
            "stored_clips": len(clips),
            "intents": sorted({c.get("intent") or "" for c in clips if c.get("intent")}),
        }
    )
    if clips and live.get("status") == "idle":
        live["status"] = "ready"
        live["clips"] = len(clips)
        live["message"] = f"На диске {len(clips)} клипов"
    return live


def _set_state(**kwargs) -> None:
    with _state_lock:
        _state.update(kwargs)


def _intent_name(ds, value) -> str:
    try:
        names = ds.features["intent_class"].names
        if isinstance(value, int) and 0 <= value < len(names):
            return names[value]
    except Exception:
        pass
    return str(value)


def _audio_to_wav(audio_obj, dest: Path) -> float:
    from app.audio import to_wav_mono_16k

    raw = None
    src_path = None
    if isinstance(audio_obj, dict):
        raw = audio_obj.get("bytes")
        src_path = audio_obj.get("path")
    elif isinstance(audio_obj, (str, Path)):
        src_path = str(audio_obj)
    tmp = dest.with_suffix(".src")
    try:
        if raw:
            tmp.write_bytes(raw)
            return to_wav_mono_16k(tmp, dest)
        if src_path and Path(src_path).exists():
            return to_wav_mono_16k(Path(src_path), dest)
        raise RuntimeError("В записи датасета нет аудио")
    finally:
        tmp.unlink(missing_ok=True)


def download_minds14(limit: int = DEFAULT_LIMIT) -> dict:
    from datasets import Audio, load_dataset

    limit = max(8, min(int(limit), 60))
    _set_state(status="downloading", message="Скачиваю PolyAI MInDS-14 (ru-RU)…", progress=5, clips=0)
    settings.dataset_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = settings.dataset_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    ds = load_dataset(DATASET_ID, DATASET_CONFIG, split="train", trust_remote_code=True)
    ds = ds.cast_column("audio", Audio(decode=False))
    n = len(ds)
    step = max(1, n // limit)
    indices = list(range(0, n, step))[:limit]
    items: list[dict] = []
    for k, i in enumerate(indices):
        row = ds[i]
        text = (row.get("transcription") or "").strip()
        if not text:
            continue
        name = f"{i:04d}.wav"
        dest = clips_dir / name
        try:
            duration = _audio_to_wav(row["audio"], dest)
        except Exception as exc:
            log.warning("skip clip %s: %s", i, exc)
            dest.unlink(missing_ok=True)
            continue
        if duration < 0.5:
            dest.unlink(missing_ok=True)
            continue
        items.append(
            {
                "id": i,
                "file": f"clips/{name}",
                "text": text,
                "intent": _intent_name(ds, row.get("intent_class")),
                "duration_sec": round(duration, 2),
            }
        )
        _set_state(progress=5 + int(90 * (k + 1) / len(indices)), clips=len(items), message=f"Сохранено {len(items)} записей")

    manifest = {
        "source": DATASET_ID,
        "config": DATASET_CONFIG,
        "license": "CC-BY-NC-4.0",
        "description": "Банковские голосовые запросы MInDS-14, русский, эталонные расшифровки.",
        "downloaded_at": datetime.utcnow().isoformat(timespec="seconds"),
        "clips": items,
    }
    (settings.dataset_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_state(status="ready", progress=100, clips=len(items), message=f"Готово: {len(items)} клипов")
    log.info("dataset stored %s clips in %s", len(items), settings.dataset_dir)
    return manifest


def _normalize_ref(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\sа-я]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def evaluate_wer(limit: int | None = None) -> dict:
    from jiwer import cer, wer

    from app.diarize import from_whisper
    from app.transcribe import transcribe_wav

    man = load_manifest()
    clips = man.get("clips") or []
    if not clips:
        raise RuntimeError("Датасет ещё не скачан")
    if limit:
        clips = clips[:limit]
    _set_state(status="evaluating", message="Считаю WER на локальном Whisper…", progress=0)
    try:
        return _score_clips(clips, wer, cer, transcribe_wav, from_whisper)
    except RuntimeError:
        _set_state(status="error", message="Не удалось посчитать WER", progress=0)
        raise
    except Exception as exc:
        log.exception("wer failed")
        _set_state(status="error", message="Не удалось посчитать WER", progress=0)
        raise RuntimeError("Не удалось посчитать WER") from exc


def _score_clips(clips, wer, cer, transcribe_wav, from_whisper) -> dict:
    hyps: list[str] = []
    refs: list[str] = []
    rows: list[dict] = []
    total = len(clips)
    for i, clip in enumerate(clips):
        try:
            path = clip_file(str(clip.get("file") or ""))
        except RuntimeError:
            continue
        if not path.exists():
            continue
        try:
            raw, _lang, audio, sr = transcribe_wav(path, duration=clip.get("duration_sec"))
            turns = from_whisper(raw, operator_first=True, audio=audio, sr=sr)
        except Exception:
            log.exception("clip %s failed", clip.get("file"))
            continue
        hyp = " ".join(t["text"] for t in turns).strip()
        ref = str(clip.get("text") or "")
        norm_hyp = _normalize_ref(hyp)
        norm_ref = _normalize_ref(ref)
        hyps.append(norm_hyp)
        refs.append(norm_ref)
        item_wer = wer(norm_ref, norm_hyp) if norm_ref else 1.0
        rows.append(
            {
                "file": str(clip.get("file") or ""),
                "intent": clip.get("intent"),
                "reference": ref,
                "hypothesis": hyp,
                "wer": round(float(item_wer), 3),
            }
        )
        _set_state(progress=int(100 * (i + 1) / total), message=f"Оценка {i + 1}/{total}")
    if not refs:
        raise RuntimeError("В датасете нет доступных клипов")
    mean_wer = float(wer(refs, hyps))
    mean_cer = float(cer(refs, hyps))
    report = {
        "clips": len(refs),
        "wer": round(mean_wer, 3),
        "cer": round(mean_cer, 3),
        "accuracy": round(max(0.0, 1.0 - mean_wer) * 100, 1),
        "samples": rows[:12],
    }
    (settings.dataset_dir / "wer_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _set_state(status="ready", progress=100, message=f"WER {report['wer']:.1%}, точность {report['accuracy']}%")
    return report


def import_to_queue(limit: int | None = None) -> list[int]:
    man = load_manifest()
    clips = man.get("clips") or []
    if not clips:
        raise RuntimeError("Датасет ещё не скачан")
    if limit:
        clips = clips[:limit]
    created: list[int] = []
    with SessionLocal() as session:
        op = session.query(Operator).filter(Operator.external_id == "DATASET-MINDS14").one_or_none()
        if op is None:
            op = Operator(name="Датасет MInDS-14 (банк)", external_id="DATASET-MINDS14")
            session.add(op)
            session.flush()
        stored_paths: list[Path] = []
        try:
            for clip in clips:
                try:
                    src = clip_file(str(clip.get("file") or ""))
                except RuntimeError:
                    continue
                if not src.exists():
                    continue
                stored = f"{uuid.uuid4().hex}.bin"
                path = settings.audio_dir / stored
                path.write_bytes(encrypt_bytes(src.read_bytes()))
                stored_paths.append(path)
                intent = str(clip.get("intent") or "clip")
                call = Call(
                    orig_filename=Path(str(clip.get("file") or "clip.wav")).name.replace(".wav", f"_{intent}.wav"),
                    stored_name=stored,
                    mime="audio/wav",
                    operator_id=op.id,
                    call_at=datetime.utcnow(),
                    status="queued",
                    progress=0,
                    is_demo=0,
                    operator_first=1,
                )
                session.add(call)
                session.flush()
                created.append(call.id)
            if not created:
                raise RuntimeError("Не удалось импортировать клипы датасета")
            session.commit()
        except Exception:
            session.rollback()
            for path in stored_paths:
                path.unlink(missing_ok=True)
            raise
    return created


def start_download_async(limit: int = DEFAULT_LIMIT) -> None:
    with _state_lock:
        if _state["status"] in {"downloading", "evaluating"}:
            return

    def _run() -> None:
        try:
            download_minds14(limit=limit)
        except Exception:
            log.exception("dataset download failed")
            _set_state(status="error", message="Не удалось скачать датасет", progress=0)

    threading.Thread(target=_run, name="dataset-download", daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_minds14(limit=24)
    print(json.dumps(status(), ensure_ascii=False, indent=2))
