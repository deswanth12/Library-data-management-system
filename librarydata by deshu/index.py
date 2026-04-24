import sqlite3
import shutil
import hashlib
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox, filedialog, END
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import logging
import pandas as pd
import matplotlib.pyplot as plt
from PIL import ImageGrab
from datetime import datetime, timedelta

DB_NAME = "library.db"

# --- Configuration ---
BORROWING_PERIOD_DAYS = 14
FINE_PER_DAY = 0.50  # 50 cents per day
RESERVATION_HOLD_DAYS = 3 # Days a member has to pick up a reserved book

# --- Logging Setup ---
admin_logger = logging.getLogger('admin_actions')
admin_logger.setLevel(logging.INFO)
# Create a file handler which logs admin actions
fh = logging.FileHandler('admin_actions.log', encoding='utf-8')
fh.setLevel(logging.INFO)
# Create a formatter that includes the custom 'user' field
formatter = logging.Formatter('%(asctime)s - User: %(user)s - %(message)s')
fh.setFormatter(formatter)
# Add the handler to the logger
admin_logger.addHandler(fh)

# ---------------- Utility Functions ----------------
def hash_password(password: str) -> str:
    """Return SHA256 hashed password."""
    return hashlib.sha256(password.encode()).hexdigest()

class Database:
    """A dedicated class to handle all database interactions."""
    def __init__(self, db_name):
        self.db_name = db_name

    def execute(self, query, params=(), fetch=None):
        """Execute a query and optionally fetch results."""
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                if fetch == "one":
                    return cursor.fetchone()
                if fetch == "all":
                    return cursor.fetchall()
                conn.commit()
                return cursor.lastrowid
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"An error occurred: {e}")
            return None

db = Database(DB_NAME)


