#!/usr/bin/env python3
"""Turn a book (.txt, .epub, .pdf) into an MP3 audiobook using Kokoro TTS.

Runs locally. Uses CUDA if available (RTX PC), otherwise CPU (Mac).

Usage:
    python audiobook.py book.epub
    python audiobook.py book.pdf --voice af_heart --speed 1.1
    python audiobook.py book.txt --single -o my_audiobook
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SAMPLE_RATE = 24000
MAX_CHUNK_CHARS = 400  # keep chunks well under Kokoro's 510-phoneme limit

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

CHAPTER_HEADING = re.compile(
    r"^\s*(chapter|part|book|prologue|epilogue|introduction|preface|appendix)"
    r"([\s.:]+([0-9]+|[ivxlcdm]+|[a-z]+))?\s*$",
    re.IGNORECASE,
)


def extract_txt(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return split_into_chapters(text)


def extract_epub(path: Path) -> list[tuple[str, str]]:
    from ebooklib import epub, ITEM_DOCUMENT
    from bs4 import BeautifulSoup

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    chapters = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style", "sup"]):
            tag.decompose()
        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        paras = [
            p.get_text(" ", strip=True)
            for p in soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"])
        ]
        text = "\n".join(p for p in paras if p)
        if not text.strip():
            continue
        chapters.append((title or f"Section {len(chapters) + 1}", text))
    # Drop front-matter sections that are too short to matter (covers, toc pages)
    kept = [(t, x) for t, x in chapters if len(x) > 200]
    return kept or chapters


def extract_pdf(path: Path) -> list[tuple[str, str]]:
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    pages = [page.get_text("text") for page in doc]
    doc.close()

    # Find lines repeated across many pages (running headers/footers) and drop them
    from collections import Counter

    edge_lines = Counter()
    for ptext in pages:
        lines = [l.strip() for l in ptext.splitlines() if l.strip()]
        for l in lines[:2] + lines[-2:]:
            norm = re.sub(r"\d+", "#", l)
            edge_lines[norm] += 1
    repeated = {l for l, n in edge_lines.items() if n >= max(3, len(pages) // 5)}

    cleaned_pages = []
    for ptext in pages:
        out = []
        for line in ptext.splitlines():
            s = line.strip()
            if not s:
                out.append("")
                continue
            if re.fullmatch(r"[\divxlcdm]+", s, re.IGNORECASE):  # bare page number
                continue
            if re.sub(r"\d+", "#", s) in repeated:
                continue
            out.append(s)
        cleaned_pages.append("\n".join(out))

    text = "\n".join(cleaned_pages)
    # Rejoin words hyphen-\n ated across line breaks
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Keep chapter headings on their own paragraph so line-merging doesn't eat them
    text = "\n".join(
        f"\n{l}\n" if CHAPTER_HEADING.match(l) else l for l in text.splitlines()
    )
    # Merge single line breaks (layout wrapping) but keep blank-line paragraph breaks
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return split_into_chapters(text)


def split_into_chapters(text: str) -> list[tuple[str, str]]:
    """Split plain text on 'Chapter N'-style headings; fall back to one chapter."""
    lines = text.splitlines()
    marks = [i for i, l in enumerate(lines) if CHAPTER_HEADING.match(l)]
    if len(marks) < 2:
        return [("Full Book", text)]
    chapters = []
    if marks[0] > 0:
        intro = "\n".join(lines[: marks[0]]).strip()
        if len(intro) > 200:
            chapters.append(("Opening", intro))
    for j, start in enumerate(marks):
        end = marks[j + 1] if j + 1 < len(marks) else len(lines)
        title = lines[start].strip()
        body = "\n".join(lines[start:end]).strip()
        if body:
            chapters.append((title, body))
    return chapters


def extract(path: Path) -> list[tuple[str, str]]:
    ext = path.suffix.lower()
    if ext == ".txt":
        return extract_txt(path)
    if ext == ".epub":
        return extract_epub(path)
    if ext == ".pdf":
        return extract_pdf(path)
    sys.exit(f"Unsupported file type: {ext} (use .txt, .epub, or .pdf)")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

SENTENCE_END = re.compile(r"(?<=[.!?…\"”'])\s+")


def chunk_text(text: str) -> list[str]:
    """Split text into sentence-safe chunks of at most MAX_CHUNK_CHARS."""
    chunks = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= MAX_CHUNK_CHARS:
            chunks.append(para)
            continue
        current = ""
        for sent in SENTENCE_END.split(para):
            if len(current) + len(sent) + 1 > MAX_CHUNK_CHARS and current:
                chunks.append(current.strip())
                current = ""
            # A single monster sentence: hard-split on commas/spaces
            while len(sent) > MAX_CHUNK_CHARS:
                cut = sent.rfind(",", 0, MAX_CHUNK_CHARS)
                if cut < MAX_CHUNK_CHARS // 2:
                    cut = sent.rfind(" ", 0, MAX_CHUNK_CHARS)
                if cut <= 0:
                    cut = MAX_CHUNK_CHARS
                chunks.append((current + " " + sent[: cut + 1]).strip())
                current = ""
                sent = sent[cut + 1 :]
            current = (current + " " + sent).strip()
        if current:
            chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


KOKORO_VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]


def validate_voice(voice: str) -> None:
    import difflib

    for v in voice.split(","):
        v = v.strip()
        if v not in KOKORO_VOICES:
            close = difflib.get_close_matches(v, KOKORO_VOICES, n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            sys.exit(f"Unknown voice '{v}'.{hint}\n"
                     f"Available: {', '.join(KOKORO_VOICES)}")


def pick_device(requested: str | None) -> str:
    if requested:
        return requested
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def synthesize_book(
    chapters: list[tuple[str, str]],
    outdir: Path,
    voice: str,
    speed: float,
    lang: str,
    device: str,
    single: bool,
    title: str,
) -> list[Path]:
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    print(f"Loading Kokoro-82M on {device} (first run downloads ~330 MB)...")
    pipeline = KPipeline(lang_code=lang, device=device)

    total_chars = sum(len(c[1]) for c in chapters)
    done_chars = 0
    start = time.time()
    pause = np.zeros(int(SAMPLE_RATE * 0.6), dtype=np.float32)  # gap between chunks

    wavs = []
    for idx, (ch_title, ch_text) in enumerate(chapters, 1):
        chunks = chunk_text(ch_text)
        wav_path = outdir / f"{idx:03d}.wav"
        audio_secs = 0.0
        with sf.SoundFile(
            wav_path, "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16"
        ) as f:
            for chunk in chunks:
                for _, _, audio in pipeline(chunk, voice=voice, speed=speed):
                    if audio is None or len(audio) == 0:
                        continue
                    data = audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio
                    f.write(data)
                    audio_secs += len(data) / SAMPLE_RATE
                f.write(pause)
                done_chars += len(chunk)
                elapsed = time.time() - start
                rate = done_chars / max(elapsed, 1e-9)
                remaining = (total_chars - done_chars) / max(rate, 1e-9)
                print(
                    f"\r[{idx}/{len(chapters)}] {ch_title[:40]:<40} "
                    f"{100 * done_chars / total_chars:5.1f}%  "
                    f"ETA {remaining / 60:5.1f} min",
                    end="",
                    flush=True,
                )
        print()
        wavs.append((wav_path, ch_title, audio_secs))

    # Encode to MP3
    if single and len(wavs) > 1:
        concat_list = outdir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.name}'" for p, _, _ in wavs), encoding="utf-8"
        )
        safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_") or "audiobook"
        combined = outdir / f"{safe_title}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", concat_list.name, "-codec:a", "libmp3lame", "-qscale:a", "4",
             "-metadata", f"title={title}", combined.name],
            check=True, cwd=outdir,
        )
        concat_list.unlink()
        for p, _, _ in wavs:
            p.unlink()
        return [combined]

    mp3s = []
    for i, (wav_path, ch_title, _) in enumerate(wavs, 1):
        safe = re.sub(r"[^\w\s-]", "", ch_title).strip().replace(" ", "_")[:60] or f"ch{i}"
        mp3_path = outdir / f"{i:03d}_{safe}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
             "-codec:a", "libmp3lame", "-qscale:a", "4",
             "-metadata", f"title={ch_title}", "-metadata", f"album={title}",
             "-metadata", f"track={i}", str(mp3_path)],
            check=True,
        )
        wav_path.unlink()
        mp3s.append(mp3_path)

    return mp3s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Local book → MP3 audiobook (Kokoro TTS)")
    ap.add_argument("book", help="Input file: .txt, .epub, or .pdf")
    ap.add_argument("-o", "--output", help="Output directory (default: <book name>_audio)")
    ap.add_argument("--voice", default="af_heart",
                    help="Voice (default af_heart). Try: af_bella, af_nicole, am_michael, "
                         "am_fenrir, bf_emma, bm_george")
    ap.add_argument("--speed", type=float, default=1.0, help="Speech speed (default 1.0)")
    ap.add_argument("--lang", default="a",
                    help="Language: a=American English, b=British English (default a)")
    ap.add_argument("--device", choices=["cpu", "cuda", "mps"], default=None,
                    help="Force device (default: cuda if available, else cpu)")
    ap.add_argument("--single", action="store_true",
                    help="Merge everything into one MP3 instead of one per chapter")
    ap.add_argument("--chapters", help="Only render these chapters, e.g. 1-3 or 5")
    ap.add_argument("--list", action="store_true",
                    help="List detected chapters and exit (no synthesis)")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found. Install it first (brew install ffmpeg).")

    book_path = Path(args.book).expanduser()
    if not book_path.exists():
        sys.exit(f"File not found: {book_path}")

    print(f"Extracting text from {book_path.name} ...")
    chapters = extract(book_path)
    total_chars = sum(len(c[1]) for c in chapters)
    est_hours = total_chars / 900 / 60  # ~900 chars/min narration
    print(f"Found {len(chapters)} chapter(s), {total_chars:,} characters "
          f"(~{est_hours:.1f} h of audio)")

    if args.list:
        for i, (t, x) in enumerate(chapters, 1):
            print(f"  {i:3d}. {t[:60]:<60} {len(x):>9,} chars")
        return

    if args.chapters:
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", args.chapters.strip())
        if not m:
            sys.exit("--chapters must look like 5 or 1-3")
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        chapters = chapters[lo - 1 : hi]
        if not chapters:
            sys.exit("No chapters in that range.")

    outdir = Path(args.output) if args.output else book_path.with_name(book_path.stem + "_audio")
    outdir.mkdir(parents=True, exist_ok=True)

    validate_voice(args.voice)
    device = pick_device(args.device)
    mp3s = synthesize_book(
        chapters, outdir, args.voice, args.speed, args.lang, device,
        args.single, title=book_path.stem,
    )

    print(f"\nDone. {len(mp3s)} file(s) in {outdir}/")
    for p in mp3s:
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
