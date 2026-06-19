# Digital Cognitive Extension

An offline AI-assisted wearable memory system built on Raspberry Pi.

Digital Cognitive Extension is a research and development project focused on creating an external cognitive aid capable of capturing, organizing, and retrieving important information through embedded hardware and edge computing techniques.

---

## Project Goals

* Build a wearable external memory assistant
* Enable local memory storage and retrieval
* Reduce dependence on smartphones for note-taking
* Explore privacy-focused edge AI systems
* Integrate voice, display, and embedded hardware interfaces

---

## Current Features

* SQLite-based memory storage
* Memory retrieval system
* Raspberry Pi deployment
* Embedded hardware integration
* OLED display development
* Push-button interaction design

---

## Hardware

* Raspberry Pi Zero W
* SSD1306 OLED Display
* USB Microphone
* Push Buttons
* MAX98357A Audio Amplifier
* Speaker
* MicroSD Card

---

## Software Stack

* Python
* SQLite3
* RPi.GPIO
* Pillow
* Luma OLED

---

## Installation

```bash
git clone https://github.com/Alwin111/Digital-Cognitive-Extension.git
cd Digital-Cognitive-Extension

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

---

## Project Structure

```text
Digital-Cognitive-Extension/
│
├── smart_memory.py
├── requirements.txt
├── README.md
├── .gitignore
│
├── docs/
├── hardware/
└── images/
```

---

## Project Images

### Prototype

![Prototype](images/project_photo.jpg)

### Hardware Setup

![Hardware Setup](images/hardware_setup.jpg)

### OLED Display

![OLED Display](images/oled_display.jpg)

---

## Repository Notes

The repository intentionally excludes:

* AI model files
* Downloaded datasets
* Local databases
* Virtual environments
* Generated audio files

These files are not required for source control and can be recreated or downloaded separately.

---

## Current Status

🚧 Active Development

Currently working on:

* OLED display integration
* Push-button controls
* Memory management improvements
* Voice interface integration

---

## Author

**Alwin Varghese**

Security Researcher — bi0s Hardware

Wireless Security • Embedded Systems • IoT Security • Edge AI

GitHub: https://github.com/Alwin111

---

## License

Educational and research use.
