from __future__ import annotations

import re
from collections import defaultdict

import numpy as np

from app.asr_fix import correct_asr_text

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
    "уточните",
    "конечно помогу",
    "конечно, помогу",
    "понимаю",
    "рада была помочь",
    "рад был помочь",
    "обращайтесь",
    "всего доброго",
    "заявку зарегистрирую",
    "счёт откроем",
    "счет откроем",
)

CLIENT_CUES = (
    "хочу открыть",
    "хочу ",
    "какие документы",
    "документы нужны",
    "у меня",
    "не работает",
    "не проходит",
    "подскажите, какие",
    "подскажите какие",
    "пока нет",
    "отлично, спасибо",
    "все понятно",
    "всё понятно",
    "налогообложен",
)

_norm_re = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return _norm_re.sub(" ", text).strip()


def speech_regions(
    audio: np.ndarray,
    sr: int,
    min_silence_s: float = 0.55,
    min_speech_s: float = 0.22,
) -> list[tuple[float, float]]:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = max(1, int(sr * 0.02))
    n = (len(audio) // frame) * frame
    if n == 0:
        return []
    rms = np.sqrt((audio[:n].reshape(-1, frame) ** 2).mean(axis=1) + 1e-12)
    thresh = max(0.008, float(np.percentile(rms, 90)) * 0.12)
    voiced = rms > thresh
    hole = max(1, int(min_silence_s / 0.02))
    voiced = _fill_short_gaps(voiced, hole)
    regions: list[tuple[float, float]] = []
    i = 0
    while i < len(voiced):
        if not voiced[i]:
            i += 1
            continue
        j = i
        while j < len(voiced) and voiced[j]:
            j += 1
        start = i * frame / sr
        end = j * frame / sr
        if end - start >= min_speech_s:
            regions.append((start, end))
        i = j
    return regions


def _fill_short_gaps(mask: np.ndarray, max_hole: int) -> np.ndarray:
    out = mask.copy()
    n = len(out)
    i = 0
    while i < n:
        if out[i]:
            i += 1
            continue
        j = i
        while j < n and not out[j]:
            j += 1
        if i > 0 and j < n and (j - i) < max_hole:
            out[i:j] = True
        i = j
    return out


def _join_words(words: list[dict]) -> str:
    parts: list[str] = []
    for w in words:
        token = (w.get("text") or "").strip()
        if not token:
            continue
        if token.startswith(("-", "—")) and parts:
            parts[-1] = parts[-1].rstrip() + token
        else:
            parts.append(token)
    return correct_asr_text(" ".join(parts))


def utterances_from_words(words: list[dict], regions: list[tuple[float, float]] | None = None) -> list[dict]:
    clean = [w for w in words if (w.get("text") or "").strip()]
    if not clean:
        return []
    if regions:
        utts: list[dict] = []
        for start, end in regions:
            chunk = [w for w in clean if w["start"] < end + 0.1 and w["end"] > start - 0.1]
            if not chunk:
                continue
            utts.append(
                {
                    "start": float(chunk[0]["start"]),
                    "end": float(chunk[-1]["end"]),
                    "text": _join_words(chunk),
                }
            )
        if utts:
            return utts
    return _gap_split(clean, gap=0.8)


def _gap_split(words: list[dict], gap: float) -> list[dict]:
    groups: list[list[dict]] = [[words[0]]]
    for word in words[1:]:
        if word["start"] - groups[-1][-1]["end"] >= gap:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [
        {
            "start": float(g[0]["start"]),
            "end": float(g[-1]["end"]),
            "text": _join_words(g),
        }
        for g in groups
        if _join_words(g)
    ]


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


def label_speakers(turns: list[dict], pause: float = 0.55) -> list[dict]:
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


def _cue_score(text: str, cues: tuple[str, ...]) -> int:
    n = normalize(text)
    return sum(1 for cue in cues if cue in n)


def classify_turns(turns: list[dict], operator_first: bool = True) -> list[dict]:
    out: list[dict] = []
    prev_role = "operator" if operator_first else "client"
    prev_end = None
    for i, turn in enumerate(turns):
        item = dict(turn)
        op = _cue_score(item["text"], OPERATOR_CUES)
        cl = _cue_score(item["text"], CLIENT_CUES)
        if op > cl:
            role = "operator"
        elif cl > op:
            role = "client"
        elif i == 0:
            role = "operator" if operator_first else "client"
        elif prev_end is not None and item["start"] - prev_end < 1.5:
            role = prev_role
        else:
            role = "client" if prev_role == "operator" else "operator"
        item["role"] = role
        item["speaker"] = "A" if role == "operator" else "B"
        out.append(item)
        prev_role = role
        prev_end = item["end"]
    return merge_same_role(out)


def merge_same_role(turns: list[dict], max_gap: float = 3.5) -> list[dict]:
    if not turns:
        return []
    merged = [dict(turns[0])]
    for turn in turns[1:]:
        last = merged[-1]
        if turn["role"] == last["role"] and turn["start"] - last["end"] <= max_gap:
            last["end"] = turn["end"]
            last["text"] = (last["text"] + " " + turn["text"]).strip()
        else:
            merged.append(dict(turn))
    return merged


def assign_roles(turns: list[dict], operator_first: bool = True) -> list[dict]:
    if not turns:
        return []
    scores: dict[str, int] = defaultdict(int)
    for turn in turns:
        scores[turn["speaker"]] += _cue_score(turn["text"], OPERATOR_CUES)
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


def from_whisper(
    raw_segments: list[dict],
    operator_first: bool = True,
    audio: np.ndarray | None = None,
    sr: int | None = None,
) -> list[dict]:
    words: list[dict] = []
    for seg in raw_segments or []:
        if not isinstance(seg, dict):
            continue
        for word in seg.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = (word.get("text") or "").strip()
            if not token:
                continue
            try:
                words.append({"start": float(word["start"]), "end": float(word["end"]), "text": token})
            except (KeyError, TypeError, ValueError):
                continue
    regions = speech_regions(audio, sr) if audio is not None and sr else None
    if words:
        turns = utterances_from_words(words, regions)
    else:
        turns = merge_whisper_segments(raw_segments)
        turns = [{"start": t["start"], "end": t["end"], "text": correct_asr_text(t["text"])} for t in turns]
    return classify_turns(turns, operator_first=operator_first)
