# VoiceBridge — local voice dictation

**English** · [Русский](README_RU.md)

VoiceBridge turns speech into text on your own computer and inserts the result into the active application. Hold a configured mouse button or keyboard key, speak, release and get text without using a hosted speech-recognition API.

It is a **free, open-source desktop app** built around locally executed Qwen3-ASR inference. The initial installation and model download may require internet access; recognition using a downloaded model is local.

## What it does

- Push-to-talk from a mouse button or keyboard shortcut (for example, `F8` as configured).
- Local transcription with Qwen3-ASR; select an existing model or download one separately.
- Inserts recognized text via clipboard/keyboard integration, with platform-specific controls.
- Desktop Qt interface, personal vocabulary, text settings, model management and diagnostics.
- Supports both CPU and compatible GPU environments, depending on installed inference dependencies and hardware.

## Supported releases

| Operating system | Version | Package | Status |
|---|---|---|---|
| Ubuntu/Debian amd64 | **0.5.6** | `VoiceBridge-Ubuntu-v0.5.6-Apache-2.0.zip` | Desktop + DEB + Python wheel |
| Windows 10/11 x64 | **0.5.4** | `VoiceBridge-Windows-v0.5.4-Apache-2.0.zip` | Online EXE installer; unsigned |

**These are two separately maintained release snapshots**, not a single unified cross-platform 0.5.6 release. See the complete, version-specific sources under [`platforms/ubuntu-v0.5.6`](platforms/ubuntu-v0.5.6) and [`platforms/windows-v0.5.4`](platforms/windows-v0.5.4).

## Install on Ubuntu

1. Download the **Ubuntu 0.5.6 release ZIP** from GitHub Releases and fully extract it.
2. In the extracted directory, as your regular desktop user, run:

   ```bash
   bash install.sh
   voice-bridge-desktop
   ```

3. Choose an existing Qwen3-ASR model or download one from the application.

Do **not** run the install script as `sudo bash install.sh`; it requests elevated privileges only for required steps. Optional diagnostics: `bash check.sh` and `voice-bridge-desktop --diagnostics`. The provided public bundle omits historical rollback packages; upgrading an old installation may require a separate migration procedure. **Do not remove a working older installation solely to try this bundle.**

## Install on Windows

1. Download the **Windows 0.5.4 release ZIP** and fully extract it.
2. Run `VoiceBridge-Setup-0.5.4-x64.exe` as a normal user.
3. Allow the installer to obtain missing Python and AI packages, then choose or download an ASR model.

The installer is **not Authenticode-signed**. It is an online installer, not a fully offline package. The public package does not include private migration/rollback files for older installations.

## Build from source

Python **3.11+** is required. Each version has its own Python package, `pyproject.toml`, tests and platform packaging scripts:

```bash
# Ubuntu source snapshot
cd platforms/ubuntu-v0.5.6
python packaging/build.py

# Windows source snapshot (requires LLVM clang/lld-link)
cd platforms/windows-v0.5.4
python packaging/windows/build_release.py --out /path/outside/source
```

The two commands are separate; run each from its own source directory. The Windows command builds an installer but does not itself validate behavior on a Windows machine.

## Privacy and network use

- Transcription runs locally; the source does not require a hosted ASR service.
- Internet access is used when installing dependencies or downloading a model, including packages from third-party hosts.
- No model weights, microphone recordings, machine-specific configurations, private keys or internal infrastructure are included in the published source.
- VoiceBridge interacts with the clipboard and desktop input APIs. Review permissions, downloaded software and target application privacy policies before use.

## License and credits

**VoiceBridge's original source code: [Apache License 2.0](LICENSE)** · Copyright 2025–2026 Andrey154154.

Third-party packages and models **retain their own licenses**. The Qwen3-ASR model is not redistributed by this project. See [third-party notices](THIRD_PARTY_NOTICES.md) and [NOTICE](NOTICE). The Apache license does not grant trademark rights.

For installation specifics and platform caveats, see [Ubuntu release notes](docs/RELEASE_UBUNTU_EN.md) and [Windows release notes](docs/RELEASE_WINDOWS_EN.md).
