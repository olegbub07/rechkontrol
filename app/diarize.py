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


STEREO_DOMINANCE = 1.6  # во сколько раз громче канал, чтобы считать фразу его
STEREO_MIN_SHARE = 0.6  # доля фраз с явным каналом, иначе стерео считается ложным


def _channel_of(stereo: np.ndarray, sr: int, start: float, end: float) -> int | None:
    a = max(0, int(start * sr))
    b = min(len(stereo), max(a + 1, int(end * sr)))
    if b <= a:
        return None
    chunk = stereo[a:b]
    e0 = float(np.sqrt(np.mean(chunk[:, 0] ** 2)) + 1e-9)
    e1 = float(np.sqrt(np.mean(chunk[:, 1] ** 2)) + 1e-9)
    if e0 >= e1 * STEREO_DOMINANCE:
        return 0
    if e1 >= e0 * STEREO_DOMINANCE:
        return 1
    return None


def _group_words_by_channel(words: list[dict], stereo: np.ndarray, sr: int) -> list[dict]:
    turns: list[dict] = []
    current: list[dict] = []
    current_ch: int | None = None
    for word in words:
        ch = _channel_of(stereo, sr, word["start"], word["end"])
        if ch is None:
            ch = current_ch
        gap = word["start"] - current[-1]["end"] if current else 0.0
        if current and (ch != current_ch or gap >= 1.5):
            turns.append({"start": current[0]["start"], "end": current[-1]["end"], "text": _join_words(current), "channel": current_ch})
            current = []
        current.append(word)
        current_ch = ch
    if current:
        turns.append({"start": current[0]["start"], "end": current[-1]["end"], "text": _join_words(current), "channel": current_ch})
    return [t for t in turns if t["text"]]


def roles_from_channels(
    turns: list[dict],
    stereo: np.ndarray | None,
    sr: int,
    operator_first: bool = True,
    operator_channel: str = "auto",
) -> list[dict] | None:
    """Роли по громкости каналов: в записи КЦ оператор и клиент обычно в разных каналах.

    Возвращает None, если запись не стерео или каналы почти одинаковые (моно, сведённое в два канала):
    тогда работает обычная разметка по паузам и фразам.
    """
    if stereo is None or getattr(stereo, "ndim", 0) != 2 or stereo.shape[1] != 2 or not turns:
        return None
    for turn in turns:
        if "channel" not in turn:
            turn["channel"] = _channel_of(stereo, sr, turn["start"], turn["end"])
    known = [t["channel"] for t in turns if t["channel"] is not None]
    if len(known) < max(1, STEREO_MIN_SHARE * len(turns)) or len(set(known)) < 2:
        return None
    side = (operator_channel or "auto").lower()
    if side in {"left", "0"}:
        op_ch = 0
    elif side in {"right", "1"}:
        op_ch = 1
    else:
        cues = {0: 0, 1: 0}
        for turn in turns:
            if turn["channel"] is not None:
                cues[turn["channel"]] += _cue_score(turn["text"], OPERATOR_CUES) - _cue_score(turn["text"], CLIENT_CUES)
        if cues[0] != cues[1]:
            op_ch = 0 if cues[0] > cues[1] else 1
        else:
            first = known[0]
            op_ch = first if operator_first else 1 - first
    out: list[dict] = []
    prev_role = "operator" if operator_first else "client"
    for turn in turns:
        item = {k: v for k, v in turn.items() if k != "channel"}
        if turn["channel"] is None:
            role = prev_role
        else:
            role = "operator" if turn["channel"] == op_ch else "client"
        item["role"] = role
        item["speaker"] = "A" if role == "operator" else "B"
        item["source"] = "stereo"
        out.append(item)
        prev_role = role
    return merge_same_role(out, max_gap=1.0)


def from_whisper(
    raw_segments: list[dict],
    operator_first: bool = True,
    audio: np.ndarray | None = None,
    sr: int | None = None,
    stereo: np.ndarray | None = None,
    operator_channel: str = "auto",
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
    if stereo is not None:
        stereo_sr = 16000
        if words:
            by_channel = _group_words_by_channel(words, stereo, stereo_sr)
        else:
            by_channel = [
                {"start": float(s["start"]), "end": float(s["end"]), "text": correct_asr_text(str(s.get("text") or ""))}
                for s in raw_segments or []
                if isinstance(s, dict) and str(s.get("text") or "").strip()
            ]
        result = roles_from_channels(by_channel, stereo, stereo_sr, operator_first, operator_channel)
        if result:
            return result
    regions = speech_regions(audio, sr) if audio is not None and sr else None
    if words:
        turns = utterances_from_words(words, regions)
    else:
        turns = merge_whisper_segments(raw_segments)
        turns = [{"start": t["start"], "end": t["end"], "text": correct_asr_text(t["text"])} for t in turns]
    return classify_turns(turns, operator_first=operator_first)
