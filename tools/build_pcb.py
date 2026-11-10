"""Build the 2-layer board from the schematic netlist with the pcbnew API (KiCad 10).

Steps:  place      footprints from design/lowpower_timer.net at the coordinates in PLACE, nets, outline, rules
        dsn        export Specctra DSN for Freerouting
        autoroute  run Freerouting until the board (with its GND pours) has no unconnected item
        silk       references and labels that clear pads and other silk
Run: "C:/Program Files/KiCad/10.0/bin/python.exe" tools/build_pcb.py place dsn autoroute silk
(JAVA must point to a Java 25 runtime for Freerouting 2.5.0)
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
PCB = ROOT / "lowpower_timer.kicad_pcb"
NET = ROOT / "design" / "lowpower_timer.net"
REP = ROOT / "reports"
FPLIB = Path(r"C:\Program Files\KiCad\10.0\share\kicad\footprints")
JAVA = os.environ.get("JAVA", "java")
FR_DIR = Path.home() / "Documents/KiCad/10.0/3rdparty/plugins/app_freerouting_kicad-plugin/jar"
FR_JAR = os.environ.get("FREEROUTING_JAR") or str(max(FR_DIR.glob("freerouting-*.jar"), key=lambda p: p.stat().st_mtime))
mm = pcbnew.FromMM
W, H = 50.0, 40.0

# JLCPCB 2-layer standard process: 0.1/0.1 mm track/space, via 0.3/0.5 mm minimum (jlcpcb.com/capabilities).
# Drawn with margin: 0.15 mm clearance, 0.2 mm signal track, 0.3/0.6 mm vias.
CLEARANCE, TRACK, VIA_D, VIA_DRILL, EDGE = 0.15, 0.20, 0.6, 0.3, 0.3
POWER = {"GND": 0.3, "+3V3": 0.3, "VIN": 0.4, "BAT_RAW": 0.5, "SW_NODE": 0.4, "BUZ_N": 0.4}  # 0.4 still enters the 0.65 mm pitch WSON pads
PROJECT_LIBS = {"lowpower_timer": ROOT / "lowpower_timer.pretty"}

# ref: (x, y, rotation) of the footprint origin in board mm, y down
PLACE = {
    # battery in on the left edge, reverse-polarity FET, boost along the top
    "J1": (5.2, 20.0, 270), "Q1": (12.5, 13.5, 0), "R1": (12.5, 17.0, 0),
    # boost: L1 and the input cap on the VIN/SW side (pins 4-6), output caps on the VOUT side (pins 1-3)
    "U1": (24.0, 9.5, 0), "L1": (27.6, 9.4, 90), "C1": (26.0, 6.3, 0),
    "C2": (20.6, 8.6, 180), "C3": (20.6, 10.4, 180),
    "R2": (20.6, 13.0, 180), "R3": (20.6, 14.5, 180), "R4": (23.6, 13.0, 90),
    # power button, latch diodes, button sense, bottom left
    "SW1": (10.0, 34.0, 0), "R5": (16.0, 30.0, 90), "D1": (18.5, 25.0, 0), "R6": (18.5, 21.5, 0),
    "R7": (21.5, 28.0, 90),
    # MCU in the middle with its crystal, decoupling and Tag-Connect
    "U2": (30.0, 25.0, 0),
    "Y1": (23.5, 21.5, 0), "C9": (22.0, 19.0, 90), "C10": (25.0, 19.0, 90),
    "C4": (35.0, 21.0, 90), "C5": (36.5, 21.0, 90), "C8": (24.5, 24.0, 0), "C7": (24.5, 25.5, 0),
    "C6": (24.5, 27.0, 0), "R8": (28.0, 19.5, 0),
    "J2": (31.0, 35.5, 0),
    # switched battery divider, top right
    "Q3": (38.0, 6.0, 0), "R9": (41.5, 4.0, 0), "Q4": (42.0, 9.0, 0), "R10": (45.5, 10.0, 90),
    "R11": (38.0, 10.0, 90), "R12": (38.0, 13.5, 90), "C11": (40.0, 13.5, 90),
    # buzzer and its driver, right; LEDs at the bottom edge
    "BZ1": (43.0, 25.0, 0), "D2": (44.5, 17.0, 0), "Q5": (36.5, 32.5, 0), "R13": (35.0, 29.0, 90),
    "R14": (39.5, 33.0, 90),
    "D3": (20.0, 37.0, 90), "R15": (20.0, 33.5, 90), "D4": (23.5, 37.0, 90), "R16": (23.5, 33.5, 90),
    "H1": (2.5, 2.5, 0), "H2": (W - 2.5, 2.5, 0), "H3": (2.5, H - 2.5, 0), "H4": (W - 2.5, H - 2.5, 0),
}
PLANES = {}


def V(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def read_netlist():
    """components {ref: dict(fpid, value, uuid, fields)} and nets {name: [ref.pin]} from the kicad-cli netlist"""
    t = NET.read_text(encoding="utf-8")
    comps = {}
    for blk in re.split(r"\(comp\s+\(ref", t.split("(components", 1)[1].split("(libparts", 1)[0])[1:]:
        ref = re.match(r'\s*"([^"]+)"', blk).group(1)
        fields = dict(re.findall(r'\(field\s+\(name\s+"([^"]+)"\)\s+"([^"]*)"\)', blk))
        comps[ref] = dict(fpid=re.search(r'\(footprint\s+"([^"]+)"\)', blk).group(1),
                          value=re.search(r'\(value\s+"([^"]*)"\)', blk).group(1),
                          # the symbol uuid is the last tstamps entry; the sheetpath one is "/" on a root sheet
                          uuid=[u for u in re.findall(r'\(tstamps\s+"([^"]+)"\)', blk) if u != "/"][-1],
                          fields=fields)
    nets = {}
    for blk in re.split(r"\(net\s+\(code", t.split("(nets", 1)[1])[1:]:
        name = re.search(r'\(name\s+"([^"]*)"\)', blk).group(1)      # keep "/NET": parity compares names
        nets[name] = ["%s.%s" % n for n in re.findall(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)', blk)]
    return comps, nets


def nn(net):
    """board net name for a schematic label name: local labels on the root sheet are "/NAME" """
    return net if net.startswith(("/", "unconnected-")) else "/" + net


def bare(net):
    return net.lstrip("/")


def outline(b):
    pts = [(0, 0), (W, 0), (W, H), (0, H)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        s.SetStart(V(x1, y1))
        s.SetEnd(V(x2, y2))
        b.Add(s)


def zone(b, net, layer, clearance=0.25, priority=0):
    z = pcbnew.ZONE(b)
    z.SetLayer(layer)
    z.SetNetCode(b.FindNet(nn(net)).GetNetCode())
    z.SetLocalClearance(mm(clearance))
    z.SetMinThickness(mm(0.2))
    z.SetThermalReliefGap(mm(0.3))
    z.SetThermalReliefSpokeWidth(mm(0.4))
    z.SetAssignedPriority(priority)
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)  # solid: assembled by JLC, and 0.5 mm FPC pads starve spokes
    o = z.Outline()
    o.NewOutline()
    for x, y in [(0, 0), (W, 0), (W, H), (0, H)]:
        o.Append(mm(x), mm(y))
    z.thisown = 0
    b.Add(z)
    return z


def netclass(ds, name, width, clearance=CLEARANCE, nets=()):
    nc = pcbnew.NETCLASS(name)
    nc.SetTrackWidth(mm(width))
    nc.SetClearance(mm(clearance))
    nc.SetViaDiameter(mm(VIA_D))
    nc.SetViaDrill(mm(VIA_DRILL))
    ns = ds.m_NetSettings
    ns.SetNetclass(name, nc)
    for n in nets:
        ns.SetNetclassPatternAssignment(nn(n), name)
    return nc


def place():
    comps, nets = read_netlist()
    assert set(comps) == set(PLACE), ("placement does not match netlist", set(comps) ^ set(PLACE))
    pinnet = {p: n for n, pins in nets.items() for p in pins}
    b = pcbnew.NewBoard(str(PCB))
    b.SetCopperLayerCount(2)
    ni = {}
    for name in nets:
        ni[name] = pcbnew.NETINFO_ITEM(b, name)
        b.Add(ni[name])
    for ref, c in comps.items():
        lib, name = c["fpid"].split(":")
        fp = pcbnew.FootprintLoad(str(PROJECT_LIBS.get(lib, FPLIB / f"{lib}.pretty")), name)
        assert fp is not None, c["fpid"]
        fp.SetFPIDAsString(c["fpid"])
        fp.SetReference(ref)
        fp.SetValue(c["value"])
        fp.SetPath(pcbnew.KIID_PATH("/" + c["uuid"]))
        for k, val in c["fields"].items():              # every symbol field, so schematic parity finds no mismatch
            if k in ("Footprint", "Reference", "Value"):
                continue
            fp.SetField(k, val)
            fp.GetField(k).SetVisible(False)
        x, y, rot = PLACE[ref]
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(V(x, y))
        if ref.startswith("H") or ref == "J2":       # J2 = Tag-Connect pads, nothing to buy
            fp.Reference().SetVisible(ref == "J2")
            fp.SetExcludedFromBOM(False)    # match the symbol; holes are dropped from the JLC BOM by the export step
        fp.thisown = 0
        b.Add(fp)
        for pad in fp.Pads():
            key = f"{ref}.{pad.GetNumber()}"
            if key in pinnet:
                pad.SetNet(ni[pinnet[key]])
        on_board = {f"{ref}.{p.GetNumber()}" for p in fp.Pads()}
        missing = {k for k in pinnet if k.split(".")[0] == ref} - on_board
        assert not missing, missing
    outline(b)
    ds = b.GetDesignSettings()
    ds.m_TrackMinWidth = mm(0.15)
    ds.m_MinClearance = mm(CLEARANCE)
    ds.m_ViasMinSize = mm(0.45)
    ds.m_MinThroughDrill = mm(0.2)
    ds.m_ViasMinAnnularWidth = mm(0.13)
    ds.m_CopperEdgeClearance = mm(EDGE)
    ds.m_HoleToHoleMin = mm(0.25)
    ds.m_SolderMaskExpansion = mm(0.05)
    d = ds.m_NetSettings.GetDefaultNetclass()
    d.SetClearance(mm(CLEARANCE))
    d.SetTrackWidth(mm(TRACK))
    d.SetViaDiameter(mm(VIA_D))
    d.SetViaDrill(mm(VIA_DRILL))
    for net, w in POWER.items():
        netclass(ds, "PWR_%s" % net.strip("+"), w, nets=[net])
    for layer, net in PLANES.items():
        zone(b, net, layer)
    pcbnew.SaveBoard(str(PCB), b)
    print("placed", len(comps), "footprints,", len(nets), "nets")


def track(b, net, pts, width, layer=pcbnew.F_Cu):
    n = b.FindNet(nn(net))
    assert n, net
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1))
        t.SetEnd(V(x2, y2))
        t.SetWidth(mm(width))
        t.SetLayer(layer)
        t.SetNet(n)
        t.SetLocked(True)
        b.Add(t)


def via(b, net, x, y, d=VIA_D, drill=VIA_DRILL):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(V(x, y))
    v.SetWidth(pcbnew.F_Cu, mm(d))
    v.SetDrill(mm(drill))
    v.SetNet(b.FindNet(nn(net)))
    v.SetLocked(True)
    b.Add(v)


def pad_xy(b, ref, num):
    for p in b.FindFootprintByReference(ref).Pads():
        if p.GetNumber() == num:
            return p.GetPosition().x / 1e6, p.GetPosition().y / 1e6
    raise KeyError(ref + "." + num)


def _seg_dist(p, a, b_):
    """distance from point p to segment a-b_"""
    import math
    ax, ay = a
    bx, by = b_
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _box_dist(p, box):
    """distance from point p to an axis-aligned box (x0, y0, x1, y1)"""
    import math
    x0, y0, x1, y1 = box
    return math.hypot(max(x0 - p[0], 0, p[0] - x1), max(y0 - p[1], 0, p[1] - y1))


def export_dsn():
    """Specctra DSN for Freerouting. KiCad writes one class per net class, so the power widths come through."""
    REP.mkdir(exist_ok=True)
    b = pcbnew.LoadBoard(str(PCB))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    dsn = REP / "route.dsn"
    assert pcbnew.ExportSpecctraDSN(b, str(dsn)), "DSN export failed"
    t = dsn.read_text(encoding="utf-8")
    # Freerouting keeps only the track clearance to the outline: pull its routing boundary in by EDGE so copper stays
    # EDGE mm inside the board (the outline itself is unchanged)
    e = int(EDGE * 1000)
    w, h = int(W * 1000), int(H * 1000)
    t, n = re.subn(r"\(boundary\n(\s+)\(path pcb 0 [^)]*\)",
                   lambda m: f"(boundary\n{m.group(1)}(path pcb 0  {w - e} {-h + e}  {e} {-h + e}  {e} {-e}  "
                             f"{w - e} {-e}  {w - e} {-h + e})", t, count=1)
    assert n == 1, "DSN boundary not found"
    dsn.write_text(t, encoding="utf-8")
    print("DSN layers:", re.findall(r"\(layer (\S+)\s*\(type (\w+)\)", t), "keepouts:", t.count("(keepout"))


def route():
    ses = REP / "route.ses"
    if ses.exists():
        ses.unlink()
    with open(REP / "freerouting.log", "w") as log:
        subprocess.run([JAVA, "-jar", FR_JAR, "-de", str(REP / "route.dsn"), "-do", str(ses), "-mp", "100",
                        "--gui.enabled=false",
                        "--router.automatic_neckdown=false"],  # neck-downs land below the 0.15 mm minimum
                       check=True, timeout=1500, stdout=log, stderr=subprocess.STDOUT)
    assert ses.exists(), "Freerouting wrote no SES, see reports/freerouting.log"


SILK_H, SILK_T = 0.9, 0.15        # JLC silkscreen: text height >= 0.8 mm, line >= 0.15 mm
KEEP_REF = ("U", "J", "SW", "D", "Q", "L", "BZ", "Y")
# (text, [(x, y) candidates]) - first candidate that clears pads, other silk and the board edge wins
LABELS = [
    ("LP timer", [(42.0, 36.5), (31.0, 39.0)]),
    ("ON/OFF", [(10.0, 30.0), (10.0, 38.3)]),
    ("2xAA", [(4.6, 13.0), (4.6, 27.0)]),
    ("SWD", [(31.0, 31.5), (36.0, 35.5)]),
    ("STAT", [(20.0, 39.0), (17.0, 37.0)]),
    ("ALARM", [(23.5, 31.0), (27.0, 37.0), (26.5, 33.5)]),
]


def _bbox_mm(item):
    bb = item.GetBoundingBox()
    return bb.GetLeft() / 1e6, bb.GetTop() / 1e6, bb.GetRight() / 1e6, bb.GetBottom() / 1e6


def _silk_boxes(g):
    """thin boxes along each stroke of a silk shape; a rectangle or polygon outline must not block its interior"""
    w = g.GetWidth() / 2e6
    if g.GetShape() in (pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_POLY):
        pts = [(c.x / 1e6, c.y / 1e6) for c in g.GetCorners()]
        edges = list(zip(pts, pts[1:] + pts[:1]))
    elif g.GetShape() == pcbnew.SHAPE_T_SEGMENT:
        edges = [((g.GetStart().x / 1e6, g.GetStart().y / 1e6), (g.GetEnd().x / 1e6, g.GetEnd().y / 1e6))]
    else:
        return [_bbox_mm(g)]
    return [(min(a[0], c[0]) - w, min(a[1], c[1]) - w, max(a[0], c[0]) + w, max(a[1], c[1]) + w) for a, c in edges]


def _hit(a, b_, m):
    return not (a[2] + m < b_[0] or b_[2] + m < a[0] or a[3] + m < b_[1] or b_[3] + m < a[1])


def silk():
    """Readable, DRC-clean silkscreen: references only on parts a person looks for (connectors, ICs, buttons,
    LEDs), a few function labels, battery polarity at J2. Passive references stay on F.Fab for the assembly
    drawing. Library footprints are left untouched (their silk past the board edge at J1/J4 is by design)."""
    b = pcbnew.LoadBoard(str(PCB))
    assert not [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.F_SilkS], "silk already done: run from place"
    pads = [_bbox_mm(p) for fp in b.GetFootprints() for p in fp.Pads()]
    placed = [bx for fp in b.GetFootprints() for g in fp.GraphicalItems()             # footprint silk strokes
              if g.GetLayer() == pcbnew.F_SilkS and isinstance(g, pcbnew.PCB_SHAPE) for bx in _silk_boxes(g)]
    for fp in b.GetFootprints():
        ref = fp.Reference()
        ref.SetTextSize(pcbnew.VECTOR2I(mm(SILK_H), mm(SILK_H)))
        ref.SetTextThickness(mm(SILK_T))
        keep = fp.GetReference().startswith(KEEP_REF) and not fp.GetReference().startswith("H")
        ref.SetVisible(keep)
    def free(bx):
        return not (any(_hit(bx, p, 0.15) for p in pads) or any(_hit(bx, q, 0.2) for q in placed)
                    or bx[0] < EDGE or bx[1] < EDGE or bx[2] > W - EDGE or bx[3] > H - EDGE)

    labels = list(LABELS)
    # battery polarity first: '+' beside J2 pin 1 (VBAT), '-' beside pin 2, just past the connector body
    crt = b.FindFootprintByReference("J1").GetCourtyard(pcbnew.F_CrtYd).BBox()
    for num, s in (("1", "+"), ("2", "-")):
        x, y = pad_xy(b, "J1", num)
        xr = crt.GetRight() / 1e6 + 0.8
        labels.insert(0, (s, [(xr, y), (xr + 0.5, y), (xr, y - 0.5 if num == "1" else y + 0.5), (xr + 1.5, y),
                                (xr + 2.5, y), (xr - 1.2, y - 1.6 if num == "1" else y + 1.6)]))
    skipped = []
    for s, cands in labels:
        for x, y in cands:
            t = pcbnew.PCB_TEXT(b)
            t.thisown = 0                                       # owned by the board once added; probes are kept
            t.SetText(s)
            t.SetLayer(pcbnew.F_SilkS)
            t.SetTextSize(pcbnew.VECTOR2I(mm(SILK_H if len(s) > 1 else 1.2), mm(SILK_H if len(s) > 1 else 1.2)))
            t.SetTextThickness(mm(SILK_T if len(s) > 1 else 0.2))
            t.SetPosition(V(x, y))
            bx = _bbox_mm(t)
            if not free(bx):
                continue
            b.Add(t)
            placed.append(bx)
            break
        else:
            skipped.append(s)
    # references of the parts people look for: library spot first, else just outside the courtyard
    for fp in b.GetFootprints():
        ref = fp.Reference()
        if not ref.IsVisible():
            continue
        c = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
        cx, cy = (c.GetLeft() + c.GetRight()) / 2e6, (c.GetTop() + c.GetBottom()) / 2e6
        spots = [None, (cx, c.GetTop() / 1e6 - 0.7), (cx, c.GetBottom() / 1e6 + 0.7),
                 (c.GetLeft() / 1e6 - 1.4, cy), (c.GetRight() / 1e6 + 1.4, cy)]
        for sp in spots:
            if sp:
                ref.SetTextAngleDegrees(0)
                ref.SetPosition(V(*sp))
            bx = _bbox_mm(ref)
            if free(bx):
                placed.append(bx)
                break
        else:
            ref.SetVisible(False)
    pcbnew.SaveBoard(str(PCB), b)
    print("silk refs shown:", sum(fp.Reference().IsVisible() for fp in b.GetFootprints()), "| labels not placed:", skipped)


def stitch_grid(b, pitch=4.0):
    """GND vias on a grid wherever one clears every other-net pad, track and via, so the top and bottom pours are
    one ground and no pour piece is left floating"""
    import math
    vias = [(t.GetPosition().x / 1e6, t.GetPosition().y / 1e6) for t in b.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    others = [_bbox_mm(p) for fp in b.GetFootprints() for p in fp.Pads() if p.GetNetname() != nn("GND")]
    holes = [_bbox_mm(p) for fp in b.GetFootprints() for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
    segs = [((t.GetStart().x / 1e6, t.GetStart().y / 1e6), (t.GetEnd().x / 1e6, t.GetEnd().y / 1e6), t.GetWidth() / 2e6)
            for t in b.GetTracks() if not isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() != nn("GND")]
    keep, r = [], VIA_D / 2
    y = 2.0
    while y < H - 1.5:
        x = 2.0
        while x < W - 1.5:
            p = (x, y)
            if (all(math.hypot(p[0] - a, p[1] - c) > 1.5 for a, c in vias + keep)
                    and all(_box_dist(p, bx) > r + CLEARANCE + 0.1 for bx in others + holes)
                    and all(_seg_dist(p, a, c) > r + CLEARANCE + 0.1 + hw for a, c, hw in segs)):
                keep.append(p)
            x += pitch
        y += pitch
    print("GND stitching vias:", len(keep))
    return keep


def finish():
    b = pcbnew.LoadBoard(str(PCB))
    assert pcbnew.ImportSpecctraSES(b, str(REP / "route.ses")), "SES import failed"
    zone(b, "GND", pcbnew.F_Cu)
    zone(b, "GND", pcbnew.B_Cu)
    for x, y in stitch_grid(b):
        via(b, "GND", x, y)
    b.BuildConnectivity()
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(str(PCB), b)
    b = pcbnew.LoadBoard(str(PCB))
    b.BuildConnectivity()
    n = b.GetConnectivity().GetUnconnectedCount(True)
    print("unconnected:", n, "| tracks+vias:", len(b.GetTracks()))
    return n


def autoroute(tries=8):
    """Freerouting is not deterministic: route from the same pre-route board until the result, with pours, has no
    unconnected item, then keep that SES (reports/route.ses) so the board can be rebuilt exactly"""
    import shutil
    pre = REP / "pre_route.kicad_pcb"
    shutil.copyfile(PCB, pre)
    for k in range(tries):
        route()
        shutil.copyfile(pre, PCB)
        if finish() == 0:
            print("routed clean on try", k + 1)
            return
    raise SystemExit("no clean route in %d tries" % tries)


if __name__ == "__main__":
    steps = {"place": place, "dsn": export_dsn, "route": route, "finish": finish, "silk": silk,
             "autoroute": autoroute}
    for s in sys.argv[1:]:
        steps[s]()
