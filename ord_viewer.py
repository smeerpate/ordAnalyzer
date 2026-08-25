"""
ord_viewer.py
-------------
Tkinter GUI voor het visualiseren van FlowNest/FlowMaster .ord bestanden.

Gebruik:
    python ord_viewer.py [bestand.ord]

Bediening:
    Slepen (linker muisknop)  : pannen
    Muiswiel                  : zoomen (gecentreerd op cursor)
    Dubbelklik                : fit alles in beeld
    Knoppen in toolbar        : open bestand / zoom / fit
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import os
import sys
import math

from ord_processor import OrdProcessor, SegmentConflict


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------

class _Tooltip:
    """Kleine popup-uitleg bij hover over een widget."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self._widget = widget
        self._text   = text
        self._delay  = delay
        self._id     = None
        self._win: tk.Toplevel | None = None
        widget.bind('<Enter>', self._schedule)
        widget.bind('<Leave>', self._cancel)
        widget.bind('<ButtonPress>', self._cancel)

    def _schedule(self, _event=None):
        self._cancel()
        self._id = self._widget.after(self._delay, self._show)

    def _cancel(self, _event=None):
        if self._id:
            self._widget.after_cancel(self._id)
            self._id = None
        if self._win:
            self._win.destroy()
            self._win = None

    def _show(self):
        if self._win:
            return
        x = self._widget.winfo_rootx() + 4
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._win = tk.Toplevel(self._widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f'+{x}+{y}')
        lbl = tk.Label(
            self._win,
            text=self._text,
            bg='#1E1E3A', fg='#C8C8E8',
            font=('Segoe UI', 8),
            relief=tk.FLAT, bd=0,
            padx=8, pady=5,
            justify=tk.LEFT,
            wraplength=280,
        )
        lbl.pack()
        # Subtiele rand
        self._win.configure(bg='#3A3A60')
        self._win.wm_attributes('-topmost', True)


def tooltip(widget: tk.Widget, text: str) -> '_Tooltip':
    """Helper: koppel een tooltip aan een widget en geef de widget terug."""
    _Tooltip(widget, text)
    return widget


class _CenteredInfoPanel:
    """
    Groter info-venster dat gecentreerd over het hoofdvenster verschijnt.
    Breedte past zich aan de langste tekstregel aan (geen wraplength).
    Bedoeld voor het ℹ-label in de toolbar.
    """
    def __init__(self, widget: tk.Widget, text: str, root: tk.Tk, delay: int = 300):
        self._widget = widget
        self._text   = text
        self._root   = root
        self._delay  = delay
        self._id     = None
        self._win    = None
        widget.bind('<Enter>',       self._schedule)
        widget.bind('<Leave>',       self._cancel)
        widget.bind('<ButtonPress>', self._cancel)

    def _schedule(self, _event=None):
        self._cancel()
        self._id = self._widget.after(self._delay, self._show)

    def _cancel(self, _event=None):
        if self._id:
            self._widget.after_cancel(self._id)
            self._id = None
        if self._win:
            self._win.destroy()
            self._win = None

    def _show(self):
        if self._win:
            return
        self._win = tk.Toplevel(self._widget)
        self._win.wm_overrideredirect(True)
        self._win.configure(bg='#3A3A60')
        self._win.wm_attributes('-topmost', True)

        lbl = tk.Label(
            self._win,
            text=self._text,
            bg='#1E1E3A', fg='#C8C8E8',
            font=('Consolas', 8),
            relief=tk.FLAT, bd=0,
            padx=12, pady=10,
            justify=tk.LEFT,
            wraplength=0,          # geen automatische afbreking
        )
        lbl.pack(padx=1, pady=1)

        # Bereken grootte na renderen
        self._win.update_idletasks()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()

        # Centreer over het hoofdvenster
        self._root.update_idletasks()
        rx = self._root.winfo_rootx()
        ry = self._root.winfo_rooty()
        rw = self._root.winfo_width()
        rh = self._root.winfo_height()
        x = rx + (rw - w) // 2
        y = ry + (rh - h) // 2

        self._win.wm_geometry(f'{w}x{h}+{x}+{y}')

# ---------------------------------------------------------------------------
# Kleurenthema
# ---------------------------------------------------------------------------
C = {
    'bg':          '#0E0E1A',
    'canvas_bg':   '#0A0A14',
    'panel_bg':    '#141422',
    'toolbar_bg':  '#1A1A2E',
    'btn_bg':      '#2A2A4A',
    'btn_hover':   '#3A3A60',
    'btn_active':  '#1A5A3A',   # groene achtergrond voor actieve modus-knop
    'btn_fg':      '#C8C8E8',
    'status_bg':   '#111120',
    'text_dim':    '#5566AA',
    'text':        '#B0B8E0',
    'text_bright': '#E0E8FF',
    'grid':        '#151530',
    'axis':        '#202048',
    'grid_label':  '#2A2A50',
    'rapid':       '#4ADE80',   # groen – rapid traverse (F4=0, F5=0, F6=0)
}

# Iron palette aangepast voor zwarte achtergrond: paars uitgerekt, geen zwart
_HEAT_STOPS = [
    # Ioniseringsgradiënt: paars → rood → oranje → geel → wit.
    # Stops liggen exact op F5-waarden 20/40/60/80 voor maximaal contrast.
    (0.00,  0x30, 0x00, 0x60),  # diep paars   –   0 %
    (0.20,  0x99, 0x00, 0xCC),  # levendig paars – 20 %
    (0.40,  0xCC, 0x00, 0x30),  # karmijnrood  –  40 %
    (0.60,  0xFF, 0x66, 0x00),  # oranje       –  60 %
    (0.80,  0xFF, 0xCC, 0x00),  # geel         –  80 %
    (1.00,  0xFF, 0xFF, 0xFF),  # wit          – 100 %
]


