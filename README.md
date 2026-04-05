# Video Operations Suite v1.0

Video Operations Suite is a desktop batch video tool for trimming, remuxing, transcoding, queue management, thumbnail control, and quick preview in one UI.

## Features
- Trim first seconds, trim last seconds, roll first seconds to the end, remux, or transcode to MP4 (H.264)
- Built-in video preview with large transport controls and direct time jump
- Flexible time input: `hh:mm:ss`, `mm:ss`, or shorthand like `2`, `200`, `0200`, `000200`
- Drag and drop in both the file list and thumbnail pane
- Queue workflow with undo/redo, per-item seconds, selection sync, and status tracking
- Thumbnail editor with custom thumbnail storage
- Session save/load with `.vos`
- Language files in `lang/` with English default on first start and last language remembered
- Portable build via PyInstaller and installer build via Inno Setup

## Requirements
- Windows
- Python 3.14 recommended
- FFmpeg and FFprobe available in `PATH`
- VLC runtime available for embedded playback
- Python packages from `requirements.txt`

## Install
```bash
python -m pip install -r requirements.txt
```

## Run From Source
```bash
python Video_Operations_Suite_v1.py
```

## Build

Portable executable:
```bash
python -m PyInstaller --noconfirm --clean Video_Operations_Suite.spec
```

Installer:
```bash
iscc Video_Operations_Suite_Setup.iss
```

Build files:
- `Video_Operations_Suite.spec`
- `Video_Operations_Suite_Setup.iss`

## Project Structure
- `Video_Operations_Suite_v1.py`: main desktop app
- `thumbnail_editor_standalone.py`: standalone thumbnail editor helper
- `assets/`: icons, splash assets, screenshot
- `lang/`: UI language JSON files

## Notes
- The app uses `assets/Video_Operations_Suite_.ico` as the application icon.
- `VOS_PYTHONPATH` can be set to prepend extra Python paths without hardcoding them in the script.
- Build output is intentionally excluded from git.

## Repository
- Project: [Rymnda/Video_Operations_Suite](https://github.com/Rymnda/Video_Operations_Suite)
- Profile: [github.com/Rymnda](https://github.com/Rymnda)
