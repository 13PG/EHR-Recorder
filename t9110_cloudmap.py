"""
High-temperature fire-flood crude-oil ignition thermo-kinetics evaluation
system (Hongrun T9110 paperless recorder front end).

Single full-screen dashboard. All functional areas (communication config,
parameters, probe configuration, pipe cloud map, per-channel curves, data
export) are laid out directly on one page - no navigation pages, no popups
for navigation.

Core acquisition logic (Modbus RTU read + word-swapped float decode in
_connect / _read_channel) is unchanged from the original. The temperature
cloud is interpolated with ordinary Kriging (implemented in _kriging_1d).
"""

import os
import struct
import threading
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
from pymodbus.client import ModbusSerialClient

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

APP_TITLE = "高温火驱原油着火热动力学特征评价系统"

# ---- Channel model ----
# T1..T7 : 7 internal pipe temperatures, single middle row (cloud map source)
# T8     : single pipe-wall temperature (external)
# P1, P2 : inlet / outlet pressures
# Channel numbers are configurable at runtime in the probe-config area; the
# values below are only the defaults.
PIPE_TEMP_LABELS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]
WALL_TEMP_LABEL = "T8"
PRESSURE_LABELS = ["P1", "P2"]
ALL_LABELS = PIPE_TEMP_LABELS + [WALL_TEMP_LABEL] + PRESSURE_LABELS

DEFAULT_CHANNEL = {
    "T1": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5, "T6": 6, "T7": 7,
    "T8": 8, "P1": 10, "P2": 11,
}
PROBE_DISPLAY = {
    "T1": "测温1", "T2": "测温2", "T3": "测温3", "T4": "测温4",
    "T5": "测温5", "T6": "测温6", "T7": "测温7",
    "T8": "管壁温度", "P1": "注入压力", "P2": "产出压力",
}

# Cloud-map geometry taken directly from PPT slide 6 (units = PPT inches,
# origin top-left; the cloud axis uses an inverted y so these map 1:1 to the
# drawing). Each rect is (x, y, w, h). The device keeps its full shape: a
# rounded pipe body (filled by the Kriging gradient), end flanges, inlet/outlet
# nozzles, the 7 probes in one middle row, P1/P2 ports, and two thick solid
# connector lines from the ports to the nozzles.
PIPE_BODY_RECT = (2.625, 2.419, 6.551, 1.472)
LEFT_FLANGE = (1.849, 2.023, 0.776, 2.16)
LEFT_NOZZLE = (1.403, 2.647, 0.445, 1.026)
RIGHT_FLANGE = (9.175, 2.023, 0.776, 2.16)
RIGHT_NOZZLE = (9.951, 2.647, 0.445, 1.026)

# 7 temperature probes, single middle row (probe center coordinates).
PIPE_POINTS = {
    "T1": (3.201, 3.108), "T2": (4.074, 3.111), "T3": (5.028, 3.108),
    "T4": (5.840, 3.111), "T5": (6.738, 3.111), "T6": (7.753, 3.111),
    "T7": (8.681, 3.108),
}
PORT_POINTS = {"P1": (0.821, 2.875), "P2": (10.981, 2.875)}
# Thick solid inlet/outlet connector lines (x_start, x_end, y).
CONNECTOR_LINES = [(1.033, 1.742, 3.108), (10.174, 10.883, 3.108)]

CURVE_COLORS = {
    "T1": "#E53935", "T2": "#FB8C00", "T3": "#C9A100", "T4": "#43A047",
    "T5": "#00ACC1", "T6": "#1E88E5", "T7": "#5E35B1",
    "T8": "#8E24AA", "P1": "#000000", "P2": "#546E7A",
}

OTHER_PARAM_LABELS = [
    "岩心孔隙度",
    "岩心渗透率",
    "预留参数1",
    "预留参数2",
    "预留参数3",
]

