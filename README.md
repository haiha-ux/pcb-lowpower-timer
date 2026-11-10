# Low-power battery timer (2×AA, soft-latch power button, STM32L0, 2-layer)

A small timer board that runs from two AA cells. One button turns it on; the firmware keeps itself powered through
a HOLD pin and turns itself off on a long press. When it is off, the battery sees only leakage, about 0.5 µA typical.
The board has a 32.768 kHz RTC, a magnetic buzzer, two LEDs and a switched battery-voltage divider. It is a 2-layer
board, with every part carrying an LCSC number for JLCPCB assembly.

**Status: milestone 1, schematic only.** ERC is clean and the BOM is complete. Layout, fab outputs and renders come
next.

| File | What |
|---|---|
| `lowpower_timer.kicad_sch` | schematic (one A3 sheet) |
| `docs/lowpower_timer_schematic.pdf` | schematic as PDF |
| `fab/BOM_JLCPCB.csv` | BOM with LCSC number, MPN and JLC library type |
| `lowpower_timer.pretty/` | project footprint: Huaneng MLT-8530 buzzer |
| `design/nets.json` | intended connection of every pin, written by hand; `tools/check_netlist.py` compares the netlist with it |

## Design

### Battery and reverse polarity
- Two AA cells in an external holder, on a JST-PH 2-pin connector (J1, pin 1 = +). A 2×AA holder is about
  58 × 31 mm and would not fit a ≤ 50 × 40 mm board; the wired holder also lets the board sit in a lid.
- Reverse-polarity protection is Q1, a DMG2305UX P-MOSFET in the + lead (drain to the battery, source to VIN), with
  its gate tied to GND through R1 100 kΩ.
  - With the cells the right way round, the body diode conducts first, then VGS = −VBAT turns the channel on. At
    the end of life (VBAT = 1.8 V) VGS is −1.8 V, where RDS(on) is at most 200 mΩ (DMG2305UX datasheet p.2,
    VGS(th) −0.5 to −0.9 V). At about 200 mA input that is a 40 mV drop.
  - With the cells reversed, the body diode is reverse-biased and VGS ≈ 0, so nothing conducts.
  - No series diode: a Schottky would cost 0.2–0.3 V of the 1.8 V left at end of life.

### 3.3 V boost
- U1 TPS61099 (adjustable, WSON-6 2 × 2 mm). Two alkaline cells give at most 3.2 V and two lithium AA cells at most
  3.6 V. A boost is enough below 3.3 V, and the TPS61099 passes VIN through above that, so no buck-boost is needed.
  It runs down to 0.7 V input (datasheet p.5) and starts at 0.7 V with a light load (p.12).
- **Load disconnect is the reason for this part.** "In shutdown mode, the device stops switching and the
  rectifying PMOS fully turns off, providing the completed disconnection between input and output" (datasheet
  p.11). An ordinary boost lets VIN through the inductor and diode to VOUT when it is off, which would keep the MCU
  half-powered.
  - The TPS61097A-33 was rejected because it does the opposite: it connects the battery to the load in shutdown
    (its datasheet p.1).
- The 3.3 V-fixed TPS610994 is only sold on LCSC as a DSBGA, so the adjustable part sets the output with a divider:
  - VOUT = VREF × (1 + (R2 + R3)/R4) = 1.0 V × (1 + 2.3 MΩ/1 MΩ) = 3.30 V (datasheet eq. 1 p.15, VREF 1.00 V p.5).
  - The divider draws 1.0 µA. TI suggests R2's current be 100× the FB leakage for best accuracy (p.15); against
    the 10 nA typical leakage this meets it, against the 50 nA maximum it does not. The output may sit about 1 %
    high in the worst case, which the MCU (1.65–3.6 V) and buzzer do not care about.
- L1 2.2 µH (Würth 74404024022, one of TI's listed parts, p.14). CIN 10 µF, COUT 2 × 10 µF (p.6: COUT 10 µF min,
  20 µF nominal). 0603 10 V X5R parts lose capacitance at 3.3 V; at about 60 % retained, COUT is still ≈ 12 µF.

### Soft-latch power button
```
VIN ─SW1─┬─ BTN ──D1a──┐
         │             ├── EN (U1) ── R6 2M ── GND
         R5 1M  HOLD ──D1b┘   (D1 = BAT54C, common cathode)
         │      (PA1)
        GND   BTN ── R7 100k ── PA3 (button sense)
```
- **Press:** SW1 pulls BTN to VIN, D1a lifts EN, and the boost starts. The MCU boots and drives HOLD (PA1) high,
  which keeps EN high through D1b after the button is released.
- **Off:** firmware sees a long press on PA3 and drives HOLD low. EN falls once the button is released, VOUT is
  disconnected, and the MCU loses power.
- **EN high at the lowest battery voltage:** pressing at VBAT = 1.8 V gives EN ≈ 1.8 V − 0.1 V (BAT54 at about
  1 µA) = 1.7 V. VIH is 1.2 V for VIN > 1.5 V, and 0.8 × VIN below that (datasheet p.6).