def init_db():
    """Initialize database tables and default users."""
    db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user'
    )
    """)
    # --- Run Migrations for security questions ---
    try:
        columns = db.execute("PRAGMA table_info(users)", fetch="all")
        if columns:
            column_names = [col[1] for col in columns]
            if 'security_question' not in column_names:
                db.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
            if 'security_answer' not in column_names:
                db.execute("ALTER TABLE users ADD COLUMN security_answer TEXT")
    except sqlite3.Error: # pragma: no cover
        pass # This is safe to ignore
    db.execute("""
    CREATE TABLE IF NOT EXISTS books (
        book_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        isbn TEXT,
        publication_year INTEGER,
        category TEXT,
        department TEXT,
        total_copies INTEGER DEFAULT 1,
        available_copies INTEGER DEFAULT 1
    )
    """)
    db.execute("""
    CREATE TABLE IF NOT EXISTS members (
        member_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        contact TEXT,
        department TEXT
    )
    """)
    db.execute("""
    CREATE TABLE IF NOT EXISTS history (
        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        member_id INTEGER NOT NULL,
        issue_date TEXT NOT NULL,
        return_date TEXT,
        due_date TEXT,
        FOREIGN KEY (book_id) REFERENCES books (book_id),
        FOREIGN KEY (member_id) REFERENCES members (member_id)
    )
    """)
    db.execute("""
    CREATE TABLE IF NOT EXISTS reservations (
        reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        member_id INTEGER NOT NULL,
        reservation_date TEXT NOT NULL,
        notification_date TEXT,
        status TEXT DEFAULT 'active', -- 'active', 'notified', 'fulfilled', 'cancelled', 'expired'
        FOREIGN KEY (book_id) REFERENCES books (book_id),
        FOREIGN KEY (member_id) REFERENCES members (member_id)
    )""")
    # Sample data with hashed passwords
    db.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", hash_password("admin"), "admin"))
    db.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("user1", hash_password("pass1"), "user"))

def _show_receipt(root, title, transaction_details):
    """Displays a receipt in a new window."""
    win = ttk.Toplevel(root)
    win.title(title)
    win.geometry("400x450")

    receipt_text = f"--- Library Transaction Receipt ---\n\n"
    receipt_text += f"Transaction: {title}\n"
    receipt_text += f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    receipt_text += "-" * 35 + "\n\n"

    for key, value in transaction_details.items():
        receipt_text += f"{key:<15}: {value}\n"

    receipt_text += "\n" + "-" * 35 + "\n"
    receipt_text += "Thank you for using the library!\n"

    text_widget = ttk.Text(win, wrap="word", font=("Courier", 10))
    text_widget.insert(END, receipt_text)
    text_widget.config(state="disabled")
    text_widget.pack(fill="both", expand=True, padx=10, pady=10)

    def save_receipt():
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
            title="Save Receipt As",
            initialfile=f"receipt_{transaction_details.get('Transaction ID', '0')}.txt"
        )
        if file_path:
            with open(file_path, "w") as f:
                f.write(text_widget.get(1.0, END))
            messagebox.showinfo("Saved", "Receipt saved successfully.", parent=win)

    ttk.Button(win, text="Save to File", command=save_receipt, bootstyle="info").pack(pady=10)


class BaseTab(ttk.Frame):
    """Base class for all notebook tabs to share common functionality."""
    def __init__(self, master, app_instance, **kwargs):
        super().__init__(master, **kwargs)
        self.app = app_instance
        self.root = app_instance.root
        self.db = app_instance.db
        self.current_username = app_instance.current_username
        self.widgets = {}

    def sort_treeview(self, tree, col, reverse):
        """Sorts a treeview column when the header is clicked."""
        try:
            data = [(tree.set(child, col), child) for child in tree.get_children('')]
            # Attempt to sort numerically if possible, otherwise sort as strings
            try:
                data.sort(key=lambda t: int(t[0]), reverse=reverse)
            except ValueError:
                data.sort(key=lambda t: t[0].lower(), reverse=reverse)

            for index, (val, child) in enumerate(data):
                tree.move(child, '', index)
            tree.heading(col, command=lambda: self.sort_treeview(tree, col, not reverse))
        except Exception: # pragma: no cover
            # Failsafe for hierarchical views or other errors
            pass

# ---------------- Main App Class ----------------
class LibraryApp:
    def __init__(self, root):
        self.root = root
        self.root.withdraw() # Hide the window initially to prevent flashing
        self.root.title("Library System")
        self.root.geometry("400x350")
        self.main_frame = None
        self.login_screen()
        self.db = db # Make db instance available to the app
        self.current_username = None
        self.root.deiconify() # Show the window now that the login screen is ready
        self.root.after(100, self.root.lift) # Bring window to the front

    def login_screen(self):
        """Display the login window content."""
        if self.main_frame:
            self.main_frame.destroy()
        
        self.root.title("Login")
        self.root.geometry("450x400")

        # A main frame to hold the card-like layout
        login_container = ttk.Frame(self.root)
        login_container.pack(expand=True, fill="both")

        # The card in the center
        login_frame = ttk.Frame(login_container, padding=30, style="primary.TFrame")
        login_frame.place(relx=0.5, rely=0.5, anchor="center")

        ttk.Label(login_frame, text="Library Management System", font=("Helvetica", 18, "bold"), style="primary.Inverse.TLabel").pack(pady=(0, 25))

        # Username
        ttk.Label(login_frame, text="Username", style="primary.Inverse.TLabel").pack(anchor="w", padx=5)
        username_entry = ttk.Entry(login_frame, width=35)
        username_entry.pack(pady=(0, 10), ipady=4)
        username_entry.focus_set()

        # Password
        ttk.Label(login_frame, text="Password", style="primary.Inverse.TLabel").pack(anchor="w", padx=5)
        password_entry = ttk.Entry(login_frame, width=35, show="*")
        password_entry.pack(pady=(0, 15), ipady=4)

        def login():
            uname = username_entry.get().strip()
            pword = password_entry.get().strip()
            if not uname or not pword:
                messagebox.showwarning("Input Error", "Username and Password are required.")
                return

            hashed_pwd = hash_password(pword)
            result = self.db.execute("SELECT role FROM users WHERE username=? AND password=?", (uname, hashed_pwd), fetch="one")
            
            if result:
                login_container.destroy()
                self.main_app(uname, result[0])
            else:
                messagebox.showerror("Login Failed", "Invalid credentials")

        button_frame = ttk.Frame(login_frame, style="primary.TFrame")
        button_frame.pack(pady=(10, 0), fill='x')

        ttk.Button(button_frame, text="Login", command=login, bootstyle="success").pack(side="left", expand=True, padx=(0, 5), ipady=5)
        ttk.Button(button_frame, text="Register", command=self.register_screen, bootstyle="info").pack(side="right", expand=True, padx=(5, 0), ipady=5)
        
        forgot_pass_btn = ttk.Button(login_frame, text="Forgot Password?", command=self.forgot_password_screen, bootstyle="link-primary")
        forgot_pass_btn.pack(pady=(15,0))
        self.root.bind('<Return>', lambda event: login())

    def register_screen(self):
        """Opens a Toplevel window for new user registration."""
        reg_win = ttk.Toplevel(self.root)
        reg_win.title("Register New User")
        reg_win.geometry("400x450")
        reg_win.transient(self.root)

        reg_frame = ttk.Frame(reg_win, padding=20)
        reg_frame.pack(expand=True, fill="both")

        ttk.Label(reg_frame, text="Create a New Account", font=("Helvetica", 16, "bold")).pack(pady=(0, 20))

        ttk.Label(reg_frame, text="Username:").pack(anchor="w", padx=5)
        user_entry = ttk.Entry(reg_frame, width=35)
        user_entry.pack(pady=(0, 10), ipady=4)
        user_entry.focus_set()

        ttk.Label(reg_frame, text="Password:").pack(anchor="w", padx=5)
        pass_entry = ttk.Entry(reg_frame, width=35, show="*")
        pass_entry.pack(pady=(0, 10), ipady=4)

        ttk.Label(reg_frame, text="Confirm Password:").pack(anchor="w", padx=5)
        confirm_pass_entry = ttk.Entry(reg_frame, width=35, show="*")
        confirm_pass_entry.pack(pady=(0, 10), ipady=4)

        ttk.Label(reg_frame, text="Security Question:").pack(anchor="w", padx=5)
        question_entry = ttk.Entry(reg_frame, width=35)
        question_entry.pack(pady=(0, 10), ipady=4)

        ttk.Label(reg_frame, text="Security Answer:").pack(anchor="w", padx=5)
        answer_entry = ttk.Entry(reg_frame, width=35, show="*")
        answer_entry.pack(pady=(0, 15), ipady=4)

        def process_registration():
            username = user_entry.get().strip()
            password = pass_entry.get().strip()
            confirm_password = confirm_pass_entry.get().strip()
            question = question_entry.get().strip()
            answer = answer_entry.get().strip()

            if not (username and password and confirm_password and question and answer):
                messagebox.showerror("Input Error", "All fields are required.", parent=reg_win)
                return

            if password != confirm_password:
                messagebox.showerror("Password Mismatch", "The passwords do not match.", parent=reg_win)
                return

            try:
                db.execute("INSERT INTO users (username, password, role, security_question, security_answer) VALUES (?, ?, ?, ?, ?)",
                           (username, hash_password(password), 'user', question, hash_password(answer)))
                messagebox.showinfo("Success", "User registered successfully! You can now log in.", parent=reg_win)
                reg_win.destroy()
            except sqlite3.IntegrityError:
                messagebox.showerror("Error", "This username is already taken. Please choose another.", parent=reg_win)
        ttk.Button(reg_frame, text="Register", command=process_registration, bootstyle="success", width=33).pack(pady=(10, 0), ipady=5)
        reg_win.bind('<Return>', lambda event: process_registration())

    def forgot_password_screen(self):
        """Opens a window to handle the password reset process using a security question."""
        fp_win = ttk.Toplevel(self.root)
        fp_win.title("Forgot Password")
        fp_win.geometry("450x450")
        fp_win.transient(self.root)

        fp_frame = ttk.Frame(fp_win, padding=20)
        fp_frame.pack(expand=True, fill="both")

        ttk.Label(fp_frame, text="Password Reset", font=("Helvetica", 16, "bold")).pack(pady=(0, 20))

        # --- Step 1: Username Verification ---
        ttk.Label(fp_frame, text="Enter your username:").pack(anchor="w", padx=5)
        username_entry = ttk.Entry(fp_frame, width=40)
        username_entry.pack(pady=(0, 10), ipady=4)
        username_entry.focus_set()

        # --- Step 2: Security Question (initially hidden) ---
        question_label = ttk.Label(fp_frame, text="", wraplength=400)
        answer_label = ttk.Label(fp_frame, text="Your Answer:")
        answer_entry = ttk.Entry(fp_frame, width=40, show="*")
        new_pass_label = ttk.Label(fp_frame, text="New Password:")
        new_pass_entry = ttk.Entry(fp_frame, width=40, show="*")
        confirm_pass_label = ttk.Label(fp_frame, text="Confirm New Password:")
        confirm_pass_entry = ttk.Entry(fp_frame, width=40, show="*")
        reset_button = ttk.Button(fp_frame, text="Reset Password", bootstyle="success")

        def verify_username():
            username = username_entry.get().strip()
            user_data = self.db.execute("SELECT security_question, security_answer FROM users WHERE username=?", (username,), fetch="one")

            if not user_data or not user_data[0]:
                messagebox.showerror("Not Found", "No account found with that username or no security question is set.", parent=fp_win)
                return

            # Username found, show security question fields
            question, hashed_answer = user_data
            username_entry.config(state="disabled")
            verify_button.pack_forget()

            question_label.config(text=f"Question: {question}")
            question_label.pack(anchor="w", padx=5, pady=(10, 5))
            answer_label.pack(anchor="w", padx=5, pady=(10,0))
            answer_entry.pack(pady=(0, 10), ipady=4)
            new_pass_label.pack(anchor="w", padx=5)
            new_pass_entry.pack(pady=(0, 10), ipady=4)
            confirm_pass_label.pack(anchor="w", padx=5)
            confirm_pass_entry.pack(pady=(0, 15), ipady=4)
            reset_button.pack(pady=(10, 0), ipady=5)
            reset_button.config(command=lambda: update_password(username, hashed_answer))

        def update_password(username, correct_hashed_answer):
            if hash_password(answer_entry.get().strip()) != correct_hashed_answer:
                messagebox.showerror("Incorrect Answer", "The security answer is incorrect.", parent=fp_win)
                return

            new_pass = new_pass_entry.get()
            confirm_pass = confirm_pass_entry.get()
            if not new_pass or not confirm_pass or new_pass != confirm_pass:
                messagebox.showerror("Input Error", "Passwords cannot be empty and must match.", parent=fp_win)
                return
            
            self.db.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_pass), username))
            messagebox.showinfo("Success", "Your password has been reset successfully. You can now log in.", parent=fp_win)
            fp_win.destroy()

        verify_button = ttk.Button(fp_frame, text="Verify Username", command=verify_username, bootstyle="primary")
        verify_button.pack(pady=(10, 0), ipady=5)

    def main_app(self, username, role):
        """Display the main application window."""
        self.current_username = username
        self.root.unbind('<Return>')
        self.root.title(f"Library System - {username} ({role})")
        self.root.geometry("1200x750")

        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Top bar for theme toggle and other controls
        top_bar = ttk.Frame(self.main_frame)
        top_bar.pack(fill='x', pady=(0, 10))

        user_info = ttk.Label(top_bar, text=f"Logged in as: {username} ({role})", font=("Helvetica", 10))
        user_info.pack(side="left", padx=5)

        def toggle_theme():
            if theme_switch.get():
                self.root.style.theme_use("superhero")
            else: # pragma: no cover
                self.root.style.theme_use("flatly")
            # Redraw charts with the new theme colors
            dashboard_tab_instance.update_chart_themes()

        theme_switch = ttk.Checkbutton(top_bar, text="Dark Mode", bootstyle="switch", command=toggle_theme)
        theme_switch.pack(side="right") # pragma: no cover

        def show_about():
            messagebox.showinfo("About Library System", "Library Management System v1.0\n\nDeveloped by Gemini Code Assist.", parent=self.root)

        about_button = ttk.Button(top_bar, text="About", command=show_about, bootstyle="outline-secondary")
        about_button.pack(side="right", padx=10)

        logout_button = ttk.Button(top_bar, text="Logout", command=self.logout, bootstyle="outline-danger")
        logout_button.pack(side="right", padx=5)

        # Notebook for Books and Members
        notebook = ttk.Notebook(self.main_frame, bootstyle="primary")
        notebook.pack(fill="both", expand=True, pady=10)

        dashboard_tab_instance = DashboardTab(notebook, self)
        self.books_tab_instance = BooksTab(notebook, self, role)
        members_tab_instance = MembersTab(notebook, self, role)
        self.members_tab_instance = members_tab_instance # Store reference
        history_tab_instance = HistoryTab(notebook, self)
        reservations_tab_instance = ReservationsTab(notebook, self, self.books_tab_instance.load_books)
        overdue_tab_instance = OverdueTab(notebook, self)
        settings_tab_instance = SettingsTab(notebook, self, role)

        notebook.add(dashboard_tab_instance, text="Dashboard", compound='left')
        notebook.add(self.books_tab_instance, text="Book Management", compound='left')
        notebook.add(members_tab_instance, text="Member Management", compound='left')
        notebook.add(history_tab_instance, text="Transaction History", compound='left')
        notebook.add(reservations_tab_instance, text="Reservations", compound='left')
        notebook.add(overdue_tab_instance, text="Overdue Books", compound='left')
        notebook.add(settings_tab_instance, text="⚙️ Settings", compound='left')
        
        self.check_for_expired_reservations()
    def logout(self):
        """Logs the user out and returns to the login screen."""
        if messagebox.askyesno("Logout", "Are you sure you want to log out?"):
            if self.main_frame:
                self.main_frame.destroy()
                self.main_frame = None
            self.login_screen()

    def check_for_expired_reservations(self):
        """Checks for and cancels reservations that have been on hold for too long."""
        cutoff_date = (datetime.now() - timedelta(days=RESERVATION_HOLD_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

        expired_reservations = self.db.execute("""
            SELECT reservation_id, book_id FROM reservations
            WHERE status = 'notified' AND notification_date < ?
        """, (cutoff_date,), fetch="all")

        if not expired_reservations: # pragma: no cover
            return

        cancelled_count = 0
        for res_id, book_id in expired_reservations:
            # Cancel the reservation
            self.db.execute("UPDATE reservations SET status='expired' WHERE reservation_id=?", (res_id,))
            # Make the book available again
            self.db.execute("UPDATE books SET available_copies = available_copies + 1 WHERE book_id=?", (book_id,))
            cancelled_count += 1

        if cancelled_count > 0:
            messagebox.showinfo(
                "Reservations Expired",
                f"{cancelled_count} reservation(s) have expired and been automatically cancelled. The books are now available.",
                parent=self.root)


class BooksTab(BaseTab):
    def __init__(self, master, app_instance, role):
        super().__init__(master, app_instance)
        self.role = role
        self.setup_ui()

    def setup_ui(self):
        # Search Frame
        search_frame = ttk.Frame(self)
        search_frame.pack(fill="x", pady=10, padx=5)
        ttk.Label(search_frame, text="Search Books:").pack(side="left", padx=(0, 5))
        book_search_entry = ttk.Entry(search_frame)
        book_search_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Treeview
        book_cols = ("ID", "Title", "Author", "Department", "Category", "ISBN", "Year", "Total", "Available")
        book_tree = ttk.Treeview(self, columns=book_cols, show="tree headings", selectmode="extended")
        for col in book_cols:
            width = {"ID": 40, "Title": 220, "Author": 150, "Department": 120, "Total": 50, "Available": 70, "Year": 50}.get(col, 100)
            book_tree.heading(col, text=col, command=lambda c=col: self.sort_treeview(book_tree, c, False))
            book_tree.column(col, width=width, anchor='center')
        
        self.widgets['book_tree'] = book_tree # Store reference

        def search_books(query=None):
            self.load_books(book_tree, query)

        ttk.Button(search_frame, text="Search", command=lambda: search_books(book_search_entry.get()), bootstyle="info").pack(side="left", padx=5)
        ttk.Button(search_frame, text="Show All", command=lambda: search_books(), bootstyle="secondary").pack(side="left")
        ttk.Button(search_frame, text="Advanced Search", command=self.open_advanced_search_dialog, bootstyle="outline-primary").pack(side="left", padx=10)

        # Pack the treeview last to fill the remaining space
        book_tree.pack(fill="both", expand=True, padx=5)
        
        # Bind right-click to show context menu
        book_tree.bind("<Button-3>", lambda event: self.show_book_context_menu(event, self.role, book_tree))
        
        # Button Frame at the bottom
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side="bottom", fill="x", pady=10, padx=5)

        # Admin-specific controls
        if self.role == "admin":
            admin_btn_frame = ttk.Labelframe(btn_frame, text="Admin Controls", padding=10)
            admin_btn_frame.pack(side="left", padx=5)
            self.widgets['add_book_btn'] = ttk.Button(admin_btn_frame, text="Add Book", command=lambda: self.open_book_dialog(book_tree), bootstyle="success")
            self.widgets['edit_book_btn'] = ttk.Button(admin_btn_frame, text="Edit Book", command=lambda: self.open_book_dialog(book_tree, book_tree.item(book_tree.selection()[0])['values'][0]) if book_tree.selection() else messagebox.showwarning("Selection", "Select a book to edit."), bootstyle="info")
            self.widgets['delete_book_btn'] = ttk.Button(admin_btn_frame, text="Delete Book", command=lambda: self.delete_book(book_tree), bootstyle="danger")
            self.widgets['add_book_btn'].grid(row=0, column=0, padx=2, pady=2)
            self.widgets['edit_book_btn'].grid(row=0, column=1, padx=2, pady=2)
            self.widgets['delete_book_btn'].grid(row=0, column=2, padx=2, pady=2)
            ttk.Button(admin_btn_frame, text="Import CSV", command=lambda: self.import_books_from_csv(book_tree), bootstyle="primary").grid(row=0, column=3, padx=2, pady=2)
            ttk.Button(admin_btn_frame, text="Bulk Update Dept", command=self.bulk_update_department_dialog, bootstyle="warning").grid(row=1, column=0, padx=2, pady=2)
            ttk.Button(admin_btn_frame, text="Unused Books", command=self.show_never_borrowed_dialog, bootstyle="secondary").grid(row=1, column=1, padx=2, pady=2)
            ttk.Button(admin_btn_frame, text="Department Popularity", command=self.show_dept_popularity_dialog, bootstyle="info").grid(row=1, column=2, padx=2, pady=2)
            ttk.Button(admin_btn_frame, text="Longest Waitlists", command=self.show_longest_waitlists_dialog, bootstyle="info").grid(row=1, column=3, padx=2, pady=2)


        # General user controls
        general_btn_frame = ttk.Labelframe(btn_frame, text="Transactions", padding=10)
        general_btn_frame.pack(side="left", padx=5)
        ttk.Button(general_btn_frame, text="Borrow Book", command=self.borrow_book_dialog, bootstyle="primary").grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(general_btn_frame, text="Return Book", command=self.return_book_dialog, bootstyle="secondary").grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(general_btn_frame, text="Who has this book?", command=self.show_borrowers_dialog, bootstyle="outline-info").grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(general_btn_frame, text="Reserve Book", command=self.reserve_book_dialog, bootstyle="outline-warning").grid(row=1, column=1, padx=2, pady=2)

        self.load_books(book_tree)

    def load_books(self, book_tree, query=None):
        """Loads books into the treeview, supporting basic and advanced search."""
        for i in book_tree.get_children():
            book_tree.delete(i)
        
        if query is not None:
            # When searching, display a flat list of results for clarity
            book_tree.config(show="tree headings") # Keep tree view for consistency
            
            base_query = "SELECT book_id, title, author, department, category, isbn, publication_year, total_copies, available_copies FROM books"
            conditions = []
            params = []

            if isinstance(query, dict): # Advanced search
                if query.get('text', '').strip():
                    conditions.append("(title LIKE ? OR author LIKE ? OR isbn LIKE ?)")
                    params.extend([f"%{query['text']}%"] * 3)
                if query.get('department', '').strip():
                    conditions.append("department = ?")
                    params.append(query['department'])
                if query.get('category', '').strip():
                    conditions.append("category = ?")
                    params.append(query['category'])
                if query.get('year_from'):
                    conditions.append("publication_year >= ?")
                    params.append(query['year_from'])
                if query.get('year_to'):
                    conditions.append("publication_year <= ?")
                    params.append(query['year_to'])
            elif isinstance(query, str) and query: # Basic search
                conditions.append("(title LIKE ? OR author LIKE ? OR isbn LIKE ? OR department LIKE ?)")
                params.extend([f"%{query}%"] * 4)
            if conditions:
                sql_query = f"{base_query} WHERE {' AND '.join(conditions)}"
                rows = self.db.execute(sql_query, tuple(params), fetch="all")
                if rows:
                    for row in rows:
                        book_tree.insert("", END, values=row)
        else:
            # Default view: build the hierarchical view grouped by department
            book_tree.config(show="tree headings") # Show the tree column for hierarchy
            departments_with_counts = self.db.execute("SELECT department, COUNT(*) FROM books GROUP BY department ORDER BY department", fetch="all")
            if not departments_with_counts:
                return
    
            for dept_name, count in departments_with_counts:
                department_display = dept_name if dept_name and dept_name.strip() else "No Department"
                display_text = f"{department_display} ({count})"
                dept_id = book_tree.insert("", END, text=display_text, open=False, tags=('department_header',))
    
                # Get books for this department
                if not dept_name or not dept_name.strip():
                    books_in_dept = self.db.execute("SELECT book_id, title, author, department, category, isbn, publication_year, total_copies, available_copies FROM books WHERE department IS NULL OR department = ''", fetch="all")
                else:
                    books_in_dept = self.db.execute("SELECT book_id, title, author, department, category, isbn, publication_year, total_copies, available_copies FROM books WHERE department=?", (dept_name,), fetch="all")
                if books_in_dept:
                    for book_row in books_in_dept:
                        book_tree.insert(dept_id, END, text=book_row[1], values=book_row, tags=('item',))
    def open_book_dialog(self, book_tree, book_id=None):
        win = ttk.Toplevel(self.root)
        win.title("Add Book" if book_id is None else "Edit Book")
        win.geometry("400x350")
        
        labels = ["Title", "Author", "Department", "Category", "ISBN", "Publication Year", "Total Copies"]
        entries = {}
        
        if book_id:
            book_data = self.db.execute("SELECT title, author, department, category, isbn, publication_year, total_copies FROM books WHERE book_id=?", (book_id,), fetch="one")

        for i, text in enumerate(labels):
            ttk.Label(win, text=text).grid(row=i, column=0, padx=10, pady=5, sticky="w")
            ent = ttk.Entry(win, width=30)
            if book_id and book_data:
                ent.insert(0, book_data[i] if book_data[i] is not None else "")
            ent.grid(row=i, column=1, padx=10, pady=5)
            entries[text] = ent

        def save():
            try:
                title = entries["Title"].get().strip()
                author = entries["Author"].get().strip()
                if not title or not author:
                    messagebox.showerror("Input Error", "Title and Author are required.", parent=win)
                    return
                
                department = entries["Department"].get().strip()
                category = entries["Category"].get().strip()
                isbn = entries["ISBN"].get().strip()
                pub_year = int(entries["Publication Year"].get() or 0)
                total_copies = int(entries["Total Copies"].get() or 1)

                if book_id is None: # Add new book
                    self.db.execute("INSERT INTO books (title, author, department, category, isbn, publication_year, total_copies, available_copies) VALUES (?,?,?,?,?,?,?,?)",
                                (title, author, department, category, isbn, pub_year, total_copies, total_copies))
                    admin_logger.info(f"Added new book: '{title}' by {author}.", extra={'user': self.current_username})
                else: # Update existing book
                    # Adjust available copies if total copies changes
                    old_total = self.db.execute("SELECT total_copies, available_copies FROM books WHERE book_id=?", (book_id,), fetch="one")
                    copy_diff = total_copies - old_total[0]
                    new_available = old_total[1] + copy_diff
                    
                    self.db.execute("UPDATE books SET title=?, author=?, department=?, category=?, isbn=?, publication_year=?, total_copies=?, available_copies=? WHERE book_id=?",
                                (title, author, department, category, isbn, pub_year, total_copies, new_available, book_id))
                    admin_logger.info(f"Updated book ID {book_id} ('{title}').", extra={'user': self.current_username})
                
                self.load_books(book_tree)
                win.destroy()
            except ValueError:
                messagebox.showerror("Input Error", "Year and Copies must be numbers.", parent=win)

        ttk.Button(win, text="Save", command=save, bootstyle="success").grid(row=len(labels), columnspan=2, pady=15)

    def delete_book(self, book_tree):
        selected = book_tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a book to delete.")
            return
        
        book_id = book_tree.item(selected[0])['values'][0]
        book_title = book_tree.item(selected[0])['values'][1]
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete book ID {book_id}? This is irreversible."):
            self.db.execute("DELETE FROM books WHERE book_id=?", (book_id,))
            admin_logger.info(f"Deleted book ID {book_id} ('{book_title}').", extra={'user': self.current_username})
            self.load_books(book_tree)

    def import_books_from_csv(self, book_tree):
        file_path = filedialog.askopenfilename(
            title="Select a CSV file to import",
            filetypes=[("CSV files", "*.csv")]
        )
        if not file_path:
            return

        try:
            df = pd.read_csv(file_path)
            # Expected columns: title, author, department, category, isbn, publication_year, total_copies
            required_cols = {'title', 'author', 'total_copies'}
            if not required_cols.issubset(df.columns):
                messagebox.showerror("Import Error", f"CSV must contain at least these columns: {', '.join(required_cols)}")
                return

            imported_count = 0
            for index, row in df.iterrows():
                self.db.execute(
                    "INSERT INTO books (title, author, department, category, isbn, publication_year, total_copies, available_copies) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        row.get('title'),
                        row.get('author'),
                        row.get('department', ''),
                        row.get('category', ''),
                        row.get('isbn', ''),
                        int(row.get('publication_year', 0)),
                        int(row.get('total_copies', 1)),
                        int(row.get('total_copies', 1))
                    )
                )
                imported_count += 1
            
            messagebox.showinfo("Import Successful", f"Successfully imported {imported_count} books.")
            admin_logger.info(f"Imported {imported_count} books from CSV file: {file_path}.", extra={'user': self.current_username})
            self.load_books(book_tree)

        except Exception as e:
            messagebox.showerror("Import Failed", f"An error occurred during import:\n{e}")

    def open_advanced_search_dialog(self):
        """Opens a dialog for advanced book searching."""
        win = ttk.Toplevel(self.root)
        win.title("Advanced Book Search")
        win.geometry("450x350")

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        # --- Widgets ---
        ttk.Label(frame, text="Search Text (Title/Author/ISBN):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        text_entry = ttk.Entry(frame, width=40)
        text_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Department:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        departments = [""] + [d[0] for d in db.execute("SELECT DISTINCT department FROM books WHERE department IS NOT NULL AND department != '' ORDER BY department", fetch="all")]
        dept_combo = ttk.Combobox(frame, values=departments, state="readonly", width=38)
        dept_combo.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Category:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        categories = [""] + [c[0] for c in db.execute("SELECT DISTINCT category FROM books WHERE category IS NOT NULL AND category != '' ORDER BY category", fetch="all")]
        cat_combo = ttk.Combobox(frame, values=categories, state="readonly", width=38)
        cat_combo.grid(row=2, column=1, padx=5, pady=5)

        # Publication Year Range
        year_frame = ttk.Frame(frame)
        year_frame.grid(row=3, column=1, sticky="ew", padx=5, pady=5)
        ttk.Label(frame, text="Publication Year:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        ttk.Label(year_frame, text="From:").pack(side="left")
        year_from_entry = ttk.Entry(year_frame, width=10)
        year_from_entry.pack(side="left", padx=(2, 10))
        ttk.Label(year_frame, text="To:").pack(side="left")
        year_to_entry = ttk.Entry(year_frame, width=10)
        year_to_entry.pack(side="left", padx=2)

        def execute_advanced_search():
            try:
                year_from = int(year_from_entry.get()) if year_from_entry.get() else None
                year_to = int(year_to_entry.get()) if year_to_entry.get() else None
            except ValueError:
                messagebox.showerror("Input Error", "Publication years must be valid numbers.", parent=win)
                return

            search_criteria = {
                'text': text_entry.get().strip(),
                'department': dept_combo.get(),
                'category': cat_combo.get(),
                'year_from': year_from,
                'year_to': year_to
            }
            
            # Filter out empty criteria
            search_criteria = {k: v for k, v in search_criteria.items() if v}

            if not search_criteria:
                messagebox.showwarning("No Criteria", "Please enter at least one search criterion.", parent=win)
                return

            self.load_books(self.widgets['book_tree'], search_criteria)
            win.destroy()

        ttk.Button(frame, text="Search", command=execute_advanced_search, bootstyle="success").grid(row=4, columnspan=2, pady=20)

    def bulk_update_department_dialog(self):
        """Opens a dialog to bulk update the department for selected books."""
        book_tree = self.widgets.get('book_tree')
        if not book_tree or not book_tree.selection():
            messagebox.showwarning("Selection Error", "Please select one or more books to update.", parent=self.root)
            return

        selected_items = book_tree.selection()
        book_ids_to_update = [book_tree.item(item, 'values')[0] for item in selected_items]

        win = ttk.Toplevel(self.root)
        win.title("Bulk Update Department")
        win.geometry("400x200")

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"Updating {len(book_ids_to_update)} book(s).").pack(pady=(0, 10))

        ttk.Label(frame, text="New Department:").pack(anchor="w")
        
        # Get existing departments for the combobox
        departments = [d[0] for d in self.db.execute("SELECT DISTINCT department FROM books WHERE department IS NOT NULL AND department != '' ORDER BY department", fetch="all")]
        dept_combo = ttk.Combobox(frame, values=departments, width=35)
        dept_combo.pack(pady=5, ipady=4)
        dept_combo.focus_set()

        def save_bulk_update():
            new_department = dept_combo.get().strip()
            if not new_department:
                messagebox.showerror("Input Error", "Department name cannot be empty.", parent=win)
                return

            for book_id in book_ids_to_update:
                self.db.execute("UPDATE books SET department=? WHERE book_id=?", (new_department, book_id))
            messagebox.showinfo("Success", f"{len(book_ids_to_update)} book(s) have been moved to the '{new_department}' department.", parent=win)
            self.load_books(book_tree)
            win.destroy()

        ttk.Button(frame, text="Update Department", command=save_bulk_update, bootstyle="success").pack(pady=20)

    def show_borrowers_dialog(self):
        """Shows a dialog with a list of members who have borrowed the selected book."""
        book_tree = self.widgets.get('book_tree')
        if not book_tree or not book_tree.selection():
            messagebox.showwarning("Selection Error", "Please select a book from the list first.", parent=self.root)
            return

        selected_item = book_tree.selection()[0]
        book_id = book_tree.item(selected_item, 'values')[0]
        book_title = book_tree.item(selected_item, 'values')[1]

        borrowers = self.db.execute(""" 
            SELECT m.name, h.issue_date, h.due_date
            FROM history h
            JOIN members m ON h.member_id = m.member_id
            WHERE h.book_id = ? AND h.return_date IS NULL
            ORDER BY h.issue_date
        """, (book_id,), fetch="all")

        win = ttk.Toplevel(self.root)
        win.title(f"Borrowers of '{book_title}'")
        win.geometry("550x300")

        if not borrowers:
            ttk.Label(win, text="No one is currently borrowing this book.", font=("Helvetica", 12)).pack(pady=50)
            return

        ttk.Label(win, text=f"Showing current borrowers for: {book_title}", bootstyle="primary").pack(pady=10)

        borrower_cols = ("Member Name", "Issue Date", "Due Date")
        borrower_tree = ttk.Treeview(win, columns=borrower_cols, show="headings", height=10)
        for col in borrower_cols:
            borrower_tree.heading(col, text=col)
            borrower_tree.column(col, width=170, anchor="center")
        borrower_tree.pack(fill="both", expand=True, padx=10, pady=5)

        for borrower in borrowers:
            borrower_tree.insert("", END, values=borrower)

    def show_book_context_menu(self, event, role, book_tree):
        """Shows a context menu on right-clicking a book."""
        if not book_tree: return # Should not happen

        # Identify item under cursor
        row_id = book_tree.identify_row(event.y)
        if not row_id:
            return

        # Select the row before showing the menu
        book_tree.selection_set(row_id)

        context_menu = ttk.Menu(self.root, tearoff=0)

        if role == "admin":
            # Use the stored widget references for a more robust approach
            context_menu.add_command(label="Edit Book", command=self.widgets['edit_book_btn'].invoke)
            context_menu.add_command(label="Delete Book", command=self.widgets['delete_book_btn'].invoke)
            context_menu.add_separator()
        
        context_menu.add_command(label="Who has this book?", command=self.show_borrowers_dialog)
        context_menu.post(event.x_root, event.y_root)

    def show_dept_popularity_dialog(self):
        """Shows a dialog to view the most popular books by department."""
        win = ttk.Toplevel(self.root)
        win.title("Book Popularity by Department")
        win.geometry("700x500")

        # Top frame for department selection
        top_frame = ttk.Frame(win)
        top_frame.pack(fill="x", padx=10, pady=10)

        ttk.Label(top_frame, text="Select Department:").pack(side="left", padx=(0, 5))
        
        departments = self.db.execute("SELECT DISTINCT department FROM books WHERE department IS NOT NULL AND department != '' ORDER BY department", fetch="all")
        dept_list = [d[0] for d in departments]

        dept_combo = ttk.Combobox(top_frame, values=dept_list, state="readonly", width=30)
        dept_combo.pack(side="left")

        # Treeview for results
        cols = ("Rank", "Title", "Author", "Borrow Count")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        col_widths = {"Rank": 50, "Title": 300, "Author": 200, "Borrow Count": 100}
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=col_widths.get(col, 100), anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=5)

        def show_popularity(event=None):
            department = dept_combo.get()
            if not department:
                return

            for i in tree.get_children():
                tree.delete(i)

            popular_books = self.db.execute("""
                SELECT b.title, b.author, COUNT(h.transaction_id) as borrow_count
                FROM history h
                JOIN books b ON h.book_id = b.book_id
                WHERE b.department = ?
                GROUP BY h.book_id
                ORDER BY borrow_count DESC
            """, (department,), fetch="all")

            if popular_books:
                for i, (title, author, count) in enumerate(popular_books, 1):
                    tree.insert("", END, values=(i, title, author, count))

        dept_combo.bind("<<ComboboxSelected>>", show_popularity)
        ttk.Button(top_frame, text="Show", command=show_popularity, bootstyle="primary").pack(side="left", padx=10)

    def show_longest_waitlists_dialog(self):
        """Shows a dialog listing books with the most active reservations."""
        waitlist_data = self.db.execute("""
            SELECT b.title, b.author, COUNT(r.reservation_id) as waitlist_count
            FROM reservations r
            JOIN books b ON r.book_id = b.book_id
            WHERE r.status = 'active'
            GROUP BY r.book_id
            ORDER BY waitlist_count DESC
        """, fetch="all")

        win = ttk.Toplevel(self.root)
        win.title("Books with the Longest Waitlists")
        win.geometry("700x450")

        if not waitlist_data:
            ttk.Label(win, text="There are no books with active reservations.", font=("Helvetica", 12)).pack(pady=50)
            return

        ttk.Label(win, text="Showing books with the most active reservations (waitlisted).", bootstyle="info").pack(pady=10)

        cols = ("Rank", "Title", "Author", "Active Reservations")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=15)
        
        col_widths = {"Rank": 50, "Title": 300, "Author": 200, "Active Reservations": 120}
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=col_widths.get(col, 100), anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=5)

        for i, (title, author, count) in enumerate(waitlist_data, 1):
            tree.insert("", END, values=(i, title, author, count))

        # Add a scrollbar
        scrollbar = ttk.Scrollbar(tree, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def show_never_borrowed_dialog(self):
        """Shows a dialog listing all books that have never been borrowed."""
        unused_books = self.db.execute("""
            SELECT book_id, title, author, department, total_copies
            FROM books
            WHERE book_id NOT IN (SELECT DISTINCT book_id FROM history)
            ORDER BY title
        """, fetch="all")

        win = ttk.Toplevel(self.root)
        win.title("Books Never Borrowed")
        win.geometry("700x450")

        if not unused_books:
            ttk.Label(win, text="All books in the library have been borrowed at least once.", font=("Helvetica", 12)).pack(pady=50)
            return

        ttk.Label(win, text=f"Found {len(unused_books)} books that have never been borrowed.", bootstyle="info").pack(pady=10)

        cols = ("ID", "Title", "Author", "Department", "Copies")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=15)
        
        col_widths = {"ID": 50, "Title": 250, "Author": 150, "Department": 120, "Copies": 60}
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=col_widths.get(col, 100), anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=5)

        for book in unused_books:
            tree.insert("", END, values=book)

    def reserve_book_dialog(self):
        """Opens a dialog to reserve a book that is currently on loan."""
        book_tree = self.widgets.get('book_tree')
        if not book_tree or not book_tree.selection():
            messagebox.showwarning("Selection Error", "Please select a book to reserve.", parent=self.root)
            return

        selected_item = book_tree.selection()[0]
        values = book_tree.item(selected_item, 'values')
        book_id, book_title, available_copies = values[0], values[1], int(values[8])

        if available_copies > 0:
            messagebox.showinfo("Book Available", "This book is currently available and can be borrowed directly.", parent=self.root)
            return

        win = ttk.Toplevel(self.root)
        win.title(f"Reserve '{book_title}'")
        win.geometry("450x200")

        ttk.Label(win, text="Select Member to Reserve For:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        members = self.db.execute("SELECT member_id, name FROM members ORDER BY name", fetch="all")
        member_map = {m[1]: m[0] for m in members}
        member_combo = ttk.Combobox(win, values=list(member_map.keys()), width=50, state="readonly")
        member_combo.grid(row=0, column=1, padx=10, pady=10)

        def process_reservation():
            member_str = member_combo.get()
            if not member_str:
                messagebox.showerror("Input Error", "Please select a member.", parent=win)
                return
            member_id = member_map[member_str]
            self.db.execute("INSERT INTO reservations (book_id, member_id, reservation_date) VALUES (?, ?, ?)",
                       (book_id, member_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            messagebox.showinfo("Success", f"Book '{book_title}' has been reserved for {member_str}.", parent=win)
            win.destroy()

        ttk.Button(win, text="Confirm Reservation", command=process_reservation, bootstyle="success").grid(row=1, columnspan=2, pady=20)

    def borrow_book_dialog(self):
        win = ttk.Toplevel(self.root)
        win.title("Borrow a Book")
        win.geometry("450x250")

        # Book selection
        ttk.Label(win, text="Select Book:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        books = self.db.execute("SELECT book_id, title, author FROM books WHERE available_copies > 0", fetch="all")
        book_map = {f"{b[1]} by {b[2]}": b[0] for b in books}
        book_combo = ttk.Combobox(win, values=list(book_map.keys()), width=50, state="readonly")
        book_combo.grid(row=0, column=1, padx=10, pady=10)

        # Member selection
        ttk.Label(win, text="Select Member:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        members = self.db.execute("SELECT member_id, name FROM members", fetch="all")
        member_map = {m[1]: m[0] for m in members}
        member_combo = ttk.Combobox(win, values=list(member_map.keys()), width=50, state="readonly")
        member_combo.grid(row=1, column=1, padx=10, pady=10)

        def process_borrow():
            book_str = book_combo.get()
            member_str = member_combo.get()
            if not book_str or not member_str:
                messagebox.showerror("Input Error", "Please select both a book and a member.", parent=win)
                return
            
            book_id = book_map[book_str]
            member_id = member_map[member_str]
            
            # Decrease available copies
            self.db.execute("UPDATE books SET available_copies = available_copies - 1 WHERE book_id=?", (book_id,))
            # Calculate due date and create history record
            issue_date = datetime.now()
            due_date = issue_date + timedelta(days=BORROWING_PERIOD_DAYS)
            # Create history record
            transaction_id = self.db.execute("INSERT INTO history (book_id, member_id, issue_date, due_date) VALUES (?, ?, ?, ?)",
                       (book_id, member_id, issue_date.strftime("%Y-%m-%d %H:%M:%S"), due_date.strftime("%Y-%m-%d")))
            
            messagebox.showinfo("Success", "Book borrowed successfully.", parent=win)
            win.destroy()

            # Show receipt
            receipt_details = {
                "Transaction ID": transaction_id,
                "Book Title": book_str.split(" by ")[0],
                "Member Name": member_str,
                "Issue Date": issue_date.strftime("%Y-%m-%d"),
                "Due Date": due_date.strftime("%Y-%m-%d"),
            }
            _show_receipt(self.root, "Borrow Receipt", receipt_details)

            # Refresh the main book list
            self.load_books(self.widgets['book_tree'])
        ttk.Button(win, text="Confirm Borrow", command=process_borrow, bootstyle="success").grid(row=2, columnspan=2, pady=20)

    def return_book_dialog(self):
        win = ttk.Toplevel(self.root)
        win.title("Return a Book")
        win.geometry("500x300")

        ttk.Label(win, text="Select Book to Return (Issued to Member):").pack(pady=10)

        # Treeview for active transactions
        return_cols = ("Txn ID", "Book Title", "Member Name", "Issue Date", "Due Date")
        return_tree = ttk.Treeview(win, columns=return_cols, show="headings", height=8)
        for col in return_cols:
            return_tree.heading(col, text=col)
            return_tree.column(col, width=110, anchor="center")
        return_tree.pack(fill="x", padx=10)

        transactions = self.db.execute("""
            SELECT h.transaction_id, b.title, m.name, h.issue_date, h.due_date
            FROM history h
            JOIN books b ON h.book_id = b.book_id
            JOIN members m ON h.member_id = m.member_id
            WHERE h.return_date IS NULL
        """, fetch="all")
        
        if transactions:
            for t in transactions:
                return_tree.insert("", END, values=t)

        def process_return():
            selected = return_tree.selection()
            if not selected:
                messagebox.showerror("Input Error", "Please select a transaction to return.", parent=win)
                return
            
            transaction_id = return_tree.item(selected[0])['values'][0]
            book_title = return_tree.item(selected[0])['values'][1]
            member_name = return_tree.item(selected[0])['values'][2]
            due_date_str = return_tree.item(selected[0])['values'][4]

            # Calculate and display fine if overdue
            fine = 0.0
            if due_date_str:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                if datetime.now().date() > due_date:
                    days_overdue = (datetime.now().date() - due_date).days
                    fine = days_overdue * FINE_PER_DAY
                    if days_overdue > 0:
                        messagebox.showinfo("Fine Due", f"This book is {days_overdue} day(s) overdue.\nFine: ${fine:.2f}", parent=self.root)
            return_date = datetime.now()
            
            # Get book_id from transaction
            book_id = self.db.execute("SELECT book_id FROM history WHERE transaction_id=?", (transaction_id,), fetch="one")[0]

            # Check for active reservations on this book
            next_reservation = self.db.execute("SELECT r.reservation_id, m.name FROM reservations r JOIN members m ON r.member_id = m.member_id WHERE r.book_id=? AND r.status='active' ORDER BY r.reservation_date LIMIT 1", (book_id,), fetch="one")

            if next_reservation:
                # Book is reserved. Mark it as 'notified' and set the notification date. Do not increment available_copies.
                res_id, member_name = next_reservation
                notification_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.db.execute("UPDATE reservations SET status='notified', notification_date=? WHERE reservation_id=?", (notification_time, res_id))
                messagebox.showinfo("Book Reserved", f"This book has been returned and is on hold for the next member in the reservation list: {member_name}.\n\nPlease fulfill the reservation from the 'Reservations' tab.", parent=self.root)
            else:
                # No reservation, increment available copies
                self.db.execute("UPDATE books SET available_copies = available_copies + 1 WHERE book_id=?", (book_id,))

            # Update history with return date
            self.db.execute("UPDATE history SET return_date=? WHERE transaction_id=?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), transaction_id))

            messagebox.showinfo("Success", "Book returned successfully.", parent=win)
            win.destroy()

            # Show receipt
            receipt_details = {
                "Transaction ID": transaction_id,
                "Book Title": book_title,
                "Member Name": member_name,
                "Return Date": return_date.strftime("%Y-%m-%d"),
                "Fine Paid": f"${fine:.2f}",
            }
            _show_receipt(self.root, "Return Receipt", receipt_details)
            # Refresh main book list
            self.load_books(self.widgets['book_tree'])
        ttk.Button(win, text="Confirm Return", command=process_return, bootstyle="success").pack(pady=15)

class MembersTab(BaseTab):
    def __init__(self, master, app_instance, role):
        super().__init__(master, app_instance)
        self.role = role
        self.setup_ui()

    def setup_ui(self):
        search_frame = ttk.Frame(self)
        search_frame.pack(fill="x", pady=10, padx=5)
        ttk.Label(search_frame, text="Search Members:").pack(side="left", padx=(0, 5))
        member_search_entry = ttk.Entry(search_frame)
        member_search_entry.pack(side="left", fill="x", expand=True, padx=5)

        member_cols = ("ID", "Name", "Contact", "Department")
        member_tree = ttk.Treeview(self, columns=member_cols, show="tree headings")
        for col in member_cols:
            width = {"ID": 50, "Name": 200}.get(col, 150)
            member_tree.heading(col, text=col, command=lambda c=col: self.sort_treeview(member_tree, c, False))
            member_tree.column(col, width=width, anchor='w')
        
        self.widgets['member_tree'] = member_tree

        def load_members(query=None):
            for i in member_tree.get_children():
                member_tree.delete(i)

            if query:
                # When searching, display a flat list of results
                member_tree.config(show="headings")
                sql_query = "SELECT member_id, name, contact, department FROM members WHERE name LIKE ? OR contact LIKE ? OR department LIKE ? OR member_id LIKE ?"
                params = (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%")
                rows = self.db.execute(sql_query, params, fetch="all")
                if rows:
                    for row in rows:
                        member_tree.insert("", END, values=row)
            else:
                # Default view: build the hierarchical view grouped by department
                departments_with_counts = self.db.execute("SELECT department, COUNT(*) FROM members GROUP BY department ORDER BY department", fetch="all")
                member_tree.config(show="tree headings")
                if not departments_with_counts:
                    return

                for dept_name, count in departments_with_counts:
                    department_display = dept_name if dept_name and dept_name.strip() else "No Department"
                    display_text = f"{department_display} ({count})"

                    # Insert department as a parent node
                    dept_id = member_tree.insert("", END, text=display_text, open=False, tags=('department_header',))

                    # Get members for this department
                    if not dept_name or not dept_name.strip(): # This handles both None and empty strings
                        members_in_dept = self.db.execute("SELECT member_id, name, contact, department FROM members WHERE department IS NULL OR department = '' ORDER BY name", fetch="all")
                    else:
                        members_in_dept = self.db.execute("SELECT member_id, name, contact, department FROM members WHERE department=?", (dept_name,), fetch="all")
                    if members_in_dept:
                        for member_row in members_in_dept:
                            member_tree.insert(dept_id, END, text=member_row[1], values=member_row, tags=('item',))

        def open_member_dialog(member_id=None):
            win = ttk.Toplevel(self.root)
            win.title("Add Member" if member_id is None else "Edit Member")
            win.geometry("400x250")

            labels = ["Name", "Contact", "Department"]
            entries = {}
            
            if member_id:
                member_data = self.db.execute("SELECT name, contact, department FROM members WHERE member_id=?", (member_id,), fetch="one")

            for i, text in enumerate(labels):
                ttk.Label(win, text=text).grid(row=i, column=0, padx=10, pady=5, sticky="w")
                ent = ttk.Entry(win, width=30)
                if member_id and member_data:
                    ent.insert(0, member_data[i] if member_data[i] is not None else "")
                ent.grid(row=i, column=1, padx=10, pady=5)
                entries[text] = ent

            def save():
                name = entries["Name"].get().strip()
                if not name:
                    messagebox.showerror("Input Error", "Name is required.", parent=win)
                    return
                
                contact = entries["Contact"].get().strip()
                department = entries["Department"].get().strip()

                if member_id is None:
                    self.db.execute("INSERT INTO members (name, contact, department) VALUES (?,?,?)", (name, contact, department))
                else:
                    self.db.execute("UPDATE members SET name=?, contact=?, department=? WHERE member_id=?", (name, contact, department, member_id))
                
                load_members()
                win.destroy()

            ttk.Button(win, text="Save", command=save, bootstyle="success").grid(row=len(labels), columnspan=2, pady=15)

        def delete_member():
            selected = member_tree.selection()
            if not selected:
                messagebox.showwarning("No Selection", "Please select a member to delete.")
                return
            
            member_id = member_tree.item(selected[0])['values'][0]
            if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete member ID {member_id}?"):
                self.db.execute("DELETE FROM members WHERE member_id=?", (member_id,))
                load_members()

        ttk.Button(search_frame, text="Search", command=lambda: load_members(member_search_entry.get()), bootstyle="info").pack(side="left", padx=5)
        ttk.Button(search_frame, text="Show All", command=lambda: load_members(), bootstyle="secondary").pack(side="left")

        # --- Button Frame at the bottom ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(side="bottom", fill="x", pady=(10, 0), padx=5)

        if self.role == "admin":
            admin_btn_frame = ttk.Labelframe(btn_frame, text="Admin Controls", padding=10)
            admin_btn_frame.pack(side="left", padx=5)
            self.widgets['add_member_btn'] = ttk.Button(admin_btn_frame, text="Add Member", command=open_member_dialog, bootstyle="success")
            self.widgets['edit_member_btn'] = ttk.Button(admin_btn_frame, text="Edit Member", command=lambda: open_member_dialog(member_tree.item(member_tree.selection()[0])['values'][0]) if member_tree.selection() else messagebox.showwarning("Selection", "Select a member to edit."), bootstyle="info")
            self.widgets['delete_member_btn'] = ttk.Button(admin_btn_frame, text="Delete Member", command=delete_member, bootstyle="danger")
            self.widgets['add_member_btn'].pack(side="left", padx=5)
            self.widgets['edit_member_btn'].pack(side="left", padx=5)
            self.widgets['delete_member_btn'].pack(side="left", padx=5)
        
        general_btn_frame = ttk.Labelframe(btn_frame, text="Actions", padding=10)
        general_btn_frame.pack(side="left", padx=5)
        ttk.Button(general_btn_frame, text="View History", command=lambda: self.show_member_history_dialog(member_tree), bootstyle="outline-primary").pack(side="left", padx=5)

        member_tree.pack(fill="both", expand=True, padx=5, pady=(0,5))
        load_members()
        
        # Bind right-click to show context menu
        member_tree.bind("<Button-3>", lambda event: self.show_member_context_menu(event, self.role, member_tree))

    def show_member_history_dialog(self, member_tree):
        """Shows a dialog with the borrowing history of the selected member."""
        if not member_tree.selection():
            messagebox.showwarning("Selection Error", "Please select a member from the list first.", parent=self.root)
            return

        selected_item = member_tree.selection()[0]
        self._show_member_history_window(member_tree.item(selected_item, 'values')[0], member_tree.item(selected_item, 'values')[1])

    def _show_member_history_window(self, member_id, member_name):
        """Creates and displays the borrowing history window for a given member."""
        win = ttk.Toplevel(self.root)
        win.title(f"History for {member_name}")
        win.geometry("750x450")

        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Borrowing History Tab ---
        borrow_tab = ttk.Frame(notebook)
        notebook.add(borrow_tab, text="Borrowing History")

        borrow_history = self.db.execute("""
            SELECT b.title, h.issue_date, h.due_date, h.return_date
            FROM history h
            JOIN books b ON h.book_id = b.book_id
            WHERE h.member_id = ?
            ORDER BY h.issue_date DESC
        """, (member_id,), fetch="all")

        if not borrow_history:
            ttk.Label(borrow_tab, text=f"{member_name} has no borrowing history.", font=("Helvetica", 12)).pack(pady=50)
        else:
            history_cols = ("Book Title", "Issue Date", "Due Date", "Return Date")
            history_tree = ttk.Treeview(borrow_tab, columns=history_cols, show="headings", height=15)
            for col in history_cols:
                history_tree.heading(col, text=col)
                history_tree.column(col, width=160, anchor="center")
            history_tree.pack(fill="both", expand=True, pady=5)

            for record in borrow_history:
                history_tree.insert("", END, values=record)

        # --- Reservation History Tab ---
        reserve_tab = ttk.Frame(notebook)
        notebook.add(reserve_tab, text="Reservation History")

        reservation_history = self.db.execute("""
            SELECT b.title, r.reservation_date, r.notification_date, r.status
            FROM reservations r
            JOIN books b ON r.book_id = b.book_id
            WHERE r.member_id = ?
            ORDER BY r.reservation_date DESC
        """, (member_id,), fetch="all")

        if not reservation_history:
            ttk.Label(reserve_tab, text=f"{member_name} has no reservation history.", font=("Helvetica", 12)).pack(pady=50)
        else:
            res_cols = ("Book Title", "Reservation Date", "Notification Date", "Status")
            res_tree = ttk.Treeview(reserve_tab, columns=res_cols, show="headings", height=15)
            for col in res_cols:
                res_tree.heading(col, text=col)
                res_tree.column(col, width=160, anchor="center")
            res_tree.pack(fill="both", expand=True, pady=5)

            for record in reservation_history:
                res_tree.insert("", END, values=record)

    def show_member_context_menu(self, event, role, member_tree):
        """Shows a context menu on right-clicking a member."""
        if not member_tree: return # Should not happen

        # Identify item under cursor
        row_id = member_tree.identify_row(event.y)
        if not row_id:
            return

        # Select the row before showing the menu
        member_tree.selection_set(row_id)

        context_menu = ttk.Menu(self.root, tearoff=0)

        if role == "admin":
            # Use the stored widget references for a robust approach
            context_menu.add_command(label="Edit Member", command=self.widgets['edit_member_btn'].invoke)
            context_menu.add_command(label="Delete Member", command=self.widgets['delete_member_btn'].invoke)
            context_menu.add_separator()
        
        context_menu.add_command(label="View History", command=lambda: self.show_member_history_dialog(member_tree))

        # Display the menu at the cursor's position
        context_menu.post(event.x_root, event.y_root)


class HistoryTab(BaseTab):
    def __init__(self, master, app_instance):
        super().__init__(master, app_instance)
        self.setup_ui()

    def setup_ui(self):
        history_cols = ("ID", "Book Title", "Member Name", "Issue Date", "Return Date")
        history_tree = ttk.Treeview(self, columns=history_cols, show="headings")
        for col in history_cols:
            width = {"ID": 50, "Book Title": 250, "Member Name": 200}.get(col, 150)
            history_tree.heading(col, text=col, command=lambda c=col: self.sort_treeview(history_tree, c, False))
            history_tree.column(col, width=width, anchor='center')
        history_tree.pack(fill="both", expand=True, pady=10)

        def load_history():
            for i in history_tree.get_children():
                history_tree.delete(i)
            rows = self.db.execute("""
                SELECT h.transaction_id, b.title, m.name, h.issue_date, h.return_date
                FROM history h
                JOIN books b ON h.book_id = b.book_id
                JOIN members m ON h.member_id = m.member_id
                ORDER BY h.issue_date DESC
            """, fetch="all")
            if rows:
                for row in rows:
                    history_tree.insert("", END, values=row)
        
        ttk.Button(self, text="Refresh History", command=load_history, bootstyle="info").pack(pady=10)
        
        # Load initially
        self.bind("<Visibility>", lambda event: load_history())


class DashboardTab(BaseTab):
    def __init__(self, master, app_instance):
        super().__init__(master, app_instance)
        self.setup_ui()

    def setup_ui(self):
        self.dashboard_initialized = False
        # Top frame for controls like PDF export
        dashboard_top_frame = ttk.Frame(self)
        dashboard_top_frame.pack(fill="x", padx=5, pady=(5, 0))

        # Frame for statistics cards
        stats_frame = ttk.Frame(self)
        stats_frame.pack(fill="x", pady=10, padx=5)

        self.total_books_card = ttk.Label(stats_frame, text="Total Books\n0", font=("Helvetica", 14), bootstyle="primary, inverse", padding=20, anchor="center")
        self.total_books_card.pack(side="left", fill="x", expand=True, padx=5)

        self.total_members_card = ttk.Label(stats_frame, text="Total Members\n0", font=("Helvetica", 14), bootstyle="success, inverse", padding=20, anchor="center")
        self.total_members_card.pack(side="left", fill="x", expand=True, padx=5)

        self.on_loan_card = ttk.Label(stats_frame, text="Books on Loan\n0", font=("Helvetica", 14), bootstyle="warning, inverse", padding=20, anchor="center")
        self.on_loan_card.pack(side="left", fill="x", expand=True, padx=5)

        def save_dashboard_as_pdf():
            """Captures the dashboard tab and saves it as a PDF."""
            file_path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF files", "*.pdf")],
                title="Save Dashboard as PDF"
            )
            if not file_path:
                return

            # Get the geometry of the dashboard tab
            x = self.winfo_rootx()
            y = self.winfo_rooty()
            width = self.winfo_width()
            height = self.winfo_height()
            img = ImageGrab.grab(bbox=(x, y, x + width, y + height))
            img.save(file_path, "PDF", resolution=100.0)
            messagebox.showinfo("Export Successful", f"Dashboard saved as PDF to:\n{file_path}", parent=self.root)

        ttk.Button(dashboard_top_frame, text="Save as PDF", command=save_dashboard_as_pdf, bootstyle="success").pack(side="right")

        # Main frame to hold two charts side-by-side
        charts_container = ttk.Frame(self, name="charts_container")
        charts_container.pack(fill="both", expand=True, padx=5, pady=5)

        # Top row for charts (2 charts)
        top_charts_frame = ttk.Frame(charts_container)
        top_charts_frame.pack(fill="both", expand=True, pady=(0, 5))
        self.widgets['top_charts_frame'] = top_charts_frame

        # Bottom row for charts (3 charts)
        bottom_charts_frame = ttk.Frame(charts_container)
        bottom_charts_frame.pack(fill="both", expand=True, pady=(5, 0))
        self.widgets['bottom_charts_frame'] = bottom_charts_frame

        def on_dashboard_visible(event):
            if not self.dashboard_initialized:
                self.initialize_dashboard_charts()
                self.dashboard_initialized = True
            self.refresh_dashboard()

        self.bind("<Visibility>", on_dashboard_visible)

    def initialize_dashboard_charts(self):
        """Creates the matplotlib charts for the dashboard. Called only once."""
        top_charts_frame = self.widgets['top_charts_frame']
        bottom_charts_frame = self.widgets['bottom_charts_frame']

        # --- Top Row ---
        # Top Books Chart (Left)
        top_books_frame = ttk.Labelframe(top_charts_frame, text="Top 5 Most Borrowed Books (30 Days)", padding=10)
        top_books_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        fig_top_books, ax_top_books = plt.subplots(figsize=(5, 4))
        self.widgets['fig_top_books'] = fig_top_books
        self.widgets['ax_top_books'] = ax_top_books
        canvas_top_books = FigureCanvasTkAgg(fig_top_books, master=top_books_frame)
        canvas_top_books.get_tk_widget().pack(fill="both", expand=True)
        self.widgets['canvas_top_books'] = canvas_top_books

        # Books per Category Chart (Right)
        category_books_frame = ttk.Labelframe(top_charts_frame, text="Books by Category", padding=10)
        category_books_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))
        fig_category_books, ax_category_books = plt.subplots(figsize=(5, 4))
        self.widgets['fig_category_books'] = fig_category_books
        self.widgets['ax_category_books'] = ax_category_books
        canvas_category_books = FigureCanvasTkAgg(fig_category_books, master=category_books_frame)
        canvas_category_books.get_tk_widget().pack(fill="both", expand=True)
        self.widgets['canvas_category_books'] = canvas_category_books
        canvas_category_books.mpl_connect('button_press_event', self.on_category_chart_click)

        # --- Bottom Row ---
        # Borrowing Activity Chart (Left)
        activity_frame = ttk.Labelframe(bottom_charts_frame, text="Borrowing Activity (Last 30 Days)", padding=10)
        activity_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        fig_activity, ax_activity = plt.subplots(figsize=(4, 4))
        self.widgets['fig_activity'] = fig_activity
        self.widgets['ax_activity'] = ax_activity
        canvas_activity = FigureCanvasTkAgg(fig_activity, master=activity_frame)
        canvas_activity.get_tk_widget().pack(fill="both", expand=True)
        self.widgets['canvas_activity'] = canvas_activity

        # Top Overdue Members Chart (Middle)
        overdue_members_frame = ttk.Labelframe(bottom_charts_frame, text="Top 5 Members by Fine Amount", padding=10)
        overdue_members_frame.pack(side="left", fill="both", expand=True, padx=(5, 5))
        fig_overdue_members, ax_overdue_members = plt.subplots(figsize=(4, 4))
        self.widgets['fig_overdue_members'] = fig_overdue_members
        self.widgets['ax_overdue_members'] = ax_overdue_members
        canvas_overdue_members = FigureCanvasTkAgg(fig_overdue_members, master=overdue_members_frame)
        canvas_overdue_members.get_tk_widget().pack(fill="both", expand=True)
        self.widgets['canvas_overdue_members'] = canvas_overdue_members
        canvas_overdue_members.mpl_connect('button_press_event', self.on_overdue_chart_click)

        # Top Abandoned Reservations Chart (Right)
        abandoned_res_frame = ttk.Labelframe(bottom_charts_frame, text="Top 5 Abandoned Reservations", padding=10)
        abandoned_res_frame.pack(side="right", fill="both", expand=True, padx=(0, 0))
        fig_abandoned, ax_abandoned = plt.subplots(figsize=(4, 4))
        self.widgets['fig_abandoned'] = fig_abandoned
        self.widgets['ax_abandoned'] = ax_abandoned
        canvas_abandoned = FigureCanvasTkAgg(fig_abandoned, master=abandoned_res_frame)
        canvas_abandoned.get_tk_widget().pack(fill="both", expand=True)
        self.widgets['canvas_abandoned'] = canvas_abandoned
        canvas_abandoned.mpl_connect('button_press_event', self.on_abandoned_res_chart_click)

    def refresh_dashboard(self):
        """Updates all dashboard components."""
        # Update stat cards
        total_books = self.db.execute("SELECT SUM(total_copies) FROM books", fetch="one")[0] or 0
        total_members = self.db.execute("SELECT COUNT(*) FROM members", fetch="one")[0] or 0
        on_loan = self.db.execute("SELECT COUNT(*) FROM history WHERE return_date IS NULL", fetch="one")[0] or 0
        self.total_books_card.config(text=f"Total Books\n{total_books}")
        self.total_members_card.config(text=f"Total Members\n{total_members}")
        self.on_loan_card.config(text=f"Books on Loan\n{on_loan}")

        # Update charts only if they have been initialized
        if self.dashboard_initialized:
            self.update_all_charts_data()

    def _style_chart_ax(self, ax, fig, has_spines=True):
        """Applies common styling to a chart's axes and figure based on the current theme."""
        bg_color = self.root.style.colors.get('bg')
        fg_color = self.root.style.colors.get('fg')

        fig.patch.set_facecolor(bg_color)
        ax.set_facecolor(bg_color)
        ax.tick_params(axis='x', colors=fg_color)
        ax.tick_params(axis='y', colors=fg_color)
        
        # Style the title and labels
        ax.title.set_color(fg_color)
        ax.xaxis.label.set_color(fg_color)
        ax.yaxis.label.set_color(fg_color)

        if has_spines:
            ax.spines['left'].set_color(fg_color)
            ax.spines['bottom'].set_color(fg_color)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
        else: # For pie charts
            for spine in ax.spines.values():
                spine.set_visible(False)

        fig.tight_layout()
        fig.canvas.draw()

    def update_all_charts_data(self):
        """Fetches new data and updates all dashboard charts."""
        primary_color = self.root.style.colors.get('primary')
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        
        # --- 1. Update Top 5 Borrowed Books Chart ---
        ax_top_books = self.widgets['ax_top_books']
        fig_top_books = self.widgets['fig_top_books']
        ax_top_books.clear()
        top_books_data = self.db.execute("""
            SELECT b.title, COUNT(h.transaction_id) as count 
            FROM history h JOIN books b ON h.book_id = b.book_id 
            WHERE h.issue_date >= ?
            GROUP BY h.book_id ORDER BY count DESC LIMIT 5
        """, (thirty_days_ago,), fetch="all")
        if top_books_data:
            titles, counts = zip(*top_books_data)
            ax_top_books.barh(titles, counts, color=primary_color)
            ax_top_books.invert_yaxis() # Highest on top
        else:
            ax_top_books.text(0.5, 0.5, "No borrowing data available.", ha='center', va='center')

        # --- 2. Update Books per Department Chart ---
        fig_category_books = self.widgets['fig_category_books']
        ax_category_books = self.widgets['ax_category_books']
        ax_category_books.clear()
        category_data = self.db.execute("SELECT category, COUNT(*) FROM books GROUP BY category", fetch="all")
        if category_data:
            labels = [d[0] if d[0] else 'Uncategorized' for d in category_data]
            sizes = [d[1] for d in category_data]
            wedges, texts, autotexts = ax_category_books.pie(sizes, labels=labels, autopct='%1.f%%', startangle=90, textprops={'color': self.root.style.colors.get('fg')})
            self.widgets['category_pie_wedges'] = wedges
        else:
            ax_category_books.text(0.5, 0.5, "No books in the library.", ha='center', va='center')

        # --- 3. Update Borrowing Activity Chart ---
        activity_data = self.db.execute("""
            SELECT date(issue_date) as day, COUNT(*) FROM history 
            WHERE issue_date >= ? GROUP BY day ORDER BY day
        """, (thirty_days_ago,), fetch="all")

        ax_activity = self.widgets['ax_activity']
        fig_activity = self.widgets['fig_activity']
        ax_activity.clear()
        if activity_data:
            dates_str, counts = zip(*activity_data)
            dates = [datetime.strptime(d, '%Y-%m-%d') for d in dates_str]
            ax_activity.plot(dates, counts, marker='o', linestyle='-', color=primary_color)
            ax_activity.set_ylabel("Books Borrowed")
            fig_activity.autofmt_xdate()
        else:
            ax_activity.text(0.5, 0.5, "No activity in the last 30 days.", ha='center', va='center')
        
        # --- 4. Update Top Overdue Members Chart ---
        ax_overdue = self.widgets['ax_overdue_members']
        fig_overdue = self.widgets['fig_overdue_members']
        ax_overdue.clear()
        overdue_members_data = self._get_overdue_fine_data(limit=5)
        if overdue_members_data:
            names, fines = zip(*overdue_members_data)
            ax_overdue.barh(names, fines, color=self.root.style.colors.get('danger'))
            ax_overdue.set_xlabel("Total Fine (USD)")
            ax_overdue.invert_yaxis()
        else:
            ax_overdue.text(0.5, 0.5, "No members with fines.", ha='center', va='center')

        # --- 5. Update Top Abandoned Reservations Chart ---
        ax_abandoned = self.widgets['ax_abandoned']
        fig_abandoned = self.widgets['fig_abandoned']
        ax_abandoned.clear()
        abandoned_data = self.db.execute("""
            SELECT b.title, COUNT(r.reservation_id) as count FROM reservations r
            JOIN books b ON r.book_id = b.book_id WHERE r.status IN ('expired', 'cancelled')
            GROUP BY r.book_id ORDER BY count DESC LIMIT 5
        """, fetch="all")
        if abandoned_data:
            titles, counts = zip(*abandoned_data)
            ax_abandoned.barh(titles, counts, color=self.root.style.colors.get('warning'))
            ax_abandoned.invert_yaxis()
        else:
            ax_abandoned.text(0.5, 0.5, "No abandoned reservations.", ha='center', va='center')

        # After all data is updated, apply the theme styling
        self.update_chart_themes()

    def update_chart_themes(self):
        """Updates the colors of all charts to match the current theme."""
        self._style_chart_ax(self.widgets['ax_top_books'], self.widgets['fig_top_books'])
        self._style_chart_ax(self.widgets['ax_category_books'], self.widgets['fig_category_books'], has_spines=False)
        self._style_chart_ax(self.widgets['ax_activity'], self.widgets['fig_activity'])
        self._style_chart_ax(self.widgets['ax_overdue_members'], self.widgets['fig_overdue_members'])
        self._style_chart_ax(self.widgets['ax_abandoned'], self.widgets['fig_abandoned'])
        
        # Special case for pie chart text color
        fg_color = self.root.style.colors.get('fg')
        if 'category_pie_wedges' in self.widgets:
            for text in self.widgets['ax_category_books'].texts:
                text.set_color(fg_color) # Ensure labels and percentages are the right color
            self.widgets['canvas_category_books'].draw()

    def on_overdue_chart_click(self, event):
        """Handles clicks on the overdue members chart to show their history."""
        if event.inaxes != self.widgets['ax_overdue_members']:
            return

        ax = self.widgets['ax_overdue_members']
        # For a horizontal bar chart, ydata corresponds to the bar index
        if event.ydata is None:
            return

        clicked_index = int(round(event.ydata))
        
        # The labels on the y-axis are the member names
        labels = [tick.get_text() for tick in ax.get_yticklabels()]

        if 0 <= clicked_index < len(labels):
            member_name = labels[clicked_index]
            
            # Fetch member_id from the database using the name
            member_data = self.db.execute("SELECT member_id FROM members WHERE name=?", (member_name,), fetch="one")
            if member_data:
                member_id = member_data[0]
                self.app.members_tab_instance._show_member_history_window(member_id, member_name)

    def on_category_chart_click(self, event):
        """Handles clicks on the category pie chart to show a list of books."""
        if event.inaxes != self.widgets['ax_category_books']:
            return

        wedges = self.widgets.get('category_pie_wedges', [])
        clicked_wedge = None
        for i, wedge in enumerate(wedges):
            # Check if the click event is inside the current wedge
            if wedge.contains_point([event.x, event.y]):
                clicked_wedge = wedge
                
                # Get the category name from the wedge's label
                category_name = clicked_wedge.get_label()
                if category_name == 'Uncategorized':
                    books = self.db.execute("SELECT title, author, available_copies FROM books WHERE category IS NULL OR category = '' ORDER BY title", fetch="all")
                else:
                    books = self.db.execute("SELECT title, author, available_copies FROM books WHERE category = ? ORDER BY title", (category_name,), fetch="all")

                self._show_book_list_window(f"Books in '{category_name}'", books)
                break

    def _show_book_list_window(self, title, books):
        """Creates and displays a window with a list of books."""
        win = ttk.Toplevel(self.root)
        win.title(title)
        win.geometry("600x400")

        if not books:
            ttk.Label(win, text="No books found for this category.", font=("Helvetica", 12)).pack(pady=50)
            return

        book_cols = ("Title", "Author", "Available Copies")
        book_tree = ttk.Treeview(win, columns=book_cols, show="headings", height=15)
        
        col_widths = {"Title": 280, "Author": 200, "Available Copies": 100}
        for col in book_cols:
            book_tree.heading(col, text=col)
            book_tree.column(col, width=col_widths.get(col, 150), anchor="center")
        
        book_tree.pack(fill="both", expand=True, padx=10, pady=10)

        # Add a scrollbar
        scrollbar = ttk.Scrollbar(book_tree, orient="vertical", command=book_tree.yview)
        book_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        for book in books:
            book_tree.insert("", END, values=book)

    def on_abandoned_res_chart_click(self, event):
        """Handles clicks on the abandoned reservations chart."""
        if event.inaxes != self.widgets['ax_abandoned']:
            return

        ax = self.widgets['ax_abandoned']
        if event.ydata is None:
            return

        clicked_index = int(round(event.ydata))
        labels = [tick.get_text() for tick in ax.get_yticklabels()]

        if 0 <= clicked_index < len(labels):
            book_title = labels[clicked_index]
            
            book_data = self.db.execute("SELECT book_id FROM books WHERE title=?", (book_title,), fetch="one")
            if book_data:
                book_id = book_data[0]
                
                abandoned_list = self.db.execute("""
                    SELECT m.name, r.reservation_date, r.status
                    FROM reservations r
                    JOIN members m ON r.member_id = m.member_id
                    WHERE r.book_id = ? AND r.status IN ('cancelled', 'expired')
                    ORDER BY r.reservation_date DESC
                """, (book_id,), fetch="all")

                self._show_abandoned_reservations_window(book_title, abandoned_list)

    def _show_abandoned_reservations_window(self, book_title, reservations):
        """Displays a window with details of abandoned reservations for a book."""
        win = ttk.Toplevel(self.root)
        win.title(f"Abandoned Reservations for '{book_title}'")
        win.geometry("600x400")

        if not reservations:
            ttk.Label(win, text="No detailed data found.", font=("Helvetica", 12)).pack(pady=50)
            return

        cols = ("Member Name", "Reservation Date", "Final Status")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=15)
        
        col_widths = {"Member Name": 200, "Reservation Date": 200, "Final Status": 100}
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=col_widths.get(col, 150), anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=10)

        for res in reservations:
            tree.insert("", END, values=res)

    def _get_overdue_fine_data(self, limit=None):
        """Helper to calculate total fines per member for overdue books."""
        overdue_books = self.db.execute("""
            SELECT m.name, h.due_date
            FROM history h
            JOIN members m ON h.member_id = m.member_id
            WHERE h.return_date IS NULL AND date(h.due_date) < date('now')
        """, fetch="all")

        fines = {}
        today = datetime.now().date()
        for name, due_date_str in overdue_books:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            days_overdue = (today - due_date).days
            fine_amount = days_overdue * FINE_PER_DAY
            fines[name] = fines.get(name, 0) + fine_amount
        
        sorted_fines = sorted(fines.items(), key=lambda item: item[1], reverse=True)
        
        return sorted_fines[:limit] if limit else sorted_fines


