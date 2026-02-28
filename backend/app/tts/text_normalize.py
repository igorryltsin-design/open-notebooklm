"""Normalize text for TTS so output stays Russian-friendly (no Latin, no raw digits)."""

from __future__ import annotations

import re

from app.config import get_pronunciation_overrides
from app.tts.ssml_pauses import strip_pause_markers

# Латинские буквы → произношение по-русски (для озвучки аббревиатур вроде API, SQL)
_LATIN_TO_RU: dict[str, str] = {
    "A": "эй", "B": "би", "C": "си", "D": "ди", "E": "и", "F": "эф", "G": "джи",
    "H": "эйч", "I": "ай", "J": "джей", "K": "кей", "L": "эл", "M": "эм", "N": "эн",
    "O": "оу", "P": "пи", "Q": "кью", "R": "ар", "S": "эс", "T": "ти", "U": "ю",
    "V": "ви", "W": "дабл-ю", "X": "икс", "Y": "уай", "Z": "зэд",
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи",
    "h": "эйч", "i": "ай", "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн",
    "o": "оу", "p": "пи", "q": "кью", "r": "ар", "s": "эс", "t": "ти", "u": "ю",
    "v": "ви", "w": "дабл-ю", "x": "икс", "y": "уай", "z": "зэд",
}

_DIGITS_TO_RU: dict[str, str] = {
    "0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре",
    "5": "пять", "6": "шесть", "7": "семь", "8": "восемь", "9": "девять",
}

# Латиница (слова) -> приблизительная русская транскрипция
_LATIN_WORD_MULTI: dict[str, str] = {
    "shch": "щ",
    "sch": "щ",
    "ch": "ч",
    "sh": "ш",
    "zh": "ж",
    "kh": "х",
    "ts": "ц",
    "ph": "ф",
    "th": "т",
    "ck": "к",
    "qu": "кв",
    "ya": "я",
    "yu": "ю",
    "yo": "ё",
    "ye": "е",
    "ee": "и",
    "oo": "у",
}

_LATIN_WORD_SINGLE: dict[str, str] = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}

_LATIN_WORD_EXCEPTIONS: dict[str, str] = {
    "telegram": "телеграмм",
    "youtube": "ютуб",
    "podcast": "подкаст",
}

_ACRONYM_RE = re.compile(r"^[A-Z]{2,8}$")
_MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}
_MONTH_NAME_TO_NUM = {v: k for k, v in _MONTHS_GEN.items()}
_DIGIT_WORDS = {
    "ноль": "0",
    "один": "1",
    "два": "2",
    "три": "3",
    "четыре": "4",
    "пять": "5",
    "шесть": "6",
    "семь": "7",
    "восемь": "8",
    "девять": "9",
}
_DIGIT_WORD_RUN_RE = re.compile(
    r"\b(?:ноль|один|два|три|четыре|пять|шесть|семь|восемь|девять)"
    r"(?:\s+(?:ноль|один|два|три|четыре|пять|шесть|семь|восемь|девять)){1,7}\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    r"(?P<prefix>\b(?:с|по|до|от)\s+)?(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>\d{4})\b",
    re.IGNORECASE,
)
_TEXTUAL_DATE_RE = re.compile(
    r"(?P<prefix>\b(?:с|по|до|от)\s+)?(?P<d>\d{1,2})\s+(?P<m>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(?P<y>\d{4})(?:\s*(?:года|году|год|г\.?))?\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(
    r"(?P<prefix>\b(?:в|к|до|с|по|от|из|о|об|при|на)\s+)?"
    r"(?P<year>[12]\d{3})\s*"
    r"(?P<suffix>года|году|год|г\.?|)?(?=\b|[^\w]|$)",
    re.IGNORECASE,
)
_INT_RE = re.compile(r"\d+")
_NUM_WORDS = {
    "ноль", "один", "одна", "два", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
    "семнадцать", "восемнадцать", "девятнадцать", "двадцать", "тридцать", "сорок", "пятьдесят",
    "шестьдесят", "семьдесят", "восемьдесят", "девяносто", "сто", "двести", "триста", "четыреста",
    "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот", "тысяча", "тысячи", "тысяч",
}
_NUM_PHRASE_RE = re.compile(
    r"\b(?P<num>(?:" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)).replace(" ", r"\ ") + r")(?:\s+(?:" +
    "|".join(sorted(_NUM_WORDS, key=len, reverse=True)).replace(" ", r"\ ") + r")){0,10})\s+"
    r"(?P<noun>сообщение|сообщения|сообщений|процент|процента|процентов|день|дня|дней|месяц|месяца|месяцев|"
    r"час|часа|часов|минута|минуты|минут|секунда|секунды|секунд|год|года|лет)\b",
    re.IGNORECASE,
)

