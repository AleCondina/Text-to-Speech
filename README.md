# Local Audiobook Maker

Turn any book (`.txt`, `.epub`, `.pdf`) into an MP3 audiobook — entirely on your own
machine, no cloud, no API keys. Powered by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
an open-weight TTS model with near-ElevenLabs narration quality.

## Quick start (Mac)

```bash
# one-time setup
brew install ffmpeg espeak-ng
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# make an audiobook (one MP3 per chapter)
.venv/bin/python audiobook.py mybook.epub

# everything in one MP3, slightly faster narration
.venv/bin/python audiobook.py mybook.pdf --single --speed 1.1

# preview detected chapters without synthesizing
.venv/bin/python audiobook.py mybook.pdf --list

# render only chapters 1-3 (great for testing a voice)
.venv/bin/python audiobook.py mybook.epub --chapters 1-3 --voice am_michael
```

First run downloads the model weights (~330 MB) from Hugging Face.

## Voices

| Voice | Description |
|---|---|
| `af_heart` | American female — warm, the default and best all-rounder |
| `af_bella` | American female — expressive |
| `af_nicole` | American female — soft, ASMR-like |
| `am_michael` | American male — deep, steady |
| `am_fenrir` | American male — energetic |
| `bf_emma` | British female (use with `--lang b`) |
| `bm_george` | British male (use with `--lang b`) |

Full list: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
You can also blend voices, e.g. `--voice "af_heart,am_michael"` averages them.

## Running on the PC (RTX 5070, CUDA)

The RTX 5070 is a Blackwell-generation card, which requires PyTorch built for
CUDA 12.8+. On the PC (Windows or WSL2):

```bash
# install espeak-ng: https://github.com/espeak-ng/espeak-ng/releases (Windows installer)
# install ffmpeg:    winget install ffmpeg   (or choco install ffmpeg)

python -m venv .venv
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python audiobook.py mybook.epub
```

The script auto-detects CUDA. Expect roughly 30-60x real-time — a 10-hour
audiobook in ~10-20 minutes. (The M1 Mac does roughly 3-5x real-time, so a full
book overnight is also perfectly doable.)

## Format notes

- **epub** — best source: clean text, real chapter titles.
- **txt** — chapters detected from `Chapter N` / `Part N` style headings.
- **pdf** — works, with automatic cleanup (page numbers, repeated headers/footers,
  re-joined hyphenated words). Scanned/image PDFs won't work — they need OCR first.

## Upgrade path: voice cloning

Kokoro uses fixed preset voices. If you want to narrate in a *custom* voice
(cloned from a ~10-second sample), the same pipeline can be swapped to
[Chatterbox](https://github.com/resemble-ai/chatterbox) or XTTS-v2 on the RTX PC.
They're slower and heavier, but the extract/chunk/stitch code here stays the same.
