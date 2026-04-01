#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stamina Cheat - Автоматический помощник для тренажёра Stamina
Автор: okyrihuw, с частичным использованием claude
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import os
import random
import sys

# ──────────────────────────────────────────
# Проверка pynput
# ──────────────────────────────────────────
try:
    from pynput.keyboard import Controller, Key
    keyboard_ctrl = Controller()
    PYNPUT_OK = True
except ImportError:
    PYNPUT_OK = False
    keyboard_ctrl = None


# ──────────────────────────────────────────
# Парсер файла уроков Stamina
# ──────────────────────────────────────────
def parse_lessons(filepath: str):
    """
    Возвращает список: [(раздел, имя_урока, текст), ...]
    """
    lessons = []
    current_section = "Без раздела"
    current_name = None
    current_lines = []

    with open(filepath, "r", encoding="cp1251", errors="ignore") as f:
        lines = f.readlines()

    def flush():
        nonlocal current_name, current_lines
        if current_name and current_lines:
            raw = " ".join(" ".join(current_lines).split())
            # Убираем ¶ (разделитель NumPad-строк) — заменяем на Enter, но для
            # автопечати просто пробел, чтобы не ломать логику
            raw = raw.replace("¶", " ")
            if raw.strip():
                lessons.append((current_section, current_name, raw.strip()))
        current_name = None
        current_lines = []

    for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("[#]>"):
            flush()
            current_section = line[4:].strip()
        elif line.startswith("[#]<") or line.startswith("[#]-"):
            flush()
        elif line.startswith("[#] ") or line == "[#]":
            flush()
            current_name = line[4:].strip() if len(line) > 4 else "(без имени)"
        elif line.startswith("[#]"):
            flush()
        else:
            if current_name is not None and line.strip():
                current_lines.append(line)

    flush()
    return lessons


# ──────────────────────────────────────────
# Русская раскладка: соседние клавиши
# ──────────────────────────────────────────
RU_ROWS = [
    "йцукенгшщзхъ",
    "фывапролджэ",
    "ячсмитьбю",
]
RU_NEIGHBORS: dict[str, list[str]] = {}
for row in RU_ROWS:
    for i, ch in enumerate(row):
        neighbors = []
        if i > 0:
            neighbors.append(row[i - 1])
        if i < len(row) - 1:
            neighbors.append(row[i + 1])
        RU_NEIGHBORS[ch] = neighbors
        RU_NEIGHBORS[ch.upper()] = [n.upper() for n in neighbors]


def wrong_char(correct: str) -> str:
    """Возвращает «ошибочный» символ — соседнюю клавишу."""
    candidates = RU_NEIGHBORS.get(correct, [])
    if candidates:
        return random.choice(candidates)
    # для цифр/символов — просто соседняя цифра
    digits = "1234567890"
    if correct in digits:
        idx = digits.index(correct)
        pool = []
        if idx > 0:
            pool.append(digits[idx - 1])
        if idx < len(digits) - 1:
            pool.append(digits[idx + 1])
        if pool:
            return random.choice(pool)
    return "а"  # крайний случай