_UNITS_MALE = [
    "ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
    "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]

_ORD_1_99_NOM = {
    1: "первый", 2: "второй", 3: "третий", 4: "четвертый", 5: "пятый", 6: "шестой",
    7: "седьмой", 8: "восьмой", 9: "девятый", 10: "десятый", 11: "одиннадцатый",
    12: "двенадцатый", 13: "тринадцатый", 14: "четырнадцатый", 15: "пятнадцатый",
    16: "шестнадцатый", 17: "семнадцатый", 18: "восемнадцатый", 19: "девятнадцатый",
    20: "двадцатый", 30: "тридцатый", 40: "сороковой", 50: "пятидесятый",
    60: "шестидесятый", 70: "семидесятый", 80: "восьмидесятый", 90: "девяностый",
}
_ORD_1_99_PREP = {
    1: "первом", 2: "втором", 3: "третьем", 4: "четвертом", 5: "пятом", 6: "шестом",
    7: "седьмом", 8: "восьмом", 9: "девятом", 10: "десятом", 11: "одиннадцатом",
    12: "двенадцатом", 13: "тринадцатом", 14: "четырнадцатом", 15: "пятнадцатом",
    16: "шестнадцатом", 17: "семнадцатом", 18: "восемнадцатом", 19: "девятнадцатом",
    20: "двадцатом", 30: "тридцатом", 40: "сороковом", 50: "пятидесятом",
    60: "шестидесятом", 70: "семидесятом", 80: "восьмидесятом", 90: "девяностом",
}
_ORD_1_99_GEN = {
    1: "первого", 2: "второго", 3: "третьего", 4: "четвертого", 5: "пятого", 6: "шестого",
    7: "седьмого", 8: "восьмого", 9: "девятого", 10: "десятого", 11: "одиннадцатого",
    12: "двенадцатого", 13: "тринадцатого", 14: "четырнадцатого", 15: "пятнадцатого",
    16: "шестнадцатого", 17: "семнадцатого", 18: "восемнадцатого", 19: "девятнадцатого",
    20: "двадцатого", 30: "тридцатого", 40: "сорокового", 50: "пятидесятого",
    60: "шестидесятого", 70: "семидесятого", 80: "восьмидесятого", 90: "девяностого",
}
_ORD_1_99_ACC = {
    1: "первое", 2: "второе", 3: "третье", 4: "четвертое", 5: "пятое", 6: "шестое",
    7: "седьмое", 8: "восьмое", 9: "девятое", 10: "десятое", 11: "одиннадцатое",
    12: "двенадцатое", 13: "тринадцатое", 14: "четырнадцатое", 15: "пятнадцатое",
    16: "шестнадцатое", 17: "семнадцатое", 18: "восемнадцатое", 19: "девятнадцатое",
    20: "двадцатое", 30: "тридцатое", 40: "сороковое", 50: "пятидесятое",
    60: "шестидесятое", 70: "семидесятое", 80: "восьмидесятое", 90: "девяностое",
}
_WORD_TO_NUM = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200,
    "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900,
}
_NOUN_FORMS = {
    "сообщение": ("сообщение", "сообщения", "сообщений"),
    "сообщения": ("сообщение", "сообщения", "сообщений"),
    "сообщений": ("сообщение", "сообщения", "сообщений"),
    "процент": ("процент", "процента", "процентов"),
    "процента": ("процент", "процента", "процентов"),
    "процентов": ("процент", "процента", "процентов"),
    "день": ("день", "дня", "дней"),
    "дня": ("день", "дня", "дней"),
    "дней": ("день", "дня", "дней"),
    "месяц": ("месяц", "месяца", "месяцев"),
    "месяца": ("месяц", "месяца", "месяцев"),
    "месяцев": ("месяц", "месяца", "месяцев"),
    "час": ("час", "часа", "часов"),
    "часа": ("час", "часа", "часов"),
    "часов": ("час", "часа", "часов"),
    "минута": ("минута", "минуты", "минут"),
    "минуты": ("минута", "минуты", "минут"),
    "минут": ("минута", "минуты", "минут"),
    "секунда": ("секунда", "секунды", "секунд"),
    "секунды": ("секунда", "секунды", "секунд"),
    "секунд": ("секунда", "секунды", "секунд"),
    "год": ("год", "года", "лет"),
    "года": ("год", "года", "лет"),
    "лет": ("год", "года", "лет"),
}


