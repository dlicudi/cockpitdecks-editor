"""Lightweight syntax highlighters for YAML and key=value blocks."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


# Pre-compile all regexes at module load time so highlightBlock never creates objects
_YAML_KEY_RE = QRegularExpression(r"^(\s*)([^#:\n\"'\[\]{},|>]+?)(\s*:)")

_YAML_RULES: list[tuple[QRegularExpression, QTextCharFormat]] = [
    # Comment
    (QRegularExpression(r"#[^\n]*"),                                      _fmt("#94a3b8", italic=True)),
    # Double-quoted string
    (QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"'),                     _fmt("#16a34a")),
    # Single-quoted string
    (QRegularExpression(r"'[^']*'"),                                      _fmt("#16a34a")),
    # Bare value after ': ' that looks like a path/command (not a key)
    (QRegularExpression(r"(?<=:\s)[a-zA-Z][a-zA-Z0-9_/.\-\[\]*]*(?=\s*$)"), _fmt("#0369a1")),
    # Numbers
    (QRegularExpression(r"\b-?\d+(\.\d+)?\b"),                            _fmt("#9333ea")),
    # Booleans / null
    (QRegularExpression(r"\b(true|false|yes|no|null|~)\b"),               _fmt("#dc2626", bold=True)),
    # List bullet
    (QRegularExpression(r"^\s*-(?=\s)"),                                  _fmt("#f59e0b", bold=True)),
    # Anchor / alias
    (QRegularExpression(r"[&*][A-Za-z_]\w*"),                             _fmt("#db2777")),
]

_YAML_KEY_FMT = _fmt("#1e40af", bold=True)


class YamlHighlighter(QSyntaxHighlighter):
    """Highlights YAML keys, strings, numbers, booleans, and comments."""

    def highlightBlock(self, text: str) -> None:
        # Keys: highlight capture group 2 (the key name) only
        it = _YAML_KEY_RE.globalMatch(text)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(2), m.capturedLength(2), _YAML_KEY_FMT)

        # All other rules apply to their full match
        for pattern, fmt in _YAML_RULES:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


_KV_COMMENT_FMT = _fmt("#94a3b8", italic=True)
_KV_KEY_FMT     = _fmt("#0369a1")
_KV_EQ_FMT      = _fmt("#64748b", bold=True)
_KV_NUM_FMT     = _fmt("#9333ea")
_KV_STR_FMT     = _fmt("#16a34a")


class KeyValueHighlighter(QSyntaxHighlighter):
    """Highlights 'dataref/name = value' lines (used in the fake-datarefs editor)."""

    def highlightBlock(self, text: str) -> None:
        if text.strip().startswith("#"):
            self.setFormat(0, len(text), _KV_COMMENT_FMT)
            return
        eq = text.find("=")
        if eq == -1:
            return
        self.setFormat(0, eq, _KV_KEY_FMT)
        self.setFormat(eq, 1, _KV_EQ_FMT)
        val = text[eq + 1:]
        val_start = eq + 1 + len(val) - len(val.lstrip())
        val_len = len(text) - val_start
        if val_len <= 0:
            return
        try:
            float(val.strip())
            self.setFormat(val_start, val_len, _KV_NUM_FMT)
        except ValueError:
            self.setFormat(val_start, val_len, _KV_STR_FMT)
