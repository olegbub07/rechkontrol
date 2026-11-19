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
    "горячая линия",
    "горячие линии",
    "компания",
    "компании",
    "слушаю вас",
    "как могу к вам обращаться",
    "как к вам обращаться",
    "спасибо за звонок",
    "спасибо вам за ваш звонок",
    "чем еще могу помочь",
    "могу чем-то еще помочь",
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


# ---------- Разделение по голосу (моно) ----------
# Текстовые подсказки и паузы не видят смену говорящего, если клиент
# отвечает сразу. Поэтому режем слова на короткие фразы, считаем по каждой
# простой «отпечаток голоса и линии» (MFCC: средние и разброс) и делим фразы
# на два кластера. Только numpy, без тяжёлых моделей.

VOICE_MIN_UNITS = 4
VOICE_MIN_SHARE = 0.12  # меньший кластер должен занимать хотя бы столько речи
VOICE_MIN_BIC = 8.0  # насколько два голоса объясняют фразы лучше одного (ΔBIC в 3 главных компонентах)
_SENT_END = re.compile(r"[.?!…]$")


def _quiet_frames(audio: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = max(1, int(sr * 0.02))
    n = (len(audio) // frame) * frame
    if n == 0:
        return np.zeros(0, dtype=bool), 0.02
    rms = np.sqrt((audio[:n].reshape(-1, frame) ** 2).mean(axis=1) + 1e-12)
    return rms < max(1e-4, float(np.percentile(rms, 90)) * 0.06), frame / float(sr)


def annotate_pauses(turns: list[dict], audio: np.ndarray | None, sr: int | None, stretch: float = 0.3) -> list[dict]:
    """Для каждой смены говорящего пишет pause_before: самую длинную тишину в аудио между репликами.

    Окно расширено на stretch секунд в обе стороны: Whisper растягивает крайние слова
    на паузу, и по таймстемпам пауза выглядит почти нулевой, хотя в звуке она есть."""
    if audio is None or not sr or len(turns) < 2:
        return turns
    quiet, step = _quiet_frames(audio, sr)
    if len(quiet) == 0:
        return turns
    for prev, cur in zip(turns, turns[1:]):
        a = max(0, int((float(prev["end"]) - stretch) / step))
        b = min(len(quiet), int((float(cur["start"]) + stretch) / step) + 1)
        best = run = 0
        for q in quiet[a:b]:
            run = run + 1 if q else 0
            best = max(best, run)
        cur["pause_before"] = round(best * step, 2)
    return turns


def _silences(audio: np.ndarray, sr: int, min_len: float = 0.25) -> list[float]:
    """Середины пауз от min_len секунд (по энергии, кадр 20 мс)."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = max(1, int(sr * 0.02))
    n = (len(audio) // frame) * frame
    if n == 0:
        return []
    rms = np.sqrt((audio[:n].reshape(-1, frame) ** 2).mean(axis=1) + 1e-12)
    quiet = rms < max(1e-4, float(np.percentile(rms, 90)) * 0.06)
    out: list[float] = []
    i = 0
    while i < len(quiet):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < len(quiet) and quiet[j]:
            j += 1
        if (j - i) * 0.02 >= min_len:
            out.append((i + j) / 2 * 0.02)
        i = j
    return out


def _phrase_units(words: list[dict], gap: float = 0.25, pauses: list[float] | None = None) -> list[list[dict]]:
    """Фразы: конец предложения, зазор между словами или пауза в звуке между серединами слов.

    Whisper иногда растягивает слова на паузу и теряет пунктуацию на длинном куске,
    поэтому зазора в таймстемпах мало, нужна ещё и тишина в самом аудио."""
    pauses = sorted(pauses or [])
    units: list[list[dict]] = [[words[0]]]
    for prev, word in zip(words, words[1:]):
        lo = (prev["start"] + prev["end"]) / 2
        hi = (word["start"] + word["end"]) / 2
        silent = any(lo < p < hi for p in pauses)
        if silent or word["start"] - prev["end"] >= gap or _SENT_END.search(prev["text"]):
            units.append([word])
        else:
            units[-1].append(word)
    return units


_FB_CACHE: dict = {}


def _mel_dct(sr: int, n_fft: int = 512, n_mel: int = 26, n_ceps: int = 13):
    key = (sr, n_fft)
    if key not in _FB_CACHE:
        mel = lambda f: 2595 * np.log10(1 + f / 700.0)  # noqa: E731
        imel = lambda m: 700 * (10 ** (m / 2595.0) - 1)  # noqa: E731
        pts = imel(np.linspace(mel(60), mel(min(4000, sr / 2)), n_mel + 2))
        bins = np.floor((n_fft + 1) * pts / sr).astype(int)
        fb = np.zeros((n_mel, n_fft // 2 + 1))
        for i in range(n_mel):
            lo, c, hi = bins[i : i + 3]
            fb[i, lo:c] = (np.arange(lo, c) - lo) / max(c - lo, 1)
            fb[i, c:hi] = (hi - np.arange(c, hi)) / max(hi - c, 1)
        dct = np.cos(np.pi / n_mel * (np.arange(n_mel)[None, :] + 0.5) * np.arange(1, n_ceps + 1)[:, None])
        _FB_CACHE[key] = (fb, dct)
    return _FB_CACHE[key]


def voice_features(audio: np.ndarray, sr: int, start: float, end: float) -> np.ndarray | None:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    x = np.asarray(audio[max(0, int(start * sr)) : int(end * sr)], dtype=np.float64)
    win, hop, n_fft = int(0.025 * sr), int(0.01 * sr), 512
    if len(x) < win * 4:
        return None
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop] * np.hamming(win)
    power = np.abs(np.fft.rfft(frames, n_fft)) ** 2
    energy = power.sum(axis=1)
    keep = energy > np.percentile(energy, 40)
    if keep.sum() < 3:
        return None
    fb, dct = _mel_dct(sr, n_fft)
    ceps = np.log(power[keep] @ fb.T + 1e-10) @ dct.T
    return np.concatenate([ceps.mean(axis=0), ceps.std(axis=0)])


def _two_means(x: np.ndarray) -> tuple[np.ndarray, float]:
    xs = (x - x.mean(axis=0)) / (x.std(axis=0) + 1e-9)
    _, _, vt = np.linalg.svd(xs, full_matrices=False)
    proj = xs @ vt[0]
    labels = (proj > np.median(proj)).astype(int)
    for _ in range(25):
        if labels.min() == labels.max():
            break
        centers = np.array([xs[labels == k].mean(axis=0) for k in (0, 1)])
        new = np.argmin(((xs[:, None, :] - centers[None]) ** 2).sum(axis=2), axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
    if labels.min() == labels.max() or min(np.bincount(labels)) < 2:
        return labels, 0.0
    k = min(3, xs.shape[1])
    z = xs @ vt[:k].T

    def loglik(y: np.ndarray) -> float:
        return -0.5 * len(y) * float(np.sum(np.log(y.var(axis=0) + 1e-6)))

    gain = loglik(z[labels == 0]) + loglik(z[labels == 1]) - loglik(z) - 0.5 * (2 * k + 1) * np.log(len(z))
    return labels, float(gain)


_SHORT_ANSWERS = {"да", "нет", "угу", "ага", "хорошо", "конечно", "верно", "правильно", "да да", "нет нет", "ага ага"}


def _is_short_answer(text: str) -> bool:
    return re.sub(r"[^\w ]+", "", normalize(text)).strip() in _SHORT_ANSWERS


def voice_turns(words: list[dict], audio: np.ndarray, sr: int, operator_first: bool = True) -> list[dict] | None:
    """Роли по голосу. None, если голоса не делятся уверенно (тогда старая разметка)."""
    if not words or audio is None or not sr:
        return None
    units = _phrase_units(words, pauses=_silences(audio, sr))
    feats = [voice_features(audio, sr, u[0]["start"], u[-1]["end"]) for u in units]
    idx = [i for i, f in enumerate(feats) if f is not None]
    if len(idx) < VOICE_MIN_UNITS:
        return None
    labels, separation = _two_means(np.array([feats[i] for i in idx]))
    dur = {k: sum(units[i][-1]["end"] - units[i][0]["start"] for i, lab in zip(idx, labels) if lab == k) for k in (0, 1)}
    total = dur[0] + dur[1]
    if separation < VOICE_MIN_BIC or total <= 0 or min(dur.values()) / total < VOICE_MIN_SHARE:
        return None
    label_of: dict[int, int] = dict(zip(idx, (int(x) for x in labels)))
    prev = None
    unit_labels: list[int] = []
    for i in range(len(units)):
        lab = label_of.get(i, prev)
        if lab is None:
            lab = next((label_of[j] for j in idx if j > i), 0)
        unit_labels.append(lab)
        prev = lab
    # Короткое «да»/«нет» сразу после вопроса почти всегда говорит другой человек,
    # а голос по полсекунды звука определяется ненадёжно.
    for i in range(1, len(units)):
        prev_text = (units[i - 1][-1].get("text") or "").strip()
        if prev_text.endswith("?") and _is_short_answer(_join_words(units[i])):
            unit_labels[i] = 1 - unit_labels[i - 1]
    texts = {0: [], 1: []}
    for u, lab in zip(units, unit_labels):
        texts[lab].append(_join_words(u))
    score = {
        k: _cue_score(" ".join(texts[k]), OPERATOR_CUES) - _cue_score(" ".join(texts[k]), CLIENT_CUES) for k in (0, 1)
    }
    if score[0] != score[1]:
        op = 0 if score[0] > score[1] else 1
    else:
        op = unit_labels[0] if operator_first else 1 - unit_labels[0]
    out: list[dict] = []
    for u, lab in zip(units, unit_labels):
        role = "operator" if lab == op else "client"
        out.append(
            {
                "start": float(u[0]["start"]),
                "end": float(u[-1]["end"]),
                "text": _join_words(u),
                "role": role,
                "speaker": "A" if role == "operator" else "B",
                "source": "voice",
            }
        )
    return merge_same_role([t for t in out if t["text"]], max_gap=3.5)


# ---------- Моно без уверенного голоса: диалог по смыслу и паузам ----------
# Запись с телефона часто одноканальная, а голоса по MFCC делятся слабо. Раньше
# тогда куски резались по тишине и роли чередовались по паузам, и смена говорящего
# внутри куска Whisper терялась: начало реплики приклеивалось к концу чужой.
# Здесь каждая фраза получает «голос» за оператора или клиента по подсказкам,
# а каждая граница между фразами получает цену смены говорящего (вопрос и ответ,
# пауза, обрыв на середине предложения). Лучшую разметку ищем Витерби.
# Слова только для моно: на стерео они не влияют.

MONO_OPERATOR_CUES = (
    "да, конечно",
    "да конечно",
    "подскажите, пожалуйста, номер",
    "подскажите номер",
    "номер вашей",
    "номер вашего",
    "вашей карты",
    "ваша карта",
    "вашу карту",
    "ваш номер",
    "ваш договор",
    "ваши данные",
    "нашел вас",
    "нашла вас",
    "вижу вас",
    "вижу ваш",
    "блокирую",
    "заблокирую",
    "была заблокирована",
    "заблокирована",
    "ожидайте",
    "оставайтесь на линии",
    "одну минуту",
    "минуточку",
    "еще вопросы",
    "ещё вопросы",
    "вопросы остались",
    "остались вопросы",
    "что-то еще",
    "проверяю",
    "чем могу",
    # грубость, которую говорит оператор (за неё его и штрафуют)
    "не наша проблема",
    "не моя проблема",
    "сами виноваты",
    "сами разбирайтесь",
    "не тупите",
    "читайте договор",
    "это к нам не относится",
    "не туда попали",
    "вам на горячую линию",
    "не ваше дело",
    "что вы от меня хотите",
    "у меня очередь",
)

MONO_CLIENT_CUES = (
    "потерял",
    "потеряла",
    "помогите",
    "украли",
    "заблокируйте",
    "моя карта",
    "мою карту",
    "моей карты",
    "мне нужно",
    "мне надо",
    "я хотел",
    "я хотела",
    "почему так долго",
    "сколько ждать",
    "вы груб",
    "грубите",
    "груба",
    "хамите",
    "жалоб",
    "не могу",
    "не приходит",
    "списали",
    "буду жаловаться",
    "мои деньги",
    "я ничего не",
)

_ANSWER_START = re.compile(r"^(да|нет|конечно|хорошо|ага|угу|ну|так|ладно|верно)\b")
_DIGITS_ONLY = re.compile(r"^[\d\s,.\-–xх*]+$")
_WORD_RE = re.compile(r"[a-zа-я0-9]+")


def _plain_words(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


def _is_echo_question(text: str, prev_text: str) -> bool:
    """«Номер карты?» в ответ на «подскажите номер карты?»: переспрос, а не новый вопрос."""
    ws = _plain_words(text)
    prev = set(_plain_words(prev_text))
    if not ws or not text.strip().endswith("?"):
        return False
    # «Номер карты?» целиком повторяет чужие слова; «Как что?» короткий и цепляется за чужое слово
    return (len(ws) <= 4 and all(w in prev for w in ws)) or (len(ws) <= 2 and any(w in prev for w in ws))


def _unit_bias(text: str) -> float:
    """> 0: похоже на оператора, < 0: на клиента."""
    n = normalize(text)
    op = _cue_score(n, OPERATOR_CUES) + _cue_score(n, MONO_OPERATOR_CUES)
    cl = _cue_score(n, CLIENT_CUES) + _cue_score(n, MONO_CLIENT_CUES)
    score = 1.6 * op - 1.6 * cl
    if _DIGITS_ONLY.match(n) and any(ch.isdigit() for ch in n):
        score -= 0.6  # цифры обычно диктует клиент
    return score


def _switch_score(
    prev: list[dict],
    cur: list[dict],
    prev_prev: list[dict] | None,
    silence: float | None = None,
) -> float:
    """Чем больше, тем вероятнее, что cur говорит другой человек."""
    prev_text = _join_words(prev)
    cur_text = _join_words(cur)
    gap = float(cur[0]["start"]) - float(prev[-1]["end"])
    pause = max(gap, silence or 0.0)
    score = -0.5  # при прочих равных говорящий не меняется
    finished = bool(_SENT_END.search((prev[-1].get("text") or "").strip()))
    question = prev_text.endswith("?") and not (
        prev_prev is not None and _is_echo_question(prev_text, _join_words(prev_prev))
    )
    if not finished:
        # После запятой фраза точно продолжается. Без знака вовсе Whisper мог
        # просто потерять пунктуацию на длинном куске, так что штраф меньше.
        score -= 3.0 if (prev[-1].get("text") or "").strip().endswith((",", ":", ";", "-")) else 1.0
    if question:
        score += 2.5  # на вопрос отвечает другой
    if "seg" in prev[-1] and "seg" in cur[0]:
        score += 1.0 if prev[-1]["seg"] != cur[0]["seg"] else -1.5  # Whisper сам начал новый кусок
    if pause >= 1.0:
        score += 2.0
    elif pause >= 0.6:
        score += 1.5
    elif not question:
        score -= 1.5 if pause < 0.3 else 1.0
    if pause >= 0.3 and _ANSWER_START.match(normalize(cur_text)):
        score += 1.5
    if finished and not question and re.match(r"^а\s", normalize(cur_text)) and cur_text.endswith("?"):
        score += 1.5  # «А ...?» после чужого ответа: встречный вопрос, обычно другой человек
    if _DIGITS_ONLY.match(normalize(prev_text)) and _DIGITS_ONLY.match(normalize(cur_text)):
        score -= 3.0  # диктовка цифр продолжается
    return score


def _gap_silence(quiet: np.ndarray, step: float, end: float, start: float, stretch: float = 0.3) -> float:
    a = max(0, int((end - stretch) / step))
    b = min(len(quiet), int((start + stretch) / step) + 1)
    best = run = 0
    for q in quiet[a:b]:
        run = run + 1 if q else 0
        best = max(best, run)
    return best * step


def _viterbi_roles(bias: list[float], switch: list[float], first_op: float) -> list[int]:
    """1 = оператор, 0 = клиент. bias[i] за оператора, switch[i] за смену между i-1 и i."""
    n = len(bias)
    score = [[0.0, 0.0] for _ in range(n)]
    back = [[0, 0] for _ in range(n)]
    score[0][1] = bias[0] / 2 + first_op
    score[0][0] = -bias[0] / 2 - first_op
    for i in range(1, n):
        for s in (0, 1):
            emit = bias[i] / 2 if s == 1 else -bias[i] / 2
            stay = score[i - 1][s] - switch[i] / 2
            move = score[i - 1][1 - s] + switch[i] / 2
            if stay >= move:
                score[i][s], back[i][s] = stay + emit, s
            else:
                score[i][s], back[i][s] = move + emit, 1 - s
    state = 1 if score[-1][1] >= score[-1][0] else 0
    path = [state]
    for i in range(n - 1, 0, -1):
        state = back[i][state]
        path.append(state)
    return path[::-1]


def dialog_turns(
    words: list[dict],
    audio: np.ndarray | None = None,
    sr: int | None = None,
    operator_first: bool = True,
) -> list[dict]:
    """Моно-разметка по фразам: смена говорящего может быть и внутри куска Whisper."""
    clean = [w for w in words if (w.get("text") or "").strip()]
    if not clean:
        return []
    pauses = _silences(audio, sr) if audio is not None and sr else None
    units = _phrase_units(clean, pauses=pauses)
    bias = [_unit_bias(_join_words(u)) for u in units]
    voice = _weak_voice_bias(units, audio, sr) if audio is not None and sr else None
    quiet, step = _quiet_frames(audio, sr) if audio is not None and sr else (None, 0.02)
    switch = [0.0]
    for i in range(1, len(units)):
        sil = None
        if quiet is not None and len(quiet):
            sil = _gap_silence(quiet, step, float(units[i - 1][-1]["end"]), float(units[i][0]["start"]))
        switch.append(_switch_score(units[i - 1], units[i], units[i - 2] if i >= 2 else None, sil))
    first_op = 2.0 if operator_first else -2.0
    path = _viterbi_roles(bias, switch, first_op)
    if voice is not None:
        # Голос сам по себе ненадёжен, но ориентируем его по текстовой разметке
        # и добавляем как слабую подсказку, если он с ней в основном согласен.
        sign = voice_orientation(voice, path)
        if sign:
            mixed = [b + sign * v for b, v in zip(bias, voice)]
            path = _viterbi_roles(mixed, switch, first_op)
    out: list[dict] = []
    for u, st in zip(units, path):
        text = _join_words(u)
        if not text:
            continue
        role = "operator" if st == 1 else "client"
        out.append(
            {
                "start": float(u[0]["start"]),
                "end": float(u[-1]["end"]),
                "text": text,
                "role": role,
                "speaker": "A" if role == "operator" else "B",
                "source": "dialog",
            }
        )
    return merge_same_role(out, max_gap=3.5)


VOICE_HINT = 1.0  # вес голоса в моно-разметке по диалогу
VOICE_HINT_AGREE = 0.6  # минимум согласия голоса с текстом, чтобы ему верить


def _weak_voice_bias(units: list[list[dict]], audio: np.ndarray, sr: int) -> list[float] | None:
    feats = [voice_features(audio, sr, u[0]["start"], u[-1]["end"]) for u in units]
    idx = [i for i, f in enumerate(feats) if f is not None]
    if len(idx) < VOICE_MIN_UNITS:
        return None
    labels, _ = _two_means(np.array([feats[i] for i in idx]))
    if labels.min() == labels.max():
        return None
    out = [0.0] * len(units)
    for i, lab in zip(idx, labels):
        out[i] = VOICE_HINT if lab == 1 else -VOICE_HINT
    return out


def voice_orientation(voice: list[float], path: list[int]) -> int:
    """+1, если кластер «1» совпадает с оператором, -1 если наоборот, 0 если голос не помогает."""
    pairs = [(v > 0, p == 1) for v, p in zip(voice, path) if v != 0]
    if len(pairs) < VOICE_MIN_UNITS:
        return 0
    agree = sum(1 for a, b in pairs if a == b) / len(pairs)
    if agree >= VOICE_HINT_AGREE:
        return 1
    if agree <= 1 - VOICE_HINT_AGREE:
        return -1
    return 0


def from_whisper(
    raw_segments: list[dict],
    operator_first: bool = True,
    audio: np.ndarray | None = None,
    sr: int | None = None,
    stereo: np.ndarray | None = None,
    operator_channel: str = "auto",
) -> list[dict]:
    words: list[dict] = []
    for seg_no, seg in enumerate(raw_segments or []):
        if not isinstance(seg, dict):
            continue
        for word in seg.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = (word.get("text") or "").strip()
            if not token:
                continue
            try:
                words.append({"start": float(word["start"]), "end": float(word["end"]), "text": token, "seg": seg_no})
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
            return annotate_pauses(result, audio, sr)
    if words and audio is not None and sr:
        voiced = voice_turns(words, audio, sr, operator_first=operator_first)
        if voiced:
            return annotate_pauses(voiced, audio, sr)
    if words:
        return annotate_pauses(dialog_turns(words, audio, sr, operator_first=operator_first), audio, sr)
    regions = speech_regions(audio, sr) if audio is not None and sr else None
    if words:
        turns = utterances_from_words(words, regions)
    else:
        turns = merge_whisper_segments(raw_segments)
        turns = [{"start": t["start"], "end": t["end"], "text": correct_asr_text(t["text"])} for t in turns]
    return annotate_pauses(classify_turns(turns, operator_first=operator_first), audio, sr)
