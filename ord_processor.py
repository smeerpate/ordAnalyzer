"""
ord_processor.py
----------------
Reader/processor klasse voor FlowNest/FlowMaster .ord bestanden.

Klassen:
  CutSegment    - één bewegingssegment (start→eind, feed_code, on_profile, boogtype)
  Contour       - één aaneengesloten reeks snijsegmenten
  OrdPart       - een onderdeel bestaande uit outer contour(s) + gaten
  OrdProcessor  - laadt en analyseert een .ord bestand

Formaat-interpretatie:
  Elke regel beschrijft een STARTPOSITIE en de parameters van de beweging
  VANUIT die positie naar de volgende regel:
    • F1, F2 = startcoördinaten
    • F4     = bewegingstype (0=recht, 1=CW boog, -1=CCW boog)
    • F5     = feed_code: bewegingsmodus én snelheid (0=rapid traverse,
               20/40/60=snijsnelheid in % of eenheid)
    • F6     = op profiel (1) of lead-in/lead-out (0); waterstraal staat
             in beide gevallen aan, maar F6=1 betekent dat de snijkop
             het eigenlijke stukprofiel volgt
  Rapid: F4=0 EN F5=0 EN F6=0
  Alle andere bewegingen: snijbeweging

Eenheden: coördinaten en afstanden in mm, oppervlakten in mm².
Het .ord bestand zelf werkt in inch; de conversie gebeurt bij het inlezen.
"""

import os
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Literal, Tuple

INCH_TO_MM   = 25.4        # 1 inch = 25.4 mm
INCH2_TO_MM2 = 25.4 ** 2  # 1 sq inch = 645.16 mm²


# ---------------------------------------------------------------------------
# Geometrische hulpfuncties
# ---------------------------------------------------------------------------

def _signed_area(pts: List[Tuple[float, float]]) -> float:
    """Gesigneerde oppervlakte via schoenveter-formule. Positief = CCW."""
    n = len(pts)
    if n < 3:
        return 0.0
    return sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    ) / 2.0


