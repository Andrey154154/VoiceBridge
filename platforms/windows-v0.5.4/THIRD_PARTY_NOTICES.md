# Third-party software and models

VoiceBridge's *own* source code and original documentation use Apache-2.0. This choice does **not** relicense the dependencies listed below. All names remain property of their respective owners.

| Component | Role | Licensing / information |
| --- | --- | --- |
| Qwen3-ASR (Qwen) | On-device speech recognition | Model weights are **not included**. The separately downloaded `Qwen/Qwen3-ASR-1.7B-hf` model card specifies Apache-2.0: https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf |
| PySide6 / Qt for Python | Desktop UI | Community distributions provide LGPLv3/GPLv3 options; commercial licensing also exists. See https://doc.qt.io/qtforpython-6 and https://www.qt.io/development/open-source-lgpl-obligations |
| PyTorch, Transformers, Accelerate, NumPy, SoundDevice, Librosa, pynput, evdev, FastAPI, Uvicorn | AI runtime, audio capture, system input and optional local API | The applications fetch dependencies from third-party repositories at installation time. Review the license supplied with **each installed version**. The files are **not vendored** into the published source archive. |
| Python runtime and pip | Used by the Windows online installer | Downloaded from Python.org / PyPI; governed by their own licenses. |

**Redistribution note:** a binary distributor must satisfy all applicable third-party notices and, where relevant, LGPL source/relinking obligations. Redistributing an EXE or DEB with a copy of this project LICENSE alone is not a substitute for those obligations. The provided Windows setup EXE is an online installer and does not embed full Qt / Qwen runtime packages.

Third-party dependencies and model weights may have licenses or terms that change between versions. This file documents known direct dependencies; it is not a complete dependency SBOM or legal review.
