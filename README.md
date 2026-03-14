# Attendance_System
This is a Attendance System which help in school traditional paper attendance.
# Automated Attendance System for Rural Schools

## 1. Project Overview
A small, offline-ready web app for marking and reporting student attendance, intended for rural schools using low-cost hardware (Raspberry Pi). Teachers can manually mark attendance or use a low-cost keyboard-wedge QR/RFID scanner.

## 2. Technical Stack
- Python 3.9+, Flask (server), SQLite (local database)
- Minimal HTML/CSS, no heavy JavaScript frameworks
- Basic local authentication (hashed passwords)

## 3. Setup Instructions
### 3.1 Prerequisites
- Python 3.9+ and pip
- (Recommended) Raspberry Pi 3/4/Zero W with Raspberry Pi OS

### 3.2 Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install flask werkzeug python-dateutil
python app.py
