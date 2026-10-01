# Verification — 2026-09-30

## Version 0.2 — 2026-10-01

- 21 tests passed, including per-submission output directories without changing defaults, cache expiry/format re-selection, immediate refresh after cache rejection, cached media download, and local playback HTTP range support.
- Live comparison on the public sample video, 1-second precise 360p cut: cold parsing 3.168 s, repeated metadata cache lookup 0.001 s; cached download handoff 0.365 s versus fresh extraction 2.021 s. Both outputs completed; total times 7.341 s and 9.037 s. These are single-run observations, not guaranteed speed. Cache rejection and intermittent CDN connection errors were also observed; cache rejection now immediately falls back to fresh extraction.
- Packaged GUI helper: hidden Tk runtime initialization succeeded. Windows Shell reveal returned success through both the helper and the application's folder button. Native folder picking still requires the user's interactive selection.
- Actual browser playback: the previous MP4 loaded through the new local media route with readyState 4, duration 4.533 s and video width 640. No Windows player association was needed.
- Existing user downloads and task records survived the rebuild. Source, dependencies, runtime data and builds remain on E:.

- JavaScript syntax check: `node --check web/app.js` passed.
- 15 automated tests passed: URL/time validation, atomic queue insertion, duplicate detection, persistent restart recovery, bounded retries and permanent failures, cancellation before process start, output directory snapshots, local HTTP authorization/origin checks, asset traversal rejection, and four real media pipelines using yt-dlp + FFmpeg.
- Media fixture pipelines: fast MP4, precise MP4, original MKV, precise M4A. Exact output durations were within 150 ms of 4.5 seconds. TLS-only input flags are omitted only for the HTTP test fixture.
- Live HTTPS integration: Google's public player demonstration video `M7lc1UVf-VE` parsed successfully; available resolutions 144/240/360/480/720p. Range 3.25–7.75 seconds downloaded at 360p, with H.264 video and AAC audio, output duration 4.533 seconds. The integration also passed with SSL_CERT_FILE/SSL_CERT_DIR unset, using bundled CA certificates.
- Browser UI: actual video metadata, embedded preview, chapter controls, time input, clip creation, resolution and precision selection, and queue submission verified in the Codex browser.
- Packaged EXE: launch, local authenticated API, all bundled tools and system tray process lifetime checked. The browser submitted the same 4.5-second range to the packaged app and its background worker produced the final MP4 successfully on the first attempt. No Python installation is required for the portable build.
- Rebuild preservation: a sentinel in the packaged application's data folder survived a rebuild. The distributable ZIP is assembled from clean staging without runtime user data.

Tests cover this computer and the public sample video; restricted videos, arbitrary network setups, long-running downloads and every available YouTube encoding remain outside this verification. Queue persistence is supported; byte-level continuation for interrupted range downloads is not guaranteed.


## Desktop shell 0.3 — 2026-10-01

- Packaged `dist/YouTubeClipper/YouTubeClipper.exe` with pywebview 6.2.1, pythonnet and WebView2 SDK/loader. Launch defaults to an independent desktop window. Clean ZIP includes the runtime dependencies and excludes user data/downloads. Existing two completed task records were retained.
- 26 automated tests pass, including close-to-tray without destroying the window, restoring a window, exit confirmation/cancellation during active work, owned directory selection/cancellation/serialization, failed-load cleanup, protected desktop activation, and validated system-player delegation. Real media pipeline tests still pass. JavaScript syntax check passes.
- Actual source and packaged GUI launches reached WebView2 controller creation but returned HRESULT 0x8000FFFF (E_UNEXPECTED) in the current restricted command environment. Window rendering, real native folder selection and in-window playback have NOT been verified for version 0.3. `scripts/desktop_smoke.py` records the failed real-GUI smoke result. Microsoft documents this error as possible when the runtime cannot access its user-data directory; that is a possible explanation here, not a confirmed diagnosis. No sandbox or TLS security settings were disabled.
- The application logs desktop errors to `data/desktop.log` and terminates failed initialization after 35 seconds rather than leaving a blank background instance. The packaged smoke instance was exited through its authenticated desktop endpoint. A normal user double-click launch is pending verification.

User verification: the user launched the packaged EXE normally from Windows and confirmed that an independent application window appears. The normal-launch desktop log contains no initialization error. A second EXE launch restored the same instance (same PID, child exited successfully); the local API still reports both retained task records. Actual folder selection and playback inside version 0.3 remain unverified in this session.

## Workbench UI 0.4 — 2026-10-01

- Browser visual verification at 1280x720 and 860x580: root scrollHeight equals viewport height; cut controls and download action remain inside viewport, with no cut-panel scrolling required for normal controls. Multiple clips scroll only inside the clip list. Six fixture queue entries scroll only inside the job list (411px visible / 683px content at 860x580).
- Verified latest save dialog opens immediately even while a toast is shown; toast no longer receives pointer events. Long titles/output paths expose native hover titles. Optional cut help is collapsible.
- Tests use isolated fixture metadata in `scripts/ui_preview.py`, with workers disabled and output under `work/ui-preview`. No production records or video downloads are created by UI fixtures. Snapshot: `work/ui-studio-1280.jpg`.
- JavaScript syntax check and 26 existing core/desktop/real-media tests pass. Download-engine and source-platform validation have not been changed. Bilibili and Douyin are planning only.

