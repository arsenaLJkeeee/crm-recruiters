import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sqlite3
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
import webbrowser
import subprocess


APP_TITLE = "CRM рекрутеров"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crm.db"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
APP_VERSION = "0.0.1"
KEY_LOGGING = True  # временно для отладки хоткеев


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    handler = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    # Console output helps debug if app is run from terminal
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root_logger.addHandler(console)


class RecruiterDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self) -> None:
        query = """
        CREATE TABLE IF NOT EXISTS recruiters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            full_name TEXT NOT NULL,
            telegram TEXT,
            phone TEXT,
            position TEXT,
            email TEXT,
            comments TEXT,
            resume_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
        self.conn.execute(query)
        self.conn.commit()

    def add_recruiter(self, data: dict) -> None:
        query = """
        INSERT INTO recruiters
        (company, full_name, telegram, phone, position, email, comments, resume_path)
        VALUES (:company, :full_name, :telegram, :phone, :position, :email, :comments, :resume_path)
        """
        self.conn.execute(query, data)
        self.conn.commit()

    def fetch_recruiters(self, company_filter: str | None = None) -> list[sqlite3.Row]:
        if company_filter and company_filter != "Все":
            cursor = self.conn.execute(
                "SELECT * FROM recruiters WHERE company = ? ORDER BY created_at DESC",
                (company_filter,),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM recruiters ORDER BY created_at DESC"
            )
        return cursor.fetchall()

    def delete_recruiter(self, recruiter_id: int) -> None:
        self.conn.execute("DELETE FROM recruiters WHERE id = ?", (recruiter_id,))
        self.conn.commit()

    def get_companies(self) -> list[str]:
        cursor = self.conn.execute("SELECT DISTINCT company FROM recruiters ORDER BY company")
        return [row["company"] for row in cursor.fetchall()]


class CRMApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1100x750")
        self.root.minsize(960, 650)
        # Шрифт с пробелом в названии нужно экранировать для Tk
        self.root.option_add("*Font", "{Segoe UI} 10")

        self.db = RecruiterDB(DB_PATH)

        self.company_var = tk.StringVar()
        self.full_name_var = tk.StringVar()
        self.telegram_var = tk.StringVar()
        self.phone_var = tk.StringVar()
        self.position_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.resume_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="Все")

        self._build_ui()
        self._bind_shortcuts()
        if KEY_LOGGING:
            self._bind_debug_key_logging()
        self._refresh_company_filter()
        self._refresh_table()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28)

        form_frame = ttk.LabelFrame(self.root, text="Новый рекрутер", padding=12)
        form_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))

        labels = [
            ("Компания", self.company_var),
            ("ФИО", self.full_name_var),
            ("Ник в TG", self.telegram_var),
            ("Телефон", self.phone_var),
            ("Должность", self.position_var),
            ("Почта", self.email_var),
        ]

        for idx, (label, var) in enumerate(labels):
            ttk.Label(form_frame, text=label).grid(row=idx, column=0, sticky="w", pady=3, padx=(0, 8))
            entry = ttk.Entry(form_frame, textvariable=var, width=40)
            entry.grid(row=idx, column=1, sticky="ew", pady=3)

        # Resume chooser
        ttk.Label(form_frame, text="Резюме (PDF)").grid(row=0, column=2, sticky="w", pady=3, padx=(16, 8))
        ttk.Button(form_frame, text="Загрузить PDF", command=self.choose_resume).grid(
            row=0, column=3, sticky="w", pady=3
        )
        self.resume_label = ttk.Label(form_frame, textvariable=self.resume_var, foreground="#555")
        self.resume_label.grid(row=1, column=2, columnspan=2, sticky="w", pady=3, padx=(16, 8))

        # Comments
        ttk.Label(form_frame, text="Комментарии / заметки").grid(
            row=2, column=2, sticky="nw", pady=3, padx=(16, 8)
        )
        comments_frame = ttk.Frame(form_frame)
        comments_frame.grid(row=2, column=3, sticky="nsew", pady=3)
        self.comments_text = tk.Text(comments_frame, width=40, height=5, wrap="word")
        scroll = ttk.Scrollbar(comments_frame, command=self.comments_text.yview)
        self.comments_text.configure(yscrollcommand=scroll.set)
        self.comments_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        comments_frame.columnconfigure(0, weight=1)
        comments_frame.rowconfigure(0, weight=1)

        # Buttons
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=len(labels) + 1, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Button(btn_frame, text="Добавить рекрутера", command=self.add_recruiter).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(btn_frame, text="Очистить форму", command=self.clear_form).grid(
            row=0, column=1, padx=(0, 8)
        )

        # Filter bar
        filter_frame = ttk.LabelFrame(self.root, text="Просмотр", padding=12)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(filter_frame, text="Компания").grid(row=0, column=0, padx=(0, 8))
        self.filter_combo = ttk.Combobox(
            filter_frame, textvariable=self.filter_var, state="readonly", width=30
        )
        self.filter_combo.grid(row=0, column=1, padx=(0, 12))
        ttk.Button(filter_frame, text="Показать рекрутеров", command=self._refresh_table).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(filter_frame, text="Обновить список компаний", command=self._refresh_company_filter).grid(
            row=0, column=3
        )

        # Table
        table_frame = ttk.Frame(self.root, padding=(12, 0))
        table_frame.grid(row=2, column=0, sticky="nsew")

        columns = (
            "id",
            "company",
            "full_name",
            "telegram",
            "phone",
            "position",
            "email",
            "resume_path",
            "comments",
        )
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse", height=12
        )
        headings = {
            "company": "Компания",
            "full_name": "ФИО",
            "telegram": "TG",
            "phone": "Телефон",
            "position": "Должность",
            "email": "Почта",
            "resume_path": "Резюме",
            "comments": "Комментарий",
        }
        for col in columns:
            if col == "id":
                self.tree.column(col, width=0, stretch=False, anchor="center")
            elif col == "comments":
                self.tree.column(col, width=220, anchor="w")
            elif col == "resume_path":
                self.tree.column(col, width=180, anchor="w")
            elif col == "full_name":
                self.tree.column(col, width=180, anchor="w")
            else:
                self.tree.column(col, width=120, anchor="w")
            self.tree.heading(col, text=headings.get(col, col))

        self.tree.bind("<Double-1>", self.on_tree_double_click)

        tree_scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        # Action buttons under table
        actions = ttk.Frame(self.root, padding=12)
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(actions, text="Открыть TG", command=self.open_tg).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Открыть резюме (PDF)", command=self.open_resume).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(actions, text="Удалить выбранного", command=self.delete_recruiter).grid(
            row=0, column=2, padx=(0, 8)
        )

        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    def _bind_shortcuts(self) -> None:
        """Рабочие хоткеи: ручная копия/вставка/вырез/выделение всего через буфер."""

        def copy(event: tk.Event) -> str | None:
            w = event.widget
            handled = False
            try:
                cls = w.winfo_class()
                if cls in ("Entry", "TEntry", "TCombobox"):
                    text = w.selection_get()
                    self.root.clipboard_clear()
                    self.root.clipboard_append(text)
                    handled = True
                elif cls == "Text":
                    text = w.get("sel.first", "sel.last")
                    self.root.clipboard_clear()
                    self.root.clipboard_append(text)
                    handled = True
            except Exception:
                handled = False
            return "break" if handled else None

        def cut(event: tk.Event) -> str | None:
            w = event.widget
            handled = False
            try:
                cls = w.winfo_class()
                if cls in ("Entry", "TEntry", "TCombobox"):
                    text = w.selection_get()
                    self.root.clipboard_clear()
                    self.root.clipboard_append(text)
                    w.delete("sel.first", "sel.last")
                    handled = True
                elif cls == "Text":
                    text = w.get("sel.first", "sel.last")
                    self.root.clipboard_clear()
                    self.root.clipboard_append(text)
                    w.delete("sel.first", "sel.last")
                    handled = True
            except Exception:
                handled = False
            return "break" if handled else None

        def paste(event: tk.Event) -> str | None:
            w = event.widget
            handled = False
            try:
                text = self.root.clipboard_get()
                cls = w.winfo_class()
                if cls in ("Entry", "TEntry", "TCombobox"):
                    w.insert(tk.INSERT, text)
                    handled = True
                elif cls == "Text":
                    w.insert(tk.INSERT, text)
                    handled = True
            except Exception:
                handled = False
            return "break" if handled else None

        def select_all(event: tk.Event) -> str | None:
            w = event.widget
            handled = False
            try:
                cls = w.winfo_class()
                if cls in ("Entry", "TEntry", "TCombobox"):
                    w.select_range(0, tk.END)
                    w.icursor(tk.END)
                    handled = True
                elif cls == "Text":
                    w.tag_add("sel", "1.0", "end-1c")
                    w.mark_set("insert", "1.0")
                    handled = True
            except Exception:
                handled = False
            return "break" if handled else None

        bindings = [
            ("<Control-c>", copy),
            ("<Control-C>", copy),
            ("<Command-c>", copy),
            ("<Command-C>", copy),
            ("<Control-Insert>", copy),
            ("<Control-x>", cut),
            ("<Control-X>", cut),
            ("<Command-x>", cut),
            ("<Command-X>", cut),
            ("<Shift-Delete>", cut),
            ("<Control-v>", paste),
            ("<Control-V>", paste),
            ("<Shift-Insert>", paste),
            ("<Command-v>", paste),
            ("<Command-V>", paste),
            ("<Control-a>", select_all),
            ("<Control-A>", select_all),
            ("<Command-a>", select_all),
            ("<Command-A>", select_all),
        ]

        for seq, func in bindings:
            self.root.bind_all(seq, func, add="+")

        # Фолбек для случаев, когда keysym не распознаётся (в логах видно "??" для C/V/A).
        # На Windows keycode: C=67, V=86, X=88, A=65.
        keycode_map = {67: copy, 86: paste, 88: cut, 65: select_all}

        def on_ctrl_keycode(event: tk.Event) -> str | None:
            func = keycode_map.get(event.keycode)
            if func:
                result = func(event)
                return "break" if result == "break" else None
            return None

        self.root.bind_all("<Control-KeyPress>", on_ctrl_keycode, add="+")

    def _bind_debug_key_logging(self) -> None:
        """Логирование нажатий и виртуальных событий для диагностики."""
        def log_key(event: tk.Event) -> None:
            logging.info(
                "KEY keycode=%s keysym=%s state=%s widget=%s",
                event.keycode,
                event.keysym,
                event.state,
                getattr(event.widget, "winfo_class", lambda: "?")(),
            )

        for seq in ("<Key>", "<KeyPress>", "<KeyRelease>"):
            self.root.bind_all(seq, log_key, add="+")

        def log_virtual(name: str):
            def _inner(event: tk.Event) -> None:
                logging.info("VIRTUAL %s widget=%s", name, getattr(event.widget, "winfo_class", lambda: "?")())
            return _inner

        for virtual in ("<<Copy>>", "<<Cut>>", "<<Paste>>", "<<SelectAll>>"):
            self.root.bind_all(virtual, log_virtual(virtual), add="+")

    def choose_resume(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Выберите PDF резюме",
            filetypes=[("PDF", "*.pdf")],
        )
        if file_path:
            self.resume_var.set(Path(file_path).resolve().as_posix())
            logging.info("Выбрано резюме: %s", file_path)

    def add_recruiter(self) -> None:
        data = {
            "company": self.company_var.get().strip(),
            "full_name": self.full_name_var.get().strip(),
            "telegram": self.telegram_var.get().strip(),
            "phone": self.phone_var.get().strip(),
            "position": self.position_var.get().strip(),
            "email": self.email_var.get().strip(),
            "comments": self.comments_text.get("1.0", tk.END).strip(),
            "resume_path": self.resume_var.get().strip(),
        }

        if not data["company"]:
            messagebox.showwarning("Поля обязательны", "Укажите компанию.")
            return
        if not data["full_name"]:
            messagebox.showwarning("Поля обязательны", "Укажите ФИО.")
            return
        if data["resume_path"]:
            path = Path(data["resume_path"])
            if path.suffix.lower() != ".pdf" or not path.exists():
                messagebox.showerror("Резюме", "Укажите существующий PDF файл резюме.")
                return

        try:
            self.db.add_recruiter(data)
            logging.info("Добавлен рекрутер: %s (%s)", data["full_name"], data["company"])
            messagebox.showinfo("Готово", "Рекрутер добавлен.")
            self.clear_form()
            self._refresh_company_filter()
            self._refresh_table()
        except Exception as exc:
            logging.exception("Ошибка при добавлении рекрутера: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {exc}")

    def clear_form(self) -> None:
        self.company_var.set("")
        self.full_name_var.set("")
        self.telegram_var.set("")
        self.phone_var.set("")
        self.position_var.set("")
        self.email_var.set("")
        self.resume_var.set("")
        self.comments_text.delete("1.0", tk.END)

    def _refresh_company_filter(self) -> None:
        companies = self.db.get_companies()
        values = ["Все"] + companies
        self.filter_combo["values"] = values
        if self.filter_var.get() not in values:
            self.filter_var.set("Все")

    def _refresh_table(self) -> None:
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        company_filter = self.filter_var.get()
        rows = self.db.fetch_recruiters(company_filter)
        for row in rows:
            comments_short = (row["comments"][:120] + "...") if row["comments"] and len(row["comments"]) > 120 else (row["comments"] or "")
            resume_short = Path(row["resume_path"]).name if row["resume_path"] else ""
            self.tree.insert(
                "",
                "end",
                values=(
                    row["id"],
                    row["company"],
                    row["full_name"],
                    row["telegram"],
                    row["phone"],
                    row["position"],
                    row["email"],
                    resume_short,
                    comments_short,
                ),
            )
        logging.info("Обновлен список рекрутеров (%s записей)", len(rows))

    def _get_selected_row(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Нет выбора", "Выберите рекрутера в таблице.")
            return None
        item = self.tree.item(selection[0])
        values = item["values"]
        if not values:
            return None
        keys = [
            "id",
            "company",
            "full_name",
            "telegram",
            "phone",
            "position",
            "email",
            "resume_path",
            "comments",
        ]
        return dict(zip(keys, values))

    def on_tree_double_click(self, event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col_id = self.tree.identify_column(event.x)
        col_index = int(col_id.replace("#", "")) - 1
        col_name = self.tree["columns"][col_index]
        if col_name == "telegram":
            self.open_tg()
        elif col_name == "resume_path":
            self.open_resume()

    def open_tg(self) -> None:
        row = self._get_selected_row()
        if not row:
            return
        handle = (row.get("telegram") or "").strip().lstrip("@")
        if not handle:
            messagebox.showwarning("TG", "У этого рекрутера не указан ник в Telegram.")
            return
        tg_url = f"tg://resolve?domain={handle}"
        web_url = f"https://t.me/{handle}"
        try:
            opened = webbrowser.open(tg_url) or webbrowser.open(web_url)
            logging.info("Открыт Telegram для %s", handle)
            # Открываем резюме, чтобы его сразу можно было прикрепить вручную
            self._open_resume_path(row.get("resume_path"), notify=False)
            if not opened:
                messagebox.showinfo("Telegram", f"Перейдите по ссылке: {web_url}")
        except Exception as exc:
            logging.exception("Не удалось открыть Telegram: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось открыть Telegram: {exc}")

    def _open_resume_path(self, path_value: str | None, notify: bool = True) -> None:
        if not path_value:
            if notify:
                messagebox.showwarning("Резюме", "Резюме не привязано.")
            return
        path = Path(path_value)
        if not path.exists():
            if notify:
                messagebox.showerror("Резюме", "Файл резюме не найден.")
            logging.warning("Файл резюме не найден: %s", path)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.call(["open", path])
            else:
                subprocess.call(["xdg-open", path])
            logging.info("Открыто резюме: %s", path)
        except Exception as exc:
            logging.exception("Ошибка при открытии резюме: %s", exc)
            if notify:
                messagebox.showerror("Резюме", f"Не удалось открыть файл: {exc}")

    def open_resume(self) -> None:
        row = self._get_selected_row()
        if not row:
            return
        self._open_resume_path(row.get("resume_path"), notify=True)

    def delete_recruiter(self) -> None:
        row = self._get_selected_row()
        if not row:
            return
        confirm = messagebox.askyesno(
            "Удалить",
            f"Удалить рекрутера {row['full_name']} из {row['company']}?",
            icon="warning",
        )
        if not confirm:
            return
        try:
            self.db.delete_recruiter(int(row["id"]))
            logging.info("Удален рекрутер id=%s", row["id"])
            self._refresh_table()
            self._refresh_company_filter()
        except Exception as exc:
            logging.exception("Ошибка при удалении: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось удалить: {exc}")


def main() -> None:
    setup_logging()
    logging.info("Запуск приложения")
    root = tk.Tk()
    CRMApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

