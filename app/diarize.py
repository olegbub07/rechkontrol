from __future__ import annotations

import re
from collections import defaultdict

OPERATOR_CUES = (
    "центр-инвест",
    "центр инвест",
    "банк",
    "меня зовут",
    "мой номер",
    "оператор",
    "специалист",
    "чем могу помочь",
    "чем могу быть полезна",
    "чем могу быть полезен",
    "контакт-центр",
    "служба поддержки",
    "заявка зарегистрирована",
    "номер обращения",
    "подскажите ваш",
    "назовите номер",
    "продиктуйте",
    "оформил",
    "оформила",
    "сейчас проверю",
    "сейчас посмотрю",
    "переведу на",
)

_norm_re = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return _norm_re.sub(" ", text).strip()


def merge_whisper_segments(raw: list[dict], gap: float = 0.55) -> list[dict]:
    turns: list[dict] = []
    for seg in raw:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        if turns and start - turns[-1]["end"] <= gap:
            turns[-1]["end"] = max(turns[-1]["end"], end)
            turns[-1]["text"] = (turns[-1]["text"] + " " + text).strip()
        else:
            turns.append({"start": start, "end": end, "text": text})
    return turns


def label_speakers(turns: list[dict], pause: float = 0.7) -> list[dict]:
    if not turns:
        return []
    speaker = "A"
    labeled = []
    prev_end = None
    for turn in turns:
        if prev_end is not None and turn["start"] - prev_end >= pause:
            speaker = "B" if speaker == "A" else "A"
        item = dict(turn)
        item["speaker"] = speaker
        labeled.append(item)
        prev_end = turn["end"]
    return labeled


def _cue_score(text: str) -> int:
    n = normalize(text)
    return sum(1 for cue in OPERATOR_CUES if cue in n)


def assign_roles(turns: list[dict], operator_first: bool = True) -> list[dict]:
    if not turns:
        return []
    scores: dict[str, int] = defaultdict(int)
    for turn in turns:
        scores[turn["speaker"]] += _cue_score(turn["text"])
    speakers = sorted({t["speaker"] for t in turns})
    if len(speakers) == 1:
        operator = speakers[0]
    elif max(scores.values()) == 0:
        operator = turns[0]["speaker"] if operator_first else ("B" if turns[0]["speaker"] == "A" else "A")
    else:
        operator = max(scores, key=lambda s: scores[s])
    out = []
    for turn in turns:
        item = dict(turn)
        item["role"] = "operator" if turn["speaker"] == operator else "client"
        out.append(item)
    return out


def from_whisper(raw_segments: list[dict], operator_first: bool = True) -> list[dict]:
    merged = merge_whisper_segments(raw_segments)
    labeled = label_speakers(merged)
    return assign_roles(labeled, operator_first=operator_first)
