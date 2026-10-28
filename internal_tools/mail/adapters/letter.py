"""What a letter says: its text and its files, from the MIME tree as mail clients see it.

The text is every text part that is not a file, plain text before HTML (in a
multipart/alternative the plain one, unless it is empty). A file is a part sent as an
attachment, a part with a file name (inline pictures too), a forwarded letter
(message/rfc822, kept as .eml) or anything that is not text — at any depth, not only
the top level. Charsets are what the sender said, else what Russian mail usually is."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import EmailMessage, Message
from html.parser import HTMLParser


# A label that is wrong, unknown or missing: these, in order; the last one always decodes.
_FALLBACKS = ("utf-8", "cp1251")


@dataclass
class File:
    # The sender's name for it, if any.
    filename: str | None
    content: bytes
    content_type: str


def decode_text(payload: bytes, charset: str | None) -> str:
    for label in (charset, *_FALLBACKS):
        if not label:
            continue

        try:
            return payload.decode(label)

        except (LookupError, UnicodeError):
            continue

    return payload.decode("utf-8", errors="replace")


def text_and_files(message: Message) -> tuple[str, list[File]]:
    plain: list[str] = []
    html: list[str] = []
    files: list[File] = []

    _walk(message, plain, html, files)

    texts = [text for text in plain if text.strip()] or [html_to_text(text) for text in html]

    return "\n\n".join(text.strip() for text in texts if text.strip()), files


def _walk(part: Message, plain: list[str], html: list[str], files: list[File]) -> None:
    if _is_file(part):
        files.append(_file(part))
        return

    if part.is_multipart():
        children = [child for child in part.get_payload() if isinstance(child, Message)]

        if part.get_content_subtype() == "alternative" and children:
            children = [_alternative(children)]

        for child in children:
            _walk(child, plain, html, files)

        return

    text = _text(part)
    (plain if part.get_content_type() == "text/plain" else html).append(text)


def _is_file(part: Message) -> bool:
    if part.get_content_disposition() == "attachment" or part.get_filename():
        return True

    if part.get_content_type() == "message/rfc822":
        return True

    return not part.is_multipart() and part.get_content_type() not in ("text/plain", "text/html")


def _alternative(children: list[Message]) -> Message:
    """The plain version, unless it says nothing; else the last (the richest) one."""
    for child in children:
        if child.get_content_type() == "text/plain" and not _is_file(child) and _text(child).strip():
            return child

    return children[-1]


def _text(part: Message) -> str:
    return decode_text(part.get_payload(decode=True) or b"", part.get_content_charset())


def _file(part: Message) -> File:
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload()[0] if isinstance(part.get_payload(), list) else None
        content = inner.as_bytes() if inner is not None else b""
        subject = str(inner.get("Subject") or "").strip() if inner is not None else ""
        return File(part.get_filename() or f"{subject or 'Письмо'}.eml", content, "message/rfc822")

    if part.is_multipart():
        return File(part.get_filename(), part.as_bytes(), part.get_content_type())

    return File(part.get_filename(), part.get_payload(decode=True) or b"", part.get_content_type())


class _Text(HTMLParser):
    """HTML as a person reads it: no scripts or styles, paragraphs on their own lines,
    a link as `text (address)`."""

    _SKIP = {"script", "style", "head", "title"}
    _BLOCK = {
        "p", "div", "br", "tr", "li", "ul", "ol", "table", "blockquote", "hr", "section", "article",
        "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
    }


    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        # The link being read: its address and its text so far.
        self.link: tuple[str, list[str]] | None = None


    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag in self._BLOCK:
            self._emit("\n")
        elif tag in ("td", "th"):
            self._emit(" ")
        elif tag == "a":
            self.link = (dict(attrs).get("href") or "").strip(), []


    def handle_startendtag(self, tag, attrs):
        if tag in self._BLOCK:
            self._emit("\n")


    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self._BLOCK:
            self._emit("\n")
        elif tag == "a" and self.link is not None:
            href, words = self.link
            self.link = None
            text = " ".join("".join(words).split())
            shown = re.sub(r"[\x00-\x20]+", "", href).lower()

            if not href or shown.startswith(("#", "mailto:", "javascript:")) or text == href:
                self._emit(text or href)
            else:
                self._emit(f"{text} ({href})" if text else href)


    def handle_data(self, data):
        if self.skip:
            return

        if self.link is not None:
            self.link[1].append(data)
        else:
            self._emit(data)


    def _emit(self, text: str) -> None:
        self.out.append(text)


def html_to_text(html: str) -> str:
    # The parser takes any text: broken HTML is read as far as it goes, never refused.
    parser = _Text()
    parser.feed(html)
    parser.close()

    lines = (" ".join(line.split()) for line in "".join(parser.out).replace("\xa0", " ").splitlines())

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def header_text(message: EmailMessage, name: str, decoded: str) -> str:
    """A header the policy could not decode (raw 8-bit bytes in a charset it does not
    know — old Russian mailers send cp1251 so): from its raw bytes, by our fallbacks."""
    if "�" not in decoded:
        return decoded

    for key, raw in message.raw_items():
        if key.lower() == name.lower() and isinstance(raw, str):
            try:
                data = raw.encode("ascii", "surrogateescape")

            except UnicodeError:
                return decoded

            return " ".join(decode_text(data, None).split())

    return decoded