class OverdueTab(BaseTab):
    def __init__(self, master, app_instance):
        super().__init__(master, app_instance)
        self.setup_ui()

    def setup_ui(self):
        overdue_cols = ("Book Title", "Member Name", "Issue Date", "Due Date", "Days Overdue", "Fine (USD)")
        overdue_tree = ttk.Treeview(self, columns=overdue_cols, show="headings")
        for col in overdue_cols:
            width = {"Book Title": 250, "Member Name": 200}.get(col, 120)
            overdue_tree.heading(col, text=col, command=lambda c=col: self.sort_treeview(overdue_tree, c, False))
            overdue_tree.column(col, width=width, anchor='center')
        overdue_tree.pack(fill="both", expand=True, pady=10)

        def load_overdue_books():
            for i in overdue_tree.get_children():
                overdue_tree.delete(i)

            rows = self.db.execute("""
                SELECT b.title, m.name, h.issue_date, h.due_date
                FROM history h
                JOIN books b ON h.book_id = b.book_id
                JOIN members m ON h.member_id = m.member_id
                WHERE h.return_date IS NULL AND date(h.due_date) < date('now')
            """, fetch="all")

            if rows:
                for row in rows:
                    due_date = datetime.strptime(row[3], '%Y-%m-%d').date()
                    days_overdue = (datetime.now().date() - due_date).days
                    fine = days_overdue * FINE_PER_DAY
                    display_row = row + (days_overdue, f"{fine:.2f}")
                    overdue_tree.insert("", END, values=display_row)

        def export_overdue_to_excel():
            rows = self.db.execute("""
                SELECT b.title, m.name, h.issue_date, h.due_date
                FROM history h
                JOIN books b ON h.book_id = b.book_id
                JOIN members m ON h.member_id = m.member_id
                WHERE h.return_date IS NULL AND date(h.due_date) < date('now')
            """, fetch="all")

            if not rows:
                messagebox.showinfo("No Data", "There are no overdue books to export.", parent=self.root)
                return

            processed_rows = []
            for row in rows:
                due_date = datetime.strptime(row[3], '%Y-%m-%d').date()
                days_overdue = (datetime.now().date() - due_date).days
                fine = days_overdue * FINE_PER_DAY
                processed_rows.append(row + (days_overdue, f"{fine:.2f}"))

            columns = ["Book Title", "Member Name", "Issue Date", "Due Date", "Days Overdue", "Fine (USD)"]
            df = pd.DataFrame(processed_rows, columns=columns)

            file_path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")], title="Save Overdue Books Report")
            if file_path:
                df.to_excel(file_path, index=False)
                messagebox.showinfo("Export Successful", f"Overdue books report saved to:\n{file_path}", parent=self.root)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Refresh Overdue List", command=load_overdue_books, bootstyle="warning").pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Export to Excel", command=export_overdue_to_excel, bootstyle="success").pack(side="left", padx=5)
        self.bind("<Visibility>", lambda event: load_overdue_books())


