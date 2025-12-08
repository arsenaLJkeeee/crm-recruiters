import calendar
import datetime
import logging
import os
import sqlite3
import subprocess
import sys
import tkinter as tk
import webbrowser
from dataclasses import asdict, dataclass
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from tkinter import messagebox, ttk

APP_TITLE = "CRM рекрутеров"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crm.db"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
APP_VERSION = "0.0.1"
KEY_LOGGING = True  # временно для отладки хоткеев
USE_GMAIL_COMPOSE = True  # если True, открываем сразу Gmail compose вместо mailto (обходит браузерные handlers)

DEFAULT_STATUS = "первичный контакт"
STATUS_OPTIONS: tuple[str, ...] = (
    "первичный контакт",
    "ожидание ответа",
    "интервью",
    "оффер",
    "отказ",
)
COMMENT_PREVIEW_LIMIT = 120


def setup_logging() -> None:
    """Инициализация логирования в файл и консоль."""
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
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root_logger.addHandler(console)


@dataclass
class Recruiter:
    id: int | None = None
    company: str = ""
    full_name: str = ""
    telegram: str = ""
    phone: str = ""
    position: str = ""
    email: str = ""
    comments: str = ""
    resume_path: str = ""
    status: str = DEFAULT_STATUS
    last_contact: str = ""
    next_step: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Recruiter":
        return cls(
            id=row["id"],
            company=row["company"],
            full_name=row["full_name"],
            telegram=row["telegram"],
            phone=row["phone"],
            position=row["position"],
            email=row["email"],
            comments=row["comments"],
            resume_path=row["resume_path"] if "resume_path" in row.keys() else "",
            status=row["status"],
            last_contact=row["last_contact"],
            next_step=row["next_step"],
        )

    def normalized(self) -> "Recruiter":
        """Возвращает копию с приведёнными значениями и дефолтным статусом."""
        return Recruiter(
            id=self.id,
            company=self.company.strip(),
            full_name=self.full_name.strip(),
            telegram=self.telegram.strip(),
            phone=self.phone.strip(),
            position=self.position.strip(),
            email=self.email.strip(),
            comments=self.comments.strip(),
            resume_path=self.resume_path.strip(),
            status=self.status.strip() if self.status else DEFAULT_STATUS,
            last_contact=self.last_contact.strip(),
            next_step=self.next_step.strip(),
        )

    def insert_params(self) -> dict:
        data = asdict(self.normalized())
        data.pop("id", None)
        return data

    def update_params(self) -> dict:
        return asdict(self.normalized())

    def comment_preview(self, limit: int = COMMENT_PREVIEW_LIMIT) -> str:
        if not self.comments:
            return ""
        return f"{self.comments[:limit]}..." if len(self.comments) > limit else self.comments


