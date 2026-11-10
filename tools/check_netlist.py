"""Compare the schematic netlist (exported by kicad-cli) with the intended connections in design/nets.json.
Every intended pin must sit on its named net, every net must contain exactly the intended pins, and every
pin of every part must be either on an intended net or listed in NO_CONNECT.  Exit 1 on any mismatch.
Usage: check_netlist.py <netlist.net> <nets.json>"""
import json
import re
import sys

net_txt = open(sys.argv[1], encoding="utf-8").read()
want = json.load(open(sys.argv[2], encoding="utf-8"))
no_connect = set(want.pop("_no_connect", []))

got = {}
blocks = re.split(r"\(net\s+\(code", net_txt.split("(nets", 1)[1])[1:]
for blk in blocks:
    name = re.search(r'\(name\s+"([^"]*)"\)', blk).group(1).lstrip("/")
    nodes = re.findall(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)', blk)
    got[name] = {"%s.%s" % n for n in nodes}
assert got, "no nets parsed from netlist"

errors = []
for net, pins in want.items():
    pins = {p for p in pins if not p.startswith("#")}
    g = got.get(net)
    if g is None:
        errors.append("missing net %s" % net)
        continue
    if g != pins:
        errors.append("net %s: extra %s, missing %s" % (net, sorted(g - pins), sorted(pins - g)))
for net, pins in got.items():
    if net.startswith("unconnected-") or net.startswith("Net-("):
        for p in pins:
            if p not in no_connect:
                errors.append("pin %s is on unnamed net %s and not declared no-connect" % (p, net))
    elif net not in want:
        errors.append("unexpected net %s %s" % (net, sorted(pins)))
print("nets checked:", len(want), "nets in netlist:", len(got))
for e in errors:
    print("ERROR", e)
sys.exit(1 if errors else 0)
