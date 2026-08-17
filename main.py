"""
main.py
-------
Commandline tool voor analyse van FlowNest/FlowMaster .ord bestanden.

Gebruik:
    python main.py bestand.ord [bestand2.ord ...]

Rapporteert per bestand:
  - Aantal onderdelen, stukken en gaten
  - Detail per onderdeel
  - Eventuele dubbele contouren
"""

import sys
import os
from ord_processor import OrdProcessor


def analyze_file(filepath: str):
    print(f"\n{'=' * 60}")

    if not os.path.isfile(filepath):
        print(f"FOUT: Bestand niet gevonden: {filepath}")
        return

    proc = OrdProcessor(filepath).parse()

    # Samenvatting
    print(proc.summary())

    # Detail per onderdeel (toon max 10 om output beheersbaar te houden)
    if proc.parts:
        print(f"\nOnderdelen:")
        max_tonen = 10
        for part in proc.parts[:max_tonen]:
            outer_areas = [f"{c.area:.1f}" for c in part.outer_contours]
            print(f"  Part #{part.part_index + 1}: "
                  f"{len(part.outer_contours)} stuk(ken) "
                  f"[{', '.join(outer_areas)} mm²], "
                  f"{part.hole_count} gat(en)")
        if len(proc.parts) > max_tonen:
            print(f"  ... en nog {len(proc.parts) - max_tonen} onderdelen")

    # Dubbele contouren controleren
    print(f"\nDubbele contouren controleren...")
    duplicates = proc.find_duplicate_contours()

    if not duplicates:
        print("  Geen dubbele contouren gevonden.")
    else:
        print(f"  WAARSCHUWING: {len(duplicates)} dubbel(e) gevonden!")
        for a, b in duplicates:
            kind_a = "gat" if a.is_hole else "stuk"
            kind_b = "gat" if b.is_hole else "stuk"
            print(f"    Contour #{a.index} ({kind_a}, area={a.area:.2f} mm²) "
                  f"== Contour #{b.index} ({kind_b}, area={b.area:.2f} mm²) "
                  f"@ ({a.centroid[0]:.3f}, {a.centroid[1]:.3f}) mm")


def main():
    if len(sys.argv) < 2:
        print("Gebruik: python main.py bestand.ord [bestand2.ord ...]")
        sys.exit(1)

    for filepath in sys.argv[1:]:
        analyze_file(filepath)

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()
