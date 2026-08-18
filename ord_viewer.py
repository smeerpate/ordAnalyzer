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
    (0.00,  0x58, 0x00, 0x90),  # indigo/paars (zichtbaar op zwart)
    (0.18,  0x90, 0x00, 0xB8),  # helder paars
    (0.35,  0xC0, 0x00, 0x80),  # paars-magenta
    (0.50,  0xD4, 0x00, 0x30),  # donkerrood
    (0.63,  0xF0, 0x50, 0x00),  # rood-oranje
    (0.75,  0xFF, 0x88, 0x00),  # oranje
    (0.88,  0xFF, 0xC8, 0x00),  # geel
    (1.00,  0xFF, 0xFF, 0xFF),  # wit
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
        self.root.title("ORD Viewer")
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
        self._sel_part:   int | None = None    # index in processor.parts
        self._dirty:      bool = False          # herordening nog niet opgeslagen

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
            'Klik op een onderdeel in de tekening om het te selecteren (geel gemarkeerd).\n'
            'Klik daarna op een tweede onderdeel\n'
            'om de snijvolgorde van beide te wisselen.\n'
            'Klik nogmaals op hetzelfde onderdeel of op ⇄ om de selectie te wissen.'
        )
        self._btn_swap.pack(side=tk.LEFT, padx=2)
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

        # ℹ bediening-hint rechts in toolbar
        tooltip(
            tk.Label(tb, text='ℹ',
                     bg=C['toolbar_bg'], fg=C['text_dim'],
                     font=('Segoe UI', 11), cursor='question_arrow'),
            'Bediening canvas\n'
            '  Slepen (links/midden)  Pannen\n'
            '  Muiswiel               Zoomen op cursor\n'
            '  Dubbelklik             Alles in beeld\n'
            '\n'
            'Kleuren\n'
            '  Groen gestippeld       Rapid traverse (geen snijden)\n'
            '  Paars → wit heatmap    Snijsnelheid (laag → hoog)\n'
            '\n'
            'Nummers in cirkel        Snijvolgorde per onderdeel\n'
            '\n'
            'Waarschuwingen (zijpaneel)\n'
            '  ⚠ Geen safety hoogte   Alle Z-waarden zijn 0\n'
            '  ⚠ Negatieve Z           Z < 0 aanwezig\n'
            '  ⚠ Snijden niet op Z=0  Snijbewegingen op Z ≠ 0\n'
            '\n'
            'Conflicten (zijpaneel)\n'
            '  ⚠ geel                 Overlappende snijlijnen\n'
            '  ✕ rood                 Kruisende snijlijnen'
        ).pack(side=tk.RIGHT, padx=12)

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

        # Heatmap gradient: blokjes voor v=0, 20, 40, 60, 80, 100
        tk.Label(leg, text='v:', bg=C['status_bg'], fg=C['text_dim'],
                 font=('Segoe UI', 8)).pack(side=tk.LEFT)
        for v in range(0, 101, 10):
            tk.Label(leg, text='█', bg=C['status_bg'], fg=speed_color(v),
                     font=('Consolas', 8)).pack(side=tk.LEFT, padx=0)
        tk.Label(leg, text=' 0→100', bg=C['status_bg'], fg=C['text_dim'],
                 font=('Segoe UI', 8)).pack(side=tk.LEFT)

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

    def _toggle_swap_mode(self):
        """Zet wissel-modus aan/uit."""
        if self._sel_part is not None:
            self._sel_part = None
            self._update_swap_status()
            self._redraw()
        # Modus is impliciet: zolang er een selectie is, zitten we in wissel-modus.
        # Gebruiker klikt op een part om te selecteren; tweede klik wisselt.

    def _on_canvas_click(self, px: int, py: int):
        """Verwerk een klik op het canvas voor part-selectie en -wissel."""
        if not self.processor:
            return
        xmm, ymm = self._to_world(px, py)
        hit = self._hit_part(xmm, ymm)

        if hit is None:
            # Klik in lege ruimte → deselecteer
            self._sel_part = None
            self._update_swap_status()
            self._redraw()
            return

        if self._sel_part is None:
            # Eerste selectie
            self._sel_part = hit
            self._update_swap_status()
            self._redraw()
            return

        if self._sel_part == hit:
            # Zelfde part nogmaals klikken → deselecteer
            self._sel_part = None
            self._update_swap_status()
            self._redraw()
            return

        # Twee verschillende parts geselecteerd → probeer te wisselen
        self._try_swap(self._sel_part, hit)

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
        self._sel_part = None
        self._update_swap_status()
        self._redraw()

    def _update_swap_status(self):
        """Pas het selectielabel in de statusbalk aan."""
        if self._sel_part is not None:
            num = self.processor.parts[self._sel_part].part_index + 1
            self._lbl_sel.config(
                text=f'Part {num} geselecteerd — klik een ander part met gelijke bbox om te wisselen.',
                fg='#FACC15'
            )
        elif self._dirty:
            self._lbl_sel.config(
                text='Volgorde gewijzigd — gebruik 💾 Opslaan om op te slaan.',
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
        self._update_swap_status()
        from tkinter import messagebox as mb
        z_info = f', traverse {traverse_z:.1f} mm' if traverse_z > 0 else ', geen traverse (Z=0)'
        mb.showinfo('Opgeslagen',
                    f'Bestand opgeslagen:\n{os.path.basename(path)}{z_info}')

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
            # Highlight ring voor geselecteerd part
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
