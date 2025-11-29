# SPECTRUM OPEN SOURCE - TECHNICAL SPECIFICATION (PHASE 2)
# VIABILITY & INTERFERENCE ANALYSIS ENGINE (ANATEL COMPLIANT)

## CONTEXT
This document defines the requirements for "Phase 2" of the Spectrum Open Source project. 
While Phase 1 focused on GIS/Database setup, Phase 2 implements the core RF Engineering logic required by Brazilian Regulations (Anatel).

## GOAL
Implement the backend logic to determine if a Proposed Station is viable or if it causes/suffers interference, strictly following Anatel Acts (FM & Digital TV).

## TECH STACK
- Language: Python 3.10+
- Database: PostgreSQL + PostGIS
- Math: NumPy, SciPy (for matrix operations)
- Async: Celery + Redis (for heavy diffraction calculations)
- GIS: Rasterio, PyCraf

---

## MODULE 2.1: NEIGHBOR DISCOVERY (SEARCH STRATEGY)

**Objective:** efficiently filter the database to find only relevant stations that could interact with the proposal.

**Logic:**
1. **Input:** `Proposal(lat, lon, frequency, type)`
2. **Spatial Filter (Radius):**
   - **FM Service:** 300 km radius (covers Class E3/Special interference range).
   - **TV Service (Digital):** 400 km radius (due to UHF tropospheric ducting).
3. **Spectral Filter (Frequency Mask):**
   - **FM:** Center Freq +/- 600 kHz (Co-channel, 1st, 2nd, and 3rd adjacencies).
   - **TV:** Center Channel +/- 1 Channel (Co-channel, Upper & Lower adjacencies).

**Implementation Requirement:**
- Use PostGIS `ST_DWithin` for the spatial filter (using Geography type for accuracy).
- Return a list of `NeighborCandidate` objects containing distance and azimuth relative to the proposal.

---

## MODULE 2.2: PROTECTION RATIO ENGINE (THE REGULATORY "LAW")

**Objective:** Enforce strict protection ratios (PR) as defined in the provided Anatel documents.

**Ruleset Implementation:**

### A. FM Radio (Stereo/Pilot Tone - Res. 67/Act 112)
| Frequency Offset | Protection Ratio (PR) |
| :--- | :--- |
| **0 kHz (Co-channel)** | **45 dB** |
| **+/- 200 kHz (1st Adj)** | **6 dB** |
| **+/- 400 kHz (2nd Adj)** | **-20 dB** |
| **+/- 600 kHz (3rd Adj)** | **-40 dB** |

### B. Digital TV (ISDB-T - Act 932)
| Channel Offset | Protection Ratio (PR) |
| :--- | :--- |
| **0 (Co-channel)** | **23 dB** |
| **-1 (Lower Adj)** | **-28 dB** |
| **+1 (Upper Adj)** | **-27 dB** |

**Deliverable:**
- Python Class `RegulatoryStandard` with a method `get_required_pr(service, offset)` that returns the specific dB value or raises an error if the offset is unregulated.

---

## MODULE 2.3: THE "FAIL-FAIL" CONTOUR ANALYSIS

**Objective:** A fast, theoretical check using ITU-R P.1546 curves to flag potential problems before running heavy terrain analysis.

**Physics Constants (Anatel Standard):**
1. **Protected Field Strength (E_min):**
   - FM (Urban): **66 dBuV/m**
   - TV (UHF): **48 dBuV/m**
   - TV (VHF-High): **51 dBuV/m**
2. **Interfering Threshold (E_int):**
   - Formula: `E_int = E_min - ProtectionRatio`
   - *Example:* For FM Co-channel (45dB), `E_int = 66 - 45 = 21 dBuV/m`.

**Algorithm:**
1. Calculate **Protected Radius (Rp)** for the Proposal using P.1546 (50% Time, 50% Loc).
2. Calculate **Interfering Radius (Ri)** for the Neighbor using P.1546 (1% Time, 50% Loc).
3. **Check:** If `Distance(Tx_A, Tx_B) < (Rp + Ri)`, mark as **FAIL** (Potential Interference).

**Deliverable:**
- Function `analyze_contours(proposal, neighbors)` returning a filtered list of "Critical Neighbors".

---

## MODULE 2.4: THE "DEYGOUT" MATRIX (TERRAIN DIFFRACTION CORE)

**Objective:** The definitive viability proof. Overrides Module 2.3 by proving terrain obstacles block the interference.

**Methodology:**
1. **Grid Generation:** Create a mesh of points (e.g., 100m resolution) inside the Victim's Protected Contour.
2. **Profile Extraction:** For every grid point, extract the elevation profile from SRTM files (`./SRTM`) to both Transmitters (Wanted and Unwanted).
3. **Path Loss Calculation:**
   - Apply **Deygout Method (91)** (Multiple Knife-Edge Diffraction).
   - `Loss_Total = Loss_FreeSpace + Loss_Diffraction`.
4. **Margin Calculation (Vectorized with NumPy):**
   - `Signal_Wanted = ERP_Victim - Loss_Total_Victim`
   - `Signal_Unwanted = ERP_Interferer - Loss_Total_Interferer`
   - `Margin = Signal_Wanted - Signal_Unwanted`
5. **Verdict:**
   - If `Margin < ProtectionRatio`: **POINT IS RED (Interference)**.
   - If `Margin >= ProtectionRatio`: **POINT IS GREEN (Safe)**.

**Deliverables:**
- A generic Heatmap Image (PNG) showing the interference zones.
- A JSON summary: `{"impacted_area_km2": 15.4, "impacted_population": 5200}` (using the Phase 1 Vector DB).

---

## INSTRUCTIONS FOR AI DEVELOPER
1. **Validation:** Ensure all inputs (ERP, Height) are validated against Class limits (e.g., Class C cannot have 50kW).
2. **Performance:** Use Python `multiprocessing` for Module 2.4 calculations.
3. **Fallback:** If SRTM data is missing, fallback to Free Space Loss and log a warning.
4. **Architecture:** Keep FM and TV logic separated via the Strategy Pattern.