def speed_color(speed: int) -> str:
    """Heatmap kleur voor snelheid 0-100."""
    t = max(0.0, min(1.0, speed / 100.0))
    for i in range(len(_HEAT_STOPS) - 1):
        t0, r0, g0, b0 = _HEAT_STOPS[i]
        t1, r1, g1, b1 = _HEAT_STOPS[i + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = int(r0 + f * (r1 - r0))
            g = int(g0 + f * (g1 - g0))
            b = int(b0 + f * (b1 - b0))
            return f'#{r:02X}{g:02X}{b:02X}'
    return '#FF0000'


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

def _nice_grid_step(scale_px_per_mm: float) -> float:
    """Berekent een mooie rasterafstand in mm zodat ~80 px tussen lijnen."""
    target = 80
    raw = target / max(scale_px_per_mm, 1e-9)
    for step in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if step * scale_px_per_mm >= target:
            return step
    return 1000


# ---------------------------------------------------------------------------
# Viewer applicatie
# ---------------------------------------------------------------------------

class OrdViewer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ORD Viewer  v0.3  —  Twenty Three BV")
        self.root.geometry("1280x800")
        self.root.configure(bg=C['bg'])

        self.processor: OrdProcessor | None = None

        self._scale: float = 1.0
        self._off_x: float = 0.0
        self._off_y: float = 0.0

        self._pan_last:   tuple | None = None
        self._press_pos:  tuple | None = None   # voor klik-vs-sleep detectie
        self._fit_pending: bool = False
        self._conflicts:  list = []   # SegmentConflict objecten + positie (mm)
        self._sel_part:   int | None = None    # index in processor.parts (wissel-modus)
        self._speed_sel:  set = set()           # onderdelen voor snelheidsaanpassing
        self._edit_mode:  str = ''              # '' | 'swap' | 'speed'
        self._dirty:      bool = False          # herordening nog niet opgeslagen
        self._speed_dirty: bool = False         # snijsnelheid aangepast, nog niet opgeslagen

        self._build_ui()
        self._bind_events()

    # ------------------------------------------------------------------
    # UI opbouw
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ---- Toolbar ----
        tb = tk.Frame(self.root, bg=C['toolbar_bg'], pady=5)
        tb.pack(side=tk.TOP, fill=tk.X)

        def btn(parent, text, cmd):
            b = tk.Label(parent, text=text, bg=C['btn_bg'], fg=C['btn_fg'],
                         font=('Segoe UI', 9), padx=12, pady=5,
                         cursor='hand2', relief=tk.FLAT)
            b.bind('<Button-1>', lambda e: cmd())
            b.bind('<Enter>', lambda e: b.config(bg=C['btn_hover']))
            b.bind('<Leave>', lambda e: b.config(bg=C['btn_bg']))
            return b

        tooltip(btn(tb, '📂  Open', self.open_file),
            'Bestand openen\n'
            'Opent een .ord bestand via een bestandskiezer.'
        ).pack(side=tk.LEFT, padx=(8, 2))
        tk.Frame(tb, bg='#303060', width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=2)
        tooltip(btn(tb, '＋  Zoom in',  lambda: self._zoom_center(1.25)),
            'Zoom in (25%)\n'
            'Ook: muiswiel omhoog.'
        ).pack(side=tk.LEFT, padx=2)
        tooltip(btn(tb, '－  Zoom uit', lambda: self._zoom_center(0.80)),
            'Zoom uit (20%)\n'
            'Ook: muiswiel omlaag.'
        ).pack(side=tk.LEFT, padx=2)
        tooltip(btn(tb, '⤢  Fit',      self.fit_to_screen),
            'Alles in beeld\n'
            'Schaalt en centreert het volledige snijplan.\n'
            'Ook: dubbelklik op de tekening.'
        ).pack(side=tk.LEFT, padx=2)
        tk.Frame(tb, bg='#303060', width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=2)
        self._btn_swap = tooltip(
            btn(tb, '⇄  Wissel', self._toggle_swap_mode),
            'Snijvolgorde wisselen\n'
            '1e klik: activeer modus (knop licht op).\n'
            'Klik een onderdeel aan om te selecteren (geel).\n'
            'Klik een tweede onderdeel aan om te wisselen.\n'
            'Opnieuw klikken op ⇄ of op leeg canvas deselecteert.'
        )
        self._btn_swap.pack(side=tk.LEFT, padx=2)

        # Leave-bindings voor modus-knoppen: add='+' zodat de tooltip-binding
        # NIET overschreven wordt. De kleur wordt dynamisch bepaald op basis
        # van de huidige modus op het moment van hoveren.
        self._btn_swap.bind('<Leave>', lambda e: self._btn_swap.config(
            bg=C['btn_active'] if self._edit_mode == 'swap' else C['btn_bg'],
            fg='#FFFFFF'       if self._edit_mode == 'swap' else C['btn_fg'],
        ), add='+')
        tooltip(
            btn(tb, '🔍  Zoeken', self._find_part_dialog),
            'Onderdeel zoeken  (Ctrl+F)\n'
            'Ga naar een onderdeel op volgnummer.\n'
            'Het onderdeel wordt gecentreerd en geselecteerd.'
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._btn_speed = tooltip(
            btn(tb, '✏  Snijsnelheid', self._toggle_speed_mode),
            'Snijsnelheid aanpassen\n'
            '1e klik: activeer modus (knop licht op).\n'
            'Klik onderdelen aan om ze te selecteren (cyaan).\n'
            '2e klik op knop: open dialoogvenster om toe te passen.\n'
            'Geen selectie = alle contouren aanpassen.'
        )
        self._btn_speed.pack(side=tk.LEFT, padx=2)
        self._btn_speed.bind('<Leave>', lambda e: self._btn_speed.config(
            bg=C['btn_active'] if self._edit_mode == 'speed' else C['btn_bg'],
            fg='#FFFFFF'       if self._edit_mode == 'speed' else C['btn_fg'],
        ), add='+')
        self._btn_similar = tooltip(
            btn(tb, '≈  Gelijkaardig', self._select_similar_contours),
            'Gelijkaardige contouren selecteren\n'
            'Selecteert alle contouren met dezelfde vorm als de\n'
            'huidige selectie (zelfde aantal ankerpunten,\n'
            'gelijke segmentlengtes en oppervlakte).\n'
            'Alleen actief in snijsnelheid-modus met een selectie.'
        )
        self._btn_similar.pack(side=tk.LEFT, padx=(0, 6))
        self._btn_save = tooltip(
            btn(tb, '💾  Opslaan', self.save_file),
            'Opslaan\n'
            'Sla het ORD-bestand op met de nieuwe snijvolgorde.\n'
            'De originele coördinaten blijven ongewijzigd;\n'
            'alleen de volgorde van de onderdelen verandert.'
        )
        self._btn_save.pack(side=tk.LEFT, padx=2)

        self._lbl_file = tk.Label(tb, text='Geen bestand geladen',
                                   bg=C['toolbar_bg'], fg=C['text_dim'],
                                   font=('Segoe UI', 9))
        self._lbl_file.pack(side=tk.LEFT, padx=16)

        # ℹ bediening-hint rechts in toolbar (gecentreerd over hoofdvenster)
        _info_lbl = tk.Label(tb, text='ℹ',
                             bg=C['toolbar_bg'], fg=C['text_dim'],
                             font=('Segoe UI', 11), cursor='question_arrow')
        _CenteredInfoPanel(_info_lbl,
            'ORD Viewer  v0.3  —  Twenty Three BV  (2026-08-25)\n'
            '─────────────────────────────────────\n'
            'Bediening canvas\n'
            '  Slepen (links/midden)  Pannen\n'
            '  Muiswiel               Zoomen op cursor\n'
            '  Dubbelklik             Alles in beeld\n'
            '\n'
            'Kleuren snijlijnen\n'
            '  Paars/rood/oranje/geel  Snijsnelheid 20/40/60/80 %\n'
            '  Groen gestippeld       Rapid traverse\n'
            '  Cyaan highlight        Geselecteerde contour (snelheid)\n'
            '\n'
            'Nummers in cirkel        Snijvolgorde per onderdeel\n'
            '\n'
            'Snijvolgorde wisselen  (⇄ Wissel)\n'
            '  Klik een onderdeel aan → geel gemarkeerd\n'
            '  Klik een tweede onderdeel → volgorde gewisseld\n'
            '  Na wissel: volgend onderdeel automatisch geselecteerd\n'
            '\n'
            'Onderdeel zoeken  (🔍 Zoeken  of  Ctrl+F)\n'
            '  Typ een volgnummer → canvas centreert en selecteert\n'
            '\n'
            'Snijsnelheid aanpassen  (✏ Snijsnelheid)\n'
            '  Klik op een snijlijn → contour geselecteerd (cyaan)\n'
            '  ≈ Gelijkaardig → selecteert alle contouren met\n'
            '    dezelfde vorm en grootte\n'
            '  Klik opnieuw op ✏ → dialoog om F5 in te stellen\n'
            '  Keuze: geselecteerde contouren of alle contouren\n'
            '  Optie: lead-in/lead-out mee aanpassen of niet\n'
            '\n'
            'Opslaan  (💾)\n'
            '  Vraagt traversehoogte (Z tijdens rapids)\n'
            '  Schrijft nieuw .ord bestand weg\n'
            '  STA-bestand wordt verwijderd na bevestiging\n'
            '\n'
            'Waarschuwingen (zijpaneel)\n'
            '  ⚠ Geen safety hoogte   Alle Z-waarden zijn 0\n'
            '  ⚠ Negatieve Z           Z < 0 aanwezig\n'
            '  ⚠ Snijden niet op Z=0  Snijbewegingen op Z ≠ 0\n'
            '\n'
            'Conflicten (zijpaneel)\n'
            '  ⚠ geel                 Overlappende snijlijnen\n'
            '  ✕ rood                 Kruisende snijlijnen',
            self.root)
        _info_lbl.pack(side=tk.RIGHT, padx=12)

        # ---- Hoofd-gebied: canvas + paneel ----
        main = tk.Frame(self.root, bg=C['bg'])
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(main, bg=C['canvas_bg'],
                                  highlightthickness=0, cursor='crosshair')
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        panel = tk.Frame(main, bg=C['panel_bg'], width=240)
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)

        tk.Label(panel, text='Bestandsinfo', bg=C['panel_bg'],
                 fg=C['text_dim'], font=('Segoe UI', 8, 'bold')
                 ).pack(anchor=tk.W, padx=12, pady=(12, 2))

        tk.Frame(panel, bg='#2A2A50', height=1).pack(fill=tk.X, padx=8, pady=(0, 6))

        self._info_var = tk.StringVar(value='—')
        tk.Label(panel, textvariable=self._info_var,
                 bg=C['panel_bg'], fg=C['text'],
                 font=('Consolas', 8), justify=tk.LEFT,
                 anchor=tk.NW, wraplength=220
                 ).pack(anchor=tk.NW, padx=12, pady=2)

        # ---- Z-waarschuwingen (dynamisch opgebouwd door _update_warnings) ----
        self._warn_frame = tk.Frame(panel, bg='#2A1A00')

        # ---- Conflicten sectie ----
        tk.Frame(panel, bg='#2A2A50', height=1).pack(fill=tk.X, padx=8, pady=(8, 2))

        self._conflict_title = tk.Label(panel, text='Conflicten', bg=C['panel_bg'],
                 fg=C['text_dim'], font=('Segoe UI', 8, 'bold'))
        self._conflict_title.pack(anchor=tk.W, padx=12, pady=(4, 2))

        conflict_frame = tk.Frame(panel, bg=C['panel_bg'])
        conflict_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 8))

        self._conflict_text = tk.Text(
            conflict_frame,
            bg=C['panel_bg'], fg=C['text'],
            font=('Consolas', 7),
            relief=tk.FLAT, bd=0,
            wrap=tk.WORD,
            state=tk.DISABLED,
            cursor='arrow',
        )
        conflict_scroll = tk.Scrollbar(conflict_frame, orient=tk.VERTICAL,
                                        command=self._conflict_text.yview)
        self._conflict_text.config(yscrollcommand=conflict_scroll.set)
        conflict_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._conflict_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Kleur-tags voor het conflicttekstvak
        self._conflict_text.tag_config('ok',       foreground='#4ADE80')
        self._conflict_text.tag_config('warn',      foreground='#FACC15')
        self._conflict_text.tag_config('error',     foreground='#F87171')
        self._conflict_text.tag_config('coord',     foreground='#93C5FD')
        self._conflict_text.tag_config('dim',       foreground=C['text_dim'])
        self._conflict_text.tag_config('separator', foreground='#2A2A50')

        # ---- Status-balk ----
        sb = tk.Frame(self.root, bg=C['status_bg'], pady=4)
        sb.pack(side=tk.BOTTOM, fill=tk.X)

        self._lbl_coords = tk.Label(sb, text='X: —  Y: —',
                                     bg=C['status_bg'], fg=C['text_dim'],
                                     font=('Consolas', 8))
        self._lbl_coords.pack(side=tk.LEFT, padx=12)

        self._lbl_sel = tk.Label(sb, text='',
                                  bg=C['status_bg'], fg='#FACC15',
                                  font=('Segoe UI', 8))
        self._lbl_sel.pack(side=tk.LEFT, padx=12)

        # Legende: rapid + heatmap gradient
        leg = tk.Frame(sb, bg=C['status_bg'])
        leg.pack(side=tk.RIGHT, padx=16)

        # Rapid
        tk.Label(leg, text='╌╌', bg=C['status_bg'], fg=C['rapid'],
                 font=('Consolas', 10)).pack(side=tk.LEFT)
        tk.Label(leg, text=' rapid  ', bg=C['status_bg'], fg=C['text_dim'],
                 font=('Segoe UI', 8)).pack(side=tk.LEFT)

        # Heatmap: toon de exacte snelheidsstappen met label
        for v, label in [(20, '20'), (40, '40'), (60, '60'), (80, '80'), (100, '100')]:
            tk.Label(leg, text='█', bg=C['status_bg'], fg=speed_color(v),
                     font=('Consolas', 10)).pack(side=tk.LEFT, padx=0)
            tk.Label(leg, text=label + ' ', bg=C['status_bg'], fg=C['text_dim'],
                     font=('Segoe UI', 8)).pack(side=tk.LEFT, padx=0)

        self._lbl_zoom = tk.Label(sb, text='—',
                                   bg=C['status_bg'], fg=C['text_dim'],
                                   font=('Consolas', 8))
        self._lbl_zoom.pack(side=tk.RIGHT, padx=12)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _bind_events(self):
        cv = self._canvas
        cv.bind('<MouseWheel>', self._on_wheel)
        cv.bind('<Button-4>',   self._on_wheel)
        cv.bind('<Button-5>',   self._on_wheel)
        cv.bind('<ButtonPress-1>',   self._on_pan_start)
        cv.bind('<B1-Motion>',       self._on_pan_move)
        cv.bind('<ButtonRelease-1>', self._on_pan_end)
        cv.bind('<ButtonPress-2>',   self._on_pan_start)
        cv.bind('<B2-Motion>',       self._on_pan_move)
        cv.bind('<ButtonRelease-2>', self._on_pan_end)
        cv.bind('<Motion>',          self._on_mouse_move)
        cv.bind('<Double-Button-1>', lambda e: self.fit_to_screen())
        cv.bind('<Configure>',       self._on_resize)
        self.root.bind('<Control-f>', lambda e: self._find_part_dialog())
        self.root.bind('<Control-F>', lambda e: self._find_part_dialog())

    def _on_wheel(self, event):
        factor = 1.15 if (event.num == 4 or
                          (hasattr(event, 'delta') and event.delta > 0)) else 1 / 1.15
        self._do_zoom(factor, event.x, event.y)

    def _on_pan_start(self, event):
        self._pan_last  = (event.x, event.y)
        self._press_pos = (event.x, event.y)

    def _on_pan_move(self, event):
        if self._pan_last:
            self._off_x += event.x - self._pan_last[0]
            self._off_y += event.y - self._pan_last[1]
            self._pan_last = (event.x, event.y)
            self._redraw()

    def _on_pan_end(self, event):
        if self._press_pos:
            dx = event.x - self._press_pos[0]
            dy = event.y - self._press_pos[1]
            if abs(dx) <= 4 and abs(dy) <= 4:
                self._on_canvas_click(event.x, event.y)
        self._pan_last  = None
        self._press_pos = None
        self._canvas.config(cursor='crosshair')

    def _on_mouse_move(self, event):
        xw, yw = self._to_world(event.x, event.y)
        MM_TO_INCH = 1 / 25.4
        xi, yi = xw * MM_TO_INCH, yw * MM_TO_INCH
        self._lbl_coords.config(
            text=f'X: {xw:8.2f} mm ({xi:.4f}")    Y: {yw:8.2f} mm ({yi:.4f}")'
        )

    def _on_resize(self, event):
        if self._fit_pending:
            self._fit_pending = False
            self._do_fit()
        else:
            self._redraw()

    # ------------------------------------------------------------------
    # Bestand openen
    # ------------------------------------------------------------------

    def open_file(self, path: str | None = None):
        if path is None:
            path = filedialog.askopenfilename(
                title='ORD bestand openen',
                filetypes=[('ORD bestanden', '*.ord'), ('Alle bestanden', '*.*')]
            )
        if not path:
            return
        try:
            self.processor = OrdProcessor(path).parse()
        except Exception as exc:
            messagebox.showerror('Fout', f'Kon bestand niet laden:\n{exc}')
            return

        self.root.title(f'ORD Viewer — {os.path.basename(path)}')
        self._lbl_file.config(text=os.path.basename(path), fg=C['text_bright'])
        _Tooltip(self._lbl_file, path)  # volledig pad als tooltip
        self._update_info_panel()
        self._fit_pending = True
        self.root.after(50, self.fit_to_screen)

        # ── Checksumwaarschuwing ──────────────────────────────────────────
        if self.processor.checksum_warning:
            messagebox.showwarning('Checksum fout', self.processor.checksum_warning)

        # ── Controleer op bijbehorend .sta-bestand ────────────────────────
        sta_path = os.path.splitext(path)[0] + '.sta'
        if os.path.isfile(sta_path):
            sta_name = os.path.basename(sta_path)
            if messagebox.askyesno(
                'STA-bestand gevonden',
                f'Er staat een STA-bestand naast dit ORD-bestand:\n\n'
                f'  {sta_name}\n\n'
                f'Wil je dit verwijderen?',
                icon='question'
            ):
                try:
                    os.remove(sta_path)
                    messagebox.showinfo('Verwijderd', f'{sta_name} is verwijderd.')
                except OSError as e:
                    messagebox.showerror('Fout', f'Kon {sta_name} niet verwijderen:\n{e}')

    # ------------------------------------------------------------------
    # Geometrische hulpfuncties voor conflictcoördinaten
    # ------------------------------------------------------------------

    @staticmethod
    def _crossing_point(c: 'SegmentConflict'):
        """Bereken het kruispunt van twee niet-parallelle segmenten."""
        ax0, ay0 = c.seg_a.start
        ax1, ay1 = c.seg_a.end
        bx0, by0 = c.seg_b.start
        bx1, by1 = c.seg_b.end
        dax, day = ax1 - ax0, ay1 - ay0
        dbx, dby = bx1 - bx0, by1 - by0
        dx, dy   = bx0 - ax0, by0 - ay0
        denom    = dax * dby - day * dbx
        if abs(denom) < 1e-12:
            return None
        t = (dx * dby - dy * dbx) / denom
        return ax0 + t * dax, ay0 + t * day

    @staticmethod
    def _overlap_midpoint(c: 'SegmentConflict'):
        """
        Bereken het middelpunt van het overlappende stuk op segment A.
        Projecteer beide segmenten op de richting van A en bereken de
        gemeenschappelijke interval.
        """
        import math
        ax0, ay0 = c.seg_a.start
        ax1, ay1 = c.seg_a.end
        bx0, by0 = c.seg_b.start
        bx1, by1 = c.seg_b.end
        dax, day = ax1 - ax0, ay1 - ay0
        la = math.hypot(dax, day)
        if la < 1e-9:
            return (ax0 + bx0) / 2, (ay0 + by0) / 2
        ux, uy = dax / la, day / la
        t_a0 = 0.0
        t_a1 = la
        t_b0 = (bx0 - ax0) * ux + (by0 - ay0) * uy
        t_b1 = (bx1 - ax0) * ux + (by1 - ay0) * uy
        if t_b0 > t_b1:
            t_b0, t_b1 = t_b1, t_b0
        t_mid = (max(t_a0, t_b0) + min(t_a1, t_b1)) / 2
        return ax0 + t_mid * ux, ay0 + t_mid * uy

    def _update_info_panel(self):
        p = self.processor
        if not p:
            self._info_var.set('—')
            self._update_warnings(None)
            self._set_conflict_text([('—', 'dim')])
            return

        all_pts = [pt for c in p.contours for pt in c.points]
        if all_pts:
            xs = [q[0] for q in all_pts]
            ys = [q[1] for q in all_pts]
            dim = f'{max(xs)-min(xs):.1f} × {max(ys)-min(ys):.1f} mm'
        else:
            dim = '—'

        self._info_var.set(
            f'Bestand:\n  {p.filename}\n'
            f'Versie:  {p.version}\n\n'
            f'Contours:   {p.num_contours}\n'
            f'  Stukken:  {len(p.outer_contours)}\n'
            f'  Gaten:    {len(p.hole_contours)}\n\n'
            f'Onderdelen: {p.num_parts}\n\n'
            f'Afmeting:\n  {dim}'
        )

        # ---- Z-waarschuwingen bijwerken ----
        self._update_warnings(p)

        # ---- Conflicten detecteren ----
        conflicts = p.find_segment_conflicts()
        overlaps  = [c for c in conflicts if c.kind == 'overlap']
        crossings = [c for c in conflicts if c.kind == 'crossing']

        # Sla posities op voor tekening op canvas
        self._conflicts = []
        for cf in overlaps:
            pos = self._overlap_midpoint(cf)
            if pos:
                self._conflicts.append((pos, cf.kind))
        for cf in crossings:
            pos = self._crossing_point(cf)
            if pos:
                self._conflicts.append((pos, cf.kind))

        parts: list = []   # list of (text, tag) tuples

        if not conflicts:
            parts.append(('✓ geen conflicten', 'ok'))
        else:
            n_ov = len(overlaps)
            n_cr = len(crossings)
            tag = 'error' if conflicts else 'ok'
            if n_ov:
                parts.append((f'⚠ {n_ov} overlap{"pen" if n_ov != 1 else ""}\n', 'warn'))
            if n_cr:
                parts.append((f'⚠ {n_cr} kruising{"en" if n_cr != 1 else ""}\n', 'error'))

            # Overlappen
            if overlaps:
                parts.append(('\nOverlappen\n', 'dim'))
                parts.append(('──────────\n', 'separator'))
                for cf in overlaps:
                    mp = self._overlap_midpoint(cf)
                    parts.append((f'({mp[0]:.1f}, {mp[1]:.1f}) mm\n', 'coord'))
                    parts.append((f'{cf.overlap_mm:.2f} mm  '
                                  f'c{cf.contour_a}↔c{cf.contour_b}\n', 'dim'))

            # Kruisingen
            if crossings:
                parts.append(('\nKruisingen\n', 'dim'))
                parts.append(('──────────\n', 'separator'))
                for cf in crossings:
                    cp = self._crossing_point(cf)
                    if cp:
                        parts.append((f'({cp[0]:.1f}, {cp[1]:.1f}) mm\n', 'coord'))
                    parts.append((f'c{cf.contour_a}↔c{cf.contour_b}\n', 'dim'))

        self._set_conflict_text(parts)

    def _update_warnings(self, p):
        """Bouw de Z-waarschuwingen opnieuw op en toon/verberg het frame."""
        # Verwijder bestaande inhoud
        for w in self._warn_frame.winfo_children():
            w.destroy()

        warnings = []
        if p is not None:
            if p.no_safety_height:
                warnings.append((
                    '⚠  Geen safety hoogte',
                    'Alle Z-waarden zijn 0.\nGeen veilige reishoogte\ntussen bewerkingen.',
                ))
            if p.has_negative_z:
                warnings.append((
                    '⚠  Negatieve Z-coördinaten',
                    'Er staan Z-waarden onder 0\nin het bestand.',
                ))
            if p.cutting_not_at_z0:
                warnings.append((
                    '⚠  Snijden niet op Z=0',
                    'Snijbewegingen vinden niet\nplaats op Z-coördinaat 0.',
                ))

        if not warnings:
            self._warn_frame.pack_forget()
            return

        for i, (title, body) in enumerate(warnings):
            top_pad = (6, 0) if i == 0 else (4, 0)
            bot_pad = (1, 6) if i == len(warnings) - 1 else (1, 2)
            tk.Label(
                self._warn_frame,
                text=title,
                bg='#2A1A00', fg='#FACC15',
                font=('Segoe UI', 8, 'bold'),
                anchor=tk.W,
            ).pack(anchor=tk.W, padx=10, pady=top_pad)
            tk.Label(
                self._warn_frame,
                text=body,
                bg='#2A1A00', fg='#FDE68A',
                font=('Segoe UI', 8),
                justify=tk.LEFT,
                anchor=tk.W,
            ).pack(anchor=tk.W, padx=10, pady=bot_pad)

        self._warn_frame.pack(fill=tk.X, padx=8, pady=(4, 2))

    # ------------------------------------------------------------------
    # Volgorde-wissel
    # ------------------------------------------------------------------

    def _find_part_dialog(self):
        """
        Zoek een onderdeel op volgnummer, centreer het op het canvas
        en selecteer het in wissel-modus (Ctrl+F).
        """
        if not self.processor or not self.processor.parts:
            return

        n_parts = len(self.processor.parts)

        dlg = tk.Toplevel(self.root)
        dlg.title('Zoeken')
        dlg.resizable(False, False)
        dlg.configure(bg=C['bg'])
        dlg.grab_set()

        self.root.update_idletasks()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        dlg.geometry(f'280x120+{rx + rw//2 - 140}+{ry + rh//2 - 60}')

        tk.Label(dlg, text=f'Ga naar onderdeel (1 – {n_parts})',
                 bg=C['bg'], fg=C['text_bright'],
                 font=('Segoe UI', 10, 'bold')).pack(pady=(16, 6))

        entry_var = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=entry_var,
                         bg=C['btn_bg'], fg=C['text_bright'],
                         insertbackground=C['text_bright'],
                         font=('Consolas', 11), width=8, justify=tk.CENTER,
                         relief=tk.FLAT, bd=4)
        entry.pack()
        entry.focus_set()

        result = [None]

        def ok(_event=None):
            try:
                v = int(entry_var.get().strip())
                if not (1 <= v <= n_parts):
                    raise ValueError
                result[0] = v
                dlg.destroy()
            except ValueError:
                entry.config(bg='#4A1A1A')
                entry.after(600, lambda: entry.config(bg=C['btn_bg']))

        entry.bind('<Return>', ok)
        entry.bind('<Escape>', lambda e: dlg.destroy())

        btn_frame = tk.Frame(dlg, bg=C['bg'])
        btn_frame.pack(pady=(10, 0))
        for text, cmd in [('Annuleren', dlg.destroy), ('Ga naar', ok)]:
            b = tk.Label(btn_frame, text=text,
                         bg=C['btn_bg'], fg=C['btn_fg'],
                         font=('Segoe UI', 9), padx=12, pady=4,
                         cursor='hand2', relief=tk.FLAT)
            b.bind('<Button-1>', lambda e, c=cmd: c())
            b.bind('<Enter>', lambda e, w=b: w.config(bg=C['btn_hover']))
            b.bind('<Leave>', lambda e, w=b: w.config(bg=C['btn_bg']))
            b.pack(side=tk.LEFT, padx=4)

        dlg.wait_window()
        if result[0] is None:
            return

        idx = result[0] - 1   # 0-gebaseerde index in processor.parts
        part = self.processor.parts[idx]

        # Centreer het canvas op het zwaartepunt van het part
        outers = part.outer_contours
        if outers:
            cx = sum(c.centroid[0] for c in outers) / len(outers)
            cy = sum(c.centroid[1] for c in outers) / len(outers)
            cw = self._canvas.winfo_width()
            ch = self._canvas.winfo_height()
            self._off_x = cw / 2 - cx * self._scale
            self._off_y = ch / 2 + cy * self._scale

        # Selecteer het part in wissel-modus
        self._set_mode('swap')
        self._sel_part = idx
        self._update_swap_status()
        self._redraw()

    def _set_mode(self, mode: str):
        """
        Wissel naar de opgegeven modus ('' | 'swap' | 'speed').
        Wist de selectie van de vorige modus.
        """
        if mode == self._edit_mode:
            return
        # Wis selectie van de huidige modus
        self._sel_part = None
        self._speed_sel.clear()
        self._edit_mode = mode
        self._update_swap_status()
        self._update_mode_buttons()
        self._redraw()

    def _update_mode_buttons(self):
        """Highlight de knop van de actieve modus; zet de andere terug.
        Bindings worden NIET opnieuw gezet — die zijn eenmalig in _build_ui
        gezet met add='+' zodat de tooltip-Leave-binding intact blijft."""
        for btn, mode_key in ((self._btn_swap, 'swap'),
                               (self._btn_speed, 'speed')):
            active = self._edit_mode == mode_key
            btn.config(
                bg=C['btn_active'] if active else C['btn_bg'],
                fg='#FFFFFF'       if active else C['btn_fg'],
            )
        # ≈-knop: alleen actief in speed-modus én met selectie
        similar_active = (self._edit_mode == 'speed' and bool(self._speed_sel))
        self._btn_similar.config(
            bg=C['btn_bg'],
            fg=C['btn_fg'] if similar_active else C['text_dim'],
            cursor='hand2' if similar_active else 'arrow',
        )

    def _toggle_swap_mode(self):
        """Zet wissel-modus aan/uit."""
        self._set_mode('' if self._edit_mode == 'swap' else 'swap')

    def _toggle_speed_mode(self):
        """
        Eerste klik: activeer snijsnelheid-modus (klik onderdelen om te selecteren).
        Tweede klik op actieve knop: open dialoogvenster om snelheid toe te passen.
        """
        if self._edit_mode == 'speed':
            self._open_speed_dialog()
        else:
            self._set_mode('speed')

    def _on_canvas_click(self, px: int, py: int):
        """Verwerk een klik op het canvas voor part-selectie en -wissel."""
        if not self.processor:
            return
        if self._edit_mode not in ('swap', 'speed'):
            return  # geen actieve modus
        xmm, ymm = self._to_world(px, py)
        hit = self._hit_part(xmm, ymm)

        if self._edit_mode == 'swap':
            if hit is None:
                self._sel_part = None
                self._update_swap_status()
                self._redraw()
                return
            if self._sel_part is None:
                self._sel_part = hit
                self._update_swap_status()
                self._redraw()
                return
            if self._sel_part == hit:
                self._sel_part = None
                self._update_swap_status()
                self._redraw()
                return
            self._try_swap(self._sel_part, hit)

        elif self._edit_mode == 'speed':
            # Gebruik dichtstbijzijnde contour, niet het part
            hit_c = self._hit_contour(xmm, ymm)
            if hit_c is None:
                return
            if hit_c in self._speed_sel:
                self._speed_sel.discard(hit_c)
            else:
                self._speed_sel.add(hit_c)
            self._update_mode_buttons()   # ≈-knop aan/uit
            self._redraw()




    def _select_similar_contours(self):
        """
        Voeg alle contouren toe aan de selectie die qua vorm gelijkaardig zijn
        aan de reeds geselecteerde contouren.

        Gelijkaardigheid = zelfde aantal segmenten + gelijke gesorteerde
        segmentlengtes + gelijke oppervlakte (alle binnen 0.5% relatieve
        tolerantie of 0.05 mm absoluut).
        """
        if self._edit_mode != 'speed' or not self._speed_sel or not self.processor:
            return

        import math as _math

        TOL_REL = 0.005   # 0.5 %
        TOL_ABS = 0.05    # mm  (voor zeer korte segmenten)

        def seg_lengths(c):
            return sorted(
                _math.hypot(s.end[0] - s.start[0], s.end[1] - s.start[1])
                for s in c.segments
            )

        def similar(ref, cand):
            if len(ref.segments) != len(cand.segments):
                return False
            # Oppervlakte
            ref_a, cand_a = ref.area, cand.area
            if ref_a > 0.01:
                if abs(ref_a - cand_a) / ref_a > TOL_REL:
                    return False
            # Segmentlengtes
            for rl, cl in zip(seg_lengths(ref), seg_lengths(cand)):
                if abs(rl - cl) > max(TOL_ABS, TOL_REL * max(rl, cl)):
                    return False
            return True

        # Bouw referentieset uit geselecteerde contouren
        all_contours = [
            c for part in self.processor.parts
            for c in part.outer_contours + part.holes
        ]
        ref_contours = [c for c in all_contours if c.index in self._speed_sel]

        added = 0
        for cand in all_contours:
            if cand.index in self._speed_sel:
                continue
            if any(similar(ref, cand) for ref in ref_contours):
                self._speed_sel.add(cand.index)
                added += 1

        self._update_mode_buttons()
        self._redraw()

    def _hit_contour(self, xmm: float, ymm: float,
                     threshold_px: float = 8.0) -> 'int | None':
        """
        Geeft de contour.index terug van de dichtstbijzijnde snijlijn.
        Zoekt de contour waarvan een segment het dichtst bij (xmm, ymm) ligt,
        binnen threshold_px schermpixels.
        """
        if not self.processor:
            return None
        import math as _math

        def dist_to_seg(px, py, x1, y1, x2, y2):
            dx, dy = x2 - x1, y2 - y1
            if dx == 0 and dy == 0:
                return _math.hypot(px - x1, py - y1)
            t = max(0.0, min(1.0,
                ((px - x1) * dx + (py - y1) * dy) / (dx*dx + dy*dy)))
            return _math.hypot(px - (x1 + t*dx), py - (y1 + t*dy))

        threshold_mm = threshold_px / max(self._scale, 0.001)
        best_dist = threshold_mm
        best_idx  = None

        for part in self.processor.parts:
            for c in part.outer_contours + part.holes:
                for seg in c.segments:
                    d = dist_to_seg(xmm, ymm,
                                    seg.start[0], seg.start[1],
                                    seg.end[0],   seg.end[1])
                    if d < best_dist:
                        best_dist = d
                        best_idx  = c.index
        return best_idx

    def _hit_part(self, xmm: float, ymm: float) -> 'int | None':
        """Geeft de index in processor.parts terug van het part onder (xmm, ymm)."""
        if not self.processor:
            return None
        for idx, part in enumerate(self.processor.parts):
            for c in part.outer_contours:
                bx0, bx1, by0, by1 = c.bbox
                if bx0 <= xmm <= bx1 and by0 <= ymm <= by1:
                    from ord_processor import _point_in_polygon
                    if _point_in_polygon(xmm, ymm, c.points):
                        return idx
        return None

    def _can_swap(self, idx_a: int, idx_b: int) -> bool:
        """Twee parts kunnen altijd van volgorde wisselen."""
        return True

    def _rebuild_display_rapids(self):
        """
        Herbereken de rapids voor de weergave op basis van de huidige snijvolgorde.

        Intra-part rapids (korte verbindingen binnen een onderdeel) blijven
        ongewijzigd. De inter-part rapids (lange verplaatsingen tussen onderdelen)
        worden opnieuw gegenereerd als rechte lijnen van het eindpunt van het
        vorige onderdeel naar het startpunt van het volgende.
        """
        p = self.processor
        if not p:
            return

        INCH_TO_MM = 25.4

        # Intra-part rapids: direct uit de parser, geen afstand-filter nodig
        intra = list(p._intra_rapids)


        # Inter-part rapids: herberekenen op basis van huidige volgorde
        inter = []
        for i in range(len(p.parts) - 1):
            pa = p.parts[i]
            pb = p.parts[i + 1]

            # Eindpunt van pa (positie na de laatste snijrij)
            all_c_a = pa.outer_contours + pa.holes
            if not all_c_a:
                continue
            lr_a   = max(c.last_data_row for c in all_c_a)
            ep_idx = lr_a + 1
            if ep_idx >= len(p._raw_data):
                continue
            r    = p._raw_data[ep_idx]
            ep_a = (float(r[0]) * INCH_TO_MM, float(r[1]) * INCH_TO_MM)

            # Startpunt van pb (eerste snijrij)
            all_c_b = pb.outer_contours + pb.holes
            if not all_c_b:
                continue
            fr_b = min(c.first_data_row for c in all_c_b)
            r    = p._raw_data[fr_b]
            sp_b = (float(r[0]) * INCH_TO_MM, float(r[1]) * INCH_TO_MM)

            inter.append([ep_a, sp_b])

        p.rapids = intra + inter

    def _try_swap(self, idx_a: int, idx_b: int):
        """Wissel de snijvolgorde van twee parts als hun bbox gelijk is."""
        # Wissel in de lijst
        p = self.processor.parts
        p[idx_a], p[idx_b] = p[idx_b], p[idx_a]
        self._rebuild_display_rapids()
        self._dirty = True
        # Selecteer automatisch het part na de eerst-geselecteerde (idx_a)
        next_idx = idx_a + 1
        self._sel_part = next_idx if next_idx < len(self.processor.parts) else None
        self._update_swap_status()
        self._redraw()

    def _update_swap_status(self):
        """Pas het selectielabel in de statusbalk aan."""
        if self._sel_part is not None:
            num = self.processor.parts[self._sel_part].part_index + 1
            self._lbl_sel.config(
                text=f'Part {num} geselecteerd — klik een ander part om te wisselen.',
                fg='#FACC15'
            )
        else:
            parts = []
            if self._dirty:       parts.append('snijvolgorde')
            if self._speed_dirty: parts.append('snijsnelheid')
            if parts:
                self._lbl_sel.config(
                    text=f'{" en ".join(parts).capitalize()} gewijzigd '
                         f'— gebruik 💾 Opslaan om op te slaan.',
                    fg='#4ADE80'
                )
            else:
                self._lbl_sel.config(text='', fg='#FACC15')

    def save_file(self):
        """Sla het hergeordende ORD-bestand op."""
        if not self.processor:
            return

        # ── Traversehoogte vragen (stel bestaande hoogte voor) ───────────────
        suggested = self.processor.detect_traverse_z_mm()
        traverse_z = self._ask_traverse_height(default_mm=suggested)
        if traverse_z is None:
            return  # gebruiker heeft geannuleerd

        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title='ORD opslaan',
            defaultextension='.ord',
            filetypes=[('ORD bestanden', '*.ord'), ('Alle bestanden', '*.*')],
            initialfile=self.processor.filename,
        )
        if not path:
            return

        new_order = [p.part_index for p in self.processor.parts]
        self.processor.save(path, new_order, traverse_z_mm=traverse_z)
        self._dirty = False
        self._speed_dirty = False
        self._update_swap_status()
        from tkinter import messagebox as mb
        z_info = f', traverse {traverse_z:.1f} mm' if traverse_z > 0 else ', geen traverse (Z=0)'
        mb.showinfo('Opgeslagen',
                    f'Bestand opgeslagen:\n{os.path.basename(path)}{z_info}')

    def _open_speed_dialog(self):
        """
        Dialoogvenster om de snijsnelheid (F5) aan te passen.
        Ctrl+klik op onderdelen voor gedeeltelijke selectie (cyaan ring).
        Zonder selectie: optie voor alle contouren.
        """
        if not self.processor:
            return

        # Detecteer huidige snijsnelheid (meest voorkomende F5 != 0)
        from collections import Counter
        speeds = Counter()
        for row in self.processor._raw_data:
            try:
                f5 = int(row[4].strip())
                if f5 != 0:
                    speeds[f5] += 1
            except (IndexError, ValueError):
                pass
        current_speed = speeds.most_common(1)[0][0] if speeds else 20

        # _speed_sel bevat contour.index waarden
        n_sel = len(self._speed_sel)
        has_sel = n_sel > 0

        dlg = tk.Toplevel(self.root)
        dlg.title('Snijsnelheid aanpassen')
        dlg.resizable(False, False)
        dlg.configure(bg=C['bg'])
        dlg.grab_set()

        tk.Label(dlg, text='Snijsnelheid (F5)',
                 bg=C['bg'], fg=C['text_bright'],
                 font=('Segoe UI', 10, 'bold')).pack(pady=(16, 4))

        tk.Label(dlg,
                 text='Nieuwe waarde voor de snijsnelheidsparameter (F5).\n'
                      'Rapids (F5=0) worden nooit aangepast.',
                 bg=C['bg'], fg=C['text_dim'],
                 font=('Segoe UI', 8), justify=tk.CENTER).pack(pady=(0, 8))

        entry_var = tk.StringVar(value=str(current_speed))
        entry = tk.Entry(dlg, textvariable=entry_var,
                         bg=C['btn_bg'], fg=C['text_bright'],
                         insertbackground=C['text_bright'],
                         font=('Consolas', 11), width=8, justify=tk.CENTER,
                         relief=tk.FLAT, bd=4)
        entry.pack()
        entry.select_range(0, tk.END)
        entry.focus_set()

        # Scope-keuze
        scope_var = tk.StringVar(value='sel' if has_sel else 'all')
        scope_frame = tk.Frame(dlg, bg=C['bg'])
        scope_frame.pack(pady=(10, 0))

        def rb(text, val, state=tk.NORMAL):
            return tk.Radiobutton(
                scope_frame, text=text, variable=scope_var, value=val,
                bg=C['bg'], fg=C['text_bright'] if state == tk.NORMAL else C['text_dim'],
                selectcolor=C['bg'], activebackground=C['bg'],
                font=('Segoe UI', 9), state=state
            )

        rb('Alle contouren', 'all').pack(anchor=tk.W)
        sel_label = (f'Geselecteerde contouren  ({n_sel} geselecteerd)'
                     if has_sel else
                     'Geselecteerde contouren  (klik op snijlijn om te selecteren)')
        rb(sel_label, 'sel',
           state=tk.NORMAL if has_sel else tk.DISABLED).pack(anchor=tk.W)

        # Lead-in/out optie
        tk.Frame(dlg, bg='#2A2A50', height=1).pack(fill=tk.X, padx=12, pady=(10, 4))
        leadin_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dlg, text='Pas ook lead-in / lead-out aan  (F6=0)',
            variable=leadin_var,
            bg=C['bg'], fg=C['text_bright'], selectcolor=C['bg'],
            activebackground=C['bg'], activeforeground=C['text_bright'],
            font=('Segoe UI', 8)
        ).pack(anchor=tk.W, padx=14)

        result = [None]

        def ok(_event=None):
            try:
                v = int(entry_var.get().strip())
                if v <= 0:
                    raise ValueError
                result[0] = (v, scope_var.get(), leadin_var.get())
                dlg.destroy()
            except ValueError:
                entry.config(bg='#4A1A1A')
                entry.after(600, lambda: entry.config(bg=C['btn_bg']))

        entry.bind('<Return>', ok)

        btn_frame = tk.Frame(dlg, bg=C['bg'])
        btn_frame.pack(pady=(14, 0))
        for text, cmd in [('Annuleren', dlg.destroy), ('Toepassen', ok)]:
            b = tk.Label(btn_frame, text=text,
                         bg=C['btn_bg'], fg=C['btn_fg'],
                         font=('Segoe UI', 9), padx=14, pady=5,
                         cursor='hand2', relief=tk.FLAT)
            b.bind('<Button-1>', lambda e, c=cmd: c())
            b.bind('<Enter>', lambda e, w=b: w.config(bg=C['btn_hover']))
            b.bind('<Leave>', lambda e, w=b: w.config(bg=C['btn_bg']))
            b.pack(side=tk.LEFT, padx=4)

        # Auto-sizing: centreer over hoofdvenster op basis van werkelijke maat
        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        self.root.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        dlg.geometry(f'{w}x{h}+{rx + (rw - w)//2}+{ry + (rh - h)//2}')
        dlg.wait_window()

        if result[0] is None:
            return
        new_speed, scope, include_leadin = result[0]

        if scope == 'sel' and has_sel:
            self.processor.apply_cutting_speed(
                new_speed, contour_indices=list(self._speed_sel),
                include_leadin=include_leadin)
        else:
            self.processor.apply_cutting_speed(
                new_speed, include_leadin=include_leadin)
        self._set_mode('')   # sluit speed-modus + wist selectie + update knoppen
        self._speed_dirty = True
        self._update_swap_status()
        self._redraw()

    def _ask_traverse_height(self, default_mm: float = 0.0) -> 'float | None':
        """
        Vraag de traversehoogte op via een klein dialoogvenster.
        default_mm: voorgestelde waarde (uit bestaand bestand afgeleid).
        Geeft de hoogte in mm terug, of None als de gebruiker annuleert.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title('Traversehoogte')
        dlg.resizable(False, False)
        dlg.configure(bg=C['bg'])
        dlg.grab_set()

        # Centreer t.o.v. hoofdvenster
        self.root.update_idletasks()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        dlg.geometry(f'320x160+{rx + rw//2 - 160}+{ry + rh//2 - 80}')

        tk.Label(dlg,
                 text='Traversehoogte (mm)',
                 bg=C['bg'], fg=C['text_bright'],
                 font=('Segoe UI', 10, 'bold')).pack(pady=(18, 4))

        tk.Label(dlg,
                 text='Veilige Z-hoogte tijdens rapid traverse tussen onderdelen.\n'                      'Gebruik 0 voor vlakke verplaatsing (geen Z-beweging).',
                 bg=C['bg'], fg=C['text_dim'],
                 font=('Segoe UI', 8), justify=tk.CENTER).pack(pady=(0, 10))

        # Formatteer: geen decimalen als de waarde een geheel getal is
        default_str = f'{default_mm:.3f}'.rstrip('0').rstrip('.')
        if not default_str or default_str == '-':
            default_str = '0'
        entry_var = tk.StringVar(value=default_str)
        entry = tk.Entry(dlg, textvariable=entry_var,
                         bg=C['btn_bg'], fg=C['text_bright'],
                         insertbackground=C['text_bright'],
                         font=('Consolas', 10), width=10, justify=tk.CENTER,
                         relief=tk.FLAT, bd=4)
        entry.pack()
        entry.select_range(0, tk.END)
        entry.focus_set()

        result = [None]

        def ok(_event=None):
            try:
                v = float(entry_var.get().replace(',', '.'))
                if v < 0:
                    raise ValueError
                result[0] = v
                dlg.destroy()
            except ValueError:
                entry.config(bg='#4A1A1A')
                entry.after(600, lambda: entry.config(bg=C['btn_bg']))

        def cancel(_event=None):
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=C['bg'])
        btn_frame.pack(pady=10)

        def mkbtn(parent, text, cmd):
            b = tk.Label(parent, text=text, bg=C['btn_bg'], fg=C['btn_fg'],
                         font=('Segoe UI', 9), padx=14, pady=4,
                         cursor='hand2', relief=tk.FLAT)
            b.bind('<Button-1>', lambda e: cmd())
            b.bind('<Enter>', lambda e: b.config(bg=C['btn_hover']))
            b.bind('<Leave>', lambda e: b.config(bg=C['btn_bg']))
            return b

        mkbtn(btn_frame, 'OK', ok).pack(side=tk.LEFT, padx=6)
        mkbtn(btn_frame, 'Annuleren', cancel).pack(side=tk.LEFT, padx=6)
        entry.bind('<Return>', ok)
        entry.bind('<Escape>', cancel)

        self.root.wait_window(dlg)
        return result[0]

    def _set_conflict_text(self, parts: list):
        """Schrijf gekleurde tekst naar het conflicttekstvak."""
        t = self._conflict_text
        t.config(state=tk.NORMAL)
        t.delete('1.0', tk.END)
        for text, tag in parts:
            t.insert(tk.END, text, tag)
        t.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Coordinaat-transformaties
    # ------------------------------------------------------------------

    def _to_screen(self, xmm: float, ymm: float) -> tuple:
        return (xmm * self._scale + self._off_x,
                -ymm * self._scale + self._off_y)

    def _to_world(self, px: float, py: float) -> tuple:
        return ((px - self._off_x) / self._scale,
                -(py - self._off_y) / self._scale)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def _do_zoom(self, factor: float, cx: float, cy: float):
        new_scale = self._scale * factor
        if not (0.01 <= new_scale <= 50):
            return
        self._off_x = cx + (self._off_x - cx) * factor
        self._off_y = cy + (self._off_y - cy) * factor
        self._scale = new_scale
        self._redraw()

    def _zoom_center(self, factor: float):
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        self._do_zoom(factor, w / 2, h / 2)

    # ------------------------------------------------------------------
    # Fit to screen
    # ------------------------------------------------------------------

    def fit_to_screen(self):
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w <= 1 or h <= 1:
            self._fit_pending = True
            return
        self._do_fit()

    def _do_fit(self):
        if not self.processor or not self.processor.contours:
            return

        all_pts = [pt for c in self.processor.contours for pt in c.points]
        if not all_pts:
            return

        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        ww = xmax - xmin or 1.0
        wh = ymax - ymin or 1.0

        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        margin = 48

        self._scale = min((cw - 2 * margin) / ww, (ch - 2 * margin) / wh)
        drawn_w = ww * self._scale
        drawn_h = wh * self._scale
        self._off_x = (cw - drawn_w) / 2 - xmin * self._scale
        self._off_y = (ch - drawn_h) / 2 + ymax * self._scale

        self._redraw()

    # ------------------------------------------------------------------
    # Tekenen
    # ------------------------------------------------------------------

    def _redraw(self):
        self._canvas.delete('all')
        self._draw_grid()
        if self.processor:
            self._draw_rapids()
            self._draw_contours()
            self._draw_speed_selection()
            self._draw_part_labels()
            self._draw_conflicts()
        self._update_zoom_label()

    def _draw_grid(self):
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        step = _nice_grid_step(self._scale)

        x0w, y0w = self._to_world(0, ch)
        x1w, y1w = self._to_world(cw, 0)
        show_labels = (step * self._scale) >= 50

        x = math.floor(x0w / step) * step
        while x <= x1w + step:
            px, _ = self._to_screen(x, 0)
            self._canvas.create_line(px, 0, px, ch, fill=C['grid'], width=1)
            if show_labels:
                self._canvas.create_text(px + 2, ch - 2, text=f'{x:.0f}',
                                          fill=C['grid_label'],
                                          font=('Consolas', 7), anchor=tk.SW)
            x += step

        y = math.floor(y0w / step) * step
        while y <= y1w + step:
            _, py = self._to_screen(0, y)
            self._canvas.create_line(0, py, cw, py, fill=C['grid'], width=1)
            if show_labels:
                self._canvas.create_text(2, py - 2, text=f'{y:.0f}',
                                          fill=C['grid_label'],
                                          font=('Consolas', 7), anchor=tk.SW)
            y += step

        px0, _ = self._to_screen(0, 0)
        _, py0 = self._to_screen(0, 0)
        self._canvas.create_line(px0, 0, px0, ch, fill=C['axis'], width=1)
        self._canvas.create_line(0, py0, cw, py0, fill=C['axis'], width=1)

    def _draw_rapids(self):
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()

        for seg in self.processor.rapids:
            if len(seg) < 2:
                continue
            xs = [p[0] for p in seg]
            ys = [p[1] for p in seg]
            sx0, sy1 = self._to_screen(min(xs), min(ys))
            sx1, sy0 = self._to_screen(max(xs), max(ys))
            if sx1 < -1 or sx0 > cw + 1 or sy1 < -1 or sy0 > ch + 1:
                continue
            coords = []
            for xmm, ymm in seg:
                px, py = self._to_screen(xmm, ymm)
                coords.extend([px, py])
            self._canvas.create_line(*coords, fill=C['rapid'], width=1,
                                      dash=(4, 4),
                                      joinstyle=tk.ROUND, capstyle=tk.ROUND)

    def _draw_contours(self):
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()

        # Outer contours eerst, gaten daarna (gaten bovenop)
        draw_order = (
            [c for c in self.processor.contours if not c.is_hole] +
            [c for c in self.processor.contours if c.is_hole]
        )

        for contour in draw_order:
            if not contour.segments:
                continue

            # Scherm-culling op bounding box
            bx0, bx1, by0, by1 = contour.bbox
            sx0, sy1 = self._to_screen(bx0, by0)
            sx1, sy0 = self._to_screen(bx1, by1)
            if sx1 < -1 or sx0 > cw + 1 or sy1 < -1 or sy0 > ch + 1:
                continue

            lw = 1 if contour.is_hole else 1.5

            for seg in contour.segments:
                color = speed_color(seg.feed_code)
                px0, py0 = self._to_screen(*seg.start)
                px1, py1 = self._to_screen(*seg.end)
                self._canvas.create_line(px0, py0, px1, py1,
                                          fill=color, width=lw,
                                          joinstyle=tk.ROUND, capstyle=tk.ROUND)


    def _draw_speed_selection(self):
        """Teken cyaan highlight over geselecteerde contouren (speed-modus)."""
        if not self._speed_sel or not self.processor:
            return
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        for part in self.processor.parts:
            for c in part.outer_contours + part.holes:
                if c.index not in self._speed_sel:
                    continue
                for seg in c.segments:
                    px0, py0 = self._to_screen(*seg.start)
                    px1, py1 = self._to_screen(*seg.end)
                    # Brede cyaan lijn als highlight onder de snijlijn
                    self._canvas.create_line(
                        px0, py0, px1, py1,
                        fill='#22D3EE', width=4,
                        joinstyle=tk.ROUND, capstyle=tk.ROUND
                    )

    def _draw_part_labels(self):
        """Teken het volgnummer van elk onderdeel op het zwaartepunt."""
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()

        # Toon labels alleen als er genoeg ruimte is (min ~20 px per mm)
        if self._scale < 0.3:
            return

        for idx, part in enumerate(self.processor.parts):
            # Zwaartepunt = gemiddelde van de outer-contour zwaartepunten
            outers = part.outer_contours
            if not outers:
                continue
            cx = sum(c.centroid[0] for c in outers) / len(outers)
            cy = sum(c.centroid[1] for c in outers) / len(outers)

            px, py = self._to_screen(cx, cy)

            # Buiten beeld? overslaan
            if px < -20 or px > cw + 20 or py < -20 or py > ch + 20:
                continue

            label = str(idx + 1)  # positie in huidige snijvolgorde
            r = 9 + (len(label) - 1) * 3
            is_sel = (idx == self._sel_part)
            # Highlight ring voor geselecteerd part (wissel: geel)
            if is_sel:
                self._canvas.create_oval(
                    px - r - 5, py - r - 5, px + r + 5, py + r + 5,
                    fill='', outline='#FACC15', width=2
                )
            # Witte rand
            self._canvas.create_oval(
                px - r - 1, py - r - 1, px + r + 1, py + r + 1,
                fill='#FFFFFF', outline=''
            )
            # Donkere of gele cirkel
            fill_col = '#2A1A00' if is_sel else '#0A0A14'
            self._canvas.create_oval(
                px - r, py - r, px + r, py + r,
                fill=fill_col, outline=''
            )
            # Getal
            text_col = '#FACC15' if is_sel else C['text_bright']
            self._canvas.create_text(
                px, py, text=label,
                fill=text_col,
                font=('Segoe UI', 8, 'bold'),
                anchor=tk.CENTER,
            )

    def _draw_conflicts(self):
        """Teken een uitroepteken op elke conflictpositie."""
        if not self._conflicts:
            return

        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()

        for (xmm, ymm), kind in self._conflicts:
            px, py = self._to_screen(xmm, ymm)

            # Buiten beeld: overslaan
            if px < -20 or px > cw + 20 or py < -20 or py > ch + 20:
                continue

            color  = '#EF4444' if kind == 'crossing' else '#FACC15'
            r = 8

            # Witte rand
            self._canvas.create_oval(
                px - r - 1, py - r - 1, px + r + 1, py + r + 1,
                fill='#FFFFFF', outline=''
            )
            # Gekleurde cirkel
            self._canvas.create_oval(
                px - r, py - r, px + r, py + r,
                fill=color, outline=''
            )
            # Uitroepteken
            self._canvas.create_text(
                px, py, text='!',
                fill='#0A0A14',
                font=('Segoe UI', 9, 'bold'),
                anchor=tk.CENTER,
            )

    def _update_zoom_label(self):
        ref = 96 / 25.4
        pct = self._scale / ref * 100
        self._lbl_zoom.config(text=f'{self._scale:.3f} px/mm   ({pct:.0f}%)')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    app = OrdViewer(root)

    if len(sys.argv) > 1:
        root.after(100, lambda: app.open_file(sys.argv[1]))

    root.mainloop()


if __name__ == '__main__':
    main()