Native follow-up: version 0.4 was relaunched through Windows Computer Use into the real user desktop (PID 1776). The packaged asset confirms 0.4 and the existing two job records remain. Initial accessibility capture returned the native window frame. Real window screenshot capture again timed out on both attempts, including fresh selection/activation; therefore native visual rendering remains unverified. The browser screenshot is explicitly a page-layout verification artifact.

## Multiple platforms 0.5 — 2026-10-01

- 37 tests pass, including source normalization/share text, part preservation, redirect allowlisting, cookie/proxy isolation, metadata adaptation, split-audio preview selection, expiration, public-media validation, range/header forwarding and session protection. Existing real FFmpeg/queue tests pass.
- Real Bilibili public sample BV1xx411c7mD parsed successfully and exported a 1-second precise clip with merged media. Video and audio preview HTTP probes both returned 206, bytes 0-1023, and valid MP4 ftyp headers; no full source was fetched by those probes. Results: `work/platform-smoke-result.json` and `work/platform-preview-probe.json`.
- Real Douyin public sample 6961737553342991651 was rejected by the upstream extractor with Fresh cookies required. Credential-free Douyin success is NOT verified; normalization, metadata and queue routing are fixture-tested. No user cookies were read or browser sessions accessed automatically.
- Browser security policy denied access to the local preview page. No alternative browser surface or policy workaround was used. UI interaction, actual browser playback/audio sync and visual layout for 0.5 therefore remain unverified.

## 0.6 connection verification

42 unittest checks passed, including actual Netscape round-trip through MozillaCookieJar (session, HttpOnly, Secure), domain/expiry filtering, repeat-open reuse, invalid/closed window handling, and session-protected connection endpoints. JavaScript syntax checked. Isolated native WebView2 smoke opened both public platform pages, loaded them, extracted platform cookies and automatically persisted the file path. Bilibili extraction succeeded. Douyin initially failed with first-visit anonymous cookies, then succeeded in the warmed WebView2 profile. A real Douyin precise one-second clip completed with H264 video + AAC audio, ffprobe duration 1.000998 seconds. No sign-in was needed for this public sample; signed-in/restricted video behavior remains unverified. No external user browser profile was read. Native visual appearance was not inspected; this is functional WebView2 verification. Smoke artifacts/profiles stay under E:/YouTubeClipper/work and are excluded from the portable archive.

## 0.7 verification

46 unittest checks passed, plus JavaScript syntax. Real fixture full-video download produced 12 seconds with chosen filename and preserved an existing file via (2) suffix. Source WebView2 DOM smoke exercised full-video save paths, batch save paths, 25% volume on both video/audio and synchronized mute. Native SAVE dialog options/results are mock-tested; no automated interaction with the real Windows save dialog or native visual inspection was performed. Actual Douyin full download selected filtered website-playback formats, yielded H264 + AAC for 19.735011 seconds, and had no section flag. Frames at 2 seconds and one second before the end were visually inspected: no platform watermark or appended logo seen in that sample. This does not prove every video is free of creator-embedded marks. Artifacts: work/douyin-full-smoke-result.json, work/download-ui-smoke-result.json, work/douyin-full-start.jpg, work/douyin-full-end.jpg.

## 0.8 verification

57 unittest checks cover preferences, legacy DB migration, persisted pause/restart, interruption-before-spawn, queue dispatch pause/resume, priority changes, numeric metrics sanitization, error categories, relocation/retry, named multi-part vs single-part parsing, and window preference saving. Real HTTP-media test pauses a partial full download, preserves .part data, resumes with nonzero HTTP Range and completes. Clip regression tests still always request the chosen section. Native Desktop.run/WebView2 smoke restores 1040x740, resizes to 1100x780 and saves that normal size; volume 35/mute/format restored; last directory chosen; failure/pause/order actions rendered; single part hidden and named part click changes p=2. Actual Bilibili sample BV1bK411W797 returns 23 named parts and both p=1/p=2 extract distinct titles. This is functional DOM/window verification; no screenshot-based native appearance verification or audible playback test was performed.


## 0.9 verification — 2026-10-01

63 unittest checks passed, including real decoded scene cuts at non-keyframes, exclusive frame boundaries, exact frame-count export without re-download, nonzero timestamps, variable frame durations, persistent analysis manifests, session-protected images/media Range, and cancellation cleanup. Native Desktop.run/WebView2 DOM smoke verified boundary images, stepping, direct save with preservation of other pending clips, clip-list addition, and a 1040x740 layout with no dialog scroll for normal controls (590px content/client height). This is functional/layout measurement, not screenshot-based native appearance inspection.

Actual Bilibili sample BV1bK411W797 p=1 analysed around 35 seconds, with cache starting at 20 seconds. The selected cut was 28.167–37.042; four boundary images were inspected and a 213-frame MP4 exported successfully. Douyin sample 6961737553342991651 analysis reached ready. Platform smoke tests are sample-dependent and are not normal unit tests. Existing three production job records survived the 0.9 rebuild; clean archive excluded runtime data and media.

Repository publication: Windows/Python 3.11 CI exposed SQLite connections left open after transaction context exit, preventing fixture cleanup. Engine.connect now explicitly closes on success and failure while preserving transaction commit/rollback. The legacy migration fixture also closes its direct connection. 64 local tests pass, including closed-connection and rollback regression coverage.
