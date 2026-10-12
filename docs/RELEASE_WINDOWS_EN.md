# VoiceBridge Windows 0.5.4 — release notes (EN)

**Release:** Windows 10/11 x64 v0.5.4. This is not a Windows build of Ubuntu v0.5.6.

**In the ZIP:** `VoiceBridge-Setup-0.5.4-x64.exe` (existing Windows online installer), full source ZIP, LICENSE, NOTICE, third-party notices and SHA256SUMS.

**Installation:** extract the ZIP and run the setup EXE as a normal Windows user. Dependencies download during setup; Qwen3-ASR weights are selected/downloaded separately. The EXE is **unsigned** (no Authenticode signature).

**Known limitations:** This release does not bundle historical private migrations/rollback packages. The original Windows EXE is preserved byte-for-byte from the previously reviewed version; the outer ZIP now explicitly supplies the chosen Apache-2.0 license. Historical text embedded in the unchanged EXE may refer to the pre-publication licensing status; the accompanying LICENSE and NOTICE describe the current licensing of the author's code. Windows setup requires Internet access for dependencies.

**License:** VoiceBridge's own code: Apache-2.0. Third-party libraries and model files are separately licensed. The original author has tested the application on Windows; assembling this publication kit did not repeat live Windows installation testing.
