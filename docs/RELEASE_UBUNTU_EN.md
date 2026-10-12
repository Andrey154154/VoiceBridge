# VoiceBridge Ubuntu 0.5.6 — release notes (EN)

**Release:** Ubuntu/Debian amd64 v0.5.6. This package is independently versioned from Windows 0.5.4.

**In the ZIP:** `VoiceBridge-Ubuntu-0.5.6-amd64.deb`, `voice_input_bridge-0.5.6-py3-none-any.whl`, `install.sh`, `check.sh`, `repair-input.sh`, `stop_old.py`, full source ZIP, LICENSE, NOTICE, third-party notices and SHA256SUMS.

**Installation:** extract the entire ZIP and run `bash install.sh` (not `sudo bash install.sh`) as a normal Ubuntu desktop user. Launch with `voice-bridge-desktop`. Dependencies and a Qwen3-ASR model might be downloaded. The model weights are not included.

**Known limitations:** Old migration/rollback assets are intentionally excluded. Do not replace a working older installation without a separate migration plan. Linux input injection may need system-level setup. This publicly packaged DEB has sanitized documentation, while the application source is unchanged from the previously reviewed package.

**License:** VoiceBridge's own code: Apache-2.0. Third-party libraries/model have separate terms. The original author has tested the application on the intended platform; no additional live hardware qualification was run while assembling this publication kit.
