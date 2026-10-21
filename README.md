# Mercado Discount Manager (美客多活动管家)

English | [简体中文](README_zh.md)

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://wiki.qt.io/Qt_for_Python)
[![Release](https://img.shields.io/badge/Release-v2.2.29-brightgreen.svg)](https://github.com/Minv5/mercado-discount-manager/releases)

**Mercado Discount Manager** is a localized, production-grade desktop application (built with **Python & PySide6**) designed for cross-border Mercado Libre sellers. It streamlines promotional campaign management, real-time FX-linked pricing, item cleanup, and post-sale claim reputation assessment—enforcing **explicit two-phase confirmation before any live API write operations** to prevent costly merchant errors.

---

## ✨ Key Capabilities

* **Two-Phase Safe Write Confirmation:** Protects live storefronts by requiring explicit verification before executing bulk promotional updates, discount cancellations, or listing modifications.
* **Real-Time FX Rate & Order Countdown Sync:** Dynamically links order countdown timers with live foreign exchange rate conversions for accurate cross-border profit calculation.
* **Two-Phase Item Cleaner Engine:** Automated identification and safe batch cleanup of underperforming or redundant listings.
* **Service Center & Claim Reputation Assessment:** Evaluates buyer claim sessions and predicts seller reputation impact directly within the desktop workspace.
* **Multi-Store OAuth2 & Webhook Architecture:** Decoupled callback handling, multi-store App whitelisting, and dead-letter protection for reliable Mercado Libre API synchronization.

---

## 📂 Project Structure

    mercado-discount-manager/
    ├── desktop-pyside/      # PySide6 desktop client GUI, pricing & service center modules
    ├── scripts/             # Automation, maintenance, and helper scripts
    ├── docs/                # Architecture and workflow documentation
    ├── requirements.txt     # Python dependencies
    ├── run-windows.bat      # Quick launcher for Windows environments
    ├── build-windows.bat    # Packaging and executable build script
    └── SECURITY.md          # Security policy and vulnerability reporting

---

## 🚀 Quick Start (Windows)

### 1. Prerequisites
* **OS:** Windows 10 / 11
* **Runtime:** Python 3.10+

### 2. Installation & Launch

    # Clone the repository
    git clone [https://github.com/Minv5/mercado-discount-manager.git](https://github.com/Minv5/mercado-discount-manager.git)
    cd mercado-discount-manager

    # Install Python dependencies
    pip install -r requirements.txt

    # Launch the desktop application
    run-windows.bat

### 3. Build Standalone Executable

    build-windows.bat

---

## 🛡️ Security & Governance

This repository follows strict open-source governance and security practices:
* **Explicit Write Guardrails:** No silent background mutation of live marketplace prices or promotions.
* **Credential Isolation:** OAuth tokens and private callback domains are strictly decoupled from the client source code.
* Please refer to [`SECURITY.md`](SECURITY.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for detailed policies.

---

## 🤝 Contributing

Contributions, bug reports, and feature requests are welcome! Please review [`CONTRIBUTING.md`](CONTRIBUTING.md) before submitting a Pull Request.

---

## 📄 License

Licensed under the **Apache-2.0 License**. See [`LICENSE`](LICENSE) for full details.
