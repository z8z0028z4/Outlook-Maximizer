# Third-party notices for the Windows review build

This is an initial inventory for the unpublished `v0.1.0` Windows x64 draft executable (SHA-256 `5262e71c9486cb4a7b3a9625660f5221e370f442a89ebe18cd2e80e8bfe74c1b`). It records the direct dependencies and license texts identified for that build. Third-party components retain their own licenses and copyright notices.

**繁體中文摘要：** 這份文件列出目前草稿執行檔已辨識的主要第三方元件、授權原文與上游原始碼參考位置。完整發行盤點尚未完成：公開執行檔前仍需核對 Qt wheel 與實際二進位相對應的原始碼、Qt PDF/PDFium 等元件的聲明，以及 LGPL 函式庫的重建或替換方式。

**This inventory is not yet a complete distribution audit.** In particular, the Qt wheel does not include its source, patch history, or an SBOM. Before publishing the executable, verify the exact corresponding source for that wheel and its bundled components, complete the notices for Qt PDF/PDFium, software OpenGL and Microsoft VC runtime components, and verify a workable way for recipients to rebuild or replace the LGPL-covered libraries. The upstream links below are references; a link to an upstream release alone does not establish that it is the complete corresponding source for this particular binary.

## Direct dependencies and build tools

| Component in or used to build the draft | Version | License or notice | Upstream source reference |
| --- | --- | --- | --- |
| PyQt6 | 6.10.1 | [GPL-3.0-only](LICENSE) | [PyQt6 6.10.1 files](https://pypi.org/project/PyQt6/6.10.1/#files) |
| PyQt6-sip | 13.10.3 | [BSD-2-Clause](third_party_licenses/PyQt6-sip-13.10.3.txt) | [PyQt6-sip 13.10.3 files](https://pypi.org/project/PyQt6-sip/13.10.3/#files) |
| Qt runtime from PyQt6-Qt6 | 6.10.1 | [LGPL-3.0](third_party_licenses/Qt6-LGPL-3.0.txt), which incorporates the [GPLv3 terms](LICENSE) | [PyQt6-Qt6 6.10.1 files](https://pypi.org/project/PyQt6-Qt6/6.10.1/#files); [Qt 6.10.1 source releases](https://download.qt.io/official_releases/qt/6.10/6.10.1/single/) |
| CPython runtime | 3.12.0 | [Python license and bundled notices](third_party_licenses/Python-3.12.0.txt) | [Python 3.12.0 release](https://www.python.org/downloads/release/python-3120/) |
| pywin32 | 311 | [win32 notice](third_party_licenses/pywin32-win32-311.txt); [win32com notice](third_party_licenses/pywin32-win32com-311.txt) | [pywin32 build 311](https://github.com/mhammond/pywin32/tree/b311) |
| tzdata Python package | 2025.2 | [Package notice](third_party_licenses/tzdata-2025.2-notice.txt); [Apache-2.0](third_party_licenses/Apache-2.0.txt) | [tzdata 2025.2 files](https://pypi.org/project/tzdata/2025.2/#files) |
| OpenSSL | 3.0.11 | [OpenSSL 3.0.11 license](third_party_licenses/OpenSSL-3.0.11.txt) | [OpenSSL 3.0.11 source](https://github.com/openssl/openssl/tree/openssl-3.0.11) |
| PyInstaller bootloader and build tool | 6.18.0 | [GPL with bootloader exception](third_party_licenses/PyInstaller-6.18.0.txt) | [PyInstaller 6.18.0 files](https://pypi.org/project/PyInstaller/6.18.0/#files) |
| PyInstaller community hooks | 2026.0 | [Hook license details](third_party_licenses/PyInstaller-hooks-contrib-2026.0.txt) | [Community hooks 2026.0 files](https://pypi.org/project/pyinstaller-hooks-contrib/2026.0/#files) |

The Python license file includes notices for some components distributed with CPython, including libffi. The draft also contains Qt plugins and libraries, including Qt PDF, as well as software OpenGL and Microsoft runtime files; their full notices and exact corresponding sources still need review.

## Rebuilding the review executable

The executable was built on Windows x64 with CPython 3.12.0. Its application code and build scripts are in this repository. With that interpreter installed, the following commands select the identified main package versions; `py -3.12` may select a different 3.12 patch release if multiple versions are installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install PyQt6==6.10.1 PyQt6-sip==13.10.3 PyQt6-Qt6==6.10.1 pywin32==311 tzdata==2025.2 PyInstaller==6.18.0 pyinstaller-hooks-contrib==2026.0
.\.venv\Scripts\python scripts/build_windows.py
```

This produces `dist/OutlookMaximizer.exe`; a rebuild is not expected to be byte-for-byte identical, and other transitive build dependencies are not pinned above. The build script packages the libraries installed in that Python environment. Rebuilding against modified Qt libraries and the exact corresponding source materials must still be verified before public distribution.
