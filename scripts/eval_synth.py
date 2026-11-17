"""Прогон синтетических звонков и таблица «ожидалось / получилось».

Без LLM, только правила. Два режима:
  python scripts/eval_synth.py --audio ПАПКА_С_MP3      полный конвейер: Whisper, роли, оценка
  python scripts/eval_synth.py --json tests/fixtures/synth_calls.json   только оценка по готовым репликам

База данных не нужна и не открывается.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.quality import analyze  # noqa: E402

# Ожидаемый диапазон итоговой оценки по сценариям из scripts.md.
# high - образцовый звонок, mid - средний, below - ниже средней, low - грубость.
BANDS = {"high": (80, 100), "mid": (55, 79.9), "below": (35, 59.9), "low": (0, 45)}
EXPECT = {
    "01": ("high", "образцовая консультация по вкладу"),
    "02": ("high", "блокировка карты, эталон"),
    "03": ("low", "грубость и мат"),
    "04": ("high", "ипотека многодетным"),
    "05": ("mid", "РКО для ИП, без имени, частичные ответы"),
    "06": ("high", "жалоба, клиент раздражён (тональность негативная)"),
    "07": ("below", "кредитка, вопросы без ответа, отфутболили"),
    "08": ("high", "Mir Supreme, кэшбэк"),
    "09": ("mid", "пенсионная карта на «ты»"),
    "10": ("high", "зелёный вклад, консультация"),
}
TONALITY_RU = {"positive": "позит.", "negative": "негат.", "neutral": "нейтр."}


def turns_from_audio(path: Path) -> list[dict]:
    from app.audio import to_wav_mono_16k
    from app.diarize import from_whisper
    from app.transcribe import transcribe_wav

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "call.wav"
        duration = to_wav_mono_16k(path, wav)
        raw, _lang, audio, sr = transcribe_wav(wav, duration=duration)
        return from_whisper(raw, operator_first=True, audio=audio, sr=sr)


def evaluate(calls: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for name, turns in sorted(calls.items()):
        key = name[:2]
        band, note = EXPECT.get(key, ("", ""))
        r = analyze(turns)
        lo, hi = BANDS.get(band, (0, 100))
        rows.append(
            {
                "id": name,
                "expect": band,
                "note": note,
                "overall": r.overall,
                "politeness": r.politeness,
                "completeness": r.completeness,
                "script": r.script_compliance,
                "resolution": r.resolution,
                "tonality": r.tonality,
                "fails": [c.title for c in r.checks if not c.passed],
                "ok": lo <= r.overall <= hi,
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", type=Path, help="папка с 01_*.mp3 ... 10_*.mp3")
    ap.add_argument("--json", type=Path, help="готовые реплики {имя: [реплики]}")
    ap.add_argument("--save-json", type=Path, help="сохранить реплики после распознавания")
    args = ap.parse_args()
    calls: dict[str, list[dict]] = {}
    if args.audio:
        for f in sorted(args.audio.glob("[0-9][0-9]_*.mp3")):
            t0 = time.time()
            calls[f.stem] = turns_from_audio(f)
            print(f"{f.name}: {time.time() - t0:.1f} с", file=sys.stderr)
        if args.save_json:
            args.save_json.write_text(json.dumps(calls, ensure_ascii=False, indent=0), encoding="utf-8")
    elif args.json:
        calls = json.loads(args.json.read_text(encoding="utf-8"))
    else:
        ap.error("нужен --audio или --json")
    rows = evaluate(calls)
    for row in rows:
        mark = "да" if row["ok"] else "НЕТ"
        print(
            f"{row['id'][:2]} | {row['expect']:5} | {row['overall']:5.1f} | {row['politeness']:5.1f} | "
            f"{row['completeness']:5.1f} | {row['script']:5.1f} | {row['resolution']:5.1f} | "
            f"{TONALITY_RU.get(row['tonality'], row['tonality'])} | {mark} | {', '.join(row['fails']) or '-'}"
        )
    bad = [r["id"] for r in rows if not r["ok"]]
    print(f"В диапазоне: {len(rows) - len(bad)} из {len(rows)}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
