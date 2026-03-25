# Video Operations Suite v1.0

Video Operations Suite is a desktop tool for batch trimming, remuxing, and basic video prep with a clean queue workflow, thumbnails, and a built‑in player.

## Features
- Batch trim first/last seconds, remux, roll, or transcode to MP4 (H.264)
- CUDA (NVENC) support with CPU fallback
- Queue with undo/redo, per‑item seconds, and status
- Thumbnail editor with custom thumbs
- Session save/load (`.vos`)

## Requirements
- Python 3.10+ recommended
- FFmpeg + FFprobe in PATH
- Optional: VLC (for splash video playback)
- Optional: OpenCV (for thumbnail extraction)

## Install
```bash
pip install -r requirements.txt
```

## Run
```bash
python Video_Operations_Suite_v1.py
```

## Assets
Place the following file in the repo root or in an `assets/` folder:
- `Video_Operations_Suite_.ico`

## Configuration
Optional environment variable:
- `VOS_PYTHONPATH` to add extra Python paths without hardcoding them in the script.

## Author
GitHub: [github.com/Rymnda](https://github.com/Rymnda)