class ReservationsTab(BaseTab):
    def __init__(self, master, app_instance, book_refresh_callback):
        super().__init__(master, app_instance)
        self.book_refresh_callback = book_refresh_callback
        self.setup_ui()

    def setup_ui(self):
        res_cols = ("Res ID", "Book Title", "Member Name", "Reservation Date", "Status")
        res_tree = ttk.Treeview(self, columns=res_cols, show="headings")
        for col in res_cols:
            width = {"Book Title": 300, "Member Name": 200}.get(col, 150)
            res_tree.heading(col, text=col, command=lambda c=col: self.sort_treeview(res_tree, c, False))
            res_tree.column(col, width=width, anchor='w')
        res_tree.pack(fill="both", expand=True, pady=10)
        self.widgets['reservations_tree'] = res_tree # pragma: no cover

        def load_reservations():
            for i in res_tree.get_children():
                res_tree.delete(i)

            rows = self.db.execute("""
                SELECT r.reservation_id, b.title, m.name, r.reservation_date, r.status
                FROM reservations r
                JOIN books b ON r.book_id = b.book_id
                JOIN members m ON r.member_id = m.member_id
                WHERE r.status IN ('active', 'notified')
                ORDER BY r.reservation_date
            """, fetch="all")

            if rows:
                for row in rows:
                    res_tree.insert("", END, values=row)

        def fulfill_reservation():
            selected = res_tree.selection()
            if not selected:
                messagebox.showwarning("Selection Error", "Please select a reservation to fulfill.", parent=self.root)
                return

            res_id = res_tree.item(selected[0])['values'][0]
            
            # Get reservation details
            res_data = self.db.execute("SELECT book_id, member_id FROM reservations WHERE reservation_id=?", (res_id,), fetch="one")
            if not res_data: return

            # Check if the book is actually on hold (i.e., status is 'notified')
            res_status = self.db.execute("SELECT status FROM reservations WHERE reservation_id=?", (res_id,), fetch="one")[0]
            if res_status != 'notified':
                messagebox.showwarning("Fulfillment Error", "This reservation cannot be fulfilled yet. The book has not been returned by the previous borrower.", parent=self.root)
                return
            book_id, member_id = res_data

            # Update reservation status to 'fulfilled'
            self.db.execute("UPDATE reservations SET status='fulfilled' WHERE reservation_id=?", (res_id,))

            # Create a new borrow history record
            issue_date = datetime.now()
            due_date = issue_date + timedelta(days=BORROWING_PERIOD_DAYS)
            self.db.execute("INSERT INTO history (book_id, member_id, issue_date, due_date) VALUES (?, ?, ?, ?)",
                       (book_id, member_id, issue_date.strftime("%Y-%m-%d %H:%M:%S"), due_date.strftime("%Y-%m-%d")))
            
            messagebox.showinfo("Success", "Reservation fulfilled. The book is now borrowed by the member.", parent=self.root)
            load_reservations()
            self.book_refresh_callback(self.app.books_tab_instance.widgets['book_tree']) # Refresh book list

        def cancel_reservation():
            selected = res_tree.selection()
            if not selected:
                messagebox.showwarning("Selection Error", "Please select a reservation to cancel.", parent=self.root)
                return
            
            res_id = res_tree.item(selected[0])['values'][0]
            if not messagebox.askyesno("Confirm Cancel", "Are you sure you want to cancel this reservation?"):
                return

            # Get reservation details to check status
            res_data = self.db.execute("SELECT book_id, status FROM reservations WHERE reservation_id=?", (res_id,), fetch="one")
            if not res_data: return
            book_id, status = res_data

            self.db.execute("UPDATE reservations SET status='cancelled' WHERE reservation_id=?", (res_id,))
            if status == 'notified': # Only increment copies if the book was being held
                self.db.execute("UPDATE books SET available_copies = available_copies + 1 WHERE book_id=?", (book_id,))
            
            messagebox.showinfo("Cancelled", "Reservation has been cancelled. The book is now available.", parent=self.root)
            load_reservations()
            self.book_refresh_callback(self.app.books_tab_instance.widgets['book_tree'])

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Fulfill Reservation", command=fulfill_reservation, bootstyle="success").pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel Reservation", command=cancel_reservation, bootstyle="danger").pack(side="left", padx=5)
        self.bind("<Visibility>", lambda event: load_reservations())


