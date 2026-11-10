"""Fab outputs for JLCPCB from the saved board and schematic (kicad-cli 10.0.8).

fab/gerber/*            Gerber X2 for the 2 copper layers, masks, silk, paste, outline + Excellon drill (PTH/NPTH)
fab/gerber_jlcpcb.zip   the above, zipped for upload
fab/BOM_JLCPCB.csv      Comment, Designator, Footprint, LCSC Part # (holes and Tag-Connect pads dropped)
fab/CPL_JLCPCB.csv      Designator, Mid X, Mid Y, Layer, Rotation, with the rotation corrections of
                        tools/cpl_rotations_db.csv (JLCKicadTools table, the one kicad-jlcpcb-tools downloads)
docs/render_top.png, docs/render_bottom.png, docs/lowpower_timer_schematic.pdf
Run: "C:/Program Files/Python311/python.exe" tools/export_fab.py
"""
import csv
import io
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
PCB = ROOT / "lowpower_timer.kicad_pcb"
SCH = ROOT / "lowpower_timer.kicad_sch"
FAB, GER, DOCS = ROOT / "fab", ROOT / "fab" / "gerber", ROOT / "docs"
LAYERS = "F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,F.Paste,B.Paste,Edge.Cuts"


def run(*args):
    r = subprocess.run([CLI, *args], capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def gerbers():
    GER.mkdir(parents=True, exist_ok=True)
    for f in GER.iterdir():
        f.unlink()
    run("pcb", "export", "gerbers", "--layers", LAYERS, "--subtract-soldermask", "-o", str(GER) + "\\", str(PCB))
    run("pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th", "--excellon-oval-format", "alternate",
        "--generate-map", "--map-format", "pdf", "-o", str(GER) + "\\", str(PCB))
    files = sorted(f for f in GER.iterdir() if f.suffix != ".pdf")
    with zipfile.ZipFile(FAB / "gerber_jlcpcb.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    names = [f.name for f in files]
    for need in ("F_Cu", "B_Cu", "Edge_Cuts", "PTH.drl", "NPTH.drl"):
        assert any(need in n for n in names), (need, names)
    print("gerbers:", len(names), "files")


def bom():
    raw = run("sch", "export", "bom", "--fields", "Reference,Value,Footprint,LCSC,${QUANTITY}",
              "--labels", "Designator,Comment,Footprint,LCSC Part #,Qty", "--group-by", "Value,Footprint,LCSC",
              "--exclude-dnp", "-o", str(FAB / "_bom_raw.csv"), str(SCH))
    rows = list(csv.DictReader(open(FAB / "_bom_raw.csv", encoding="utf-8")))
    (FAB / "_bom_raw.csv").unlink()
    out, dropped = [], []
    for r in rows:
        if not r["LCSC Part #"]:
            dropped.append(r["Designator"])
            continue
        out.append({"Comment": r["Comment"], "Designator": r["Designator"],
                    "Footprint": r["Footprint"].split(":")[-1], "LCSC Part #": r["LCSC Part #"]})
    assert all(d.startswith("H") or d == "J2" for d in dropped), dropped    # holes and Tag-Connect pads have no part
    with open(FAB / "BOM_JLCPCB.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print("BOM:", len(out), "lines; dropped", dropped)


def rotations():
    """upstream JLCKicadTools table (unchanged) plus local overrides; the upstream '^SOT-23' (-90, meant for the
    3-pin package) is a prefix match that would also catch SOT-23-5/-6, so cpl_rotations_local.csv gives those 180
    like the other 5/6-pin packages in the table (TSOT-23, SOT-353, SOT-363)"""
    rules = []
    for name in ("cpl_rotations_db.csv", "cpl_rotations_local.csv"):
        for row in csv.reader(open(Path(__file__).with_name(name), encoding="utf-8")):
            if row and row[0] != "Footprint pattern":
                rules.append((re.compile(row[0]), float(row[1])))
    return rules


def pick(rules, pkg):
    """longest match wins, ties keep table order (the rule kicad-jlcpcb-tools uses, see its CORRECTIONS.md)"""
    best, best_len = None, -1
    for rx, d in rules:
        m = rx.search(pkg)
        if m and m.end() - m.start() > best_len:
            best, best_len = d, m.end() - m.start()
    return best


assert pick([(re.compile("^SOT-23"), -90), (re.compile("^SOT-23-5"), 180)], "SOT-23-5") == 180
assert pick([(re.compile("^SOT-23"), -90), (re.compile("^SOT-23-5"), 180)], "SOT-23") == -90


def cpl():
    raw = run("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
              "-o", str(FAB / "_pos_raw.csv"), str(PCB))
    rows = list(csv.DictReader(open(FAB / "_pos_raw.csv", encoding="utf-8")))
    (FAB / "_pos_raw.csv").unlink()
    rules, out, corrected = rotations(), [], {}
    for r in rows:
        ref, pkg = r["Ref"], r["Package"]
        if ref.startswith("H") or ref == "J2":                 # nothing to place
            continue
        rot = float(r["Rot"])
        d = pick(rules, pkg)
        if d is not None:
            rot = (rot + d) % 360
            corrected[ref] = (pkg, d)
        out.append({"Designator": ref, "Mid X": r["PosX"], "Mid Y": r["PosY"],
                    "Layer": "Top" if r["Side"] == "top" else "Bottom", "Rotation": "%g" % rot})
    with open(FAB / "CPL_JLCPCB.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print("CPL:", len(out), "parts; rotation corrected:", {k: v[1] for k, v in sorted(corrected.items())})
    return corrected


def renders():
    for side in ("top", "bottom"):
        run("pcb", "render", "--side", side, "--background", "opaque", "--quality", "high", "-w", "2000", "-h", "1400",
            "-o", str(DOCS / f"render_{side}.png"), str(PCB))
    run("sch", "export", "pdf", "-o", str(DOCS / "lowpower_timer_schematic.pdf"), str(SCH))
    print("renders + schematic PDF done")


if __name__ == "__main__":
    gerbers()
    bom()
    cpl()
    renders()
