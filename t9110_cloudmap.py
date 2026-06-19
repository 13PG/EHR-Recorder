"""
Hongrun T9110 Paperless Recorder - 1D Fire-Flood Simulation Monitor

Single full-screen dashboard. All functional areas (communication config,
parameters, pipe cloud map, per-channel curves, data export) are laid out
directly on one page - no navigation pages, no popups for navigation.

Core acquisition logic (Modbus RTU read + word-swapped float decode in
_connect / _read_channel, and scipy griddata interpolation) is unchanged
from the original; only the visualization / layout has been redesigned.
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
from matplotlib.patches import Rectangle, FancyBboxPatch, Ellipse
from scipy.interpolate import griddata
from pymodbus.client import ModbusSerialClient

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


# ---- Channel model (mapping option A) ----
# T1..T7  = CH1..CH7  internal pipe temperatures (interpolated cloud map)
# T8, T9  = CH8, CH9  pipe-wall temperatures (external)
# P1      = CH10      injection (inlet) pressure
# P2      = CH11      production (outlet) pressure

PIPE_TEMP_LABELS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]
WALL_TEMP_LABELS = ["T8", "T9"]
PRESSURE_LABELS = ["P1", "P2"]

# label -> modbus channel number
CHANNEL_NUMBER = {
    "T1": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5, "T6": 6, "T7": 7,
    "T8": 8, "T9": 9,
    "P1": 10, "P2": 11,
}
ALL_LABELS = PIPE_TEMP_LABELS + WALL_TEMP_LABELS + PRESSURE_LABELS

# Device geometry, taken directly from the PPT slide-3 cloud-map diagram
# (units = PPT inches, origin top-left; the cloud axis uses an inverted y so
# these coordinates map 1:1 to the drawing). Each rect is (x, y, w, h).
PIPE_BODY_RECT = (2.42, 2.14, 7.06, 1.47)   # main pipe body (cloud goes here)
LEFT_FLANGE = (1.88, 1.75, 0.78, 2.16)
LEFT_NOZZLE = (1.44, 2.37, 0.45, 1.03)
RIGHT_FLANGE = (9.21, 1.75, 0.78, 2.16)
RIGHT_NOZZLE = (9.99, 2.37, 0.45, 1.03)

# 7 internal temperature measurement points (center coordinates), zig-zag
# order 1/3/5/7 (top row) and 2/4/6 (bottom row), matching the PPT ellipses.
PIPE_POINTS = {
    "T1": (3.425, 2.525),   # 1 (top)
    "T2": (4.145, 3.295),   # 2 (bottom)
    "T3": (4.875, 2.525),   # 3 (top)
    "T4": (5.605, 3.295),   # 4 (bottom)
    "T5": (6.415, 2.525),   # 5 (top)
    "T6": (7.375, 3.295),   # 6 (bottom)
    "T7": (8.185, 2.525),   # 7 (top)
}
# Pressure ports at inlet (P1) / outlet (P2).
PORT_POINTS = {
    "P1": (1.62, 2.60),
    "P2": (10.41, 2.615),
}

# Colors for the per-channel curves on the personalized chart.
CURVE_COLORS = {
    "T1": "#E53935", "T2": "#FB8C00", "T3": "#FDD835", "T4": "#43A047",
    "T5": "#00ACC1", "T6": "#1E88E5", "T7": "#5E35B1",
    "T8": "#8E24AA", "T9": "#6D4C41",
    "P1": "#000000", "P2": "#546E7A",
}

# Manual (operator-entered) parameters shown in the "其他参数" block.
OTHER_PARAM_LABELS = [
    "岩心孔隙度",
    "岩心渗透率",
    "预留参数1",
    "预留参数2",
    "预留参数3",
]

BG = "#EAF4F4"        # page background (cyan-tinted, matches reference photo)
PANEL_BG = "#F7FBFB"
ACCENT = "#1565C0"
TITLE_FG = "#0D47A1"


class T9110App:
    def __init__(self, root):
        self.root = root
        self.root.title("一维火驱物理模拟装置 - T9110")
        self.root.configure(bg=BG)

        # Open maximized / full screen by default so every area is visible.
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                self.root.attributes("-fullscreen", True)
        self.root.minsize(1100, 700)
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self._fullscreen = False

        # Communication parameters (editable inline, defaults from spec).
        self.port_var = tk.StringVar(value="COM3")
        self.baud_var = tk.IntVar(value=9600)
        self.slave_var = tk.IntVar(value=1)
        self.interval_var = tk.IntVar(value=2)

        # Live values keyed by label, None when not yet read / read failed.
        self.values = {lab: None for lab in ALL_LABELS}
        # Manual parameter entry variables.
        self.other_param_vars = {lab: tk.StringVar(value="0") for lab in OTHER_PARAM_LABELS}

        # Personalized chart state.
        self.history = {lab: [] for lab in ALL_LABELS}   # label -> [(t_minutes, value)]
        self.history_start = None
        # Only one channel's curve is displayed at a time.
        self.selected_channel = tk.StringVar(value=ALL_LABELS[0])

        self.rendering = False
        self.value_labels = {}    # left-panel live value widgets

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
        main.grid_columnconfigure(0, weight=0, minsize=290)   # left params
        main.grid_columnconfigure(1, weight=1)                # right content
        main.grid_rowconfigure(0, weight=1)

        self._build_param_panel(main)

        # Right content stacked vertically: cloud map on top, personalized
        # display on the bottom.
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
            bar, text="一维火驱物理模拟装置",
            bg=BG, fg=TITLE_FG, font=("Microsoft YaHei", 22, "bold"),
        )
        title.pack(side="left", padx=(10, 0))

        # Communication configuration - edited directly on the page (no popup).
        comm = tk.Frame(bar, bg=BG)
        comm.pack(side="right", padx=10)
        tk.Label(comm, text="通讯配置", bg=BG, fg="#444",
                 font=("Microsoft YaHei", 9, "bold")).grid(row=0, column=0, columnspan=6)
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

    # ---- Left: parameters ----

    def _build_param_panel(self, parent):
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        base = ttk.LabelFrame(left, text="基础参数", style="Panel.TLabelframe")
        base.pack(fill="x", pady=(0, 6))
        base_rows = [
            ("管壁温度1", "T8", "°C"),
            ("管壁温度2", "T9", "°C"),
            ("注入压力", "P1", "MPa"),
            ("产出压力", "P2", "MPa"),
        ]
        for r, (name, label, unit) in enumerate(base_rows):
            ttk.Label(base, text=name + "：").grid(row=r, column=0, sticky="e",
                                                       padx=(8, 2), pady=4)
            val = ttk.Label(base, text="--", style="Value.TLabel", width=9)
            val.grid(row=r, column=1, padx=2, pady=4, sticky="ew")
            ttk.Label(base, text=unit).grid(row=r, column=2, sticky="w", padx=(2, 8))
            self.value_labels[label] = val
        base.grid_columnconfigure(1, weight=1)

        other = ttk.LabelFrame(left, text="其他参数", style="Panel.TLabelframe")
        other.pack(fill="x", pady=(0, 6))
        for r, label in enumerate(OTHER_PARAM_LABELS):
            ttk.Label(other, text=label + "：").grid(row=r, column=0, sticky="e",
                                                         padx=(8, 2), pady=4)
            tk.Entry(other, textvariable=self.other_param_vars[label], width=10,
                     font=("Consolas", 9)).grid(row=r, column=1, padx=2, pady=4, sticky="ew")
        ttk.Button(other, text="修改值", command=self.on_apply_other_params,
                   style="Accent.TButton").grid(row=len(OTHER_PARAM_LABELS), column=0,
                                                 columnspan=2, pady=6)
        other.grid_columnconfigure(1, weight=1)

    # ---- Center: pipe cloud map ----

    def _build_cloudmap_panel(self, parent):
        center = ttk.LabelFrame(parent, text="云图区（测温）",
                                style="Panel.TLabelframe")
        center.grid(row=0, column=0, sticky="nsew", pady=(0, 6))

        # Pack the button bar at the bottom FIRST so it is always reserved and
        # visible; the expanding canvas then takes the remaining space above it.
        btns = tk.Frame(center, bg=PANEL_BG)
        btns.pack(side="bottom", fill="x", padx=6, pady=(0, 8))
        self.start_btn = ttk.Button(btns, text="开始渲染",
                                    command=self.on_start_render, style="Accent.TButton")
        self.start_btn.pack(side="left", padx=10, pady=4)
        self.stop_btn = ttk.Button(btns, text="停止渲染",
                                   command=self.on_stop_render, state="disabled",
                                   style="Accent.TButton")
        self.stop_btn.pack(side="left", padx=10, pady=4)

        # The device frame (pipe, flanges, nozzles, measurement points) is the
        # figure itself - no separate strip of widgets. It is drawn empty on
        # startup and filled with the colour cloud on Start Render.
        self.cloud_fig = Figure(figsize=(7, 3.6), dpi=100, facecolor=PANEL_BG)
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

        # Single row of channel buttons - only one channel's curve is shown at
        # a time (single selection, radio behaviour).
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
                ch = CHANNEL_NUMBER[label]
                try:
                    readings[label] = self._read_channel(client, ch)
                except Exception as e:
                    errors.append(str(e))
                    readings[label] = None
        finally:
            client.close()
        return readings, errors

    # ----------------------------------------------------- Rendering

    def _draw_device(self, with_cloud):
        # Draw the 1D fire-flood device frame (pipe body, end flanges, inlet/
        # outlet nozzles, measurement points, pressure ports). When with_cloud
        # is True the interpolated colour cloud is filled inside the pipe body.
        # The whole figure is cleared each call so the colorbar does not stack.
        self.cloud_fig.clear()
        ax = self.cloud_fig.add_subplot(111)
        self.cloud_ax = ax
        ax.set_xlim(0.9, 11.0)
        ax.set_ylim(4.7, 0.9)        # inverted y -> top-left origin, like the PPT
        ax.set_aspect("equal")
        ax.axis("off")

        bx, by, bw, bh = PIPE_BODY_RECT
        cxp = bx + bw / 2.0
        cyp = by + bh / 2.0

        # End flanges + inlet/outlet nozzles (behind the pipe body).
        for (x, y, w, h) in (LEFT_FLANGE, RIGHT_FLANGE):
            ax.add_patch(Rectangle((x, y), w, h, fc="#CFD8DC", ec="#37474F",
                                   lw=1.5, zorder=3))
        for (x, y, w, h) in (LEFT_NOZZLE, RIGHT_NOZZLE):
            ax.add_patch(Rectangle((x, y), w, h, fc="#B0BEC5", ec="#37474F",
                                   lw=1.5, zorder=3))

        # Pipe body outline (rounded) - also used to clip the colour cloud.
        pipe = FancyBboxPatch(
            (bx, by), bw, bh,
            boxstyle="round,pad=0,rounding_size=0.25",
            fill=False, edgecolor="#222222", linewidth=2.4, zorder=6,
        )
        ax.add_patch(pipe)

        if with_cloud:
            self._fill_cloud(ax, pipe)

        # 7 internal temperature measurement points (numbered ellipses).
        for i, label in enumerate(PIPE_TEMP_LABELS, start=1):
            px, py = PIPE_POINTS[label]
            self._draw_point(ax, px, py, str(i), self.values.get(label), "°C",
                             up=(py < cyp), with_value=with_cloud)

        # Pressure ports P1 (inlet) / P2 (outlet) on the nozzles.
        for label in PRESSURE_LABELS:
            px, py = PORT_POINTS[label]
            self._draw_point(ax, px, py, label, self.values.get(label), "MPa",
                             up=False, with_value=with_cloud, port=True)

        # Pipe-wall temperatures (external) above / below the pipe body.
        self._annotate_wall(ax, "T8", "管壁温度1", cxp, by - 0.45, with_cloud)
        self._annotate_wall(ax, "T9", "管壁温度2", cxp, by + bh + 0.45, with_cloud)

        self.cloud_fig.tight_layout()
        self.cloud_canvas.draw()

    def _fill_cloud(self, ax, pipe):
        # Interpolate the internal temperatures and clip the field to the pipe.
        bx, by, bw, bh = PIPE_BODY_RECT
        pts_x, pts_y, vals = [], [], []
        for label in PIPE_TEMP_LABELS:
            v = self.values.get(label)
            if v is not None:
                px, py = PIPE_POINTS[label]
                pts_x.append(px)
                pts_y.append(py)
                vals.append(v)
        n = len(vals)
        if n == 0:
            return

        xi = np.linspace(bx, bx + bw, 260)
        yi = np.linspace(by, by + bh, 100)
        Xi, Yi = np.meshgrid(xi, yi)
        vals_arr = np.array(vals)
        vmin, vmax = vals_arr.min(), vals_arr.max()
        if vmin == vmax:
            vmin -= 1
            vmax += 1

        if n == 1:
            Zi = np.full_like(Xi, vals[0])
        else:
            points = np.column_stack([pts_x, pts_y])
            Zi = None
            for method in ("cubic", "linear", "nearest"):
                try:
                    Zi = griddata(points, vals_arr, (Xi, Yi), method=method)
                    if not np.all(np.isnan(Zi)):
                        break
                except Exception:
                    continue
            if Zi is None:
                Zi = np.full_like(Xi, vals_arr.mean())
            mask = np.isnan(Zi)
            if mask.any():
                fill = griddata(points, vals_arr, (Xi, Yi), method="nearest")
                Zi[mask] = fill[mask]

        levels = np.linspace(vmin, vmax, 30)
        cf = ax.contourf(Xi, Yi, Zi, levels=levels, cmap="jet",
                         extend="both", zorder=2)
        cf.set_clip_path(pipe)
        self.cloud_fig.colorbar(cf, ax=ax, label="温度 (°C)", shrink=0.6, pad=0.02)

    def _draw_point(self, ax, x, y, label, v, unit, up, with_value, port=False):
        fc = "#FFE082" if port else "white"
        ax.add_patch(Ellipse((x, y), 0.64, 0.42, fc=fc, ec="#263238",
                            lw=1.4, zorder=7))
        ax.text(x, y, label, ha="center", va="center", fontsize=8,
                fontweight="bold", color="#263238", zorder=8)
        if with_value:
            txt = f"{v:.1f}{unit}" if v is not None else "--"
            off = 18 if up else -18
            ax.annotate(txt, (x, y), textcoords="offset points", xytext=(0, off),
                        ha="center", va="center", fontsize=7, fontweight="bold",
                        color="white",
                        bbox=dict(boxstyle="round,pad=0.2", fc="black", alpha=0.75),
                        zorder=9)

    def _annotate_wall(self, ax, label, name, x, y, with_value):
        v = self.values.get(label)
        if with_value:
            txt = f"{name} {v:.1f}°C" if v is not None else f"{name} --"
        else:
            txt = name
        ax.annotate(txt, (x, y), ha="center", va="center", fontsize=8,
                    fontweight="bold", color="#0D47A1",
                    bbox=dict(boxstyle="round,pad=0.3", fc="#FFF8E1", ec="#0D47A1"),
                    zorder=8)

    def _render_personalized(self):
        ax = self.curve_ax
        ax.clear()
        if self.history_start is None:
            self._draw_curve_placeholder()
            self.curve_canvas.draw()
            return

        # Show only the currently selected channel's value-vs-time curve.
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
        # Update the left-panel live value labels (basic parameters).
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
        # Reset personalized chart and clear any previous cloud on each new
        # render session; the empty device frame stays visible until the first
        # reading arrives.
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

        # Only raise an error popup when EVERY channel failed to read.
        if all(v is None for v in readings.values()):
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
        # Connection-level failure (e.g. wrong comm config) -> stop with popup.
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
            messagebox.showwarning("无数据",
                                   "当前没有可导出的实时数据。")
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
