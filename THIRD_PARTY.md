# Third-party components

The application source is MIT-licensed; this does not relicense any dependency. Runtime tools and vendored libraries are downloaded during setup/build and are not committed to this source repository.

This portable application includes unmodified third-party executables.

- yt-dlp: https://github.com/yt-dlp/yt-dlp (Unlicense; executable dependencies have additional licenses, see tools/licenses/yt-dlp-third-party.txt).
- FFmpeg and ffprobe: https://www.gyan.dev/ffmpeg/builds/ (GPL essentials build, linked by ffmpeg.org). Corresponding source: https://github.com/FFmpeg/FFmpeg and build details at https://www.gyan.dev/ffmpeg/builds/. License files are bundled under tools/licenses.
- Deno: https://github.com/denoland/deno (MIT, see tools/licenses/deno.txt).
- pystray: https://github.com/moses-palmer/pystray (LGPL-3.0).
- Pillow: https://github.com/python-pillow/Pillow (HPND).
- Python: https://www.python.org/ (PSF license).
- PyInstaller bootloader: https://pyinstaller.org/ (GPL with exception).
- pywebview: https://github.com/r0x0r/pywebview (BSD-3-Clause).
- pythonnet: https://github.com/pythonnet/pythonnet (MIT); clr-loader (MIT), cffi (MIT), pycparser (BSD-3-Clause), Bottle (MIT), proxy-tools (BSD), typing-extensions (PSF).
- WebView2 .NET SDK/loader: https://learn.microsoft.com/microsoft-edge/webview2/ (Microsoft license; runtime is provided by Windows/Edge).

Source and license details are retained in the development project. A public distribution should also retain corresponding licenses and source provision required by the shipped components.

For binary releases, retain the exact upstream versions, checksums, license notices and corresponding-source/build information for redistributed components. In particular, the GPL FFmpeg build is distributed under its own terms; the MIT application license does not replace these obligations. Setup downloads current upstream tools, so re-running it may produce a different runtime bundle.
