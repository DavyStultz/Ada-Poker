# Security

Report suspected vulnerabilities privately using
[GitHub's private vulnerability reporting](https://github.com/DavyStultz/Ada-Poker/security/advisories/new).
Include reproduction steps using synthetic inputs. Do not attach credentials,
recordings or player databases to public issues.

Only the current default branch receives security maintenance. This is a local
Windows prototype; keep its HTTP service on loopback and use recordings you trust.
Input restrictions reduce decoder exposure but do not make arbitrary media safe.

The native runtime is pinned in `ada_poker/ffmpeg-build.json`. Setup downloads
FFmpeg from the Windows build provider linked by [FFmpeg](https://ffmpeg.org/download.html),
checks the archive and executable SHA-256 hashes, and retains the provider's
license and build information under `.local/ffmpeg/`. The application checks the
executable before use and has no PATH or wheel fallback. Recordings are restricted
to Matroska/H.264; recognition evidence is restricted to JPEG. Normal recording
and analysis do not download software or send media to external services.

A weekly workflow compares the pin with the publisher's release. Updating it
requires reviewing the release, checking both hashes and passing the unit,
synthetic media and native capture checks. It never installs updates automatically.
CodeQL scans application code; Dependabot monitors GitHub Actions. Neither replaces
review of native FFmpeg advisories or independent testing.