class SettingsTab(BaseTab):
    def __init__(self, master, app_instance, role):
        super().__init__(master, app_instance)
        self.role = role
        self.setup_ui()

    def setup_ui(self):
        # --- Change Password Frame (for all users) ---
        pwd_frame = ttk.Labelframe(self, text="Change Your Password", padding=20)
        pwd_frame.pack(fill="x", padx=10, pady=10)

        ttk.Label(pwd_frame, text="Current Password:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        current_pwd_entry = ttk.Entry(pwd_frame, show="*", width=30)
        current_pwd_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(pwd_frame, text="New Password:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        new_pwd_entry = ttk.Entry(pwd_frame, show="*", width=30)
        new_pwd_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(pwd_frame, text="Confirm New Password:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        confirm_pwd_entry = ttk.Entry(pwd_frame, show="*", width=30)
        confirm_pwd_entry.grid(row=2, column=1, padx=5, pady=5)

        def change_password():
            current_pwd = current_pwd_entry.get()
            new_pwd = new_pwd_entry.get()
            confirm_pwd = confirm_pwd_entry.get()

            if not (current_pwd and new_pwd and confirm_pwd):
                messagebox.showerror("Input Error", "All password fields are required.", parent=self.root)
                return

            # Verify current password
            user_data = self.db.execute("SELECT password FROM users WHERE username=?", (self.current_username,), fetch="one")
            if not user_data or user_data[0] != hash_password(current_pwd):
                messagebox.showerror("Authentication Error", "Your current password is incorrect.", parent=self.root)
                return

            if new_pwd != confirm_pwd:
                messagebox.showerror("Input Error", "New passwords do not match.", parent=self.root)
                return

            # Update password
            self.db.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_pwd), self.current_username))
            messagebox.showinfo("Success", "Your password has been changed successfully.", parent=self.root)
            
            # Clear fields
            current_pwd_entry.delete(0, END)
            new_pwd_entry.delete(0, END)
            confirm_pwd_entry.delete(0, END)

        ttk.Button(pwd_frame, text="Update Password", command=change_password, bootstyle="success").grid(row=3, columnspan=2, pady=15)

        # --- Admin-only settings ---
        if self.role != 'admin':
            return

        admin_settings_frame = ttk.Labelframe(self, text="Admin Settings", padding=10)
        admin_settings_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Database Management ---
        db_mgmt_frame = ttk.Labelframe(admin_settings_frame, text="Database Management", padding=10)
        db_mgmt_frame.pack(fill="x", pady=5)

        # --- User Management ---
        user_mgmt_frame = ttk.Labelframe(admin_settings_frame, text="User Management", padding=10)
        user_mgmt_frame.pack(fill="both", expand=True, pady=5)


        user_cols = ("Username", "Role")
        user_tree = ttk.Treeview(user_mgmt_frame, columns=user_cols, show="headings", height=5)
        for col in user_cols:
            user_tree.heading(col, text=col, command=lambda c=col: self.sort_treeview(user_tree, c, False))
            user_tree.column(col, anchor="center")
        user_tree.pack(fill="x", pady=(0, 10))
        self.widgets['user_tree'] = user_tree
        
        def load_users():
            for i in user_tree.get_children():
                user_tree.delete(i)
            rows = self.db.execute("SELECT username, role FROM users", fetch="all")
            if rows:
                for row in rows:
                    user_tree.insert("", END, values=row)

        def backup_database():
            try:
                backup_path = filedialog.asksaveasfilename(
                    defaultextension=".db",
                    filetypes=[("Database files", "*.db"), ("All files", "*.*")],
                    title="Save Database Backup As"
                )
                if backup_path:
                    shutil.copyfile(DB_NAME, backup_path)
                    messagebox.showinfo("Backup Successful", f"Database backed up successfully to:\n{backup_path}", parent=self.root)
            except Exception as e:
                messagebox.showerror("Backup Failed", f"An error occurred during backup: {e}", parent=self.root)

        def restore_database():
            if not messagebox.askyesno("Confirm Restore", "WARNING: This will overwrite all current data with the backup file. Are you sure you want to continue?", parent=self.root):
                return
            
            restore_path = filedialog.askopenfilename(
                filetypes=[("Database files", "*.db"), ("All files", "*.*")],
                title="Select Database Backup to Restore"
            )
            if restore_path:
                shutil.copyfile(restore_path, DB_NAME)
                messagebox.showinfo("Restore Successful", "Database restored successfully.\nPlease restart the application for the changes to take effect.", parent=self.root)
                self.root.destroy()

        ttk.Button(db_mgmt_frame, text="Export Backup", command=backup_database, bootstyle="info").pack(side='left', padx=5, pady=5)
        ttk.Button(db_mgmt_frame, text="Restore from Backup", command=restore_database, bootstyle="danger").pack(side='left', padx=5, pady=5)

        def add_user_dialog():
            win = ttk.Toplevel(self.root)
            win.title("Add New User")
            win.geometry("350x280")

            ttk.Label(win, text="Username:").pack(pady=(10, 0))
            user_entry = ttk.Entry(win, width=30)
            user_entry.pack(pady=5)

            ttk.Label(win, text="Password:").pack(pady=(10, 0))
            pass_entry = ttk.Entry(win, width=30, show="*")
            pass_entry.pack(pady=5)

            ttk.Label(win, text="Role:").pack(pady=(10, 0))
            role_combo = ttk.Combobox(win, values=["user", "admin"], state="readonly", width=28)
            role_combo.set("user")
            role_combo.pack(pady=5)

            def save_user():
                username = user_entry.get().strip()
                password = pass_entry.get().strip()
                role = role_combo.get()
                if not (username and password):
                    messagebox.showerror("Input Error", "Username and Password are required.", parent=win)
                    return
                
                try:
                    self.db.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                               (username, hash_password(password), role))
                    messagebox.showinfo("Success", "User added successfully.", parent=win)
                    load_users()
                    win.destroy()
                except sqlite3.IntegrityError:
                    messagebox.showerror("Error", "Username already exists.", parent=win)

            ttk.Button(win, text="Save User", command=save_user, bootstyle="success").pack(pady=15)

        def delete_user():
            selected = user_tree.selection()
            if not selected:
                messagebox.showwarning("Selection Error", "Please select a user to delete.", parent=self.root)
                return
            
            username_to_delete = user_tree.item(selected[0])['values'][0]
            if username_to_delete == self.current_username:
                messagebox.showerror("Action Forbidden", "You cannot delete your own account.", parent=self.root)
                return

            if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the user '{username_to_delete}'?"):
                self.db.execute("DELETE FROM users WHERE username=?", (username_to_delete,))
                load_users()

        user_btn_frame = ttk.Frame(user_mgmt_frame)
        user_btn_frame.pack(fill="x")
        ttk.Button(user_btn_frame, text="Add User", command=add_user_dialog, bootstyle="success").pack(side="left", padx=5)
        ttk.Button(user_btn_frame, text="Delete User", command=delete_user, bootstyle="danger").pack(side="left", padx=5)
        ttk.Button(user_btn_frame, text="Reset Password", command=self.reset_password_dialog, bootstyle="warning").pack(side="left", padx=5)
        ttk.Button(user_btn_frame, text="Edit Security", command=self.edit_security_info_dialog, bootstyle="info").pack(side="left", padx=5)

        user_mgmt_frame.bind("<Visibility>", lambda event: load_users()) # pragma: no cover

    def reset_password_dialog(self):
        """Opens a dialog for an admin to reset a selected user's password."""
        user_tree = self.widgets.get('user_tree')
        if not user_tree or not user_tree.selection():
            messagebox.showwarning("Selection Error", "Please select a user from the list to reset their password.", parent=self.root)
            return

        username_to_reset = user_tree.item(user_tree.selection()[0])['values'][0]

        win = ttk.Toplevel(self.root)
        win.title(f"Reset Password for {username_to_reset}")
        win.geometry("350x250")
        win.transient(self.root)

        ttk.Label(win, text=f"Enter new password for '{username_to_reset}':").pack(pady=(10, 5))
        new_pass_entry = ttk.Entry(win, width=30, show="*")
        new_pass_entry.pack(pady=5)
        new_pass_entry.focus_set()

        ttk.Label(win, text="Confirm new password:").pack(pady=(10, 5))
        confirm_pass_entry = ttk.Entry(win, width=30, show="*")
        confirm_pass_entry.pack(pady=5)

        def process_reset():
            new_pass = new_pass_entry.get().strip()
            confirm_pass = confirm_pass_entry.get().strip()

            if not new_pass or not confirm_pass or new_pass != confirm_pass:
                messagebox.showerror("Input Error", "Passwords cannot be empty and must match.", parent=win)
                return
            
            self.db.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_pass), username_to_reset))
            messagebox.showinfo("Success", f"Password for '{username_to_reset}' has been reset successfully.", parent=win)
            win.destroy()

        ttk.Button(win, text="Confirm Reset", command=process_reset, bootstyle="success").pack(pady=20) # pragma: no cover

    def edit_security_info_dialog(self):
        """Opens a dialog for an admin to edit a user's security question and answer."""
        user_tree = self.widgets.get('user_tree')
        if not user_tree or not user_tree.selection():
            messagebox.showwarning("Selection Error", "Please select a user from the list to edit their security info.", parent=self.root)
            return

        username_to_edit = user_tree.item(user_tree.selection()[0])['values'][0]

        win = ttk.Toplevel(self.root)
        win.title(f"Edit Security for {username_to_edit}")
        win.geometry("400x350")
        win.transient(self.root)

        # Fetch current question
        current_question_data = self.db.execute("SELECT security_question FROM users WHERE username=?", (username_to_edit,), fetch="one")
        current_question = current_question_data[0] if current_question_data else ""

        ttk.Label(win, text=f"Editing security info for '{username_to_edit}'").pack(pady=(10, 5))

        ttk.Label(win, text="New Security Question:").pack(pady=(10, 5))
        question_entry = ttk.Entry(win, width=40)
        question_entry.insert(0, current_question)
        question_entry.pack(pady=5)

        ttk.Label(win, text="New Security Answer:").pack(pady=(10, 5))
        answer_entry = ttk.Entry(win, width=40, show="*")
        answer_entry.pack(pady=5)

        ttk.Label(win, text="Confirm New Answer:").pack(pady=(10, 5))
        confirm_answer_entry = ttk.Entry(win, width=40, show="*")
        confirm_answer_entry.pack(pady=5)

        def process_update():
            new_question = question_entry.get().strip()
            new_answer = answer_entry.get().strip()
            confirm_answer = confirm_answer_entry.get().strip()

            if not (new_question and new_answer and confirm_answer):
                messagebox.showerror("Input Error", "All fields are required.", parent=win)
                return
            if new_answer != confirm_answer:
                messagebox.showerror("Input Error", "The new answers do not match.", parent=win)
                return
            
            self.db.execute("UPDATE users SET security_question=?, security_answer=? WHERE username=?", (new_question, hash_password(new_answer), username_to_edit))
            messagebox.showinfo("Success", f"Security info for '{username_to_edit}' has been updated.", parent=win)
            win.destroy()

        ttk.Button(win, text="Update Security Info", command=process_update, bootstyle="success").pack(pady=20)

if __name__ == "__main__":
    init_db()
    root = ttk.Window(themename="flatly")
    app = LibraryApp(root)
    root.mainloop()