# ──────────────────────────────────────────
# Главное окно
# ──────────────────────────────────────────
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Stamina Cheat 1.0")
        self.root.geometry("860x620")
        self.root.minsize(760, 520)

        self.lessons: list[tuple[str, str, str]] = []
        self.filtered_indices: list[int | None] = []  # None = заголовок раздела
        self.current_text: str = ""

        self.stop_event = threading.Event()
        self.typing_thread: threading.Thread | None = None

        self._build_ui()
        self._try_autoload()

    # ── UI ────────────────────────────────
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        root = self.root

        # ── Верхняя панель: путь к файлу ──
        top = ttk.LabelFrame(root, text=" Файл уроков Stamina ", padding=6)
        top.pack(fill=tk.X, padx=10, pady=(10, 4))

        self.path_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.path_var, font=("Consolas", 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6)
        )
        ttk.Button(top, text="Обзор…", width=9, command=self._browse).pack(side=tk.LEFT)
        ttk.Button(top, text="Загрузить", width=10, command=self._load).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        # ── Центр: список уроков + настройки ──
        mid = ttk.Frame(root)
        mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        # Левая часть: список
        left = ttk.LabelFrame(mid, text=" Уроки ", padding=6)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        search_row = ttk.Frame(left)
        search_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search_row, text="🔍").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter())
        ttk.Entry(search_row, textvariable=self.search_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )

        lb_frame = ttk.Frame(left)
        lb_frame.pack(fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lb_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(
            lb_frame,
            yscrollcommand=sb.set,
            selectmode=tk.SINGLE,
            activestyle="none",
            font=("Arial", 10),
        )
        self.listbox.pack(fill=tk.BOTH, expand=True)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # Правая часть: настройки
        right = ttk.LabelFrame(mid, text=" Настройки ", padding=10)
        right.pack(side=tk.RIGHT, fill=tk.Y)

        # Скорость
        ttk.Label(right, text="Скорость (зн/мин):").grid(
            row=0, column=0, sticky=tk.W, pady=4
        )
        self.wpm_var = tk.IntVar(value=200)
        ttk.Spinbox(
            right, from_=30, to=1500, textvariable=self.wpm_var, width=10
        ).grid(row=0, column=1, padx=6)

        # Вариативность скорости
        ttk.Label(right, text="Вариативность (±%):").grid(
            row=1, column=0, sticky=tk.W, pady=4
        )
        self.jitter_var = tk.IntVar(value=15)
        ttk.Spinbox(
            right, from_=0, to=50, textvariable=self.jitter_var, width=10
        ).grid(row=1, column=1, padx=6)

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(
            row=2, column=0, columnspan=2, sticky=tk.EW, pady=8
        )

        # Ошибки
        ttk.Label(right, text="Ошибки (%):").grid(row=3, column=0, sticky=tk.W, pady=4)
        self.err_var = tk.DoubleVar(value=2.0)
        ttk.Spinbox(
            right,
            from_=0,
            to=99,
            increment=0.5,
            textvariable=self.err_var,
            width=10,
            format="%.1f",
        ).grid(row=3, column=1, padx=6)

        ttk.Label(right, text="Режим ошибок:").grid(
            row=4, column=0, sticky=tk.W, pady=4
        )
        self.err_mode = tk.StringVar(value="max")
        err_frame = ttk.Frame(right)
        err_frame.grid(row=4, column=1, sticky=tk.W)
        ttk.Radiobutton(
            err_frame, text="≤ X%", variable=self.err_mode, value="max"
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            err_frame, text="= X%", variable=self.err_mode, value="exact"
        ).pack(anchor=tk.W)

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=2, sticky=tk.EW, pady=8
        )

        # Задержка перед стартом
        ttk.Label(right, text="Задержка старта (с):").grid(
            row=6, column=0, sticky=tk.W, pady=4
        )
        self.delay_var = tk.IntVar(value=5)
        ttk.Spinbox(
            right, from_=2, to=60, textvariable=self.delay_var, width=10
        ).grid(row=6, column=1, padx=6)

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(
            row=7, column=0, columnspan=2, sticky=tk.EW, pady=8
        )

        # Инфо об уроке
        ttk.Label(right, text="Выбран урок:", font=("Arial", 9, "bold")).grid(
            row=8, column=0, columnspan=2, sticky=tk.W
        )
        self.info_name = ttk.Label(
            right, text="—", foreground="#555", wraplength=220, justify=tk.LEFT
        )
        self.info_name.grid(row=9, column=0, columnspan=2, sticky=tk.W, pady=2)
        self.info_chars = ttk.Label(right, text="", foreground="#0055aa")
        self.info_chars.grid(row=10, column=0, columnspan=2, sticky=tk.W)
        self.info_time = ttk.Label(right, text="", foreground="#007700")
        self.info_time.grid(row=11, column=0, columnspan=2, sticky=tk.W)

        # ── Нижняя панель: статус + кнопки ──
        bot = ttk.Frame(root)
        bot.pack(fill=tk.X, padx=10, pady=(4, 10))

        stat_frame = ttk.LabelFrame(bot, text=" Статус ", padding=6)
        stat_frame.pack(fill=tk.X, pady=(0, 6))

        self.status_var = tk.StringVar(value="Загрузите файл уроков Stamina.")
        self.status_lbl = ttk.Label(
            stat_frame, textvariable=self.status_var, font=("Arial", 12, "bold")
        )
        self.status_lbl.pack()

        self.prog_var = tk.DoubleVar(value=0)
        self.prog_bar = ttk.Progressbar(
            stat_frame, variable=self.prog_var, maximum=100, length=400
        )
        self.prog_bar.pack(fill=tk.X, pady=(4, 0))

        btn_row = ttk.Frame(bot)
        btn_row.pack()

        self.btn_start = ttk.Button(
            btn_row,
            text="▶  СТАРТ",
            width=22,
            command=self._start,
        )
        self.btn_start.pack(side=tk.LEFT, padx=8)

        self.btn_stop = ttk.Button(
            btn_row,
            text="■  СТОП",
            width=22,
            command=self._stop,
            state=tk.DISABLED,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=8)

    # ── Загрузка файла ────────────────────
    def _browse(self):
        path = filedialog.askopenfilename(
            title="Выберите файл уроков Stamina",
            filetypes=[("Файлы уроков", "lessons.*"), ("Все файлы", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self._load()

    def _try_autoload(self):
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])),
                         "Stamina", "Data", "lessons.ru"),
            os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])),
                         "Data", "lessons.ru"),
            r"C:\Stamina\Data\lessons.ru",
        ]
        for p in candidates:
            if os.path.exists(p):
                self.path_var.set(p)
                self._load()
                return

    def _load(self):
        path = self.path_var.get().strip()
        if not os.path.isfile(path):
            messagebox.showerror("Ошибка", "Файл не найден:\n" + path)
            return
        try:
            self.lessons = parse_lessons(path)
            self._filter()
            self.status_var.set(f"Загружено {len(self.lessons)} уроков. Выберите урок.")
        except Exception as exc:
            messagebox.showerror("Ошибка чтения", str(exc))

    # ── Список уроков ─────────────────────
    def _filter(self, *_):
        q = self.search_var.get().strip().lower()
        lb = self.listbox
        lb.delete(0, tk.END)
        self.filtered_indices = []

        last_section = None
        for idx, (sec, name, text) in enumerate(self.lessons):
            if q and q not in name.lower() and q not in sec.lower():
                continue
            if sec != last_section:
                lb.insert(tk.END, f"── {sec} ──")
                lb.itemconfig(tk.END, fg="#888888", selectbackground="#cccccc",
                              selectforeground="#888888")
                self.filtered_indices.append(None)
                last_section = sec
            lb.insert(tk.END, f"   {name}")
            self.filtered_indices.append(idx)

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        row = sel[0]
        if row >= len(self.filtered_indices):
            return
        idx = self.filtered_indices[row]
        if idx is None:
            return  # заголовок раздела — не выбираем
        sec, name, text = self.lessons[idx]
        self.current_text = text
        self.info_name.config(text=f"{sec} → {name}")
        n = len(text)
        self.info_chars.config(text=f"Символов: {n}")
        self._update_time_estimate()

    def _update_time_estimate(self):
        if not self.current_text:
            return
        wpm = self.wpm_var.get()
        if wpm <= 0:
            return
        secs = len(self.current_text) / wpm * 60
        m, s = divmod(int(secs), 60)
        self.info_time.config(text=f"~Время: {m}м {s:02d}с при {wpm} зн/мин")

    # ── Автопечать ────────────────────────
    def _start(self):
        if not self.current_text:
            messagebox.showwarning("Нет урока", "Сначала выберите урок из списка.")
            return
        if not PYNPUT_OK:
            messagebox.showerror(
                "Нет библиотеки",
                "Не установлена pynput.\n\nУстановите:\n  pip install pynput"
            )
            return

        self.stop_event.clear()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self._update_time_estimate()

        self.typing_thread = threading.Thread(
            target=self._worker, daemon=True
        )
        self.typing_thread.start()

    def _stop(self):
        self.stop_event.set()
        self.status_var.set("Остановлено пользователем.")
        self.prog_var.set(0)
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def _worker(self):
        try:
            # ── Обратный отсчёт ──
            delay = self.delay_var.get()
            for i in range(delay, 0, -1):
                if self.stop_event.is_set():
                    return
                self._set_status(f"⏳ Переключитесь в Stamina! Старт через {i} сек…")
                time.sleep(1)

            if self.stop_event.is_set():
                return

            # Нажимаем Enter, чтобы начать урок в Stamina
            keyboard_ctrl.press(Key.enter)
            keyboard_ctrl.release(Key.enter)
            time.sleep(0.15)

            if self.stop_event.is_set():
                return

            self._set_status("⌨  Печатаю…")

            text = self.current_text
            wpm = self.wpm_var.get()
            jitter = self.jitter_var.get() / 100.0
            err_pct = self.err_var.get()
            err_mode = self.err_mode.get()

            # Позиции ошибок (вычисляем ДО корректировки скорости)
            err_positions = self._calc_errors(text, err_pct, err_mode)

            # Корректируем base_delay с учётом накладных расходов на ошибки.
            # Stamina НЕ переходит к следующему символу при ошибке — она ждёт
            # правильный. Поэтому backspace не нужен и вреден (Stamina считает
            # его отдельной ошибкой). Нужно: wrong_char → (пауза) → correct_char.
            # Overhead = 1 лишнее нажатие на каждую ошибку.
            total_chars = len(text)
            n_errors = len(err_positions)
            overhead = (total_chars + n_errors) / total_chars if total_chars > 0 else 1.0
            # Базовая задержка между символами (с), скорректированная
            base_delay = 60.0 / (max(wpm, 1) * overhead)

            # Случайные «думательные» паузы включаем только при вариативности > 0,
            # иначе они нарушают скорость и могут вызывать таймаут-ошибки в Stamina.
            think_pause_prob = 0.03 * min(jitter / 0.10, 1.0) if jitter > 0 else 0.0

            for i, ch in enumerate(text):
                if self.stop_event.is_set():
                    break

                if i in err_positions:
                    # Печатаем неправильный символ.
                    # Backspace НЕ нужен: Stamina ждёт правильный символ на месте,
                    # а backspace она засчитала бы как ещё одну ошибку.
                    bad = wrong_char(ch)
                    keyboard_ctrl.type(bad)
                    d = base_delay * random.uniform(0.8, 1.2) * (1 + random.uniform(-jitter, jitter))
                    time.sleep(max(d, 0.01))

                    if self.stop_event.is_set():
                        break

                # Правильный символ (Stamina принимает его и переходит дальше)
                keyboard_ctrl.type(ch)

                # Обновляем прогресс
                pct = (i + 1) / total_chars * 100
                self.root.after(0, self.prog_var.set, pct)

                # Задержка с вариативностью (имитация человека)
                d = base_delay * (1 + random.uniform(-jitter, jitter))
                # Случайная «думательная» пауза — только при jitter > 0
                if think_pause_prob > 0 and random.random() < think_pause_prob:
                    d *= random.uniform(2.0, 4.0)
                time.sleep(max(d, 0.005))

            if not self.stop_event.is_set():
                self._set_status("✅ Готово!")

        except Exception as exc:
            self.root.after(
                0, lambda: messagebox.showerror("Ошибка при печати", str(exc))
            )
        finally:
            self.root.after(0, self._on_done)

    def _on_done(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def _set_status(self, msg: str):
        self.root.after(0, self.status_var.set, msg)

    @staticmethod
    def _calc_errors(text: str, err_pct: float, mode: str) -> set[int]:
        """Вычисляет множество индексов символов, на которых будет ошибка."""
        total = len(text)
        if err_pct <= 0 or total == 0:
            return set()

        max_errors = round(total * err_pct / 100)  # round() вместо int() — точный процент
        if mode == "exact":
            n_errors = max_errors
        else:  # "max"
            n_errors = random.randint(0, max_errors)

        # Допустимые позиции: не пробел, не первый символ
        valid = [i for i, c in enumerate(text) if c.strip() and i > 0]
        if not valid or n_errors <= 0:
            return set()

        n_errors = min(n_errors, len(valid))
        return set(random.sample(valid, n_errors))


# ──────────────────────────────────────────
# Точка входа
# ──────────────────────────────────────────
def main():
    if not PYNPUT_OK:
        print(
            "ВНИМАНИЕ: библиотека pynput не установлена!\n"
            "Установите командой:  pip install pynput\n"
            "Программа откроется, но автопечать работать не будет."
        )

    root = tk.Tk()
    
    # Умный поиск иконки (работает и при запуске .py, и после сборки в .exe)
    try:
        import sys
        import os
        
        # Определяем, где искать иконку
        if getattr(sys, 'frozen', False):
            # Запущено как .exe
            base_path = sys._MEIPASS
        else:
            # Запущено как .py
            base_path = os.path.dirname(os.path.abspath(__file__))
        
        icon_path = os.path.join(base_path, 'stamina.ico')
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
    except Exception:
        pass  # Если иконка не найдена — просто пропускаем, без ошибки
    
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