BG = "#EAF4F4"
PANEL_BG = "#F7FBFB"
ACCENT = "#1565C0"
TITLE_FG = "#0D47A1"


class T9110App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)

        # Open maximized / full screen by default so every area is visible.
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                self.root.attributes("-fullscreen", True)
        self.root.minsize(1200, 720)
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self._fullscreen = False

        # Communication parameters (editable inline, defaults from spec).
        self.port_var = tk.StringVar(value="COM3")
        self.baud_var = tk.IntVar(value=9600)
        self.slave_var = tk.IntVar(value=1)
        self.interval_var = tk.IntVar(value=2)

        # Per-probe channel assignment and enable/disable state.
        self.probe_channel = {lab: tk.IntVar(value=DEFAULT_CHANNEL[lab])
                              for lab in ALL_LABELS}
        self.probe_enabled = {lab: tk.BooleanVar(value=True) for lab in ALL_LABELS}

        # Live values keyed by label, None when not read / disabled / failed.
        self.values = {lab: None for lab in ALL_LABELS}
        self.other_param_vars = {lab: tk.StringVar(value="0") for lab in OTHER_PARAM_LABELS}

        # Personalized chart state (one channel shown at a time).
        self.history = {lab: [] for lab in ALL_LABELS}
        self.history_start = None
        self.selected_channel = tk.StringVar(value=ALL_LABELS[0])

        self.rendering = False
        self.value_labels = {}

        self._build_styles()
        self._build_ui()

    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TLabelframe", background=PANEL_BG, borderwidth=2,
                        relief="groove")
        style.configure("Panel.TLabelframe.Label", background=BG,
                        foreground=TITLE_FG, font=("Microsoft YaHei", 10, "bold"))
        style.configure("TLabel", background=PANEL_BG, font=("Microsoft YaHei", 9))
        style.configure("Page.TLabel", background=BG, font=("Microsoft YaHei", 9))
        style.configure("Value.TLabel", background="white", relief="sunken",
                        font=("Consolas", 10), anchor="center")
        style.configure("Accent.TButton", font=("Microsoft YaHei", 9, "bold"))

    def _toggle_fullscreen(self, _event=None):
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    # ----------------------------------------------------------------- UI

    def _build_ui(self):
        self._build_title()

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        main.grid_columnconfigure(0, weight=0, minsize=300)   # left params
        main.grid_columnconfigure(1, weight=1)                # right content
        main.grid_rowconfigure(0, weight=1)

        self._build_param_panel(main)

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=3)   # cloud map (top)
        right.grid_rowconfigure(1, weight=2)   # personalized (bottom)

        self._build_cloudmap_panel(right)
        self._build_personalized_panel(right)

        self.status_var = tk.StringVar(value="就绪")
        status = ttk.Label(self.root, textvariable=self.status_var, style="Page.TLabel",
                           foreground="#555", anchor="w")
        status.pack(fill="x", side="bottom", padx=10, pady=(0, 4))

    def _build_title(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=8, pady=(8, 4))

        title = tk.Label(
            bar, text=APP_TITLE,
            bg=BG, fg=TITLE_FG, font=("Microsoft YaHei", 20, "bold"),
        )
        title.pack(side="left", padx=(10, 0))

        comm = tk.Frame(bar, bg=BG)
        comm.pack(side="right", padx=10)
        tk.Label(comm, text="通讯配置", bg=BG, fg="#444",
                 font=("Microsoft YaHei", 9, "bold")).grid(row=0, column=0, columnspan=8)
        fields = [
            ("端口:", self.port_var, 8),
            ("波特率:", self.baud_var, 7),
            ("通讯地址:", self.slave_var, 4),
            ("刷新(s):", self.interval_var, 4),
        ]
        for i, (lab, var, w) in enumerate(fields):
            tk.Label(comm, text=lab, bg=BG, font=("Microsoft YaHei", 9)).grid(
                row=1, column=i * 2, sticky="e", padx=(8, 2))
            tk.Entry(comm, textvariable=var, width=w, font=("Consolas", 9)).grid(
                row=1, column=i * 2 + 1, padx=(0, 4))

    # ---- Left: parameters + probe config ----

    def _build_param_panel(self, parent):
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        base = ttk.LabelFrame(left, text="基础参数", style="Panel.TLabelframe")
        base.pack(fill="x", pady=(0, 6))
        base_rows = [
            ("管壁温度", "T8", "°C"),
            ("注入压力", "P1", "MPa"),
            ("产出压力", "P2", "MPa"),
        ]
        for r, (name, label, unit) in enumerate(base_rows):
            ttk.Label(base, text=name + "：").grid(row=r, column=0, sticky="e",
                                                       padx=(8, 2), pady=3)
            val = ttk.Label(base, text="--", style="Value.TLabel", width=9)
            val.grid(row=r, column=1, padx=2, pady=3, sticky="ew")
            ttk.Label(base, text=unit).grid(row=r, column=2, sticky="w", padx=(2, 8))
            self.value_labels[label] = val
        base.grid_columnconfigure(1, weight=1)

        self._build_probe_panel(left)

        other = ttk.LabelFrame(left, text="其他参数", style="Panel.TLabelframe")
        other.pack(fill="x", pady=(0, 6))
        for r, label in enumerate(OTHER_PARAM_LABELS):
            ttk.Label(other, text=label + "：").grid(row=r, column=0, sticky="e",
                                                         padx=(8, 2), pady=2)
            tk.Entry(other, textvariable=self.other_param_vars[label], width=10,
                     font=("Consolas", 9)).grid(row=r, column=1, padx=2, pady=2, sticky="ew")
        ttk.Button(other, text="修改值", command=self.on_apply_other_params,
                   style="Accent.TButton").grid(row=len(OTHER_PARAM_LABELS), column=0,
                                                 columnspan=2, pady=5)
        other.grid_columnconfigure(1, weight=1)

    def _build_probe_panel(self, parent):
        # Functional area: assign each probe to a Modbus channel and toggle it
        # on/off. A disabled temperature probe is filled in by Kriging.
        cfg = ttk.LabelFrame(parent, text="探头配置（通道映射 / 开关）",
                             style="Panel.TLabelframe")
        cfg.pack(fill="x", pady=(0, 6))
        for c, head in enumerate(("点位", "通道", "启用")):
            ttk.Label(cfg, text=head, font=("Microsoft YaHei", 9, "bold")).grid(
                row=0, column=c, padx=6, pady=(4, 2))
        for r, label in enumerate(ALL_LABELS, start=1):
            ttk.Label(cfg, text=f"{label}  {PROBE_DISPLAY[label]}").grid(
                row=r, column=0, sticky="w", padx=6, pady=1)
            tk.Spinbox(cfg, from_=1, to=64, width=4, font=("Consolas", 9),
                       textvariable=self.probe_channel[label]).grid(
                row=r, column=1, padx=6, pady=1)
            tk.Checkbutton(cfg, variable=self.probe_enabled[label], bg=PANEL_BG,
                           activebackground=PANEL_BG,
                           command=self._on_probe_config_changed).grid(
                row=r, column=2, padx=6, pady=1)

    def _on_probe_config_changed(self):
        # Reflect enable/disable immediately (grey markers, refilled cloud).
        self._draw_device(with_cloud=self.rendering)

    # ---- Center: pipe cloud map ----

    def _build_cloudmap_panel(self, parent):
        center = ttk.LabelFrame(parent, text="云图区（测温）",
                                style="Panel.TLabelframe")
        center.grid(row=0, column=0, sticky="nsew", pady=(0, 6))

        btns = tk.Frame(center, bg=PANEL_BG)
        btns.pack(side="bottom", fill="x", padx=6, pady=(0, 8))
        self.start_btn = ttk.Button(btns, text="开始渲染",
                                    command=self.on_start_render, style="Accent.TButton")
        self.start_btn.pack(side="left", padx=10, pady=4)
        self.stop_btn = ttk.Button(btns, text="停止渲染",
                                   command=self.on_stop_render, state="disabled",
                                   style="Accent.TButton")
        self.stop_btn.pack(side="left", padx=10, pady=4)

        self.cloud_fig = Figure(figsize=(8, 3.4), dpi=100, facecolor=PANEL_BG)
        self.cloud_ax = self.cloud_fig.add_subplot(111)
        self.cloud_canvas = FigureCanvasTkAgg(self.cloud_fig, master=center)
        self.cloud_canvas.get_tk_widget().pack(side="top", fill="both",
                                               expand=True, padx=6, pady=4)
        self._draw_device(with_cloud=False)

    # ---- Right: personalized display ----

    def _build_personalized_panel(self, parent):
        right = ttk.LabelFrame(parent, text="个性化展示",
                               style="Panel.TLabelframe")
        right.grid(row=1, column=0, sticky="nsew")

        top = tk.Frame(right, bg=PANEL_BG)
        top.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Button(top, text="导出数据", command=self.on_export,
                   style="Accent.TButton").pack(side="right", padx=6)

        # Single row of channel buttons - only one channel's curve at a time.
        chan_frame = tk.Frame(top, bg=PANEL_BG)
        chan_frame.pack(side="left", fill="x", expand=True)
        for label in ALL_LABELS:
            rb = tk.Radiobutton(
                chan_frame, text=label, value=label, variable=self.selected_channel,
                bg=PANEL_BG, fg=CURVE_COLORS[label], selectcolor="#BBDEFB",
                font=("Microsoft YaHei", 8, "bold"), command=self._render_personalized,
                indicatoron=False, width=4, relief="raised", bd=1,
            )
            rb.pack(side="left", padx=1)

        self.curve_fig = Figure(figsize=(5, 4.2), dpi=100, facecolor=PANEL_BG)
        self.curve_ax = self.curve_fig.add_subplot(111)
        self._draw_curve_placeholder()
        self.curve_canvas = FigureCanvasTkAgg(self.curve_fig, master=right)
        self.curve_canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=4)

    def _draw_curve_placeholder(self):
        self.curve_ax.clear()
        self.curve_ax.text(0.5, 0.5,
                           "开始渲染后显示各通道数值随时间变化曲线",
                           ha="center", va="center", fontsize=10, color="gray",
                           transform=self.curve_ax.transAxes)
        self.curve_ax.set_xticks([])
        self.curve_ax.set_yticks([])

    # ----------------------------------------------------- Modbus core
    # (Unchanged acquisition logic carried over from the original program.)

    def _connect(self):
        client = ModbusSerialClient(
            port=self.port_var.get(),
            baudrate=self.baud_var.get(),
            bytesize=8, stopbits=1, parity="N", timeout=3,
        )
        if not client.connect():
            raise ConnectionError(f"Cannot connect to {self.port_var.get()}")
        return client

    def _read_channel(self, client, ch):
        addr = 2 * (ch - 1)
        result = client.read_input_registers(
            address=addr, count=2, device_id=self.slave_var.get()
        )
        if result.isError():
            raise IOError(f"CH{ch} error: {result}")
        r = result.registers
        raw = struct.pack(">HH", r[1], r[0])
        return struct.unpack(">f", raw)[0]

    def _read_all(self):
        readings = {}
        errors = []
        client = self._connect()
        try:
            for label in ALL_LABELS:
                if not self.probe_enabled[label].get():
                    readings[label] = None      # disabled probe -> no reading
                    continue
                ch = self.probe_channel[label].get()
                try:
                    readings[label] = self._read_channel(client, ch)
                except Exception as e:
                    errors.append(str(e))
                    readings[label] = None
        finally:
            client.close()
        return readings, errors

    # ----------------------------------------------------- Kriging

    def _kriging_1d(self, xs, vs, xq):
        # Ordinary Kriging in 1D (along the pipe length) with a Gaussian
        # variogram. Returns the estimated value at each query position xq.
        xs = np.asarray(xs, dtype=float)
        vs = np.asarray(vs, dtype=float)
        xq = np.asarray(xq, dtype=float)
        n = len(xs)
        if n == 1:
            return np.full_like(xq, vs[0])

        span = xs.max() - xs.min()
        a = span if span > 0 else 1.0          # correlation range
        nugget = 1e-6

        def vario(h):
            return (1.0 - np.exp(-(h ** 2) / (a ** 2))) + nugget * (h > 0)

        H = np.abs(xs[:, None] - xs[None, :])
        A = np.ones((n + 1, n + 1))
        A[:n, :n] = vario(H)
        A[n, n] = 0.0

        Hq = np.abs(xq[:, None] - xs[None, :])  # (Q, n)
        B = np.ones((n + 1, len(xq)))
        B[:n, :] = vario(Hq).T
        try:
            W = np.linalg.solve(A, B)
        except np.linalg.LinAlgError:
            W = np.linalg.lstsq(A, B, rcond=None)[0]
        w = W[:n, :]                             # (n, Q)
        return (w * vs[:, None]).sum(axis=0)

    # ----------------------------------------------------- Rendering

    def _draw_device(self, with_cloud):
        # Faithful reproduction of the PPT slide-6 cloud diagram: rounded pipe
        # body filled by the Kriging gradient, end flanges + inlet/outlet
        # nozzles, the 7 probes in one middle row, P1/P2 ports, and two thick
        # solid connector lines. (No pipe-wall box - it lives in the left panel.)
        self.cloud_fig.clear()
        ax = self.cloud_fig.add_subplot(111)
        self.cloud_ax = ax
        # Tight limits + free aspect so the device fills the whole area. The
        # bottom bound includes the flange bottom (y=4.183) so nothing is cut.
        ax.set_xlim(0.45, 11.35)
        ax.set_ylim(4.30, 1.88)      # inverted y -> top-left origin, like PPT
        ax.set_aspect("auto")
        ax.set_facecolor("white")
        ax.axis("off")
        # Solid white background so nothing renders dark in any backend.
        ax.add_patch(Rectangle((0.45, 1.88), 10.9, 2.42, fc="white", ec="none",
                               zorder=0))

        bx, by, bw, bh = PIPE_BODY_RECT

        # End flanges + inlet/outlet nozzles (behind the body).
        for (x, y, w, h) in (LEFT_FLANGE, RIGHT_FLANGE):
            ax.add_patch(Rectangle((x, y), w, h, fc="#CFD8DC", ec="#37474F",
                                   lw=1.5, zorder=3))
        for (x, y, w, h) in (LEFT_NOZZLE, RIGHT_NOZZLE):
            ax.add_patch(Rectangle((x, y), w, h, fc="#B0BEC5", ec="#37474F",
                                   lw=1.5, zorder=3))

        # Kriging gradient inside the pipe body (sharp-rect extent; no clip).
        if with_cloud:
            self._fill_cloud(ax, bx, by, bw, bh)

        # Pipe body outline on top of the gradient.
        ax.add_patch(Rectangle((bx, by), bw, bh, fill=False, ec="#222222",
                               lw=2.2, zorder=6))

        # Thick solid inlet / outlet connector lines (ports -> nozzles).
        for (x1l, x2l, yl) in CONNECTOR_LINES:
            ax.plot([x1l, x2l], [yl, yl], color="#111111", linewidth=4,
                    solid_capstyle="round", zorder=5)

        # 7 temperature probes in one middle row.
        for i, label in enumerate(PIPE_TEMP_LABELS, start=1):
            px, py = PIPE_POINTS[label]
            self._draw_marker(ax, px, py, str(i), label, "°C",
                              with_value=with_cloud, above=True)

        # Inlet / outlet pressure ports.
        for label in PRESSURE_LABELS:
            px, py = PORT_POINTS[label]
            self._draw_marker(ax, px, py, label, label, "MPa",
                              with_value=with_cloud, above=False, port=True)

        self.cloud_fig.tight_layout(pad=0.4)
        self.cloud_canvas.draw()

    def _fill_cloud(self, ax, bx, by, bw, bh):
        # Column-constant cloud inside the pipe body: each probe temperature
        # fills its whole vertical column; values between probes are Kriging-
        # interpolated along x and held constant beyond the end probes.
        xs, vs = [], []
        for label in PIPE_TEMP_LABELS:
            if not self.probe_enabled[label].get():
                continue                         # disabled -> Kriging fills it
            v = self.values.get(label)
            if v is None:
                continue
            xs.append(PIPE_POINTS[label][0])
            vs.append(v)
        if not xs:
            return

        order = np.argsort(xs)
        xs = np.asarray(xs)[order]
        vs = np.asarray(vs)[order]

        xi = np.linspace(bx, bx + bw, 360)
        xq = np.clip(xi, xs.min(), xs.max())     # hold end values at the edges
        line = self._kriging_1d(xs, vs, xq)
        Zi = np.tile(line, (2, 1))               # column-constant field

        vmin, vmax = float(vs.min()), float(vs.max())
        if vmin == vmax:
            vmin -= 1
            vmax += 1

        im = ax.imshow(Zi, extent=[bx, bx + bw, by, by + bh], origin="lower",
                       aspect="auto", cmap="jet", vmin=vmin, vmax=vmax,
                       interpolation="bilinear", zorder=2)
        self.cloud_fig.colorbar(im, ax=ax, label="温度 (°C)", shrink=0.7,
                                pad=0.015, fraction=0.045)

    def _draw_marker(self, ax, x, y, text, label, unit, with_value, above,
                     port=False):
        enabled = self.probe_enabled[label].get()
        if not enabled:
            face = "#CFD8DC"
        elif port:
            face = "#FFE082"
        else:
            face = "white"
        ax.plot(x, y, "o", markersize=16, markerfacecolor=face,
                markeredgecolor="#263238", markeredgewidth=1.4, zorder=8)
        ax.text(x, y, text, ha="center", va="center", fontsize=8,
                fontweight="bold", color="#263238", zorder=9)
        if with_value:
            v = self.values.get(label)
            if not enabled:
                txt = "关闭"
            elif v is None:
                txt = "--"
            else:
                txt = f"{v:.1f}{unit}"
            dy = 16 if above else -16
            ax.annotate(txt, (x, y), textcoords="offset points", xytext=(0, dy),
                        ha="center", va="center", fontsize=7, fontweight="bold",
                        color="white",
                        bbox=dict(boxstyle="round,pad=0.2", fc="black", alpha=0.75),
                        zorder=9)

    def _render_personalized(self):
        ax = self.curve_ax
        ax.clear()
        if self.history_start is None:
            self._draw_curve_placeholder()
            self.curve_canvas.draw()
            return

        label = self.selected_channel.get()
        series = self.history.get(label, [])
        if series:
            ts = [p[0] for p in series]
            ys = [p[1] for p in series]
            ax.plot(ts, ys, color=CURVE_COLORS[label], linewidth=1.6)
            ax.set_title(f"通道 {label}", fontsize=10, fontweight="bold")
        else:
            ax.text(0.5, 0.5, f"通道 {label} 暂无数据",
                    ha="center", va="center", fontsize=10, color="gray",
                    transform=ax.transAxes)

        ax.set_xlabel("时间 / mins")
        ax.set_ylabel("对应值")
        ax.grid(True, alpha=0.3)
        self.curve_fig.tight_layout()
        self.curve_canvas.draw()

    def _update_indicators(self):
        for label, widget in self.value_labels.items():
            v = self.values.get(label)
            widget.config(text=("--" if v is None else f"{v:.2f}"))

    # ----------------------------------------------------- Actions

    def on_apply_other_params(self):
        bad = []
        for label, var in self.other_param_vars.items():
            try:
                float(var.get())
            except ValueError:
                bad.append(label)
        if bad:
            messagebox.showwarning("参数无效",
                                   "以下参数不是有效数字：\n" + "\n".join(bad))
            return
        self.status_var.set("其他参数已保存")

    def on_start_render(self):
        if self.rendering:
            return
        self.history = {lab: [] for lab in ALL_LABELS}
        self.history_start = datetime.now()
        self.values = {lab: None for lab in ALL_LABELS}
        self._render_personalized()
        self._draw_device(with_cloud=False)

        self.rendering = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("正在读取通道数据...")
        self._refresh_tick()

    def on_stop_render(self):
        self.rendering = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("已停止渲染")

    def _refresh_tick(self):
        if not self.rendering:
            return

        def work():
            try:
                readings, errors = self._read_all()
                self.root.after(0, lambda: self._on_tick_done(readings, errors))
            except Exception as e:
                self.root.after(0, lambda err=e: self._on_tick_error(err))

        threading.Thread(target=work, daemon=True).start()

    def _on_tick_done(self, readings, errors):
        if not self.rendering:
            return

        # Error popup only when at least one probe is enabled yet every reading
        # failed (a real communication problem).
        any_enabled = any(self.probe_enabled[l].get() for l in ALL_LABELS)
        if any_enabled and all(v is None for v in readings.values()):
            self.rendering = False
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            messagebox.showerror(
                "通讯异常",
                "所有通道均未获取到数据，请检查通讯配置（端口/波特率/地址）。")
            self.status_var.set("错误：所有通道读取失败")
            return

        self.values = readings
        t_min = (datetime.now() - self.history_start).total_seconds() / 60.0
        for label, v in readings.items():
            if v is not None:
                self.history[label].append((t_min, v))

        self._update_indicators()
        self._draw_device(with_cloud=True)
        self._render_personalized()

        now = datetime.now().strftime("%H:%M:%S")
        got = sum(1 for v in readings.values() if v is not None)
        msg = f"实时渲染中 | {got}/{len(ALL_LABELS)} 通道 | {now}"
        if errors:
            msg += f" | {len(errors)} 个读取错误"
        self.status_var.set(msg)
        self.root.after(max(self.interval_var.get(), 1) * 1000, self._refresh_tick)

    def _on_tick_error(self, error):
        self.rendering = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        messagebox.showerror("通讯错误", str(error))
        self.status_var.set(f"错误：{error}")

    def on_export(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            messagebox.showerror(
                "缺少依赖",
                "导出 Excel 需要 openpyxl，请先执行：\npip install openpyxl")
            return

        if all(len(s) == 0 for s in self.history.values()):
            messagebox.showwarning("无数据", "当前没有可导出的实时数据。")
            return

        default = "T9110_data_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx"
        path = filedialog.asksaveasfilename(
            title="导出数据", defaultextension=".xlsx",
            initialfile=default, filetypes=[("Excel", "*.xlsx")])
        if not path:
            return

        wb = Workbook()
        wb.remove(wb.active)
        for label in ALL_LABELS:
            ws = wb.create_sheet(title=label)
            ws.append(["时间 (mins)", "数值"])
            for t_min, v in self.history[label]:
                ws.append([round(t_min, 4), v])
        try:
            wb.save(path)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
            return
        self.status_var.set("已导出：" + os.path.basename(path))
        messagebox.showinfo("导出成功", "数据已导出到：\n" + path)


def main():
    root = tk.Tk()
    T9110App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
