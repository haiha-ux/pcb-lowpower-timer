"""Look up parts in the local JLCPCB parts database (bouni/kicad-jlcpcb-tools cache).
Usage: jlc.py <MFR part LIKE pattern> [more patterns...]  -> best-stocked matches per pattern."""
import sqlite3
import sys

DB = r"C:\Users\USER\Documents\KiCad\jlcpcb\jlcpcb_parts.db"
c = sqlite3.connect(DB)
for pat in sys.argv[1:]:
    print("==", pat)
    q = ("select LCSC, MFR_Part, Package, Library_Type, Stock, Price, Description from components "
         "where MFR_Part like ? order by (Library_Type='Basic') desc, Stock desc limit 6")
    for r in c.execute(q, (pat,)):
        print("  %-9s %-28s %-14s %-9s %8d  %s" % (r[0], r[1][:28], r[2][:14], r[3], r[4], r[6][:70]))