- **EN low when released:** EN leakage is 50 nA max (p.6); through R6 2 MΩ that is 0.1 V, under VIL = 0.4 V. A 10 MΩ
  pull-down would have saved 1.3 µA in sleep but could leave EN at 0.5 V in the worst case, so it was not used.
- **Button sense:** PA3 is an FT (5 V-tolerant) pin (DS10668 Table 15 p.40), so BTN at 3.2–3.6 V (fresh alkaline or
  lithium cells) is safe on it, and R7 limits the current if the MCU is unpowered during a press.
- **Diode leakage:** when the board is off, every node of the latch sits at 0 V, so the diodes carry no reverse
  current. When it is on, D1a is reverse-biased by about 3.2 V. BAT54C reverse current at 3 V is about 0.1 µA at
  25 °C and about 3 µA at 75 °C (Diodes BAT54 datasheet ds11005, Fig. 2 p.3, read off the curve). That current
  counts in the sleep budget only.

### MCU and RTC
- U2 STM32L031F6P6 (TSSOP-20). I chose it over the ATtiny1616 for two reasons.
  - Stop mode with the RTC on the 32.768 kHz crystal draws about 0.55 µA at 3 V (DS10668 Fig. 18 p.63; the
    feature list on p.1 says 0.6 µA). The ATtiny1616's equivalent figure is 0.69 µA (DS40002204A Table 36-5 p.508).
  - The ATtiny's 32k oscillator wants CL 7.5–12.5 pF with ESR at most 40–80 kΩ (Table 36-14 p.512), and the common
    Epson crystal's 70 kΩ typical ESR sits at that edge.
  - SWD also gives breakpoint debugging. Programming is through a Tag-Connect TC2030-NL footprint (no part to buy).
- **Pin use:**

| Function | Pin |
|---|---|
| HOLD | PA1 (7) |
| Button sense | PA3 (9), FT |
| VBAT ADC | PA4 (10), ADC_IN4 |
| Battery-sense enable | PA5 (11) |
| Buzzer PWM | PA6 (12), TIM22_CH1, FT |
| LEDs | PA7 (13) / PB1 (14) |
| SWD | PA13 (19) / PA14 (20) |
| Unused, left open | PA0, PA2, PA9, PA10 |

  - BOOT0 is pulled to GND by R8 10 kΩ, and NRST has 100 nF to GND.
- **Crystal:** Y1 Epson FC-135, 32.768 kHz, CL = 7 pF; C0 1.0 pF typ, ESR 70 kΩ typ (FC-135 specification p.2).
  - Oscillator margin, after ST AN2867: gm_crit = 4 · ESR · (2π f)² · (C0 + CL)² = 4 · 70 kΩ · (2π · 32768 Hz)² ·
    (8 pF)² = 0.76 µA/V.
  - The L031's LSE "medium high" drive is rated for crystals up to 1.7 µA/V (DS10668 Table 40 p.71), so it is
    used there with 2.2× headroom.
  - The 12.5 pF version of the same crystal (JLC Basic) would need 2.16 µA/V, which only the highest drive level
    (2.7 µA/V) covers. That leaves 25 % headroom at typical ESR, and the crystal has no maximum ESR listed.
- **Load capacitors:** CL = C9·C10/(C9 + C10) + Cstray. With Cstray ≈ 3.5 pF (pin plus trace; an assumption, ST
  suggests 2–7 pF), C9 = C10 = 2 × (7 − 3.5) = 7 pF; 6.8 pF C0G is used. Trim with the RTC calibration register if
  needed.

### Battery voltage measurement
- Q3 (AO3401A) is a high-side switch in front of the R11/R12 100 kΩ/100 kΩ divider. Q4 (2N7002) pulls Q3's gate
  down only while PA5 is high. R9 holds Q3 off and R10 holds Q4 off when the MCU is unpowered or in reset.
- Off, the divider draws nothing. Because the divider is cut off at the top, VBAT never reaches PA4 through it while
  the MCU is unpowered (a low-side switch would leave PA4 at VBAT through R11 and leak into the pin's protection
  diode).
- PA4 sees at most 1.8 V. C11 100 nF holds the node for the ADC sample.

### Buzzer and LEDs
- BZ1 Huaneng MLT-8530 magnetic buzzer: 2.7 kHz, 16 Ω coil, 95 mA max at 5 Vp-p (specification p.2).
  - It is driven exactly as its specification p.3 shows: NPN with a 470 Ω base resistor and a 1N4148 across the
    coil (Q5 MMBT3904, D2 1N4148W).
  - R14 100 kΩ keeps Q5 off while PA6 floats during reset.
- D3/D4 red 0603 LEDs with 1 kΩ (≈ 1.3 mA each); red is the only JLC Basic 0603 LED.

## Current budget

