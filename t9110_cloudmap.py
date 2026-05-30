"""
Hongrun T9110 Paperless Recorder - Temperature Cloud Map Visualization
"""

import sys
import struct
import threading
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from scipy.interpolate import griddata
from pymodbus.client import ModbusSerialClient

matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


class T9110CloudMap:
    def __init__(self, root):
        self.root = root
        self.root.title("T9110 CloudMap - Hongrun Paperless Recorder")
        self.root.geometry("1200x800")
        self.root.minsize(900, 650)

        self.channel_map = {}
        self.grid_buttons = {}
        self.grid_size = (0, 0)
        self.refreshing = False

        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(10, 5))

        sf = ttk.LabelFrame(top, text="Communication")
        sf.pack(side="left", padx=(0, 10))

        self.port_var = tk.StringVar(value="COM3")
        self.baud_var = tk.IntVar(value=9600)
        self.slave_var = tk.IntVar(value=1)

        for label, var, w in [
            ("Port:", self.port_var, 8),
            ("Baud:", self.baud_var, 7),
            ("Addr:", self.slave_var, 4),
        ]:
            ttk.Label(sf, text=label).pack(side="left", padx=(8, 2))
            ttk.Entry(sf, textvariable=var, width=w).pack(side="left", padx=(0, 4))

        gf = ttk.LabelFrame(top, text="Grid Scale")
        gf.pack(side="left", padx=10)

        self.cols_var = tk.IntVar(value=4)
        self.rows_var = tk.IntVar(value=4)

        ttk.Label(gf, text="X:").pack(side="left", padx=(8, 2))
        ttk.Spinbox(gf, from_=2, to=20, textvariable=self.cols_var, width=4).pack(side="left")
        ttk.Label(gf, text="  Y:").pack(side="left", padx=(4, 2))
        ttk.Spinbox(gf, from_=2, to=20, textvariable=self.rows_var, width=4).pack(side="left")
        ttk.Button(gf, text="Create Grid", command=self.create_grid).pack(side="left", padx=10)

        rf = ttk.LabelFrame(top, text="Auto Refresh")
        rf.pack(side="left", padx=10)
        self.interval_var = tk.IntVar(value=2)
        ttk.Label(rf, text="Interval(s):").pack(side="left", padx=(8, 2))
        ttk.Spinbox(rf, from_=1, to=60, textvariable=self.interval_var, width=4).pack(
            side="left", padx=(0, 8)
        )

        content = ttk.PanedWindow(self.root, orient="horizontal")
        content.pack(fill="both", expand=True, padx=10, pady=5)

        left = ttk.Frame(content)
        content.add(left, weight=1)

        self.grid_frame = ttk.LabelFrame(left, text="Channel Grid (click to assign)")
        self.grid_frame.pack(fill="both", expand=True)
        self.grid_container = ttk.Frame(self.grid_frame)
        self.grid_container.pack(fill="both", expand=True, padx=5, pady=5)

        right = ttk.Frame(content)
        content.add(right, weight=2)

        chart = ttk.LabelFrame(right, text="Temperature Cloud Map")
        chart.pack(fill="both", expand=True)

        self.fig = Figure(figsize=(6, 5), dpi=100, facecolor="white")
        self.ax = self.fig.add_subplot(111)
        self.ax.text(
            0.5, 0.5,
            "Create grid and assign channels\nto get started",
            ha="center", va="center", fontsize=12, color="gray",
            transform=self.ax.transAxes,
        )
        self.ax.set_xticks([])
        self.ax.set_yticks([])

        self.map_canvas = FigureCanvasTkAgg(self.fig, master=chart)
        self.map_canvas.draw()
        self.map_canvas.get_tk_widget().pack(fill="both", expand=True)

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(bottom, text="Show Cloud Map", command=self.on_show).pack(side="left", padx=5)
        self.refresh_btn = ttk.Button(
            bottom, text="Start Auto-Refresh", command=self.toggle_refresh
        )
        self.refresh_btn.pack(side="left", padx=5)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bottom, textvariable=self.status_var, foreground="gray").pack(
            side="right", padx=5
        )

    # ---- Grid ----

    def create_grid(self):
        for w in self.grid_container.winfo_children():
            w.destroy()
        self.channel_map.clear()
        self.grid_buttons.clear()

        rows = self.rows_var.get()
        cols = self.cols_var.get()
        if rows < 2 or cols < 2:
            messagebox.showwarning("Invalid", "Grid must be at least 2 x 2.")
            return

        self.grid_size = (rows, cols)

        for r in range(rows):
            self.grid_container.grid_rowconfigure(r, weight=1)
            for c in range(cols):
                self.grid_container.grid_columnconfigure(c, weight=1)
                btn = tk.Button(
                    self.grid_container,
                    text=f"({r+1},{c+1})\n--",
                    bg="#E0E0E0", fg="#333",
                    activebackground="#BDBDBD",
                    relief="raised", bd=2,
                    font=("Consolas", 9),
                    command=lambda row=r, col=c: self.assign_channel(row, col),
                )
                btn.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")
                self.grid_buttons[(r, c)] = btn

        self.status_var.set(f"Grid {cols}x{rows} created. Click cells to assign channels.")

    def assign_channel(self, row, col):
        current = self.channel_map.get((row, col), 1)
        ch = simpledialog.askinteger(
            "Assign Channel",
            f"Grid point ({row+1}, {col+1})\n\n"
            f"Enter channel number (1-18):\n"
            f"Enter 0 to clear.",
            initialvalue=current,
            minvalue=0, maxvalue=18,
            parent=self.root,
        )
        if ch is None:
            return

        btn = self.grid_buttons[(row, col)]
        if ch > 0:
            self.channel_map[(row, col)] = ch
            btn.config(
                text=f"({row+1},{col+1})\nCH{ch}", bg="#42A5F5", fg="white",
                activebackground="#1E88E5",
            )
        else:
            self.channel_map.pop((row, col), None)
            btn.config(
                text=f"({row+1},{col+1})\n--", bg="#E0E0E0", fg="#333",
                activebackground="#BDBDBD",
            )

    # ---- Modbus ----

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
        rows, cols = self.grid_size
        values = np.zeros((rows, cols))
        errors = []
        client = self._connect()
        try:
            for (r, c), ch in self.channel_map.items():
                try:
                    values[r][c] = self._read_channel(client, ch)
                except Exception as e:
                    errors.append(str(e))
        finally:
            client.close()
        return values, errors

    # ---- Rendering ----

    def _render(self, values):
        self.fig.clear()
        ax = self.fig.add_subplot(111)

        rows, cols = values.shape
        x = np.arange(cols)
        y = np.arange(rows)
        X, Y = np.meshgrid(x, y)

        xi = np.linspace(0, cols - 1, max(cols * 25, 100))
        yi = np.linspace(0, rows - 1, max(rows * 25, 100))
        Xi, Yi = np.meshgrid(xi, yi)

        points = np.column_stack([X.ravel(), Y.ravel()])
        vals = values.ravel()

        Zi = None
        for method in ("cubic", "linear", "nearest"):
            try:
                Zi = griddata(points, vals, (Xi, Yi), method=method)
                if not np.all(np.isnan(Zi)):
                    break
            except Exception:
                continue
        if Zi is None:
            Zi = np.zeros_like(Xi)

        mask = np.isnan(Zi)
        if mask.any():
            Zi_fill = griddata(points, vals, (Xi, Yi), method="nearest")
            Zi[mask] = Zi_fill[mask]

        vmin, vmax = np.nanmin(vals), np.nanmax(vals)
        if vmin == vmax:
            vmin -= 1
            vmax += 1

        levels = np.linspace(vmin, vmax, 30)
        cf = ax.contourf(Xi, Yi, Zi, levels=levels, cmap="jet", extend="both")
        ax.contour(Xi, Yi, Zi, levels=10, colors="white", linewidths=0.4, alpha=0.5)
        self.fig.colorbar(cf, ax=ax, label="Temperature (\u00b0C)", shrink=0.9, pad=0.02)

        for (r, c), ch in self.channel_map.items():
            v = values[r][c]
            ax.plot(c, r, "ko", markersize=7, zorder=5)
            ax.annotate(
                f"CH{ch}\n{v:.1f}\u00b0C",
                (c, r),
                textcoords="offset points", xytext=(10, 8),
                fontsize=8, fontweight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.3", fc="black", alpha=0.75),
                zorder=6,
            )

        for r in range(rows):
            for c in range(cols):
                if (r, c) not in self.channel_map:
                    ax.plot(c, r, "+", color="gray", markersize=8,
                            markeredgewidth=1.5, alpha=0.5)

        ax.set_xlim(-0.5, cols - 0.5)
        ax.set_ylim(rows - 0.5, -0.5)
        ax.set_xticks(range(cols))
        ax.set_yticks(range(rows))
        ax.set_xticklabels([str(i + 1) for i in range(cols)])
        ax.set_yticklabels([str(i + 1) for i in range(rows)])
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.set_title("T9110 Temperature Cloud Map", fontsize=13, fontweight="bold")

        self.fig.tight_layout()
        self.map_canvas.draw()

    # ---- Actions ----

    def on_show(self):
        if self.grid_size == (0, 0):
            messagebox.showwarning("Warning", "Please create a grid first.")
            return
        if not self.channel_map:
            messagebox.showwarning("Warning", "Please assign at least one channel.")
            return

        self.status_var.set("Reading channels...")
        self.root.update()

        try:
            values, errors = self._read_all()
            self._render(values)
            t = datetime.now().strftime("%H:%M:%S")
            msg = f"Updated at {t}"
            if errors:
                msg += f" ({len(errors)} read errors)"
            self.status_var.set(msg)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_var.set(f"Error: {e}")

    def toggle_refresh(self):
        if self.refreshing:
            self.stop_refresh()
        else:
            self.start_refresh()

    def start_refresh(self):
        if self.grid_size == (0, 0) or not self.channel_map:
            messagebox.showwarning("Warning", "Create grid and assign channels first.")
            return
        self.refreshing = True
        self.refresh_btn.config(text="Stop Auto-Refresh")
        self._refresh_tick()

    def stop_refresh(self):
        self.refreshing = False
        self.refresh_btn.config(text="Start Auto-Refresh")
        self.status_var.set("Auto-refresh stopped")

    def _refresh_tick(self):
        if not self.refreshing:
            return

        self.status_var.set("Reading...")

        def work():
            try:
                values, errors = self._read_all()
                self.root.after(0, lambda v=values, e=errors: self._on_tick_done(v, e))
            except Exception as e:
                self.root.after(0, lambda err=e: self._on_tick_error(err))

        threading.Thread(target=work, daemon=True).start()

    def _on_tick_done(self, values, errors):
        if not self.refreshing:
            return
        self._render(values)
        t = datetime.now().strftime("%H:%M:%S")
        n = len(self.channel_map)
        msg = f"Auto-refresh | {n} channels | {t}"
        if errors:
            msg += f" | {len(errors)} errors"
        self.status_var.set(msg)
        self.root.after(self.interval_var.get() * 1000, self._refresh_tick)

    def _on_tick_error(self, error):
        self.status_var.set(f"Error: {error}")
        if self.refreshing:
            self.root.after(self.interval_var.get() * 1000, self._refresh_tick)


def main():
    root = tk.Tk()
    T9110CloudMap(root)
    root.mainloop()


if __name__ == "__main__":
    main()