# Маркеры пауз для Silero — не трогать при замене латиницы
_PAUSE_MARKER = re.compile(r"\[PAUSE_[^\]]+\]", re.IGNORECASE)

# Русские гласные для конвертации "гласная+" → "гласная́" (U+0301 combining acute accent)
_RU_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_COMBINING_ACUTE = "\u0301"


def _is_latin_char(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z")


def _spell_latin_acronym(word: str) -> str:
    return "-".join(_LATIN_TO_RU.get(ch, ch) for ch in word)


def _transliterate_latin_word(word: str) -> str:
    if word.lower() in _LATIN_WORD_EXCEPTIONS:
        return _LATIN_WORD_EXCEPTIONS[word.lower()]
    lower = word.lower()
    out: list[str] = []
    i = 0
    while i < len(lower):
        matched = False
        for n in (4, 3, 2):
            if i + n <= len(lower):
                part = lower[i : i + n]
                repl = _LATIN_WORD_MULTI.get(part)
                if repl:
                    out.append(repl)
                    i += n
                    matched = True
                    break
        if matched:
            continue
        out.append(_LATIN_WORD_SINGLE.get(lower[i], lower[i]))
        i += 1
    return "".join(out)


def _int_to_words_1_999(n: int) -> str:
    if n < 20:
        return _UNITS_MALE[n]
    if n < 100:
        t, u = divmod(n, 10)
        return _TENS[t] + (f" {_UNITS_MALE[u]}" if u else "")
    h, rem = divmod(n, 100)
    if rem == 0:
        return _HUNDREDS[h]
    return f"{_HUNDREDS[h]} {_int_to_words_1_999(rem)}"


def _int_to_words(n: int) -> str:
    if n < 1000:
        return _int_to_words_1_999(n)
    if n < 1_000_000:
        th, rem = divmod(n, 1000)
        if th == 1:
            left = "одна тысяча"
        elif th == 2:
            left = "две тысячи"
        elif 3 <= th <= 4:
            left = f"{_int_to_words_1_999(th)} тысячи"
        else:
            left = f"{_int_to_words_1_999(th)} тысяч"
        return left if rem == 0 else f"{left} {_int_to_words_1_999(rem)}"
    return " ".join(_DIGITS_TO_RU[d] for d in str(n))


def _ordinal_1_99(n: int, case: str) -> str:
    if n in _ORD_1_99_NOM:
        if case == "prep":
            return _ORD_1_99_PREP[n]
        if case == "gen":
            return _ORD_1_99_GEN[n]
        if case == "acc":
            return _ORD_1_99_ACC[n]
        return _ORD_1_99_NOM[n]
    tens = (n // 10) * 10
    unit = n % 10
    if case == "prep":
        return f"{_TENS[tens // 10]} {_ORD_1_99_PREP[unit]}"
    if case == "gen":
        return f"{_TENS[tens // 10]} {_ORD_1_99_GEN[unit]}"
    if case == "acc":
        return f"{_TENS[tens // 10]} {_ORD_1_99_ACC[unit]}"
    return f"{_TENS[tens // 10]} {_ORD_1_99_NOM[unit]}"


def _year_words(year: int, case: str, with_noun: bool = True) -> str:
    if 1000 <= year <= 2099:
        if 2000 <= year <= 2099:
            tail = year - 2000
            if tail == 0:
                base = "двухтысячный" if case == "nom" else ("двухтысячном" if case == "prep" else "двухтысячного")
                if not with_noun:
                    return base
                return f"{base} {'год' if case == 'nom' else ('году' if case == 'prep' else 'года')}"
            ord_tail = _ordinal_1_99(tail, case)
            if with_noun:
                noun = "год" if case == "nom" else ("году" if case == "prep" else "года")
                return f"две тысячи {ord_tail} {noun}"
            return f"две тысячи {ord_tail}"
        # Для других диапазонов оставляем кардинальное чтение + слово "год".
        if with_noun:
            noun = "год" if case == "nom" else ("году" if case == "prep" else "года")
            return f"{_int_to_words(year)} {noun}"
        return _int_to_words(year)
    return _int_to_words(year)


def _replace_years(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        prefix = m.group("prefix") or ""
        suffix = (m.group("suffix") or "").lower()
        year = int(m.group("year"))
        case = "nom"
        if suffix in {"году"}:
            case = "prep"
        elif suffix in {"года"}:
            case = "gen"
        elif prefix.strip().lower() in {"в", "к", "о", "об", "при", "на", "по"}:
            case = "prep"
        elif prefix.strip().lower() in {"до", "с", "от", "из"}:
            case = "gen"
        return f"{prefix}{_year_words(year, case)}"

    return _YEAR_RE.sub(repl, text)


def _collapse_digit_word_runs(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        words = re.split(r"\s+", m.group(0).lower().strip())
        digits = "".join(_DIGIT_WORDS.get(w, "") for w in words)
        return digits if digits else m.group(0)
    return _DIGIT_WORD_RUN_RE.sub(repl, text)


def _day_ordinal(day: int, case: str) -> str:
    if day <= 0 or day > 31:
        return _int_to_words(day)
    return _ordinal_1_99(day, case)


def _replace_dates(text: str) -> str:
    def repl_numeric(m: re.Match[str]) -> str:
        prefix = m.group("prefix") or ""
        prefix_l = prefix.strip().lower()
        d = int(m.group("d"))
        mon = int(m.group("m"))
        y = int(m.group("y"))
        month = _MONTHS_GEN.get(mon)
        if not month:
            return m.group(0)
        day_case = "acc" if prefix_l == "по" else "gen"
        return f"{prefix}{_day_ordinal(d, day_case)} {month} {_year_words(y, 'gen')}"

    text = _NUMERIC_DATE_RE.sub(repl_numeric, text)

    def repl_textual(m: re.Match[str]) -> str:
        prefix = m.group("prefix") or ""
        prefix_l = prefix.strip().lower()
        d = int(m.group("d"))
        month_raw = m.group("m").lower()
        mon = _MONTH_NAME_TO_NUM.get(month_raw)
        y = int(m.group("y"))
        if not mon:
            return m.group(0)
        month = _MONTHS_GEN[mon]
        day_case = "acc" if prefix_l == "по" else "gen"
        return f"{prefix}{_day_ordinal(d, day_case)} {month} {_year_words(y, 'gen')}"

    return _TEXTUAL_DATE_RE.sub(repl_textual, text)


def _replace_common_symbols(text: str) -> str:
    text = text.replace("№", " номер ")
    text = re.sub(r"(?<=\d)\s*%", " процентов", text)
    text = text.replace("&", " и ")
    text = text.replace("—", " ")
    text = text.replace("–", " ")
    text = text.replace("…", ".")
    text = text.replace("/", " ")
    return text


def _apply_pronunciation_overrides(text: str) -> str:
    overrides = get_pronunciation_overrides()
    if not overrides:
        return text

    out = text
    for src, dst in sorted(overrides.items(), key=lambda kv: len(kv[0]), reverse=True):
        key = re.sub(r"\s+", " ", src.strip())
        val = dst.strip()
        if not key or not val:
            continue
        # Поддерживаем фразы: пробелы в ключе матчатся как один или более пробелов в тексте.
        body = re.escape(key).replace(r"\ ", r"\s+")

        # Для "словесных" ключей (включая многословные) ограничиваем по границам токенов,
        # чтобы не срабатывать внутри других слов.
        if re.match(r"^[\w\-. ]+$", key, re.IGNORECASE):
            pattern = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
        else:
            pattern = re.compile(body, re.IGNORECASE)
        out = pattern.sub(val, out)
    return out


def _words_to_int(num_words: str) -> int | None:
    words = [w.lower() for w in re.split(r"\s+", num_words.strip()) if w]
    if not words:
        return None
    total = 0
    current = 0
    for w in words:
        if w in {"тысяча", "тысячи", "тысяч"}:
            if current == 0:
                current = 1
            total += current * 1000
            current = 0
            continue
        val = _WORD_TO_NUM.get(w)
        if val is None:
            return None
        current += val
    return total + current


def _noun_form_by_number(n: int, forms: tuple[str, str, str]) -> str:
    n_abs = abs(n)
    n_mod100 = n_abs % 100
    n_mod10 = n_abs % 10
    if 11 <= n_mod100 <= 14:
        return forms[2]
    if n_mod10 == 1:
        return forms[0]
    if n_mod10 in {2, 3, 4}:
        return forms[1]
    return forms[2]


def _agree_known_nouns(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        num_words = m.group("num")
        noun = m.group("noun")
        n = _words_to_int(num_words)
        if n is None:
            return m.group(0)
        forms = _NOUN_FORMS.get(noun.lower())
        if not forms:
            return m.group(0)
        return f"{num_words} {_noun_form_by_number(n, forms)}"
    return _NUM_PHRASE_RE.sub(repl, text)


def plus_stress_to_unicode(text: str) -> str:
    """Заменяет маркер ударения 'гласная+' на гласную с combining acute accent (U+0301).

    Модель Silero из v5_ru.pt может озвучивать '+' буквально. Стандарт для русской
    типографики ударения — символ ́ после ударной гласной; многие TTS его понимают.
    """
    if not text or "+" not in text:
        return text
    result: list[str] = []
    i = 0
    while i < len(text):
        if i + 1 < len(text) and text[i] in _RU_VOWELS and text[i + 1] == "+":
            result.append(text[i])
            result.append(_COMBINING_ACUTE)
            i += 2  # пропустить гласную и +
            continue
        result.append(text[i])
        i += 1
    return "".join(result)


def latin_to_russian_readable_keep_pauses(text: str, digits: bool = True) -> str:
    """Back-compat: normalizes text and removes [PAUSE_*] markers."""
    if not text:
        return text
    clean = strip_pause_markers(text)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return latin_to_russian_readable(clean, digits=digits)


def latin_to_russian_readable(text: str, digits: bool = True) -> str:
    """Replace Latin letters (and optionally digits) with Russian pronunciation for TTS.

    Silero v5 is trained mainly on Russian; Latin characters are often skipped.
    This converts e.g. 'API' -> 'эй пи ай', 'SQL' -> 'эс кью эл'. Leaves Cyrillic and punctuation unchanged.
    """
    if not text:
        return text
    text = _apply_pronunciation_overrides(text)
    text = _replace_common_symbols(text)
    text = _collapse_digit_word_runs(text)
    text = _replace_dates(text)
    text = _replace_years(text)
    tokens: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if _is_latin_char(char):
            start = i
            while i < len(text) and _is_latin_char(text[i]):
                i += 1
            word = text[start:i]
            # Короткие UPPERCASE-токены обычно аббревиатуры (SQL, API, GPT).
            if _ACRONYM_RE.match(word):
                tokens.append(_spell_latin_acronym(word))
            else:
                tokens.append(_transliterate_latin_word(word))
            continue
        if digits and char.isdigit():
            start = i
            while i < len(text) and text[i].isdigit():
                i += 1
            number = int(text[start:i])
            tokens.append(_int_to_words(number))
            continue
        tokens.append(char)
        i += 1
    out = "".join(tokens)
    out = re.sub(r"[\[\]{}<>|*_=~^`]+", " ", out)
    out = _agree_known_nouns(out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out