### OFF (battery connected, boost disabled), VBAT 2.4–3.2 V

| Item | Typical | Datasheet maximum | Source |
|---|---|---|---|
| TPS61099 shutdown current into VIN | 0.5 µA | 1.6 µA (at VIN 3.7 V) | TPS61099 datasheet p.5 |
| Q3 AO3401A off (IDSS) | not given (nA range) | 1 µA (at VDS −30 V) | AO3401A datasheet p.2 |
| Q4 2N7002 off (IDSS), through R9 | not given | 1 µA (at VDS 60 V), datasheet not read | NOT VERIFIED |
| Q1 gate leakage through R1 | not given | IGSS, datasheet value not read | NOT VERIFIED |
| Button network, D1, R5, R6, R7 | 0 (all nodes at 0 V) | 0 | by construction |
| MCU, divider, buzzer, LEDs | 0 (VOUT disconnected) | 0 | TPS61099 p.11 |
| **Total** | **≈ 0.5 µA** | **≈ 3.6 µA** (worst-case specs taken at far higher voltages than 3.2 V) | |

The ≤ 1 µA target is met at typical values. The guaranteed maxima are specified at 20–60 V and 3.7 V, so they
overstate the real worst case at 3.2 V, but no datasheet bounds it lower. I have not measured it.

### Sleep (on, MCU in Stop with RTC, HOLD high, everything else off), from the 3.3 V rail

| Item | Current | Source |
|---|---|---|
| STM32L031 Stop + RTC (LSE) | ≈ 0.55 µA | DS10668 Fig. 18 p.63 (measured with LSE low drive; medium-high drive adds an unquantified amount) |
| FB divider R2+R3+R4 | 1.0 µA | 3.3 V / 3.3 MΩ |
| EN pull-down R6 (EN ≈ 3.2 V) | 1.6 µA | 3.2 V / 2 MΩ |
| D1a reverse leakage (≈ 3.2 V) | 0.1 µA at 25 °C, ≈ 3 µA at 75 °C | Diodes ds11005 Fig. 2 p.3 |
| TPS61099 IQ from VOUT | 0.6 µA | TPS61099 datasheet p.5 |
| **Total on 3.3 V** | **≈ 3.9 µA** at 25 °C | |

From the battery, at VIN 2.4 V: TPS61099 IQ from VIN is 0.4 µA (p.5). The efficiency curve gives 83 % at 10 µA load
(VIN 2.5 V, VOUT 3.3 V, Fig. 6-5 p.7), and it falls further at 4 µA; with an assumed 70 %, the battery current is
0.4 + 3.9 × 3.3 / (2.4 × 0.70) ≈ **8 µA**.

### Alarm (buzzer sounding, MCU running at 1 MHz, alarm LED on)

| Item | Current | Source |
|---|---|---|
| Buzzer, 2.7 kHz 50 % square wave | ≤ 95 mA (rated maximum at 5 Vp-p; lower at 3.3 V, not measured) | MLT-8530 spec p.2 |
| MCU Run, 1 MHz, Range 3 | 140 µA | DS10668 Table 24 p.57 |
| LED D4 | 1.3 mA | (3.3 − 2.0) V / 1 kΩ |
| **Total on 3.3 V** | **≤ 97 mA** | |

From the battery at 2.4 V and 93.6 % efficiency at 50 mA (Fig. 6-5 p.7, the closest point read): ≤ 97 × 3.3 /
(2.4 × 0.93) ≈ **143 mA**.

### Battery life with 2000 mAh AA cells

Example: always on, sleeping, with a 1 ms wake every second at 140 µA (+0.14 µA average) and 30 s of alarm per day.

| Part | Per day |
|---|---|
| Sleep, 8.1 µA × 24 h | 0.19 mAh |
| Alarm, 143 mA × 30 s | 1.19 mAh |
| **Total** | **≈ 1.4 mAh/day** |

- With 80 % of 2000 mAh usable, that is about 1150 days, **roughly 3 years**.
- At 140 mA an alkaline AA delivers noticeably less than its rated capacity, so the alarm share is optimistic.
- Switched OFF at 0.5 µA, the cells' own self-discharge (a few % per year) is larger than the board's draw.

## Checks done so far

- `kicad-cli sch erc` (10.0.8): 0 errors, 0 warnings.
- `tools/check_netlist.py`: 29 named nets match `design/nets.json` pin for pin, and every other pin is declared
  no-connect. On the first pass it caught a schematic short between LED2 and GND that came from overlapping label
  stubs; the netlist now matches.

## Not checked yet

- Nothing has been built or measured: OFF current, sleep current, soft-latch timing, crystal start-up, buzzer
  current at 3.3 V.
- The MLT-8530 land pattern is drawn from the maker's outline drawing (spec p.2), which has no recommended pads.
  Check it against the part before ordering.
- 2N7002 IDSS and DMG2305UX IGSS were not read from their datasheets.
- PCB, DRC, fab outputs: next milestone.
