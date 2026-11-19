"""Поиск по смыслу: реплика оператора сравнивается с примерами фраз критерия.

Словарь фраз в app/checklist.py ловит только точные формулировки. Если оператор сказал
то же самое другими словами («это не в моей компетенции» вместо «это не ко мне»), фраза
не найдётся. Тогда реплика разбивается на предложения, каждое переводится в вектор моделью
intfloat/multilingual-e5-small, и вектор сравнивается с векторами примеров (косинус).

Правила:
- точная фраза всегда главнее, поиск по смыслу только добавляет то, что словарь пропустил;
- предложение засчитывается, если похожесть на пример критерия не ниже порога критерия и
  заметно выше, чем на любую обычную «нейтральную» фразу оператора (NEUTRAL), и этот
  критерий для предложения самый близкий из всех (иначе «добрый день» засчиталось бы прощанием);
- для нарушений (грубость, отфутболивание) пороги строже: ложный штраф хуже пропуска;
- модель не установлена или выключена (SEMANTIC_MATCH=off) - работают только фразы.

Префиксы e5: для сравнения коротких фраз между собой (симметричная задача) авторы модели
советуют "query: " с обеих сторон. "passage: " нужен для поиска документов по вопросу,
здесь он не подходит. Поэтому обе стороны кодируются с QUERY_PREFIX.

Модель работает на CPU через onnxruntime и tokenizers (они уже стоят вместе с faster-whisper),
torch не нужен. Файлы модели: data/models/multilingual-e5-small/{model.onnx,tokenizer.json},
скачать: python scripts/download_models.py --only e5
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import settings
from app.diarize import normalize

log = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"
QUERY_PREFIX = "query: "
MODEL_FILES = ("model.onnx", "tokenizer.json")

# Примеры по смыслу. Это не словарь точных фраз: модель ищет похожие по смыслу предложения.
# Отложенная проверочная выборка лежит в tests/fixtures/paraphrases_heldout.json, её фразы
# сюда не добавлять, иначе оценка обобщения станет нечестной.
EXAMPLES: dict[str, list[str]] = {
    "greeting": [
        "здравствуйте", "добрый день", "доброе утро", "добрый вечер", "приветствую вас",
        "рада вас слышать", "доброго времени суток",
    ],
    "intro": [
        "меня зовут анна", "с вами говорит оператор дмитрий", "на связи специалист контактного центра",
        "вас приветствует консультант банка", "я ваш персональный менеджер", "моё имя елена",
    ],
    "help_offer": [
        "чем могу помочь", "чем могу быть полезна", "какой у вас вопрос", "я вас слушаю",
        "что вас интересует", "по какому вопросу вы звоните", "расскажите, что случилось",
    ],
    "closing": [
        "всего доброго", "до свидания", "хорошего дня", "спасибо за обращение",
        "обращайтесь, будем рады помочь", "благодарю за звонок, до свидания", "удачного вам вечера",
    ],
    "empathy": [
        "понимаю вас", "сожалею, что так вышло", "мне очень жаль, что вы столкнулись с этим",
        "понимаю, как это неприятно", "давайте вместе разберемся", "я понимаю ваше беспокойство",
        "приношу извинения за неудобства",
    ],
    "resolution_lang": [
        "заявка оформлена", "карта заблокирована", "я все сделала, вопрос решен", "перевод выполнен",
        "откройте в приложении раздел платежи", "могу предложить вам вклад", "я зарегистрировала обращение",
        "подключила услугу", "вам подойдет кредитная карта", "документы приняты",
    ],
    "next_steps": [
        "ответ придет в течение трех дней", "ожидайте смс", "мы перезвоним завтра",
        "приходите в офис с паспортом", "деньги поступят не позднее пятницы", "номер обращения придет на почту",
        "в личном кабинете появится статус заявки", "специалист свяжется с вами",
    ],
    "confirm": [
        "остались ли у вас вопросы", "могу еще чем-то помочь", "вопрос решен",
        "я ответила на ваш вопрос", "все понятно", "есть еще вопросы",
    ],
    "no_hanging": [
        "это не ко мне", "я не знаю", "все написано на сайте", "смотрите на сайте",
        "звоните на горячую линию", "это не наш отдел", "менеджер вам скажет", "ничем не могу помочь",
        "перезвоните сами потом", "это не входит в мои обязанности",
    ],
    "no_rudeness": [
        "это не моя проблема", "сами виноваты", "отстаньте от меня", "не грузите меня",
        "вы что, издеваетесь", "сколько можно повторять", "читайте договор", "мне все равно",
        "вы вообще слушаете, что я говорю", "не тупите",
    ],
}

# Обычные деловые фразы оператора, которые не относятся ни к одному критерию. Нужны для
# защиты от ложных срабатываний: у e5 все русские фразы похожи друг на друга на 0,8 и выше,
# поэтому важна не сама похожесть, а насколько она выше, чем на нейтральную фразу.
NEUTRAL: list[str] = [
    "ставка по вкладу составляет восемь процентов", "минимальная сумма десять тысяч рублей",
    "кэшбэк начисляется раз в месяц", "лимит по карте сто тысяч", "карта действует пять лет",
    "сейчас посмотрю информацию", "одну минуту", "назовите, пожалуйста, дату рождения",
    "продиктуйте последние четыре цифры карты", "у вас есть кредитная история",
    "комиссия за перевод не взимается", "срок кредита до пяти лет", "проценты капитализируются",
    "по ипотеке нужен первоначальный взнос", "уточните сумму платежа", "да, конечно",
    "нет, такой услуги нет", "это зависит от тарифа", "обслуживание бесплатное",
    "вы клиент нашего банка", "какой у вас тип карты", "платеж прошел вчера вечером",
    "приложение обновилось", "это стандартные условия", "секундочку, проверяю",
]

# Порог похожести и минимальный отрыв от нейтральных фраз. Подобраны на примерах выше
# (оставляя по одному примеру) и нейтральных фразах, а не на отложенной выборке.
THRESHOLDS: dict[str, float] = {
    "greeting": 0.88, "intro": 0.87, "help_offer": 0.87, "closing": 0.87, "empathy": 0.87,
    "resolution_lang": 0.88, "next_steps": 0.87, "confirm": 0.88,
    "no_hanging": 0.89, "no_rudeness": 0.89,
}
MARGIN = 0.04
NEGATIVE = {"no_hanging", "no_rudeness"}
NEGATIVE_MARGIN = 0.05

_SENT_RE = re.compile(r"[^.!?…;]+[.!?…;]*")


@dataclass
class SemanticHit:
    text: str  # предложение как в исходной реплике (для подсветки)
    norm: str  # то же после normalize (как фразы в правилах)
    score: float
    example: str
    turn_index: int


class OnnxE5Encoder:
    """multilingual-e5-small в ONNX: среднее по токенам + L2-нормировка."""

    def __init__(self, model_dir: Path, threads: int = 0):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=128)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        opts = ort.SessionOptions()
        if threads > 0:
            opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(model_dir / "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        enc = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feed)[0]
        vec = (hidden * mask[..., None]).sum(axis=1) / np.maximum(mask.sum(axis=1, keepdims=True), 1)
        return vec / np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12)


_lock = threading.Lock()
_encoder = None
_encoder_tried = False
_example_cache: dict[tuple, np.ndarray] = {}
_text_cache: dict[str, np.ndarray] = {}
_TEXT_CACHE_MAX = 20000


def model_dir() -> Path:
    raw = (settings.semantic_model_dir or "").strip()
    return Path(raw).expanduser() if raw else settings.models_dir / "multilingual-e5-small"


def model_installed() -> bool:
    d = model_dir()
    return all((d / f).is_file() for f in MODEL_FILES)


def set_encoder(encoder) -> None:
    """Подменить кодировщик (тесты) или сбросить: None - снова грузить модель при первом вызове."""
    global _encoder, _encoder_tried
    with _lock:
        _encoder = encoder
        _encoder_tried = encoder is not None
        _example_cache.clear()
        _text_cache.clear()


def mode() -> str:
    value = (settings.semantic_match or "off").strip().lower()
    return value if value in ("off", "auto", "on") else "off"


def get_encoder():
    """Кодировщик или None. Ничего не скачивает: модель кладётся заранее."""
    global _encoder, _encoder_tried
    if mode() == "off":
        return None
    if _encoder is not None or _encoder_tried:
        return _encoder
    with _lock:
        if _encoder is None and not _encoder_tried:
            _encoder_tried = True
            if not model_installed():
                lvl = logging.WARNING if mode() == "on" else logging.INFO
                log.log(lvl, "Поиск по смыслу выключен: нет файлов модели в %s", model_dir())
                return None
            try:
                _encoder = OnnxE5Encoder(model_dir(), settings.semantic_threads)
                log.info("Поиск по смыслу: модель %s загружена", MODEL_NAME)
            except Exception as exc:  # noqa: BLE001 - любая ошибка модели = работаем по фразам
                log.warning("Поиск по смыслу выключен: модель не загрузилась (%s)", exc)
                _encoder = None
    return _encoder


def enabled() -> bool:
    return get_encoder() is not None


def _embed(encoder, texts: list[str]) -> np.ndarray:
    """Векторы с кэшем по тексту. Тексты уже нормализованы, префикс добавляется здесь."""
    missing = [t for t in dict.fromkeys(texts) if t not in _text_cache]
    if missing:
        vecs = np.asarray(encoder.encode([QUERY_PREFIX + t for t in missing]), dtype=np.float32)
        if len(_text_cache) + len(missing) > _TEXT_CACHE_MAX:
            _text_cache.clear()
        for t, v in zip(missing, vecs):
            _text_cache[t] = v
    return np.stack([_text_cache[t] for t in texts]) if texts else np.zeros((0, 1), dtype=np.float32)


def _examples(encoder, key: tuple[str, ...]) -> np.ndarray:
    cached = _example_cache.get((id(encoder), key))
    if cached is None:
        cached = np.asarray(encoder.encode([QUERY_PREFIX + t for t in key]), dtype=np.float32)
        _example_cache[(id(encoder), key)] = cached
    return cached


def sentences(text: str) -> list[tuple[str, str]]:
    """(как в тексте, нормализованное) для каждого предложения реплики, не короче одного слова."""
    out = []
    for m in _SENT_RE.finditer(text or ""):
        raw = m.group(0).strip().rstrip(".!?…;").strip()
        norm = normalize(raw)
        if norm and len(norm) >= 3:
            out.append((raw, norm))
    return out


class Matcher:
    """Предложения реплик оператора одного звонка, векторы считаются один раз."""

    def __init__(self, turns: list[dict], encoder=None):
        self.encoder = encoder if encoder is not None else get_encoder()
        self.items: list[tuple[int, str, str]] = []
        for i, t in enumerate(turns):
            if t.get("role") != "operator":
                continue
            for raw, norm in sentences(str(t.get("text") or "")):
                self.items.append((i, raw, norm))
        self._vecs = None
        self._neutral = None
        self._owner = None

    @property
    def active(self) -> bool:
        return self.encoder is not None and bool(self.items)

    def _prepare(self) -> None:
        if self._vecs is None:
            self._vecs = _embed(self.encoder, [n for _i, _r, n in self.items])
            neutral = _examples(self.encoder, tuple(NEUTRAL))
            self._neutral = (self._vecs @ neutral.T).max(axis=1)
            # Какому критерию предложение ближе всего. «Добрый день» похоже и на «хорошего дня»,
            # но ближе к приветствию, значит прощанием не считается.
            labels, best = [], None
            for cid, ex in EXAMPLES.items():
                sims = (self._vecs @ _examples(self.encoder, tuple(ex)).T).max(axis=1)
                labels.append(cid)
                best = sims[None, :] if best is None else np.vstack([best, sims[None, :]])
            self._owner = [labels[int(k)] for k in best.argmax(axis=0)] if best is not None else []

    def find(self, cid: str, turn_indexes: set[int] | None = None, examples: list[str] | None = None) -> SemanticHit | None:
        if not self.active:
            return None
        ex = tuple(examples if examples is not None else EXAMPLES.get(cid, ()))
        if not ex:
            return None
        self._prepare()
        sims = self._vecs @ _examples(self.encoder, ex).T
        threshold = THRESHOLDS.get(cid, 0.88) + float(settings.semantic_threshold_offset)
        margin = NEGATIVE_MARGIN if cid in NEGATIVE else MARGIN
        best: SemanticHit | None = None
        for row, (ti, raw, norm) in enumerate(self.items):
            if turn_indexes is not None and ti not in turn_indexes:
                continue
            j = int(sims[row].argmax())
            score = float(sims[row, j])
            if score < threshold or score - float(self._neutral[row]) < margin:
                continue
            if examples is None and self._owner and self._owner[row] != cid:
                continue
            if best is None or score > best.score:
                best = SemanticHit(text=raw, norm=norm, score=round(score, 3), example=ex[j], turn_index=ti)
        return best


def classify(text: str, cid: str, encoder=None) -> SemanticHit | None:
    """Одна фраза оператора: сработал бы критерий по смыслу (для оценки и тестов)."""
    return Matcher([{"role": "operator", "text": text}], encoder=encoder).find(cid)