def _centroid(pts: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(pts)
    return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n


def _point_in_polygon(px: float, py: float,
                      poly: List[Tuple[float, float]]) -> bool:
    """Ray-casting algoritme."""
    inside = False
    x0, y0 = poly[-1]
    for x1, y1 in poly:
        if (y0 > py) != (y1 > py):
            if px < (x1 - x0) * (py - y0) / (y1 - y0) + x0:
                inside = not inside
        x0, y0 = x1, y1
    return inside


def _bbox(pts: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    """(xmin, xmax, ymin, ymax)"""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


# ---------------------------------------------------------------------------
# Dataklassen
# ---------------------------------------------------------------------------

@dataclass
class CutSegment:
    """Één bewegingssegment van start naar eind."""
    start: Tuple[float, float]   # startpunt in mm
    end:   Tuple[float, float]   # eindpunt in mm
    feed_code: int               # F5: bewegingsmodus + snelheid (0=rapid, 20/40/60=snijsnelheid)
    on_profile: bool             # F6: snijdt het eigenlijke profiel (True) of lead-in/out (False)
    arc:   int                   # F4: 0=recht, 1=CW boog, -1=CCW boog


@dataclass
class Contour:
    """Aaneengesloten reeks snijsegmenten (één gesloten snijpad)."""
    index:    int
    segments: List[CutSegment]
    is_hole:  bool = field(default=False)

    area:     float                          = field(init=False)
    centroid: Tuple[float, float]            = field(init=False)
    bbox:     Tuple[float, float, float, float] = field(init=False)
    first_data_row: int = field(default=-1, init=False)  # eerste rij in _raw_data
    last_data_row:  int = field(default=-1, init=False)  # laatste rij in _raw_data

    def __post_init__(self):
        pts = self.points
        self.area     = abs(_signed_area(pts))
        self.centroid = _centroid(pts) if pts else (0.0, 0.0)
        self.bbox     = _bbox(pts)     if pts else (0, 0, 0, 0)

    @property
    def points(self) -> List[Tuple[float, float]]:
        """
        Startpunten van alle snijsegmenten.

        Omdat opeenvolgende segmenten aaneensluiten (end[k] == start[k+1]),
        krijgen we hiermee alle unieke snijposities. Het eindpunt van het
        laatste segment wordt bewust weggelaten: dat is de startpositie van
        de volgende rapid, en een rapid mag nooit deel uitmaken van een contour.
        """
        return [s.start for s in self.segments]

    @property
    def num_points(self) -> int:
        return len(self.points)

    def __repr__(self):
        kind = "gat" if self.is_hole else "stuk"
        return (f"Contour(idx={self.index}, {kind}, "
                f"{len(self.segments)} segmenten, area={self.area:.2f} mm²)")


@dataclass
class OrdPart:
    """Één onderdeel: outer contour(s) + eventuele gaten."""
    part_index:     int
    outer_contours: List[Contour]
    holes:          List[Contour] = field(default_factory=list)

    @property
    def total_area(self) -> float:
        return sum(c.area for c in self.outer_contours)

    @property
    def hole_count(self) -> int:
        return len(self.holes)

    def __repr__(self):
        return (f"OrdPart(#{self.part_index}, "
                f"{len(self.outer_contours)} outer(s), "
                f"{self.hole_count} gaten, "
                f"area={self.total_area:.2f} mm²)")


@dataclass
class SegmentConflict:
    """Een conflict tussen twee snijsegmenten uit verschillende contouren."""
    seg_a:       CutSegment
    contour_a:   int
    seg_b:       CutSegment
    contour_b:   int
    kind:        str   # 'overlap' of 'crossing'
    overlap_mm:  float = 0.0   # lengte bij overlap; 0 bij kruising

    def __repr__(self):
        if self.kind == 'overlap':
            return (f"SegmentConflict(overlap {self.overlap_mm:.2f} mm, "
                    f"contouren {self.contour_a} & {self.contour_b})")
        return (f"SegmentConflict(kruising, "
                f"contouren {self.contour_a} & {self.contour_b})")


# ---------------------------------------------------------------------------
# Geometrische hulpfunctie: segmentkruising
# ---------------------------------------------------------------------------

def _segments_cross(
    ax0: float, ay0: float, ax1: float, ay1: float,
    bx0: float, by0: float, bx1: float, by1: float,
    tol: float = 1e-9,
) -> bool:
    """
    True als segment A (ax0,ay0)→(ax1,ay1) segment B kruist
    (geen co-lineaire overlap; dat wordt elders afgehandeld).

    Gebruikt het standaard kruisproduct-teken-test algoritme.
    Eindpunten die precies op het andere segment liggen worden
    NIET als kruising beschouwd (tol-marge).
    """
    def cross2d(ux, uy, vx, vy):
        return ux * vy - uy * vx

    dax, day = ax1 - ax0, ay1 - ay0
    dbx, dby = bx1 - bx0, by1 - by0

    denom = cross2d(dax, day, dbx, dby)
    if abs(denom) < tol:
        return False  # parallel (inclusief co-lineair)

    # Parameter t langs A, s langs B waarbij de lijnen snijden
    dx = bx0 - ax0
    dy = by0 - ay0
    t = cross2d(dx, dy, dbx, dby) / denom
    s = cross2d(dx, dy, dax, day) / denom

    eps = 1e-6   # sluit raken in eindpunten uit
    return eps < t < 1 - eps and eps < s < 1 - eps


# ---------------------------------------------------------------------------
# Hoofdklasse
# ---------------------------------------------------------------------------

class OrdProcessor:
    """
    Laadt en analyseert een FlowNest/FlowMaster .ord bestand.

    Gebruik:
        proc = OrdProcessor("bestand.ord").parse()
        print(proc.summary())
        duplicaten = proc.find_duplicate_contours()
    """

    def __init__(self, filepath: str, tab_threshold_mm: float = 5.0):
        """
        Parameters
        ----------
        filepath          : pad naar het .ord bestand
        tab_threshold_mm  : rapids korter dan deze waarde (mm) worden
                            beschouwd als tab-rapid en splitsen de contour
                            niet. Standaard 5 mm.
        """
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.tab_threshold_mm: float = tab_threshold_mm

        self.header:  str = ""
        self.version: str = ""
        self._raw_data:  List[List[str]] = []
        self._raw_lines: List[str]       = []  # parallelle lijst met originele regeltekst

        self.contours:      List[Contour] = []
        self.rapids:          List[List[Tuple[float, float]]] = []  # alle rapids
        self._intra_rapids:   List[List[Tuple[float, float]]] = []  # binnen onderdeel
        self._inter_rapids:   List[List[Tuple[float, float]]] = []  # tussen onderdelen
        self._rapid_start_rows: List[int] = []  # startrow in _raw_data per rapid
        self.parts:    List[OrdPart] = []
        self.no_safety_height:   bool = False  # True als alle Z-waarden 0 zijn
        self.has_negative_z:     bool = False  # True als er Z < 0 in het bestand staat
        self.cutting_not_at_z0:  bool = False  # True als snijbewegingen niet op Z=0 plaatsvinden
        self._parsed = False

    # ------------------------------------------------------------------
    # Publieke interface
    # ------------------------------------------------------------------

    def parse(self) -> "OrdProcessor":
        """Laad en verwerk het bestand. Geeft self terug voor method chaining."""
        self._read_file()
        self._extract_contours()
        self._classify_contours()
        self._group_into_parts()
        self._classify_rapids()
        self._parsed = True
        return self

    @property
    def num_contours(self) -> int:
        return len(self.contours)

    @property
    def num_parts(self) -> int:
        return len(self.parts)

    @property
    def outer_contours(self) -> List[Contour]:
        return [c for c in self.contours if not c.is_hole]

    @property
    def hole_contours(self) -> List[Contour]:
        return [c for c in self.contours if c.is_hole]

    def find_segment_conflicts(
        self,
        overlap_min_mm: float = 1.0,
        dist_tol_mm:    float = 0.15,
        angle_tol_deg:  float = 0.5,
    ) -> List[SegmentConflict]:
        """
        Zoek conflicten tussen snijsegmenten uit VERSCHILLENDE contouren:

        - **overlap**  : twee collineaire segmenten delen een stuk snijlijn
                         (minimaal overlap_min_mm lang)
        - **crossing** : twee niet-parallelle segmenten kruisen elkaar

        Enkel rechte segmenten (arc=0) worden gecontroleerd.
        Rapids zijn nooit deel van een contour en worden automatisch
        Segmenten waarbij minstens een van de twee on_profile=False
        is (lead-in/lead-out) worden genegeerd; enkel profielsneden
        (on_profile=True op beide) worden gecontroleerd.




        Parameters
        ----------
        overlap_min_mm  : minimale overlappende lengte om te rapporteren (mm)
        dist_tol_mm     : maximale loodrechte afstand voor collineariteit (mm)
        angle_tol_deg   : maximale hoekafwijking voor evenwijdigheid (graden)

        Returns
        -------
        Lijst van SegmentConflict objecten, gesorteerd op contour_a.
        """
        self._ensure_parsed()

        angle_tol_rad = math.radians(angle_tol_deg)
        bucket_rad    = angle_tol_rad

        # --- Verzamel alle rechte snijsegmenten met hun bbox ---
        # entry = (seg, cidx, angle_norm, ux, uy, length, xmin, xmax, ymin, ymax)
        entries = []
        for c in self.contours:
            for seg in c.segments:
                if seg.arc != 0:
                    continue
                dx = seg.end[0] - seg.start[0]
                dy = seg.end[1] - seg.start[1]
                length = math.hypot(dx, dy)
                if length < 1e-9:
                    continue
                ux, uy = dx / length, dy / length
                angle  = math.atan2(uy, ux) % math.pi
                xmin = min(seg.start[0], seg.end[0])
                xmax = max(seg.start[0], seg.end[0])
                ymin = min(seg.start[1], seg.end[1])
                ymax = max(seg.start[1], seg.end[1])
                entries.append((seg, c.index, angle, ux, uy, length,
                                xmin, xmax, ymin, ymax))

        conflicts: List[SegmentConflict] = []

        # ----------------------------------------------------------------
        # 1. Overlappen: groepeer op hoek-bucket en lijnbucket
        # ----------------------------------------------------------------
        angle_groups: dict = defaultdict(list)
        for entry in entries:
            seg, cidx, angle, ux, uy, length, *_ = entry
            bucket = round(angle / bucket_rad)
            angle_groups[bucket].append(entry)

        for bucket, group in angle_groups.items():
            ref_angle = bucket * bucket_rad
            ref_ux =  math.cos(ref_angle)
            ref_uy =  math.sin(ref_angle)
            perp_ux = -ref_uy
            perp_uy =  ref_ux

            line_groups: dict = defaultdict(list)
            for entry in group:
                seg, cidx, angle, ux, uy, length, *_ = entry
                px, py = seg.start
                perp_dist = px * perp_ux + py * perp_uy
                line_key = round(perp_dist / dist_tol_mm)
                line_groups[line_key].append(entry)

            for line_segs in line_groups.values():
                if len(line_segs) < 2:
                    continue

                projected = []
                for entry in line_segs:
                    seg, cidx, angle, ux, uy, length, *_ = entry
                    px, py = seg.start
                    t0 = px * ref_ux + py * ref_uy
                    dot = ux * ref_ux + uy * ref_uy
                    t1 = t0 + length * dot
                    if t0 > t1:
                        t0, t1 = t1, t0
                    projected.append((t0, t1, seg, cidx))

                for i in range(len(projected)):
                    t0a, t1a, seg_a, cidx_a = projected[i]
                    for j in range(i + 1, len(projected)):
                        t0b, t1b, seg_b, cidx_b = projected[j]
                        # Alleen echte snijbewegingen (water aan op beide)
                        if not (seg_a.on_profile and seg_b.on_profile):
                            continue
                        px, py = seg_b.start
                        perp_d = abs(
                            (px - seg_a.start[0]) * perp_ux +
                            (py - seg_a.start[1]) * perp_uy
                        )
                        if perp_d > dist_tol_mm:
                            continue
                        ov = min(t1a, t1b) - max(t0a, t0b)
                        if ov >= overlap_min_mm:
                            conflicts.append(SegmentConflict(
                                seg_a=seg_a, contour_a=cidx_a,
                                seg_b=seg_b, contour_b=cidx_b,
                                kind='overlap', overlap_mm=ov,
                            ))

        # ----------------------------------------------------------------
        # 2. Kruisingen: ruimtelijk grid om kandidaat-paren te beperken
        # ----------------------------------------------------------------
        CELL = 100.0  # gridcel in mm

        # Registreer elk segment in alle gridcellen die zijn bbox overlapt
        grid: dict = defaultdict(list)
        for idx, entry in enumerate(entries):
            seg, cidx, ang, ux, uy, length, xmn, xmx, ymn, ymx = entry
            col0 = int(xmn // CELL)
            col1 = int(xmx // CELL)
            row0 = int(ymn // CELL)
            row1 = int(ymx // CELL)
            for col in range(col0, col1 + 1):
                for row in range(row0, row1 + 1):
                    grid[(col, row)].append(idx)

        # Controleer paren die dezelfde gridcel delen (met deduplicatie)
        checked: set = set()
        for cell_entries in grid.values():
            if len(cell_entries) < 2:
                continue
            for ii in range(len(cell_entries)):
                i = cell_entries[ii]
                seg_a, cidx_a, ang_a, *_, xmna, xmxa, ymna, ymxa = entries[i]
                for jj in range(ii + 1, len(cell_entries)):
                    j = cell_entries[jj]
                    if i >= j:
                        continue
                    pair = (i, j)
                    if pair in checked:
                        continue
                    checked.add(pair)

                    seg_b, cidx_b, ang_b, *_, xmnb, xmxb, ymnb, ymxb = entries[j]

                    # Alleen echte snijbewegingen
                    if not (seg_a.on_profile and seg_b.on_profile):
                        continue

                    # Sla (bijna-)parallelle paren over
                    angle_diff = abs(ang_a - ang_b) % math.pi
                    if (angle_diff < angle_tol_rad or
                            angle_diff > math.pi - angle_tol_rad):
                        continue

                    if _segments_cross(
                        seg_a.start[0], seg_a.start[1],
                        seg_a.end[0],   seg_a.end[1],
                        seg_b.start[0], seg_b.start[1],
                        seg_b.end[0],   seg_b.end[1],
                    ):
                        conflicts.append(SegmentConflict(
                            seg_a=seg_a, contour_a=cidx_a,
                            seg_b=seg_b, contour_b=cidx_b,
                            kind='crossing', overlap_mm=0.0,
                        ))

        conflicts.sort(key=lambda c: (c.contour_a, c.contour_b))
        return conflicts

    def summary(self) -> str:
        """Geeft een tekstuele samenvatting."""
        self._ensure_parsed()
        lines = [
            f"Bestand   : {self.filename}",
            f"Versie    : {self.version}",
            f"Contours  : {self.num_contours} totaal",
            f"  Stukken : {len(self.outer_contours)} outer contours",
            f"  Gaten   : {len(self.hole_contours)} inner contours",
            f"Onderdelen: {self.num_parts}",
        ]
        if self.parts:
            sizes = sorted(set(len(p.outer_contours) for p in self.parts))
            if sizes != [1]:
                lines.append(f"  (elk onderdeel = {sizes} outer contour(s))")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Interne methoden
    # ------------------------------------------------------------------

    def _read_file(self):
        with open(self.filepath, "r") as f:
            raw_lines = f.readlines()

        if len(raw_lines) < 2:
            raise ValueError(f"Bestand te kort: {self.filepath}")

        self.header  = raw_lines[0].strip()
        self.version = raw_lines[1].strip()

        self._raw_data = []
        for line in raw_lines[2:]:
            line = line.strip()
            if not line:
                continue
            flds = [v.strip() for v in line.split(",")]
            if len(flds) >= 7:
                self._raw_data.append(flds)
                self._raw_lines.append(line.strip())

        # ── Z-coördinaat controles (kolom 3, index 2) ──────────────────
        z_vals = [float(r[2]) for r in self._raw_data]

        # Alle Z=0 → geen safety-hoogte tussen bewerkingen
        self.no_safety_height = bool(z_vals) and all(z == 0.0 for z in z_vals)

        # Eén of meer negatieve Z-waarden
        self.has_negative_z = any(z < 0.0 for z in z_vals)

        # Snijbewegingen (niet-rapids) op Z ≠ 0
        # Rapid: F4=0 EN F5=0 EN F6=0  →  indices 3, 4, 5
        cut_z = [
            float(r[2]) for r in self._raw_data
            if not (r[3] == '0' and r[4] == '0' and r[5] == '0')
        ]
        self.cutting_not_at_z0 = bool(cut_z) and any(z != 0.0 for z in cut_z)

    def _extract_contours(self):
        """
        Verwerkt de datarijen per opeenvolgend paar (regel i → regel i+1).

        Segment i→i+1:
          • startpunt : F1, F2 van regel i
          • eindpunt  : F1, F2 van regel i+1
          • type      : F4 van regel i (0=recht, 1=CW, -1=CCW)
          • feed_code : F5 van regel i (0=rapid, 20/40/60=snijsnelheid)
          • water     : F6 van regel i

        Rapid  : F4=0 EN F5=0 EN F6=0
        Snijden: alle andere segmenten

        Korte rapids (< _INTRA_CONTOUR_RAPID_MM) splitsen de lopende
        contour NIET — ze zijn interne markeringen binnen één snijpad.
        """
        self.contours = []
        self.rapids   = []

        cur_segments:       List[CutSegment] = []
        cur_rapid:          List[Tuple[float, float]] = []
        cur_rapid_first_row: int = -1
        contour_idx   = 0
        cur_first_row: int = -1
        cur_last_row:  int = -1

        def flush_contour():
            nonlocal cur_segments, contour_idx, cur_first_row, cur_last_row
            if cur_segments:
                c = Contour(contour_idx, cur_segments[:])
                c.first_data_row = cur_first_row
                c.last_data_row  = cur_last_row
                self.contours.append(c)
                contour_idx += 1
            cur_segments  = []
            cur_first_row = -1
            cur_last_row  = -1

        def flush_rapid(keep_if_short: bool = False) -> bool:
            """
            Sla de huidige rapid op als `self.rapid`.
            Geeft True terug als de rapid kort genoeg is om de contour
            NIET te splitsen (intra-contour rapid).
            """
            nonlocal cur_rapid, cur_rapid_first_row
            if len(cur_rapid) < 2:
                cur_rapid = []
                cur_rapid_first_row = -1
                return False
            dx = cur_rapid[-1][0] - cur_rapid[0][0]
            dy = cur_rapid[-1][1] - cur_rapid[0][1]
            length = math.hypot(dx, dy)
            is_short = length < self.tab_threshold_mm
            self.rapids.append(cur_rapid)
            self._rapid_start_rows.append(
                cur_rapid_first_row if cur_rapid_first_row >= 0 else 0
            )
            cur_rapid = []
            cur_rapid_first_row = -1
            return is_short

        data = self._raw_data

        for i in range(len(data) - 1):
            cur = data[i]
            nxt = data[i + 1]

            x0 = float(cur[0]) * INCH_TO_MM
            y0 = float(cur[1]) * INCH_TO_MM
            x1 = float(nxt[0]) * INCH_TO_MM
            y1 = float(nxt[1]) * INCH_TO_MM

            f4 = cur[3]
            f5 = cur[4]
            f6 = cur[5]

            is_rapid = (f4 == "0" and f5 == "0" and f6 == "0")

            if is_rapid:
                if not cur_rapid:
                    cur_rapid = [(x0, y0), (x1, y1)]
                    cur_rapid_first_row = i
                else:
                    cur_rapid.append((x1, y1))
            else:
                if cur_rapid:
                    short = flush_rapid()
                    if not short:
                        # Lange rapid: sluit lopende contour
                        flush_contour()
                try:
                    feed_code = int(f5)
                    arc   = int(f4)
                except ValueError:
                    feed_code, arc = 0, 0
                if cur_first_row < 0:
                    cur_first_row = i
                cur_last_row = i
                cur_segments.append(CutSegment(
                    start=(x0, y0),
                    end=(x1, y1),
                    feed_code=feed_code,
                    on_profile=(f6 == "1"),
                    arc=arc,
                ))

        flush_contour()
        if cur_rapid:
            flush_rapid()

    def _classify_contours(self):
        """
        Classificeer elke contour als outer of gat via directe-ouder + diepte.

        Directe ouder van X = kleinste contour die X ruimtelijk omsluit.
        Diepte 0 (geen ouder)       → outer contour
        Diepte 1 (ouder is outer)   → gat
        Diepte 2 (ouder is gat)     → outer van genest stuk (ongebruikelijk)
        Algemeen: even diepte = outer, oneven diepte = gat.

        Constraint: een stuk met gaten kan geen stuk met gaten bevatten.
        Een stuk heeft altijd precies één outer contour.
        """
        n = len(self.contours)
        if n == 0:
            return

        by_size = sorted(range(n), key=lambda i: self.contours[i].area,
                         reverse=True)

        # Vind directe ouder van elke contour: de kleinste contour die hem omsluit
        parent: List[int | None] = [None] * n
        for i in range(n):
            c = self.contours[i]
            cx, cy = c.centroid
            best_j    = None
            best_area = float('inf')
            for j in by_size:
                if j == i:
                    continue
                other = self.contours[j]
                if other.area <= c.area:
                    break                   # alles hierna is kleiner, stop
                bx0, bx1, by0, by1 = other.bbox
                if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                    if _point_in_polygon(cx, cy, other.points):
                        if other.area < best_area:
                            best_area = other.area
                            best_j    = j
            parent[i] = best_j

        # Bereken diepte via gememoïseerde recursie
        depth_memo: dict = {}

        def get_depth(i: int) -> int:
            if i in depth_memo:
                return depth_memo[i]
            depth_memo[i] = 0 if parent[i] is None else get_depth(parent[i]) + 1
            return depth_memo[i]

        for i, c in enumerate(self.contours):
            c.is_hole = (get_depth(i) % 2 == 1)

    def _group_into_parts(self):
        """
        Elk outer contour = 1 onderdeel. Gaten worden toegewezen aan de
        outer contour die hen ruimtelijk omsluit.
        """
        self.parts = []
        holes = self.hole_contours

        for pi, outer in enumerate(self.outer_contours):
            part = OrdPart(part_index=pi, outer_contours=[outer])
            bx0, bx1, by0, by1 = outer.bbox
            for hole in holes:
                hx, hy = hole.centroid
                if bx0 <= hx <= bx1 and by0 <= hy <= by1:
                    if _point_in_polygon(hx, hy, outer.points):
                        part.holes.append(hole)
            self.parts.append(part)


    def reorder_parts(self, new_order: List[int],
                      traverse_z_mm: float = 0.0) -> str:
        """
        Genereer ORD-bestandsinhoud met de parts in de opgegeven volgorde.

        Elke part wordt behandeld als een aaneengesloten tekstblok
        (van eerste snijregel tot laatste snijregel). De rapids tussen
        blokken worden opnieuw berekend, inclusief een optionele
        traverse-hoogte (in mm).

        Parameters
        ----------
        new_order      : originele part-indices in de gewenste snijvolgorde.
        traverse_z_mm  : veilige reishoogte in mm tussen bewerkingen (0 = vlak).
        """
        self._ensure_parsed()

        traverse_z_inch = traverse_z_mm / INCH_TO_MM

        # ── Rij-bereik per ORIGINELE part-index ──────────────────────────────
        # Gebruik part_index als sleutel zodat de mapping correct blijft
        # ook na visuele herordening in de viewer.
        def part_rows(p):
            all_c = p.outer_contours + p.holes
            if not all_c or any(c.first_data_row < 0 for c in all_c):
                return None, None
            return (min(c.first_data_row for c in all_c),
                    max(c.last_data_row  for c in all_c))

        ranges = {}          # orig_part_index → (first_row, last_row)
        for p in self.parts:
            ranges[p.part_index] = part_rows(p)

        # ── Preamble: rijen vóór het eerste snijblok ──────────────────────────
        valid_firsts = [fr for fr, _ in ranges.values() if fr is not None]
        first_fr = min(valid_firsts) if valid_firsts else 0

        out = [self.header, self.version]
        for i in range(first_fr):
            out.append(self._raw_lines[i])

        # ── Hulpfunctie: genereer rapid-blok van (x0,y0) naar (x1,y1) ───────
        def rapid_lines(x0, y0, x1, y1) -> List[str]:
            """
            Genereer de verbindende rapid-regels van eindpunt naar startpunt.
            Elke rij heeft formaat: x, y, z, 0, 0, 0, 0
            Met traverse_z_inch > 0:
              1. Hef op naar traverse-hoogte boven eindpunt
              2. Beweeg horizontaal naar startpunt (op traverse-hoogte)
              (de eerste snijrij van het volgende blok heeft Z=0,
               wat automatisch het neerlaten verzorgt)
            Met traverse_z_inch = 0:
              1. Directe horizontal rapid (één rij)
            """
            def fmt(x, y, z):
                return f"   {x:10.4f},  {y:10.4f},  {z:10.4f}, 0,    0, 0, 0"

            if traverse_z_inch > 0:
                return [
                    fmt(x0, y0, 0.0),             # eindpunt op snijhoogte
                    fmt(x0, y0, traverse_z_inch),  # omhoog naar travershoogte
                    fmt(x1, y1, traverse_z_inch),  # horizontaal naar startpunt
                    # de eerste snijrij van het volgende blok (Z=0) verzorgt
                    # het neerlaten impliciet
                ]
            else:
                return [fmt(x0, y0, 0.0)]

        # ── Blokken in nieuwe volgorde emitteren ─────────────────────────────
        prev_ep: tuple | None = None   # (x, y) eindpunt vorig blok (in inch)

        for part_idx in new_order:
            fr, lr = ranges.get(part_idx, (None, None))
            if fr is None:
                continue

            ep_idx = lr + 1   # rij na laatste snijrij = eindpositie

            # Bepaal start- en eindpositie van dit blok (in inch)
            part_start_x = float(self._raw_data[fr][0])
            part_start_y = float(self._raw_data[fr][1])

            if ep_idx < len(self._raw_data):
                ep_x = float(self._raw_data[ep_idx][0])
                ep_y = float(self._raw_data[ep_idx][1])
            else:
                # Geen eindpositierij → gebruik startpositie als noodoplossing
                ep_x, ep_y = part_start_x, part_start_y

            # Verbindende rapid (niet vóór het allereerste blok)
            if prev_ep is not None:
                for line in rapid_lines(prev_ep[0], prev_ep[1],
                                        part_start_x, part_start_y):
                    out.append(line)

            # Snijrijen van dit blok (fr t/m lr)
            for i in range(fr, lr + 1):
                out.append(self._raw_lines[i])

            prev_ep = (ep_x, ep_y)

        # ── Sluitende rij: eindpositie van het laatste blok ──────────────────
        if prev_ep is not None:
            out.append(
                f"   {prev_ep[0]:10.4f},  {prev_ep[1]:10.4f},  {0.0:10.4f}, 0,    0, 0, 0"
            )

        # ── Trailer: rijen na het origineel laatste blok ─────────────────────
        valid_lasts = [lr for _, lr in ranges.values() if lr is not None]
        if valid_lasts:
            orig_last_ep = max(valid_lasts) + 1
            for i in range(orig_last_ep + 1, len(self._raw_lines)):
                out.append(self._raw_lines[i])

        return "\n".join(out) + "\n"

    def save(self, filepath: str,
             new_order: List[int] | None = None,
             traverse_z_mm: float = 0.0) -> None:
        """Schrijf het (her)geordende ORD-bestand naar schijf."""
        if new_order is None:
            new_order = list(range(len(self.parts)))
        content = self.reorder_parts(new_order, traverse_z_mm=traverse_z_mm)
        with open(filepath, "w", newline="\n") as fh:
            fh.write(content)

    def _classify_rapids(self):
        """
        Verdeel self.rapids in _intra_rapids en _inter_rapids.

        Een rapid is intra-part als zijn startrow valt binnen het
        aaneengesloten rijbereik [first_data_row, last_data_row] van
        een OrdPart (inclusief alle tussenliggende rapids van dat part,
        bv. tussen outer-contour en gaten).

        Een rapid is inter-part als zijn startrow buiten alle part-
        rijbereiken valt.
        """
        # Rijbereik per part: van eerste snijrij t/m laatste snijrij
        # Alle intra-part rapids (ook outer→gat) vallen per definitie
        # BINNEN dit bereik.
        part_ranges = []
        for part in self.parts:
            all_c = part.outer_contours + part.holes
            if not all_c:
                continue
            fr = min(c.first_data_row for c in all_c)
            lr = max(c.last_data_row  for c in all_c)
            part_ranges.append((fr, lr))

        self._intra_rapids = []
        self._inter_rapids = []

        for rapid, start_row in zip(self.rapids, self._rapid_start_rows):
            is_intra = any(fr <= start_row <= lr for fr, lr in part_ranges)
            if is_intra:
                self._intra_rapids.append(rapid)
            else:
                self._inter_rapids.append(rapid)

    def apply_cutting_speed(self, new_speed: int,
                             part_indices: 'list[int] | None' = None):
        """
        Pas de snijsnelheid (F5) aan in _raw_data en _raw_lines.

        new_speed     : de nieuwe F5-waarde (bv. 20, 40, 60)
        part_indices  : lijst van part.part_index waarden die aangepast
                        worden. None = alle snijcontouren.

        Rijen met F5=0 (rapids) worden nooit aangepast.
        """
        self._ensure_parsed()

        # Bepaal de rijbereiken die aangepast mogen worden
        if part_indices is None:
            # Alle rijen
            allowed_rows = set(range(len(self._raw_data)))
        else:
            allowed_rows: set[int] = set()
            idx_set = set(part_indices)
            for part in self.parts:
                if part.part_index not in idx_set:
                    continue
                for c in part.outer_contours + part.holes:
                    if c.first_data_row >= 0:
                        for r in range(c.first_data_row, c.last_data_row + 1):
                            allowed_rows.add(r)

        for i, row in enumerate(self._raw_data):
            if i not in allowed_rows:
                continue
            try:
                f5_val = int(row[4].strip())
            except (IndexError, ValueError):
                continue
            if f5_val == 0:
                continue  # rapid-rij, niet aanpassen

            # Vervang de F5-kolom in de raw lijn
            # Formaat: "   X,   Y,   Z, F4, F5, F6, ..."
            # Gebruik de raw lijn als basis en vervang het F5-veld
            old_line = self._raw_lines[i]
            parts_line = old_line.split(',')
            if len(parts_line) < 6:
                continue
            # F5 staat op positie 4 (0-gebaseerd)
            old_f5 = parts_line[4]
            # Behoud breedte van het veld
            width = len(old_f5)
            new_f5_str = f'{new_speed:>{width}}'
            parts_line[4] = new_f5_str
            self._raw_lines[i] = ','.join(parts_line)
            row[4] = str(new_speed)

        # Update ook de CutSegment-objecten zodat de UI direct de nieuwe
        # kleurschakering toont zonder herparsen.
        part_idx_set = None if part_indices is None else set(part_indices)
        for part in self.parts:
            if part_idx_set is not None and part.part_index not in part_idx_set:
                continue
            for c in part.outer_contours + part.holes:
                for seg in c.segments:
                    if seg.feed_code != 0:   # rapid-segmenten niet aanpassen
                        seg.feed_code = new_speed

        self._dirty_speed = True

    def detect_traverse_z_mm(self) -> float:
        """
        Lees de hoogste Z-waarde uit de inter-part rapid rijen in _raw_data.
        Inter-part rapid rijen: F4=0, F5=0, F6=0.
        Geeft de waarde in mm terug (raw data staat in inch).
        Geeft 0.0 terug als er geen traverse-hoogte gevonden wordt.
        """
        self._ensure_parsed()
        max_z_inch = 0.0
        for row in self._raw_data:
            try:
                f4 = row[3].strip()
                f5 = row[4].strip()
                f6 = row[5].strip()
                if f4 == '0' and f5 == '0' and f6 == '0':
                    z = float(row[2])
                    if z > max_z_inch:
                        max_z_inch = z
            except (IndexError, ValueError):
                continue
        return max_z_inch * INCH_TO_MM

    def _ensure_parsed(self):
        if not self._parsed:
            raise RuntimeError("Roep eerst .parse() aan.")
