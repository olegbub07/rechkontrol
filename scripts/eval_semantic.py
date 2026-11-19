"""Оценка поиска по смыслу: только фразы против фраз + эмбеддинги.

  python scripts/eval_semantic.py              отложенная выборка tests/fixtures/paraphrases_heldout.json
  python scripts/eval_semantic.py --dev        подбор порогов: примеры по одному (leave-one-out) против NEUTRAL

Нужна модель в data/models/multilingual-e5-small (python scripts/download_models.py --only e5)
или путь в SEMANTIC_MODEL_DIR. База данных не открывается.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SEMANTIC_MATCH", "on")

from app import semantic  # noqa: E402
from app.checklist import by_id, default_checklist  # noqa: E402
from app.diarize import normalize  # noqa: E402
from app.quality import _has, _profanity  # noqa: E402

CFG = by_id(default_checklist())


def phrase_hit(text: str, cid: str) -> bool:
    norm = normalize(text)
    if cid == "no_rudeness" and _profanity(norm):
        return True
    phrases = CFG[cid]["phrases"]
    return isinstance(phrases, list) and bool(_has(norm, phrases))


def sem_hit(text: str, cid: str, enc) -> bool:
    m = semantic.Matcher([{"role": "operator", "text": text}], encoder=enc)
    return m.find(cid) is not None


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def run(positives: dict[str, list[str]], negatives: list[str], enc, loo: bool = False) -> None:
    tot = [0, 0, 0]
    print("Критерий: только фразы | фразы + смысл")
    for cid, items in positives.items():
        p = s = 0
        for text in items:
            full = semantic.EXAMPLES[cid]
            if loo:
                semantic.EXAMPLES[cid] = [e for e in full if e != text]
            ph = phrase_hit(text, cid)
            p += ph
            s += ph or sem_hit(text, cid, enc)
            semantic.EXAMPLES[cid] = full
        tot[0] += p
        tot[1] += s
        tot[2] += len(items)
        print(f"- {cid}: {pct(p, len(items))} | {pct(s, len(items))}")
    print(f"Итого попаданий: только фразы {pct(tot[0], tot[2])}, фразы + смысл {pct(tot[1], tot[2])}")
    fp_p = fp_s = 0
    for text in negatives:
        neutral = [n for n in semantic.NEUTRAL if n != text] if loo else None
        old = semantic.NEUTRAL
        if neutral is not None:
            semantic.NEUTRAL = neutral
            semantic._example_cache.clear()
        ph = [c for c in positives if phrase_hit(text, c)]
        sm = [c for c in positives if not phrase_hit(text, c) and sem_hit(text, c, enc)]
        semantic.NEUTRAL = old
        fp_p += bool(ph)
        fp_s += bool(ph or sm)
        if ph or sm:
            print(f"  ложное срабатывание: «{text}» фразы={ph} смысл={sm}")
    print(f"Ложные срабатывания на нейтральных: только фразы {pct(fp_p, len(negatives))}, фразы + смысл {pct(fp_s, len(negatives))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()
    enc = semantic.get_encoder()
    if enc is None:
        sys.exit(f"Модель не найдена в {semantic.model_dir()}, оценка невозможна.")
    if args.dev:
        print("DEV: примеры leave-one-out, нейтральные leave-one-out\n")
        run(semantic.EXAMPLES, list(semantic.NEUTRAL), enc, loo=True)
    else:
        data = json.loads((ROOT / "tests/fixtures/paraphrases_heldout.json").read_text(encoding="utf-8"))
        print("Отложенная выборка\n")
        run(data["positives"], data["negatives"], enc)


if __name__ == "__main__":
    main()
