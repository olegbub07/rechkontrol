"""Скачать модели заранее, чтобы дальше всё работало без интернета.

  python scripts/download_models.py                 Whisper (WHISPER_MODEL) и e5 в data/models
  python scripts/download_models.py --dir D:\\models  в свою папку (например, для переноса на сервер)
  python scripts/download_models.py --only e5        только модель поиска по смыслу

Whisper: Systran/faster-whisper-<WHISPER_MODEL> (~480 МБ для small).
e5: intfloat/multilingual-e5-small, ONNX и токенизатор (~490 МБ).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

E5_REPO = "intfloat/multilingual-e5-small"
E5_FILES = {"onnx/model.onnx": "model.onnx", "onnx/tokenizer.json": "tokenizer.json", "onnx/config.json": "config.json"}


def download_e5(target: Path) -> Path:
    from huggingface_hub import hf_hub_download

    target.mkdir(parents=True, exist_ok=True)
    for remote, local in E5_FILES.items():
        path = Path(hf_hub_download(repo_id=E5_REPO, filename=remote, local_dir=str(target / "_hf")))
        shutil.copyfile(path, target / local)
    for p in sorted((target / "_hf").rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    (target / "_hf").rmdir()
    return target


def download_whisper(target_root: Path) -> Path:
    from huggingface_hub import snapshot_download

    name = settings.whisper_model
    target = target_root / f"faster-whisper-{name.replace('/', '_')}"
    snapshot_download(repo_id=f"Systran/faster-whisper-{name}", local_dir=str(target))
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=settings.models_dir)
    ap.add_argument("--only", choices=["whisper", "e5"])
    args = ap.parse_args()
    if args.only in (None, "whisper"):
        print("Whisper:", download_whisper(args.dir))
    if args.only in (None, "e5"):
        print("e5:", download_e5(args.dir / "multilingual-e5-small"))


if __name__ == "__main__":
    main()
