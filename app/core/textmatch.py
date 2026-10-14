# Поиск выделенного в браузере текста в исходном HTML слайда.
# Сравнение идёт по тексту без пробельных символов (NBSP, переносы строк между блоками,
# мягкие переносы не влияют), а наружу отдаётся точный кусок сырого HTML для правки.
import html
import re
from html.parser import HTMLParser

SKIP_TAGS = {"style", "script", "title", "head"}
INVISIBLE = {"­", "​", "‌", "‍", "﻿"}


def _norm_char(c):
    if c.isspace() or c in INVISIBLE:
        return ""
    return c


def normalize(text):
    return "".join(_norm_char(c) for c in html.unescape(text or ""))


class _Indexer(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.line_starts = [0] + [m.end() for m in re.finditer(r"\n", raw)]
        self.skip = 0
        self.chars = []  # (символ, raw_start, raw_end)

    def _offset(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1

    def _add(self, decoded, start, end):
        if self.skip:
            return
        for c in decoded:
            n = _norm_char(c)
            if n:
                self.chars.append((n, start, end))

    def handle_data(self, data):
        if self.skip:
            return
        start = self._offset()
        for i, c in enumerate(data):
            n = _norm_char(c)
            if n:
                self.chars.append((n, start + i, start + i + 1))

    def _ref(self, decoded):
        start = self._offset()
        end = self.raw.index(";", start) + 1 if self.raw.find(";", start, start + 12) != -1 else start + 1
        self._add(decoded, start, end)

    def handle_entityref(self, name):
        self._ref(html.unescape("&%s;" % name))

    def handle_charref(self, name):
        self._ref(html.unescape("&#%s;" % name))


def find(raw_html, text, prefix="", suffix=""):
    """Возвращает список совпадений: [{'raw': кусок HTML, 'start', 'end', 'line'}].
    Если текст встречается несколько раз — фильтрует по prefix/suffix."""
    idx = _Indexer(raw_html)
    idx.feed(raw_html)
    idx.close()
    flat = "".join(c for c, _, _ in idx.chars)
    key = normalize(text)
    if not key:
        return []
    hits = [m.start() for m in re.finditer(re.escape(key), flat)]
    if len(hits) > 1:
        pre, suf = normalize(prefix)[-40:], normalize(suffix)[:40]
        narrowed = [h for h in hits
                    if (not pre or flat[:h].endswith(pre)) and (not suf or flat[h + len(key):].startswith(suf))]
        if narrowed:
            hits = narrowed
    out = []
    for h in hits:
        start, end = idx.chars[h][1], idx.chars[h + len(key) - 1][2]
        out.append({"raw": raw_html[start:end], "start": start, "end": end,
                    "line": raw_html.count("\n", 0, start) + 1})
    return out


def visible_text(raw_html):
    """Видимый текст слайда (для проверки, что правка не снесла содержимое)."""
    idx = _Indexer(raw_html)
    idx.feed(raw_html)
    idx.close()
    return "".join(c for c, _, _ in idx.chars)
