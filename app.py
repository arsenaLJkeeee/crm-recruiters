import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sqlite3
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from tkinter import ttk
import webbrowser
import subprocess
import datetime
import calendar


APP_TITLE = "CRM рекрутеров"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crm.db"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
APP_VERSION = "0.0.1"
KEY_LOGGING = True  # временно для отладки хоткеев
USE_GMAIL_COMPOSE = True  # если True, открываем сразу Gmail compose вместо mailto (обходит браузерные handlers)


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
        self._ensure_columns()

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
            status TEXT DEFAULT 'первичный контакт',
            last_contact TEXT,
            next_step TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
        self.conn.execute(query)
        self.conn.commit()

    def _ensure_columns(self) -> None:
        cursor = self.conn.execute("PRAGMA table_info(recruiters)")
        cols = {row["name"] for row in cursor.fetchall()}
        alter_queries = []
        if "status" not in cols:
            alter_queries.append("ALTER TABLE recruiters ADD COLUMN status TEXT DEFAULT 'первичный контакт'")
        if "last_contact" not in cols:
            alter_queries.append("ALTER TABLE recruiters ADD COLUMN last_contact TEXT")
        if "next_step" not in cols:
            alter_queries.append("ALTER TABLE recruiters ADD COLUMN next_step TEXT")
        for q in alter_queries:
            self.conn.execute(q)
        if alter_queries:
            self.conn.commit()

    def update_recruiter(self, data: dict) -> None:
        query = """
        UPDATE recruiters
        SET company = :company,
            full_name = :full_name,
            telegram = :telegram,
            phone = :phone,
            position = :position,
            email = :email,
            comments = :comments,
            status = :status,
            last_contact = :last_contact,
            next_step = :next_step
        WHERE id = :id
        """
        self.conn.execute(query, data)
        self.conn.commit()

    def add_recruiter(self, data: dict) -> None:
        query = """
        INSERT INTO recruiters
        (company, full_name, telegram, phone, position, email, comments, status, last_contact, next_step)
        VALUES (:company, :full_name, :telegram, :phone, :position, :email, :comments, :status, :last_contact, :next_step)
        """
        self.conn.execute(query, data)
        self.conn.commit()

    def fetch_recruiters(self, company_filter: str | None = None, status_filter: str | None = None) -> list[sqlite3.Row]:
        where = []
        params: list = []
        if company_filter and company_filter != "Все":
            where.append("company = ?")
            params.append(company_filter)
        if status_filter and status_filter != "Все":
            where.append("status = ?")
            params.append(status_filter)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        query = f"""
        SELECT * FROM recruiters
        {where_sql}
        ORDER BY
            CASE WHEN next_step IS NULL OR next_step = '' THEN 1 ELSE 0 END,
            next_step ASC,
            created_at DESC
        """
        cursor = self.conn.execute(query, params)
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
        self.status_var = tk.StringVar(value="первичный контакт")
        self.last_contact_var = tk.StringVar()
        self.next_step_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="Все")
        self.filter_status_var = tk.StringVar(value="Все")
        self.current_edit_id: int | None = None

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

        # Status and dates
        ttk.Label(form_frame, text="Статус").grid(row=0, column=2, sticky="w", pady=3, padx=(16, 8))
        self.status_combo = ttk.Combobox(
            form_frame,
            textvariable=self.status_var,
            state="readonly",
            values=[
                "первичный контакт",
                "ожидание ответа",
                "интервью",
                "оффер",
                "отказ",
            ],
            width=28,
        )
        self.status_combo.grid(row=0, column=3, sticky="w", pady=3)

        ttk.Label(form_frame, text="Последний контакт (YYYY-MM-DD)").grid(row=1, column=2, sticky="w", pady=3, padx=(16, 8))
        ttk.Entry(form_frame, textvariable=self.last_contact_var, width=22).grid(row=1, column=3, sticky="w", pady=3)
        ttk.Button(form_frame, text="Выбрать", command=lambda: self._pick_date(self.last_contact_var)).grid(
            row=1, column=4, sticky="w", padx=(4, 0)
        )

        ttk.Label(form_frame, text="Следующий шаг (YYYY-MM-DD)").grid(row=2, column=2, sticky="w", pady=3, padx=(16, 8))
        ttk.Entry(form_frame, textvariable=self.next_step_var, width=22).grid(row=2, column=3, sticky="w", pady=3)
        ttk.Button(form_frame, text="Выбрать", command=lambda: self._pick_date(self.next_step_var)).grid(
            row=2, column=4, sticky="w", padx=(4, 0)
        )

        # Comments
        ttk.Label(form_frame, text="Комментарии / заметки").grid(
            row=3, column=2, sticky="nw", pady=3, padx=(16, 8)
        )
        comments_frame = ttk.Frame(form_frame)
        comments_frame.grid(row=3, column=3, columnspan=2, sticky="nsew", pady=3)
        self.comments_text = tk.Text(comments_frame, width=40, height=5, wrap="word", undo=True, maxundo=256)
        scroll = ttk.Scrollbar(comments_frame, command=self.comments_text.yview)
        self.comments_text.configure(yscrollcommand=scroll.set)
        self.comments_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        comments_frame.columnconfigure(0, weight=1)
        comments_frame.rowconfigure(0, weight=1)

        # Buttons
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=len(labels) + 4, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        ttk.Button(btn_frame, text="Добавить рекрутера", command=self.add_recruiter).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(btn_frame, text="Сохранить правки", command=self.save_edit).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(btn_frame, text="Отменить/очистить", command=self.clear_form).grid(
            row=0, column=2, padx=(0, 8)
        )

        # Filter bar
        filter_frame = ttk.LabelFrame(self.root, text="Просмотр", padding=12)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(filter_frame, text="Компания").grid(row=0, column=0, padx=(0, 8))
        self.filter_combo = ttk.Combobox(
            filter_frame, textvariable=self.filter_var, state="readonly", width=30
        )
        self.filter_combo.grid(row=0, column=1, padx=(0, 12))
        ttk.Label(filter_frame, text="Статус").grid(row=0, column=2, padx=(0, 8))
        self.filter_status_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_status_var,
            state="readonly",
            width=22,
            values=["Все", "первичный контакт", "ожидание ответа", "интервью", "оффер", "отказ"],
        )
        self.filter_status_combo.grid(row=0, column=3, padx=(0, 12))
        ttk.Button(filter_frame, text="Показать рекрутеров", command=self._refresh_table).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(filter_frame, text="Обновить список компаний", command=self._refresh_company_filter).grid(
            row=0, column=5
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
            "status",
            "last_contact",
            "next_step",
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
            "status": "Статус",
            "last_contact": "Последний контакт",
            "next_step": "След. шаг",
            "comments": "Комментарий",
        }
        for col in columns:
            if col == "id":
                self.tree.column(col, width=0, stretch=False, anchor="center")
            elif col == "comments":
                self.tree.column(col, width=220, anchor="w")
            elif col in ("last_contact", "next_step"):
                self.tree.column(col, width=120, anchor="w")
            elif col == "full_name":
                self.tree.column(col, width=180, anchor="w")
            else:
                self.tree.column(col, width=120, anchor="w")
            self.tree.heading(col, text=headings.get(col, col))

        self.tree.bind("<Double-1>", self.on_tree_double_click)
        # Отдельный биндинг на клик по email — открывать почту
        self.tree.tag_configure("email", foreground="#0a5dbd")

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
        ttk.Button(actions, text="Написать письмо", command=self.open_email).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Удалить выбранного", command=self.delete_recruiter).grid(
            row=0, column=2, padx=(0, 8)
        )

        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    def _bind_shortcuts(self) -> None:
        """Хоткеи: используем стандартные бинды, плюс фолбек по keycode для нераспознанных раскладок."""

        # Фолбек только когда keysym не распознан (например, "??" при русской раскладке).
        keycode_map = {
            67: "<<Copy>>",   # C
            86: "<<Paste>>",  # V
            88: "<<Cut>>",    # X
            65: "<<SelectAll>>",  # A
            90: "<<Undo>>",   # Z
        }

        def on_ctrl_keycode(event: tk.Event) -> str | None:
            # Если keysym нормальный (c, v, x, a, z) — пусть работает дефолтный биндинг Tk.
            if event.keysym and event.keysym.lower() in ("c", "v", "x", "a", "z"):
                return None
            action = keycode_map.get(event.keycode)
            if action:
                try:
                    event.widget.event_generate(action)
                    return "break"
                except Exception:
                    return None
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

    def add_recruiter(self) -> None:
        data = {
            "company": self.company_var.get().strip(),
            "full_name": self.full_name_var.get().strip(),
            "telegram": self.telegram_var.get().strip(),
            "phone": self.phone_var.get().strip(),
            "position": self.position_var.get().strip(),
            "email": self.email_var.get().strip(),
            "comments": self.comments_text.get("1.0", tk.END).strip(),
            "status": self.status_var.get().strip() or "первичный контакт",
            "last_contact": self.last_contact_var.get().strip(),
            "next_step": self.next_step_var.get().strip(),
        }

        if not data["company"]:
            messagebox.showwarning("Поля обязательны", "Укажите компанию.")
            return
        if not data["full_name"]:
            messagebox.showwarning("Поля обязательны", "Укажите ФИО.")
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

    def save_edit(self) -> None:
        if not self.current_edit_id:
            messagebox.showinfo("Правка", "Сначала выберите запись (двойной клик), чтобы редактировать.")
            return
        data = {
            "id": self.current_edit_id,
            "company": self.company_var.get().strip(),
            "full_name": self.full_name_var.get().strip(),
            "telegram": self.telegram_var.get().strip(),
            "phone": self.phone_var.get().strip(),
            "position": self.position_var.get().strip(),
            "email": self.email_var.get().strip(),
            "comments": self.comments_text.get("1.0", tk.END).strip(),
            "status": self.status_var.get().strip() or "первичный контакт",
            "last_contact": self.last_contact_var.get().strip(),
            "next_step": self.next_step_var.get().strip(),
        }

        if not data["company"] or not data["full_name"]:
            messagebox.showwarning("Поля обязательны", "Укажите компанию и ФИО.")
            return
        try:
            self.db.update_recruiter(data)
            logging.info("Обновлен рекрутер id=%s", data["id"])
            messagebox.showinfo("Готово", "Изменения сохранены.")
            self.clear_form()
            self._refresh_company_filter()
            self._refresh_table()
        except Exception as exc:
            logging.exception("Ошибка при обновлении рекрутера: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {exc}")

    def clear_form(self) -> None:
        self.company_var.set("")
        self.full_name_var.set("")
        self.telegram_var.set("")
        self.phone_var.set("")
        self.position_var.set("")
        self.email_var.set("")
        self.status_var.set("первичный контакт")
        self.last_contact_var.set("")
        self.next_step_var.set("")
        self.comments_text.delete("1.0", tk.END)
        self.current_edit_id = None

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
        status_filter = self.filter_status_var.get()
        rows = self.db.fetch_recruiters(company_filter, status_filter)
        for row in rows:
            comments_short = (row["comments"][:120] + "...") if row["comments"] and len(row["comments"]) > 120 else (row["comments"] or "")
            item_id = self.tree.insert(
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
                    row["status"] or "",
                    row["last_contact"] or "",
                    row["next_step"] or "",
                    comments_short,
                ),
            )
            # подсвечиваем email (тег не обязателен, но пригодится)
            self.tree.item(item_id, tags=("email",))
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
            "status",
            "last_contact",
            "next_step",
            "comments",
        ]
        return dict(zip(keys, values))

    def _fill_form_from_row(self, row: dict) -> None:
        self.current_edit_id = int(row["id"])
        self.company_var.set(row.get("company", ""))
        self.full_name_var.set(row.get("full_name", ""))
        self.telegram_var.set(row.get("telegram", ""))
        self.phone_var.set(row.get("phone", ""))
        self.position_var.set(row.get("position", ""))
        self.email_var.set(row.get("email", ""))
        self.status_var.set(row.get("status", "") or "первичный контакт")
        self.last_contact_var.set(row.get("last_contact", "") or "")
        self.next_step_var.set(row.get("next_step", "") or "")
        self.comments_text.delete("1.0", tk.END)
        self.comments_text.insert("1.0", row.get("comments", "") or "")

    def _fill_form_from_row(self, row: dict) -> None:
        self.current_edit_id = int(row["id"])
        self.company_var.set(row.get("company", ""))
        self.full_name_var.set(row.get("full_name", ""))
        self.telegram_var.set(row.get("telegram", ""))
        self.phone_var.set(row.get("phone", ""))
        self.position_var.set(row.get("position", ""))
        self.email_var.set(row.get("email", ""))
        self.status_var.set(row.get("status", "") or "первичный контакт")
        self.last_contact_var.set(row.get("last_contact", "") or "")
        self.next_step_var.set(row.get("next_step", "") or "")
        self.comments_text.delete("1.0", tk.END)
        self.comments_text.insert("1.0", row.get("comments", "") or "")

    def on_tree_double_click(self, event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col_id = self.tree.identify_column(event.x)
        col_index = int(col_id.replace("#", "")) - 1
        col_name = self.tree["columns"][col_index]
        if col_name == "telegram":
            self.open_tg()
        elif col_name == "email":
            self.open_email()
        else:
            row = self._get_selected_row()
            if row:
                self._fill_form_from_row(row)

    def _pick_date(self, target_var: tk.StringVar) -> None:
        """Простая модалка-календарь без внешних зависимостей."""
        top = tk.Toplevel(self.root)
        top.title("Выбор даты")
        top.grab_set()
        top.resizable(False, False)

        today = datetime.date.today()
        current = {"year": today.year, "month": today.month}

        header = ttk.Frame(top, padding=6)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        month_label = ttk.Label(header, text="")
        month_label.grid(row=0, column=1)

        def render():
            month_label.config(text=f"{calendar.month_name[current['month']]} {current['year']}")
            for w in body.winfo_children():
                w.destroy()
            week_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            for idx, name in enumerate(week_names):
                ttk.Label(body, text=name, width=4, anchor="center").grid(row=0, column=idx, padx=1, pady=1)
            weeks = calendar.monthcalendar(current["year"], current["month"])
            for r, week in enumerate(weeks, start=1):
                for c, day in enumerate(week):
                    if day == 0:
                        ttk.Label(body, text=" ", width=4).grid(row=r, column=c, padx=1, pady=1)
                    else:
                        def select(d=day):
                            date_str = f"{current['year']:04d}-{current['month']:02d}-{d:02d}"
                            target_var.set(date_str)
                            top.destroy()
                        ttk.Button(body, text=str(day), width=4, command=select).grid(row=r, column=c, padx=1, pady=1)

        def prev_month():
            if current["month"] == 1:
                current["month"] = 12
                current["year"] -= 1
            else:
                current["month"] -= 1
            render()

        def next_month():
            if current["month"] == 12:
                current["month"] = 1
                current["year"] += 1
            else:
                current["month"] += 1
            render()

        ttk.Button(header, text="<", width=3, command=prev_month).grid(row=0, column=0, padx=2)
        ttk.Button(header, text=">", width=3, command=next_month).grid(row=0, column=2, padx=2)

        body = ttk.Frame(top, padding=6)
        body.grid(row=1, column=0, sticky="nsew")

        render()

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
            if not opened:
                messagebox.showinfo("Telegram", f"Перейдите по ссылке: {web_url}")
        except Exception as exc:
            logging.exception("Не удалось открыть Telegram: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось открыть Telegram: {exc}")

    def open_email(self) -> None:
        row = self._get_selected_row()
        if not row:
            return
        email = (row.get("email") or "").strip()
        if not email:
            messagebox.showwarning("Почта", "У этого рекрутера не указана почта.")
            return
        subject = "Вопрос по вакансии"
        try:
            # Быстрый путь: всегда Gmail compose, чтобы не зависеть от mailto и браузерных handler'ов
            if USE_GMAIL_COMPOSE:
                gmail_url = f"https://mail.google.com/mail/?view=cm&fs=1&to={email}&su={subject.replace(' ', '%20')}"
                webbrowser.open(gmail_url)
                logging.info("Gmail compose для %s", email)
            else:
                mailto = f"mailto:{email}?subject={subject}"
                opened = False
                if sys.platform.startswith("win"):
                    try:
                        os.startfile(mailto)  # type: ignore[attr-defined]
                        opened = True
                    except Exception:
                        pass
                    if not opened:
                        try:
                            subprocess.Popen(["cmd", "/c", "start", "", mailto], shell=True)
                            opened = True
                        except Exception:
                            pass
                if not opened:
                    opened = webbrowser.open(mailto)
                logging.info("Открыт mailto для %s (opened=%s)", email, opened)
                if not opened:
                    gmail_url = f"https://mail.google.com/mail/?view=cm&fs=1&to={email}&su={subject.replace(' ', '%20')}"
                    webbrowser.open(gmail_url)
                    logging.info("Fallback Gmail compose для %s", email)
                    messagebox.showinfo(
                        "Почта",
                        "Не получилось открыть системный почтовый клиент.\nОткрыл Gmail compose в браузере.\n"
                        "Проверьте, что по умолчанию назначен почтовый клиент для mailto.",
                    )
        except Exception as exc:
            logging.exception("Не удалось открыть почту: %s", exc)
            messagebox.showerror(
                "Почта",
                f"Не удалось открыть почту.\nОшибка: {exc}\n"
                f"Адрес: {email}",
            )
        finally:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(email)
            except Exception:
                pass


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