class RecruiterRepository:
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
        if "resume_path" not in cols:
            alter_queries.append("ALTER TABLE recruiters ADD COLUMN resume_path TEXT")
        for query in alter_queries:
            self.conn.execute(query)
        if alter_queries:
            self.conn.commit()

    def add(self, recruiter: Recruiter) -> None:
        query = """
        INSERT INTO recruiters
        (company, full_name, telegram, phone, position, email, comments, resume_path, status, last_contact, next_step)
        VALUES (:company, :full_name, :telegram, :phone, :position, :email, :comments, :resume_path, :status, :last_contact, :next_step)
        """
        self.conn.execute(query, recruiter.insert_params())
        self.conn.commit()

    def update(self, recruiter: Recruiter) -> None:
        if recruiter.id is None:
            raise ValueError("Не указан id для обновления рекрутера")
        query = """
        UPDATE recruiters
        SET company = :company,
            full_name = :full_name,
            telegram = :telegram,
            phone = :phone,
            position = :position,
            email = :email,
            comments = :comments,
            resume_path = :resume_path,
            status = :status,
            last_contact = :last_contact,
            next_step = :next_step
        WHERE id = :id
        """
        self.conn.execute(query, recruiter.update_params())
        self.conn.commit()

    def fetch(self, company_filter: str | None = None, status_filter: str | None = None) -> list[Recruiter]:
        where = []
        params: list = []
        if company_filter and company_filter != "Все":
            where.append("company = ?")
            params.append(company_filter)
        if status_filter and status_filter != "Все":
            where.append("status = ?")
            params.append(status_filter)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
        SELECT * FROM recruiters
        {where_sql}
        ORDER BY
            CASE WHEN next_step IS NULL OR next_step = '' THEN 1 ELSE 0 END,
            next_step ASC,
            created_at DESC
        """
        cursor = self.conn.execute(query, params)
        return [Recruiter.from_row(row) for row in cursor.fetchall()]

    def delete(self, recruiter_id: int) -> None:
        self.conn.execute("DELETE FROM recruiters WHERE id = ?", (recruiter_id,))
        self.conn.commit()

    def get(self, recruiter_id: int) -> Recruiter | None:
        cursor = self.conn.execute("SELECT * FROM recruiters WHERE id = ?", (recruiter_id,))
        row = cursor.fetchone()
        return Recruiter.from_row(row) if row else None

    def get_companies(self) -> list[str]:
        cursor = self.conn.execute("SELECT DISTINCT company FROM recruiters ORDER BY company")
        return [row["company"] for row in cursor.fetchall()]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


class DatePicker:
    def __init__(self, parent: tk.Tk, target_var: tk.StringVar) -> None:
        self.parent = parent
        self.target_var = target_var
        self.top = tk.Toplevel(self.parent)
        self.top.title("Выбор даты")
        self.top.grab_set()
        self.top.resizable(False, False)
        today = datetime.date.today()
        self.current = {"year": today.year, "month": today.month}
        self._build()

    @classmethod
    def open(cls, parent: tk.Tk, target_var: tk.StringVar) -> None:
        cls(parent, target_var)

    def _build(self) -> None:
        header = ttk.Frame(self.top, padding=6)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        self.month_label = ttk.Label(header, text="")
        self.month_label.grid(row=0, column=1)

        ttk.Button(header, text="<", width=3, command=self._prev_month).grid(row=0, column=0, padx=2)
        ttk.Button(header, text=">", width=3, command=self._next_month).grid(row=0, column=2, padx=2)

        self.body = ttk.Frame(self.top, padding=6)
        self.body.grid(row=1, column=0, sticky="nsew")

        self._render_calendar()

    def _render_calendar(self) -> None:
        self.month_label.config(text=f"{calendar.month_name[self.current['month']]} {self.current['year']}")
        for widget in self.body.winfo_children():
            widget.destroy()
        week_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        for idx, name in enumerate(week_names):
            ttk.Label(self.body, text=name, width=4, anchor="center").grid(row=0, column=idx, padx=1, pady=1)
        weeks = calendar.monthcalendar(self.current["year"], self.current["month"])
        for r, week in enumerate(weeks, start=1):
            for c, day in enumerate(week):
                if day == 0:
                    ttk.Label(self.body, text=" ", width=4).grid(row=r, column=c, padx=1, pady=1)
                else:
                    ttk.Button(
                        self.body,
                        text=str(day),
                        width=4,
                        command=lambda d=day: self._select_date(d),
                    ).grid(row=r, column=c, padx=1, pady=1)

    def _prev_month(self) -> None:
        if self.current["month"] == 1:
            self.current["month"] = 12
            self.current["year"] -= 1
        else:
            self.current["month"] -= 1
        self._render_calendar()

    def _next_month(self) -> None:
        if self.current["month"] == 12:
            self.current["month"] = 1
            self.current["year"] += 1
        else:
            self.current["month"] += 1
        self._render_calendar()

    def _select_date(self, day: int) -> None:
        date_str = f"{self.current['year']:04d}-{self.current['month']:02d}-{day:02d}"
        self.target_var.set(date_str)
        self.top.destroy()


class CRMApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1100x750")
        self.root.minsize(960, 650)
        self.root.option_add("*Font", "{Segoe UI} 10")  # экранирование пробела в названии шрифта

        self.repo = RecruiterRepository(DB_PATH)

        self._init_vars()
        self._build_ui()
        self._bind_shortcuts()
        if KEY_LOGGING:
            self._bind_debug_key_logging()
        self._refresh_company_filter()
        self._refresh_table()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_vars(self) -> None:
        self.company_var = tk.StringVar()
        self.full_name_var = tk.StringVar()
        self.telegram_var = tk.StringVar()
        self.phone_var = tk.StringVar()
        self.position_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.status_var = tk.StringVar(value=DEFAULT_STATUS)
        self.last_contact_var = tk.StringVar()
        self.next_step_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="Все")
        self.filter_status_var = tk.StringVar(value="Все")
        self.current_edit_id: int | None = None
        self.current_resume_path: str = ""

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28)

        self._build_form()
        self._build_filters()
        self._build_table()
        self._build_actions()

        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

    def _build_form(self) -> None:
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
            ttk.Entry(form_frame, textvariable=var, width=40).grid(row=idx, column=1, sticky="ew", pady=3)

        ttk.Label(form_frame, text="Статус").grid(row=0, column=2, sticky="w", pady=3, padx=(16, 8))
        self.status_combo = ttk.Combobox(
            form_frame,
            textvariable=self.status_var,
            state="readonly",
            values=list(STATUS_OPTIONS),
            width=28,
        )
        self.status_combo.grid(row=0, column=3, sticky="w", pady=3)

        ttk.Label(form_frame, text="Последний контакт (YYYY-MM-DD)").grid(
            row=1, column=2, sticky="w", pady=3, padx=(16, 8)
        )
        ttk.Entry(form_frame, textvariable=self.last_contact_var, width=22).grid(row=1, column=3, sticky="w", pady=3)
        ttk.Button(form_frame, text="Выбрать", command=lambda: DatePicker.open(self.root, self.last_contact_var)).grid(
            row=1, column=4, sticky="w", padx=(4, 0)
        )

        ttk.Label(form_frame, text="Следующий шаг (YYYY-MM-DD)").grid(
            row=2, column=2, sticky="w", pady=3, padx=(16, 8)
        )
        ttk.Entry(form_frame, textvariable=self.next_step_var, width=22).grid(row=2, column=3, sticky="w", pady=3)
        ttk.Button(form_frame, text="Выбрать", command=lambda: DatePicker.open(self.root, self.next_step_var)).grid(
            row=2, column=4, sticky="w", padx=(4, 0)
        )

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

    def _build_filters(self) -> None:
        filter_frame = ttk.LabelFrame(self.root, text="Просмотр", padding=12)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(filter_frame, text="Компания").grid(row=0, column=0, padx=(0, 8))
        self.filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, state="readonly", width=30)
        self.filter_combo.grid(row=0, column=1, padx=(0, 12))
        ttk.Label(filter_frame, text="Статус").grid(row=0, column=2, padx=(0, 8))
        self.filter_status_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_status_var,
            state="readonly",
            width=22,
            values=["Все", *STATUS_OPTIONS],
        )
        self.filter_status_combo.grid(row=0, column=3, padx=(0, 12))
        ttk.Button(filter_frame, text="Показать рекрутеров", command=self._refresh_table).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(filter_frame, text="Обновить список компаний", command=self._refresh_company_filter).grid(
            row=0, column=5
        )

    def _build_table(self) -> None:
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
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", height=12)
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

        self.tree.tag_configure("email", foreground="#0a5dbd")
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        tree_scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

    def _build_actions(self) -> None:
        actions = ttk.Frame(self.root, padding=12)
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(actions, text="Открыть TG", command=self.open_tg).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Написать письмо", command=self.open_email).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Удалить выбранного", command=self.delete_recruiter).grid(
            row=0, column=2, padx=(0, 8)
        )

    def _bind_shortcuts(self) -> None:
        """Хоткеи: стандартные бинды + фолбек по keycode для нераспознанных раскладок."""
        keycode_map = {
            67: "<<Copy>>",  # C
            86: "<<Paste>>",  # V
            88: "<<Cut>>",  # X
            65: "<<SelectAll>>",  # A
            90: "<<Undo>>",  # Z
        }

        def on_ctrl_keycode(event: tk.Event) -> str | None:
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
        recruiter = self._get_recruiter_from_form()
        if not self._validate_required(recruiter):
            return
        try:
            self.repo.add(recruiter)
            logging.info("Добавлен рекрутер: %s (%s)", recruiter.full_name, recruiter.company)
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
        recruiter = self._get_recruiter_from_form(include_id=True)
        if not self._validate_required(recruiter):
            return
        try:
            self.repo.update(recruiter)
            logging.info("Обновлен рекрутер id=%s", recruiter.id)
            messagebox.showinfo("Готово", "Изменения сохранены.")
            self.clear_form()
            self._refresh_company_filter()
            self._refresh_table()
        except Exception as exc:
            logging.exception("Ошибка при обновлении рекрутера: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {exc}")

    def _get_recruiter_from_form(self, include_id: bool = False) -> Recruiter:
        recruiter = Recruiter(
            id=self.current_edit_id if include_id else None,
            company=self.company_var.get(),
            full_name=self.full_name_var.get(),
            telegram=self.telegram_var.get(),
            phone=self.phone_var.get(),
            position=self.position_var.get(),
            email=self.email_var.get(),
            comments=self.comments_text.get("1.0", tk.END),
            resume_path=self.current_resume_path,
            status=self.status_var.get() or DEFAULT_STATUS,
            last_contact=self.last_contact_var.get(),
            next_step=self.next_step_var.get(),
        )
        return recruiter.normalized()

    def _validate_required(self, recruiter: Recruiter) -> bool:
        if not recruiter.company:
            messagebox.showwarning("Поля обязательны", "Укажите компанию.")
            return False
        if not recruiter.full_name:
            messagebox.showwarning("Поля обязательны", "Укажите ФИО.")
            return False
        return True

    def clear_form(self) -> None:
        self.company_var.set("")
        self.full_name_var.set("")
        self.telegram_var.set("")
        self.phone_var.set("")
        self.position_var.set("")
        self.email_var.set("")
        self.status_var.set(DEFAULT_STATUS)
        self.last_contact_var.set("")
        self.next_step_var.set("")
        self.comments_text.delete("1.0", tk.END)
        self.current_edit_id = None
        self.current_resume_path = ""

    def _refresh_company_filter(self) -> None:
        companies = self.repo.get_companies()
        values = ["Все"] + companies
        self.filter_combo["values"] = values
        if self.filter_var.get() not in values:
            self.filter_var.set("Все")

    def _refresh_table(self) -> None:
        for row_id in self.tree.get_children():
            self.tree.delete(row_id)
        rows = self.repo.fetch(self.filter_var.get(), self.filter_status_var.get())
        for recruiter in rows:
            self._insert_tree_row(recruiter)
        logging.info("Обновлен список рекрутеров (%s записей)", len(rows))

    def _insert_tree_row(self, recruiter: Recruiter) -> None:
        item_id = self.tree.insert(
            "",
            "end",
            values=(
                recruiter.id,
                recruiter.company,
                recruiter.full_name,
                recruiter.telegram,
                recruiter.phone,
                recruiter.position,
                recruiter.email,
                recruiter.status or "",
                recruiter.last_contact or "",
                recruiter.next_step or "",
                recruiter.comment_preview(),
            ),
        )
        self.tree.item(item_id, tags=("email",))

    def _get_selected_recruiter(self) -> Recruiter | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Нет выбора", "Выберите рекрутера в таблице.")
            return None
        item = self.tree.item(selection[0])
        values = item["values"]
        if not values:
            return None
        try:
            recruiter_id = int(values[0])
        except (TypeError, ValueError):
            recruiter_id = None
        recruiter = self.repo.get(recruiter_id) if recruiter_id else None
        if recruiter:
            return recruiter
        return Recruiter(
            id=recruiter_id,
            company=values[1],
            full_name=values[2],
            telegram=values[3],
            phone=values[4],
            position=values[5],
            email=values[6],
            status=values[7],
            last_contact=values[8],
            next_step=values[9],
            comments=values[10],
        )

    def _fill_form(self, recruiter: Recruiter) -> None:
        self.current_edit_id = recruiter.id
        self.current_resume_path = recruiter.resume_path
        self.company_var.set(recruiter.company)
        self.full_name_var.set(recruiter.full_name)
        self.telegram_var.set(recruiter.telegram)
        self.phone_var.set(recruiter.phone)
        self.position_var.set(recruiter.position)
        self.email_var.set(recruiter.email)
        self.status_var.set(recruiter.status or DEFAULT_STATUS)
        self.last_contact_var.set(recruiter.last_contact or "")
        self.next_step_var.set(recruiter.next_step or "")
        self.comments_text.delete("1.0", tk.END)
        self.comments_text.insert("1.0", recruiter.comments or "")

    def on_tree_double_click(self, event: tk.Event) -> None:
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        col_name = self.tree["columns"][int(self.tree.identify_column(event.x).replace("#", "")) - 1]
        if col_name == "telegram":
            self.open_tg()
        elif col_name == "email":
            self.open_email()
        else:
            recruiter = self._get_selected_recruiter()
            if recruiter:
                self._fill_form(recruiter)

    def open_tg(self) -> None:
        recruiter = self._get_selected_recruiter()
        if not recruiter:
            return
        handle = recruiter.telegram.strip().lstrip("@")
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
        recruiter = self._get_selected_recruiter()
        if not recruiter:
            return
        email = recruiter.email.strip()
        if not email:
            messagebox.showwarning("Почта", "У этого рекрутера не указана почта.")
            return
        subject = "Вопрос по вакансии"
        try:
            if USE_GMAIL_COMPOSE:
                self._open_gmail_compose(email, subject)
            else:
                opened = self._open_mailto(email, subject)
                if not opened:
                    self._open_gmail_compose(email, subject)
                    messagebox.showinfo(
                        "Почта",
                        "Не получилось открыть системный почтовый клиент.\nОткрыл Gmail compose в браузере.\n"
                        "Проверьте, что по умолчанию назначен почтовый клиент для mailto.",
                    )
        except Exception as exc:
            logging.exception("Не удалось открыть почту: %s", exc)
            messagebox.showerror("Почта", f"Не удалось открыть почту.\nОшибка: {exc}\nАдрес: {email}")
        finally:
            self._copy_to_clipboard(email)

    def _open_gmail_compose(self, email: str, subject: str) -> None:
        gmail_url = f"https://mail.google.com/mail/?view=cm&fs=1&to={email}&su={subject.replace(' ', '%20')}"
        webbrowser.open(gmail_url)
        logging.info("Gmail compose для %s", email)

    def _open_mailto(self, email: str, subject: str) -> bool:
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
        return opened

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    def delete_recruiter(self) -> None:
        recruiter = self._get_selected_recruiter()
        if not recruiter:
            return
        confirm = messagebox.askyesno(
            "Удалить",
            f"Удалить рекрутера {recruiter.full_name} из {recruiter.company}?",
            icon="warning",
        )
        if not confirm:
            return
        try:
            self.repo.delete(int(recruiter.id))  # type: ignore[arg-type]
            logging.info("Удален рекрутер id=%s", recruiter.id)
            self._refresh_table()
            self._refresh_company_filter()
        except Exception as exc:
            logging.exception("Ошибка при удалении: %s", exc)
            messagebox.showerror("Ошибка", f"Не удалось удалить: {exc}")

    def _on_close(self) -> None:
        try:
            self.repo.close()
        finally:
            self.root.destroy()


def main() -> None:
    setup_logging()
    logging.info("Запуск приложения версии %s", APP_VERSION)
    root = tk.Tk()
    CRMApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

