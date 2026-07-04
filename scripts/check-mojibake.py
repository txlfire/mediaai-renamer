"""Semantic mojibake detector for repository text files.

The encoding checker already catches BOM, mixed EOL, and a few suspicious
characters. This script adds a semantic guard for the common accident where a
UTF-8 file is read and written through a GBK-oriented PowerShell pipeline.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


TEXT_EXTENSIONS = {
    ".py",
    ".ts",
    ".vue",
    ".css",
    ".md",
    ".txt",
    ".json",
    ".ps1",
    ".sh",
    ".html",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".env",
    ".editorconfig",
    ".gitattributes",
}

SPECIAL_TEXT_FILES = {".editorconfig", ".gitattributes"}

# 常见中文字符用于判断“反向修复后是否更像正常中文”。
COMMON_CHINESE_CHARS = set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分"
    "对成会可主发年动同工也能下过子说产种面而方后多定行学法所民"
    "得经十三之进着等部度家电力里如水化高自二理起小物现实加量都"
    "两体制机当使点从业本去把性好应开它合还因由其些然前外天政"
    "四日那社义事平形相全表间样与关各重新线内数正心反你明看原"
    "又么利比或但质气第向道命此变条只没结解问意建月公无系"
)

PRIVATE_USE_PATTERN = re.compile(r"[\ue000-\uf8ff]")
CJK_RUN_PATTERN = re.compile(r"[\u4e00-\u9fff]{3,}")


def _run_git_list(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _repository_text_files() -> list[Path]:
    names = set(_run_git_list(["ls-files"]))
    names.update(_run_git_list(["ls-files", "--others", "--exclude-standard"]))
    paths: list[Path] = []
    for name in sorted(names):
        path = Path(name)
        if path.name in SPECIAL_TEXT_FILES or path.suffix.lower() in TEXT_EXTENSIONS:
            paths.append(path)
    return paths


def _chinese_score(value: str) -> int:
    return sum(character in COMMON_CHINESE_CHARS for character in value)


def _looks_semantically_repaired(original_line: str, repaired_line: str) -> bool:
    original_score = _chinese_score(original_line)
    repaired_score = _chinese_score(repaired_line)
    return repaired_score >= max(3, original_score + 3)


def _candidate_repair(line: str) -> str | None:
    if not CJK_RUN_PATTERN.search(line):
        return None
    try:
        return line.encode("gbk").decode("utf-8")
    except UnicodeError:
        return None


def _scan_file(path: Path) -> list[str]:
    issues: list[str] = []
    if not path.exists() or not path.is_file():
        return issues

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"READ_ERROR\t{path}\t{exc}"]

    if b"\xef\xbf\xbd" in raw:
        issues.append(f"REPLACEMENT_CHAR\t{path}")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [*issues, f"UTF8_DECODE_ERROR\t{path}\t{exc}"]

    for line_number, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_USE_PATTERN.search(line):
            issues.append(f"PRIVATE_USE_CHAR\t{path}:{line_number}")
            continue

        repaired = _candidate_repair(line)
        if repaired is None:
            continue
        if _looks_semantically_repaired(line, repaired):
            original = line.strip()[:120]
            fixed = repaired.strip()[:120]
            issues.append(f"SEMANTIC_MOJIBAKE\t{path}:{line_number}\t{original}\t=>\t{fixed}")

    return issues


def main() -> int:
    issues: list[str] = []
    for path in _repository_text_files():
        issues.extend(_scan_file(path))

    if not issues:
        print("Mojibake semantic check passed.")
        return 0

    for issue in sorted(set(issues)):
        print(issue)
    return 1


if __name__ == "__main__":
    sys.exit(main())
