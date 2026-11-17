"""Замер скорости и точности локального Whisper.

Примеры:
  # собрать ~10-минутную запись из клипов MInDS-14 (ru-RU) с эталонным текстом
  python scripts/benchmark.py make-sample --minutes 10 --out data/bench/sample_10min.wav

  # время обработки и real-time factor (RTF) для файла и моделей
  python scripts/benchmark.py speed --audio data/bench/sample_10min.wav --models base,small

  # WER/CER на клипах датасета (вкладка «Датасет» или python -m app.dataset)
  python scripts/benchmark.py wer --models base,small --clips 40

RTF = время обработки / длительность записи. Норматив кейса 10 минут за 3-5 минут = RTF 0,3-0,5.
Результаты дописываются в data/bench/results.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def machine_info() -> dict:
    return {"cpu": cpu_name(), "cores": os.cpu_count(), "python": platform.python_version(), "os": platform.platform()}


def results_path() -> Path:
    from app.config import settings

    path = settings.data_dir / "bench" / "results.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def save(record: dict) -> None:
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **machine_info(), **record}
    with results_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


def use_model(name: str, compute_type: str | None = None) -> None:
    from app.config import settings
    from app.transcribe import download_model, local_model_ready, reset_model

    settings.whisper_model = name
    if compute_type:
        settings.whisper_compute_type = compute_type
    reset_model()
    if not local_model_ready(name):
        print(f"Скачиваю модель {name}…", flush=True)
        download_model(name)


def cmd_make_sample(args) -> None:
    import numpy as np
    import soundfile as sf
    from datasets import Audio, load_dataset

    from app.dataset import DATASET_CONFIG, DATASET_ID, _audio_to_wav

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(DATASET_ID, DATASET_CONFIG, split="train", trust_remote_code=True)
    ds = ds.cast_column("audio", Audio(decode=False))
    target = args.minutes * 60
    gap = np.zeros(int(16000 * args.gap), dtype=np.float32)
    parts, refs, total = [], [], 0.0
    tmp = out.with_suffix(".clip.wav")
    for row in ds:
        text = (row.get("transcription") or "").strip()
        if not text:
            continue
        try:
            _audio_to_wav(row["audio"], tmp)
            audio, sr = sf.read(str(tmp), dtype="float32")
        except Exception:
            continue
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        parts += [audio, gap]
        refs.append(text)
        total += len(audio) / sr + args.gap
        if total >= target:
            break
    tmp.unlink(missing_ok=True)
    sf.write(str(out), np.concatenate(parts), 16000)
    out.with_suffix(".txt").write_text("\n".join(refs) + "\n", encoding="utf-8")
    print(f"{out}: {total / 60:.2f} мин, {len(refs)} клипов, эталон в {out.with_suffix('.txt')}")


def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cmd_speed(args) -> None:
    import tempfile

    from app.audio import to_wav_mono_16k
    from app.diarize import from_whisper
    from app.quality import analyze
    from app.config import settings
    from app.transcribe import cpu_threads, load_model, transcribe_wav

    src = Path(args.audio)
    ref_path = src.with_suffix(".txt")
    reference = ref_path.read_text(encoding="utf-8") if ref_path.exists() else None
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        use_model(name, args.compute_type)
        t0 = time.perf_counter()
        load_model()
        load_sec = time.perf_counter() - t0
        for run in range(args.runs):
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "audio.wav"
                t0 = time.perf_counter()
                duration = to_wav_mono_16k(src, wav, max_duration_sec=24 * 3600)
                t1 = time.perf_counter()
                raw, _lang, audio, sr = transcribe_wav(wav, duration=duration)
                t2 = time.perf_counter()
                turns = from_whisper(raw, operator_first=True, audio=audio, sr=sr)
                analyze(turns)
                t3 = time.perf_counter()
            total = t3 - t0
            record = {
                "kind": "speed",
                "model": name,
                "compute_type": settings.whisper_compute_type,
                "threads": cpu_threads(),
                "run": run + 1,
                "audio": src.name,
                "audio_sec": round(duration, 1),
                "model_load_sec": round(load_sec, 1),
                "convert_sec": round(t1 - t0, 2),
                "transcribe_sec": round(t2 - t1, 1),
                "analyze_sec": round(t3 - t2, 2),
                "total_sec": round(total, 1),
                "rtf": round(total / duration, 3) if duration else None,
                "beam_size_used": 1 if duration >= 180 else None,
            }
            if reference:
                from jiwer import cer, wer

                hyp = _normalize(" ".join(t["text"] for t in turns))
                ref = _normalize(reference)
                record["wer"] = round(float(wer(ref, hyp)), 3)
                record["cer"] = round(float(cer(ref, hyp)), 3)
            save(record)


def cmd_wer(args) -> None:
    from app.dataset import dataset_ready, download_minds14, evaluate_wer

    if not dataset_ready():
        download_minds14(limit=args.clips)
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        use_model(name, args.compute_type)
        from app.config import settings

        t0 = time.perf_counter()
        report = evaluate_wer(limit=args.clips)
        save(
            {
                "kind": "wer",
                "model": name,
                "compute_type": settings.whisper_compute_type,
                "clips": report["clips"],
                "wer": report["wer"],
                "cer": report["cer"],
                "elapsed_sec": round(time.perf_counter() - t0, 1),
            }
        )


def cmd_llm(args) -> None:
    """Оценка демо-звонков локальной LLM: время, валидность JSON, оценки правил и модели."""
    from app import llm
    from app.config import settings
    from app.demo_calls import DEMO_CALLS
    from app.quality import analyze

    settings.llm_provider = "ollama"
    if args.model:
        settings.llm_model = args.model
    calls = DEMO_CALLS[: args.calls]
    if args.warmup and calls:
        t0 = time.perf_counter()
        llm.ask_ollama(llm.build_prompt(calls[0]["segments"], analyze(calls[0]["segments"])))
        print(f"прогрев (загрузка модели в память): {time.perf_counter() - t0:.1f} с")
    ok = 0
    times = []
    for item in calls:
        turns = item["segments"]
        report = analyze(turns)
        t0 = time.perf_counter()
        answer = llm.ask_ollama(llm.build_prompt(turns, report))
        sec = round(time.perf_counter() - t0, 1)
        times.append(sec)
        ok += answer is not None
        record = {
            "kind": "llm",
            "model": settings.llm_model,
            "call": item["filename"],
            "turns": len(turns),
            "sec": sec,
            "valid_json": answer is not None,
            "rules": {m: round(getattr(report, m)) for m in llm.METRICS},
            "llm": {m: round(answer[m]) for m in llm.METRICS} if answer else None,
            "recommendations": answer["recommendations"] if answer else None,
        }
        save(record)
        print(json.dumps(record, ensure_ascii=False))
    if times:
        print(f"{settings.llm_model}: валидный JSON {ok}/{len(times)}, время {min(times)}-{max(times)} с, среднее {sum(times) / len(times):.1f} с")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make-sample", help="собрать длинную запись из MInDS-14")
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--gap", type=float, default=0.6, help="пауза между клипами, с")
    p.add_argument("--out", default="data/bench/sample_10min.wav")
    p.set_defaults(func=cmd_make_sample)
    p = sub.add_parser("speed", help="время обработки и RTF")
    p.add_argument("--audio", required=True)
    p.add_argument("--models", default="base")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--compute-type", default=None, help="int8, int8_float32, float32; по умолчанию WHISPER_COMPUTE_TYPE")
    p.set_defaults(func=cmd_speed)
    p = sub.add_parser("wer", help="WER/CER на клипах датасета")
    p.add_argument("--models", default="base")
    p.add_argument("--clips", type=int, default=40)
    p.add_argument("--compute-type", default=None)
    p.set_defaults(func=cmd_wer)
    p = sub.add_parser("llm", help="оценка демо-звонков локальной LLM (нужна запущенная Ollama)")
    p.add_argument("--model", default=None, help="по умолчанию LLM_MODEL")
    p.add_argument("--calls", type=int, default=5)
    p.add_argument("--warmup", action="store_true", help="сначала один прогон для загрузки модели")
    p.set_defaults(func=cmd_llm)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
