"""Strip emojis to text equivalents + render Week-2 handoff MD to PDF via pandoc+xelatex.

Adapted from _render_pdf.py for the W2 (Phase 3 W1) handoff.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "handoff_to_koi_w2_2026-05-20.md"
TMP = HERE / "handoff_to_koi_w2_2026-05-20.print.md"
PDF = HERE / "handoff_to_koi_w2_2026-05-20.pdf"

# Specific mappings for symbols actually used in this doc
EXPLICIT = {
    "✅": "[DONE]",
    "⚠️": "[!]",
    "⚠":  "[!]",
    "⏸️": "[TODO]",
    "⏸":  "[TODO]",
    "⚙️": "[WIP]",
    "⚙":  "[WIP]",
    "⭐": "*",
    "🥇": "Tier 1.",
    "🥈": "Tier 2.",
    "🥉": "Tier 3.",
    "🔗": "Link:",
    "🎯": "",
    "📁": "",
    "📋": "",
    "💡": "Note:",
    "📄": "",
    "📦": "",
    "📊": "",
    "📷": "",
    "🛠": "",
    "🛠️": "",
    "🎁": "",
    "🔧": "",
    "🚦": "",
    "🚀": "",
    "🚫": "[BLOCK]",
    "🔍": "",
    "🏆": "[WIN]",
    "🤔": "",
    "🅐": "(A)",
    "🅑": "(B)",
    "🅒": "(C)",
    "🅓": "(D)",
    "🟢": "(green)",
    "🟡": "(yellow)",
    "🟠": "(orange)",
    "🔴": "(red)",
    "👀": "",
    "🎨": "",
    "👋": "",
    "🎉": "[!]",
    "📝": "",
    "→": "->",
    "←": "<-",
    "↓": "v",
    "↑": "^",
    "↔": "<->",
    "▶": ">",
    "◀": "<",
    "▼": "v",
    "▲": "^",
    "│": "|",
    "├": "+",
    "└": "+",
    "─": "-",
    "━": "=",
    "≥": ">=",
    "≤": "<=",
    "≈": "~",
    "×": "x",
    "÷": "/",
    "·": ".",
    "•": "-",
}


def clean(text: str) -> str:
    # First, apply explicit replacements (longest-first to handle ⚠️ before ⚠)
    for k in sorted(EXPLICIT.keys(), key=len, reverse=True):
        text = text.replace(k, EXPLICIT[k])

    # Then strip any remaining chars in emoji blocks (excluding CJK ideographs 0x4E00-0x9FFF, common punct)
    def is_emoji(c):
        cp = ord(c)
        return (
            (0x2600 <= cp <= 0x27BF)
            or (0x1F000 <= cp <= 0x1FFFF)
            or cp == 0xFE0F
            or cp == 0x200D  # zero-width joiner
        )

    text = "".join("" if is_emoji(c) else c for c in text)
    return text


def main() -> int:
    if not SRC.exists():
        print(f"missing: {SRC}", file=sys.stderr)
        return 1
    raw = SRC.read_text(encoding="utf-8")
    cleaned = clean(raw)
    TMP.write_text(cleaned, encoding="utf-8")
    print(f"wrote cleaned MD: {TMP} ({len(cleaned)} chars)")

    pandoc = "D:/miniconda3/envs/LLMs/Library/bin/pandoc.exe"
    cmd = [
        pandoc,
        str(TMP),
        "-o", str(PDF),
        "--pdf-engine=xelatex",
        "-V", "geometry:margin=2cm",
        "-V", "mainfont=Cambria",
        "-V", "CJKmainfont=Microsoft YaHei",
        "-V", "monofont=Consolas",
        "--highlight-style=tango",
        "--resource-path", str(HERE),
    ]
    print(f"running pandoc -> {PDF.name} ...")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    print("STDOUT:", r.stdout)
    if r.stderr:
        warns = [ln for ln in r.stderr.splitlines() if "Missing character" in ln]
        errs = [ln for ln in r.stderr.splitlines() if "Missing character" not in ln and ln.strip()]
        print(f"(suppressed {len(warns)} 'Missing character' warnings)")
        if errs:
            print("STDERR:", "\n".join(errs))
    if PDF.exists():
        print(f"PDF size: {PDF.stat().st_size / 1024:.1f} KB")
        return 0
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
