# Library Management System

A desktop library administration and resource management suite built in Python with modern UI components (`ttkbootstrap`), SQLite storage, and visual analytics (`matplotlib`, `pandas`).

---

## Overview

The **Library Management System** streamlines book circulation, patron administration, queue reservations, and fine auditing. Featuring role-based access control (Admin & User roles), interactive charts, and database backup utilities, it provides a centralized platform for school, departmental, or private libraries.

---

## Features

* **Book Catalog Management (`BooksTab`)**:
  * Add, update, delete, and search books by Title, Author, Category, or ISBN.
  * Specialized query views: Department popularity analysis, longest waitlists, and never-borrowed book reports.
  * Right-click context menus for rapid book status updates.
* **Member Administration (`MembersTab`)**:
  * Register and manage member profiles.
  * View borrowing history per member and handle account permissions.
* **Circulation & Return Tracking (`HistoryTab`)**:
  * Log book checkouts, returns, and track active borrowers with timestamp auditing.
* **Overdue & Fine Management (`OverdueTab`)**:
  * Identify overdue materials and calculate outstanding return intervals.
* **Reservations & Hold Queue (`ReservationsTab`)**:
  * Manage reservations for books currently checked out and automate hold queues.
* **Analytics Dashboard (`DashboardTab`)**:
  * Visual distribution charts powered by Matplotlib: book categories, checkout activity, and overdue breakdowns.
* **Security & Administration (`SettingsTab`)**:
  * Role-based access control with SHA-256 hashed passwords and security question recovery.
  * One-click database backup and restore routines.
  * Modern theme switching via `ttkbootstrap`.

---

## Project Structure

```text
Library-data-management-system/
├── .gitignore               # Ignored build, cache, and database artifacts
├── requirements.txt         # Project dependencies
├── README.md                # Documentation and setup guide
└── index.py                 # Main entry point and GUI application
```

---

## Prerequisites & Dependencies

* **Python 3.10+**
* `ttkbootstrap` – Modern Bootstrap UI styling for Tkinter
* `pandas` – Data manipulation and CSV reporting
* `matplotlib` – Embedded dashboard charts
* `Pillow` – Image manipulation utilities

Install all requirements via `pip`:

```bash
pip install -r requirements.txt
```

---

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/deswanth12/Library-data-management-system.git
   cd Library-data-management-system
   ```

2. **Create and activate a virtual environment (optional but recommended):**
   ```bash
   # Windows (PowerShell)
   python -m venv venv
   .\venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the application:**
   ```bash
   python index.py
   ```

*Note: On initial launch, the system automatically initializes the SQLite schema (`library.db`) if not already present.*

---

## Contributing

Contributions and enhancements are welcome:

1. Fork the project.
2. Create your feature branch (`git checkout -b feature/improvement`).
3. Commit your changes (`git commit -m 'feat: add feature'`).
4. Push to the branch (`git push origin feature/improvement`).
5. Open a Pull Request.

---

## Author

Developed by **k Deswanth** ([@deswanth12](https://github.com/deswanth12)).
