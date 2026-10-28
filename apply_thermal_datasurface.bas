'==============================================================================
' APPLY THERMAL CLOUD TO MIRROR NODES VIA A DATA SURFACE
'==============================================================================
'
' PURPOSE
'   Take the x,y,z,T point cloud written by npz_to_3D.py and apply it to a
'   mirror's FE nodes as nodal temperatures, automatically and repeatably --
'   no manual Data Surface Editor clicking.  The macro:
'     1. builds an Arbitrary 3D Data Surface from the cloud,
'     2. sets the interpolation the way we validated (nearest neighbor),
'     3. creates a fresh load set,
'     4. evaluates the surface at every selected mirror node and writes the
'        resulting temperatures as nodal temperature loads in one bulk call.
'
' WHY A DATA SURFACE + "EVALUATE PER NODE"
'   FEMAP's Data Surface does inverse-distance-weighted (IDW) interpolation from
'   the scattered cloud points onto any location.  Two options control it:
'     InterpPercent = % of candidate locations to include in the weighting
'     InterpMin     = minimum number of locations to include
'   Setting InterpPercent = 0 and InterpMin = 1 makes it a NEAREST-NEIGHBOR
'   lookup: each target takes the value of the single closest cloud point, with
'   no averaging.  That is what we want here -- the thermal field is essentially
'   a pure through-thickness gradient (uniform across the mirror face), so
'   nearest neighbor reproduces it cleanly and avoids the smeared/rippled result
'   that multi-point IDW produces on a sparse or ring-shaped source.
'
'   Rather than leave each load "live-linked" to the surface, we EVALUATE the
'   surface at each node (ds.Evaluate + ds.GetData) and store the resulting
'   number as a plain nodal temperature.  This uses FEMAP's own IDW math -- so
'   it matches exactly what you would get in the Data Surface Editor with the
'   same options -- but the load set ends up self-contained (just numbers), and
'   there is no dependence on the surface persisting or being re-evaluated.
'
' COORDINATE FRAME / ASSUMPTIONS
'   * The cloud from npz_to_3D.py is in the MIRROR-LOCAL frame: origin at the
'     vertex, +Z along the optical axis, x/y in the mirror plane.
'   * You enter that mirror-local CSys ID at the prompt.  It is stored on the
'     Data Surface (ds.csys), so when we feed GLOBAL node coordinates to
'     ds.GetData, FEMAP evaluates them in the correct frame.
'   * The CSys must be RECTANGULAR (the macro checks this); a cylindrical or
'     spherical CSys would make FEMAP read the columns as (R, theta, Z).
'   * You select the mirror NODES to receive the temperatures.
'   * Model length units are assumed to match the file (inches); SCALE_FAC is
'     available if not.  Temperatures carry through as-is (Kelvin in the data).
'
' OUTPUT
'   * One Arbitrary 3D Data Surface (kept in the model; handy to re-inspect).
'   * One new load set named "<LS_TITLE_PFX>_<tag>" holding the nodal temps.
'   Re-running makes a NEW surface and load set (named by your tag), so previous
'   runs are never clobbered.
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (Ctrl+F11), paste, then Run.
'
' STATUS
'   Verified working in FEMAP (2026-10-26). Notes from first run, now baked in:
'     * Nodal temps are created with feLoadNTemp.PutArray -- you must set the
'       object's setID (the load set) and color FIRST; the active-set flag alone
'       is not enough. (The fuller PutAllArray needs the same setID and was
'       dropped in favor of the simpler PutArray.)
'     * The load set is also made active via Info_ActiveID(FT_LOAD_DIR) for
'       convenience (so it shows as active in the UI).
'     * ds.Evaluate(0,0,0,False) then ds.GetData(0, id, Coord, ans, vXYZ)
'       returns the interpolated temperature in the scalar "ans".
'     * Mirror nodes are chosen with es.Select(FT_NODE, ...).
'     * WinWrap note: do NOT combine Dim with Set/ReDim on one line via ":".
'
' CURVED SURFACES (SAG_MODE)
'   Mirror optical surfaces are not always flat. The flat cloud stores T vs depth
'   below a flat vertex plane; a curved surface sits at z = sag(x,y). The chosen
'   (interim) rule: shift each through-thickness column straight DOWN the optical
'   axis by the local sag, so a node whose axial depth below the curved surface is
'   d still gets the flat column's T(d). ONLY z is shifted -- x and y are kept, so
'   nothing moves sideways. Depth is axial (along z), not along the surface normal;
'   for steep ("fast") mirrors that is a small, flagged approximation.
'     SAG_MODE = 0  flat     -> sag = 0 (exact original behavior)
'     SAG_MODE = 1  conic    -> sag from ROC + conic constant K (sign of ROC sets
'                               convex vs concave); sphere/parabola/ellipse/hyperbola
'     SAG_MODE = 2  geometry -> sag read from a modeled optical Surface by projecting
'                               each node onto it (any shape; asphere/measured/freeform)
'   Sag is computed in the vertex-local frame (each node is transformed global->local
'   so radius and depth are correct), z is shifted there, then transformed back to
'   global for GetData. A fast-mirror warning prints when the rim surface tilt exceeds
'   SLOPE_WARN_DEG, with the estimated axial-vs-normal depth error.
'
' API CALLS USED (Femap API Guide 2025)
'   feFemap, feDataSurf (csys, vector, InterpPercent, InterpMin,
'     VarArbitrary3DCoord, PutDataSurf, GetDataSurf, Evaluate, GetData),
'   feLoadSet (Put), feLoadNTemp (setID, color, PutArray), feNode (Get, x/y/z),
'   feSet (Select, First, Next), feCSys (Get, type),
'   feSurface (Get, XYZToParam, ParamToXYZ), feCoordTransform,
'   Info_NextID, Info_ActiveID, feAppLock/feAppUnlock, feViewRegenerate
'==============================================================================


'------------------------------------------------------------------------------
' USER SETTINGS
'------------------------------------------------------------------------------
Const INTERP_PERCENT As Double = 0#     ' Data Surface: % of locations to include (0 = nearest)
Const INTERP_MIN     As Long   = 1      ' Data Surface: min locations (1 = nearest neighbor)
Const SCALE_FAC      As Double = 1.0    ' scales file coords on read (0.0393701 = mm->in)
Const LOAD_COLOR     As Long   = 10     ' Femap palette index for the created loads
Const DS_TITLE_PFX   As String = "ThermalCloud"
Const LS_TITLE_PFX   As String = "ThermalMap"

' ---- curved-surface (sag) settings ----
Const SAG_MODE As Long       = 1      ' 0 = flat, 1 = conic (ROC/K), 2 = geometry (Surface ID)
Const Z_SIGN   As Double     = -1.0   ' depth direction into the substrate (confirm on first run)
Const MIRROR_ROC As Double   = 0.0    ' vertex radius of curvature; sign sets convex/concave; 0 = flat
Const MIRROR_K   As Double   = 0.0    ' conic constant: 0 sphere, -1 parabola, <-1 hyperbola, >0 oblate
Const SLOPE_WARN_DEG As Double = 7.0  ' warn if rim surface tilt exceeds this (fast mirror)

' ---- module-level sag state (set in Main, read by SagFunc) ----
Dim gApp As femap.model
Dim gCSys As Long
Dim gMode As Long
Dim gROC As Double
Dim gK As Double
Dim gSurf As femap.Surface


Sub Main

    '--------------------------------------------------------------------------
    ' STEP 1: Connect to FEMAP and create the reusable objects
    '--------------------------------------------------------------------------
    Dim App As femap.model
    Set App = feFemap()

    Dim ds As femap.DataSurf
    Set ds = App.feDataSurf
    Dim ls As femap.LoadSet
    Set ls = App.feLoadSet
    Dim nt As femap.LoadNTemp
    Set nt = App.feLoadNTemp
    Dim nd As femap.Node
    Set nd = App.feNode
    Dim es As femap.Set
    Set es = App.feSet
    Dim cs As femap.CSys
    Set cs = App.feCSys

    Dim rc As Long


    '--------------------------------------------------------------------------
    ' STEP 2: Prompts -- file, mirror-local CSys, naming tag
    '--------------------------------------------------------------------------
    Dim fname As String
    fname = InputBox("Full path to the x,y,z,T cloud file (csv or txt):", _
                     "Apply Thermal Data Surface", "C:\temp\M1_MirrorCloud3D_120s.csv")
    If fname = "" Then Exit Sub
    If Dir(fname) = "" Then
        MsgBox "File not found: " & fname
        Exit Sub
    End If

    Dim csysStr As String
    csysStr = InputBox("Mirror local CSys ID (rectangular, origin at vertex, +Z = optical axis)." & vbCrLf & _
                       "0 = global:", "Apply Thermal Data Surface", "0")
    If csysStr = "" Then Exit Sub
    If Not IsNumeric(csysStr) Then
        MsgBox "CSys ID must be a number."
        Exit Sub
    End If
    Dim surfCSys As Long
    surfCSys = CLng(csysStr)

    ' Validate the CSys BEFORE building anything. IDs 0-2 are built-in global
    ' (0 = rectangular, 1 = cylindrical, 2 = spherical); user CSys are ID > 2.
    ' Our cloud is cartesian, so the CSys must be RECTANGULAR.
    If surfCSys < 0 Then
        MsgBox "CSys ID can't be negative."
        Exit Sub
    ElseIf surfCSys = 1 Or surfCSys = 2 Then
        MsgBox "This macro needs a RECTANGULAR CSys (0 or a user rectangular CSys)."
        Exit Sub
    ElseIf surfCSys > 2 Then
        If cs.Get(surfCSys) <> FE_OK Then
            MsgBox "CSys " & surfCSys & " does not exist in this model."
            Exit Sub
        End If
        If cs.type <> 0 Then
            MsgBox "CSys " & surfCSys & " is not rectangular."
            Exit Sub
        End If
    End If

    Dim tag As String
    tag = InputBox("Short tag for naming the surface and load set (e.g. M1_120s):", _
                   "Apply Thermal Data Surface", "map")
    If tag = "" Then tag = "map"

    '--------------------------------------------------------------------------
    ' STEP 2b: Curved-surface (sag) setup
    '   SAG_MODE 0 = flat, 1 = conic (ROC/K), 2 = geometry (optical Surface).
    '   The sag only shifts the lookup z (down the optical axis); x,y are kept.
    '   Module-level gApp/gCSys/gMode/gROC/gK/gSurf feed the SagFunc helper.
    '--------------------------------------------------------------------------
    If SAG_MODE < 0 Or SAG_MODE > 2 Then
        MsgBox "SAG_MODE must be 0 (flat), 1 (conic) or 2 (geometry)."
        Exit Sub
    End If
    Set gApp = App
    gCSys = surfCSys
    gMode = SAG_MODE
    gROC = MIRROR_ROC
    gK = MIRROR_K

    Dim surfID As Long
    surfID = 0
    Dim sstr As String
    If SAG_MODE = 1 Then
        sstr = InputBox("Conic ROC (vertex radius of curvature; sign sets convex/concave; 0 = flat):", _
                        "Curved surface - conic", Format(MIRROR_ROC, "0.0####"))
        If sstr = "" Or Not IsNumeric(sstr) Then Exit Sub
        gROC = CDbl(sstr)
        sstr = InputBox("Conic constant K (0 sphere, -1 parabola, <-1 hyperbola, >0 oblate):", _
                        "Curved surface - conic", Format(MIRROR_K, "0.0####"))
        If sstr = "" Or Not IsNumeric(sstr) Then Exit Sub
        gK = CDbl(sstr)
    ElseIf SAG_MODE = 2 Then
        sstr = InputBox("Optical Surface ID to project each node onto:", "Curved surface - geometry", "1")
        If sstr = "" Or Not IsNumeric(sstr) Then Exit Sub
        surfID = CLng(sstr)
        Set gSurf = App.feSurface
        If gSurf.Get(surfID) <> FE_OK Then
            MsgBox "Surface " & surfID & " does not exist in this model."
            Exit Sub
        End If
    End If


    '--------------------------------------------------------------------------
    ' STEP 3: Read the cloud file into coordinate / value arrays
    '   Same delimiter-tolerant reader as show_point_cloud.bas. We need 4 numeric
    '   columns (x,y,z,T); header and non-numeric lines are skipped.
    '--------------------------------------------------------------------------
    Dim cx() As Double, cy() As Double, cz() As Double, cv() As Double
    Dim nPts As Long, nBad As Long
    Dim fnum As Integer, ln As String, parts() As String
    Dim ok As Boolean, k As Long

    fnum = FreeFile
    Open fname For Input As #fnum
    Do While Not EOF(fnum)
        Line Input #fnum, ln
        ln = Replace(ln, vbTab, ",")
        ln = Replace(ln, ";", ",")
        ln = Trim(ln)
        Do While InStr(ln, "  ") > 0
            ln = Replace(ln, "  ", " ")
        Loop
        ln = Replace(ln, " ", ",")
        Do While InStr(ln, ",,") > 0
            ln = Replace(ln, ",,", ",")
        Loop

        If ln <> "" Then
            parts = Split(ln, ",")
            ok = (UBound(parts) >= 3)              ' need x,y,z,T
            If ok Then
                For k = 0 To 3
                    If Not IsNumeric(parts(k)) Then ok = False
                Next k
            End If
            If ok Then
                ReDim Preserve cx(nPts), cy(nPts), cz(nPts), cv(nPts)
                cx(nPts) = CDbl(parts(0)) * SCALE_FAC
                cy(nPts) = CDbl(parts(1)) * SCALE_FAC
                cz(nPts) = CDbl(parts(2)) * SCALE_FAC
                cv(nPts) = CDbl(parts(3))          ' temperature (not scaled)
                nPts = nPts + 1
            Else
                nBad = nBad + 1
            End If
        End If
    Loop
    Close #fnum

    If nPts = 0 Then
        MsgBox "No valid data lines (need 4 numeric columns x,y,z,T)."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 4: Build the Arbitrary 3D Data Surface
    '   VarArbitrary3DCoord wants xyz packed 3-per-location, and val_xyz packed
    '   3-per-location too; for a SCALAR surface the value goes in slot 0 and
    '   the other two slots are 0.
    '--------------------------------------------------------------------------
    Dim xyz() As Double, valxyz() As Double
    ReDim xyz(3 * nPts - 1)
    ReDim valxyz(3 * nPts - 1)
    Dim i As Long
    For i = 0 To nPts - 1
        xyz(3 * i + 0) = cx(i)
        xyz(3 * i + 1) = cy(i)
        xyz(3 * i + 2) = cz(i)
        valxyz(3 * i + 0) = cv(i)      ' scalar temperature
        valxyz(3 * i + 1) = 0#
        valxyz(3 * i + 2) = 0#
    Next i

    ds.title = DS_TITLE_PFX & "_" & tag
    ds.csys = surfCSys                 ' evaluate in the mirror-local frame
    ds.vector = False                  ' scalar (temperature)
    ds.InterpPercent = INTERP_PERCENT  ' 0  -> nearest only
    ds.InterpMin = INTERP_MIN          ' 1  -> nearest neighbor

    rc = ds.VarArbitrary3DCoord(nPts, xyz, valxyz)
    If rc <> FE_OK Then
        MsgBox "Failed to build the Arbitrary 3D Data Surface (rc=" & rc & ")."
        Exit Sub
    End If
    Dim dsID As Long
    dsID = App.Info_NextID(FT_DATA_SURF)
    rc = ds.PutDataSurf(dsID)
    If rc <> FE_OK Then
        MsgBox "Failed to store the Data Surface (rc=" & rc & ")."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 5: Select the mirror nodes to receive temperatures
    '--------------------------------------------------------------------------
    rc = es.Select(FT_NODE, True, "Select the mirror NODES to receive temperatures")
    If rc <> FE_OK Then Exit Sub

    ' Count selected nodes (First/Next pre-pass) so we can size the load arrays.
    Dim nNodes As Long, id As Long
    nNodes = 0
    id = es.First()
    While id > 0
        nNodes = nNodes + 1
        id = es.Next()
    Wend
    If nNodes = 0 Then
        MsgBox "No nodes selected."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 6: Create and ACTIVATE a fresh load set
    '   New nodal temperatures always go into the active load set, so set it
    '   active via Info_ActiveID before creating any loads.
    '--------------------------------------------------------------------------
    Dim lsID As Long
    lsID = App.Info_NextID(FT_LOAD_DIR)
    ls.title = LS_TITLE_PFX & "_" & tag
    rc = ls.Put(lsID)
    If rc <> FE_OK Then
        MsgBox "Failed to create the load set (rc=" & rc & ")."
        Exit Sub
    End If
    App.Info_ActiveID(FT_LOAD_DIR) = lsID      ' make it the active load set


    '--------------------------------------------------------------------------
    ' STEP 7: Evaluate the Data Surface at each node (FEMAP's own IDW)
    '--------------------------------------------------------------------------
    ' Prepare the arrays feLoadNTemp.PutAllArray expects (one entry per node).
    Dim NodeID() As Long
    Dim Temp() As Double
    Dim Expanded() As Boolean
    Dim Layer() As Long
    Dim Color() As Long
    Dim LoadDef() As Long
    Dim FuncID() As Long
    ReDim NodeID(nNodes - 1)
    ReDim Temp(nNodes - 1)
    ReDim Expanded(nNodes - 1)
    ReDim Layer(nNodes - 1)
    ReDim Color(nNodes - 1)
    ReDim LoadDef(nNodes - 1)
    ReDim FuncID(nNodes - 1)

    Dim Coord(3) As Double     ' node location fed to GetData (size per guide example)
    Dim ans As Double          ' scalar result
    Dim vXYZ As Variant        ' vector result (unused for a scalar surface)

    ' Flat-cloud depth range, for the out-of-range diagnostic after the sag shift.
    Dim czmin As Double
    Dim czmax As Double
    czmin = cz(0)
    czmax = cz(0)
    For i = 1 To nPts - 1
        If cz(i) < czmin Then czmin = cz(i)
        If cz(i) > czmax Then czmax = cz(i)
    Next i

    ' Per-node working values for the sag shift (all in the vertex-local frame).
    Dim gxyz(2) As Double
    Dim loc2(2) As Double
    Dim locv As Variant
    Dim gout As Variant
    Dim lx As Double
    Dim ly As Double
    Dim lz As Double
    Dim sagv As Double
    Dim rr As Double
    Dim maxR As Double
    Dim sagMin As Double
    Dim sagMax As Double
    Dim flatMin As Double
    Dim flatMax As Double
    Dim nOut As Long
    Dim seen As Boolean
    maxR = 0#
    nOut = 0
    seen = False

    ' The surface must be evaluated once before GetData. For an Arbitrary 3D
    ' surface the geom/type/map/elemental arguments are not used -> 0,0,0,False.
    rc = ds.GetDataSurf(dsID)
    rc = ds.Evaluate(0, 0, 0, False)

    App.feAppLock      ' quiet the UI during the node loop

    Dim nGot As Long
    nGot = 0
    id = es.First()
    While id > 0
        If nd.Get(id) = FE_OK Then
            ' 1) node global -> vertex-local, so radius and depth are measured from the vertex
            '    (flat mode skips the transform entirely -> exact original behavior)
            If gCSys = 0 Or gMode = 0 Then
                lx = nd.x
                ly = nd.y
                lz = nd.z
            Else
                gxyz(0) = nd.x
                gxyz(1) = nd.y
                gxyz(2) = nd.z
                rc = App.feCoordTransform(0, gxyz, gCSys, locv)
                lx = locv(0)
                ly = locv(1)
                lz = locv(2)
            End If

            ' 2) local sag; shift ONLY the depth (z) down the optical axis -- x,y unchanged
            sagv = SagFunc(lx, ly)
            loc2(0) = lx
            loc2(1) = ly
            loc2(2) = lz - Z_SIGN * sagv

            ' 3) diagnostics: rim radius, sag range, shifted-depth range, out-of-range count
            rr = Sqr(lx * lx + ly * ly)
            If rr > maxR Then maxR = rr
            If Not seen Then
                sagMin = sagv
                sagMax = sagv
                flatMin = loc2(2)
                flatMax = loc2(2)
                seen = True
            Else
                If sagv < sagMin Then sagMin = sagv
                If sagv > sagMax Then sagMax = sagv
                If loc2(2) < flatMin Then flatMin = loc2(2)
                If loc2(2) > flatMax Then flatMax = loc2(2)
            End If
            If loc2(2) < czmin - 0.000001 Or loc2(2) > czmax + 0.000001 Then nOut = nOut + 1

            ' 4) shifted local point -> global for GetData (ds.csys handles its own frame)
            If gCSys = 0 Or gMode = 0 Then
                Coord(0) = loc2(0)
                Coord(1) = loc2(1)
                Coord(2) = loc2(2)
            Else
                rc = App.feCoordTransform(gCSys, loc2, 0, gout)
                Coord(0) = gout(0)
                Coord(1) = gout(1)
                Coord(2) = gout(2)
            End If

            ' GetData interpolates the (flat) surface at the shifted location
            rc = ds.GetData(0, id, Coord, ans, vXYZ)
            If rc = FE_OK Then
                NodeID(nGot) = id
                Temp(nGot) = ans
                Expanded(nGot) = False
                Layer(nGot) = 1
                Color(nGot) = LOAD_COLOR
                LoadDef(nGot) = 0
                FuncID(nGot) = 0
                nGot = nGot + 1
            End If
        End If
        id = es.Next()
    Wend

    App.feAppUnlock

    If nGot = 0 Then
        MsgBox "Could not evaluate the Data Surface at any node."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 8: Bulk-apply the nodal temperatures into our load set
    '   PutArray writes directly to the database (no Put needed afterward). Per
    '   the API guide you must first set the object's setID (which load set) and
    '   color. doValues=True -> use our Temp() values; doFunctions=False ->
    '   constant temperatures (no time function), so FuncID() is ignored.
    '   Arrays are trimmed to the number actually evaluated (nGot).
    '--------------------------------------------------------------------------
    nt.setID = lsID
    nt.color = LOAD_COLOR
    ReDim Preserve NodeID(nGot - 1)
    ReDim Preserve Temp(nGot - 1)
    ReDim Preserve FuncID(nGot - 1)
    rc = nt.PutArray(nGot, True, False, NodeID, Temp, FuncID)
    If rc <> FE_OK Then
        MsgBox "Failed to create nodal temperatures (rc=" & rc & ")."
        Exit Sub
    End If
    App.feViewRegenerate(0)


    '--------------------------------------------------------------------------
    ' STEP 9: Report
    '--------------------------------------------------------------------------
    Dim tmin As Double, tmax As Double
    tmin = Temp(0)
    tmax = Temp(0)
    For i = 1 To nGot - 1
        If Temp(i) < tmin Then tmin = Temp(i)
        If Temp(i) > tmax Then tmax = Temp(i)
    Next i

    ' Sag-mode label for the report
    Dim modeStr As String
    If SAG_MODE = 0 Then modeStr = "flat (no sag)"
    If SAG_MODE = 1 Then modeStr = "conic  ROC=" & Format(gROC, "0.0###") & "  K=" & Format(gK, "0.0###")
    If SAG_MODE = 2 Then modeStr = "geometry  Surface " & surfID

    ' Fast-mirror warning: rim surface tilt from a finite difference of the sag,
    ' and the implied axial-vs-normal depth error (~ 1 - cos(theta)).
    Dim PI As Double
    PI = 4# * Atn(1#)
    Dim thetaDeg As Double
    Dim errPct As Double
    Dim dR As Double
    Dim sA As Double
    Dim sB As Double
    thetaDeg = 0#
    errPct = 0#
    If SAG_MODE <> 0 And maxR > 0# Then
        dR = maxR * 0.01
        If dR <= 0# Then dR = 0.001
        sA = SagFunc(maxR, 0#)
        sB = SagFunc(maxR - dR, 0#)
        thetaDeg = Atn(Abs(sA - sB) / dR) * 180# / PI
        errPct = (1# - Cos(thetaDeg * PI / 180#)) * 100#
    End If

    Dim msg As String
    msg = "Cloud points read: " & nPts & "  (skipped " & nBad & " unreadable)" & vbCrLf & _
          "Data Surface ID:   " & dsID & "  (CSys " & surfCSys & _
          ", %=" & INTERP_PERCENT & ", min=" & INTERP_MIN & " nearest neighbor)" & vbCrLf & _
          "Sag mode:          " & modeStr & vbCrLf & _
          "Sag applied:       " & Format(sagMin, "0.0####") & " to " & Format(sagMax, "0.0####") & " in" & vbCrLf & _
          "Shifted depth:     " & Format(flatMin, "0.0###") & " to " & Format(flatMax, "0.0###") & _
          " in  (cloud " & Format(czmin, "0.0###") & " to " & Format(czmax, "0.0###") & ")" & vbCrLf & _
          "Load set ID:       " & lsID & "  (" & ls.title & ")" & vbCrLf & _
          "Nodes loaded:      " & nGot & " of " & nNodes & " selected" & vbCrLf & _
          "Temperature range: " & Format(tmin, "0.000") & " to " & Format(tmax, "0.000")
    If nOut > 0 Then
        msg = msg & vbCrLf & "** WARNING: " & nOut & " node(s) shifted outside the cloud depth range" & _
              " (clamped to the end value). Check Z_SIGN and the vertex CSys. **"
    End If
    If SAG_MODE <> 0 And thetaDeg > SLOPE_WARN_DEG Then
        msg = msg & vbCrLf & "** WARNING: rim surface tilt " & Format(thetaDeg, "0.0") & " deg (~" & _
              Format(errPct, "0.00") & "% axial-depth error) -- fast mirror; the axial-depth rule is approximate here. **"
    End If
    MsgBox msg

End Sub


'------------------------------------------------------------------------------
' SagFunc - local surface sag at in-plane (lx, ly), in the vertex-local frame.
'   Mode 0 (flat): 0. Mode 1 (conic): conic sag from gROC/gK. Mode 2 (geometry):
'   project the local point onto the optical Surface (transforming to/from global,
'   since surface geometry and XYZToParam work in global) and return its local z.
'------------------------------------------------------------------------------
Function SagFunc(lx As Double, ly As Double) As Double
    Dim r2 As Double
    Dim cc As Double
    Dim arg As Double
    Dim rc As Long
    Dim u As Double
    Dim v As Double
    Dim qp(2) As Double
    Dim qg As Variant
    Dim qgd(2) As Double
    Dim sxyz As Variant
    Dim spd(2) As Double
    Dim spl As Variant

    SagFunc = 0#

    If gMode = 1 Then
        ' Conic sag: ROC = 0 -> flat (no divide). Guard the sqrt beyond the aperture.
        If gROC <> 0# Then
            cc = 1# / gROC
            r2 = lx * lx + ly * ly
            arg = 1# - (1# + gK) * cc * cc * r2
            If arg < 0# Then arg = 0#
            SagFunc = cc * r2 / (1# + Sqr(arg))
        End If

    ElseIf gMode = 2 Then
        ' Build the local query point on the vertex plane, take it to global.
        qp(0) = lx
        qp(1) = ly
        qp(2) = 0#
        If gCSys = 0 Then
            qgd(0) = qp(0)
            qgd(1) = qp(1)
            qgd(2) = qp(2)
        Else
            rc = gApp.feCoordTransform(gCSys, qp, 0, qg)
            qgd(0) = qg(0)
            qgd(1) = qg(1)
            qgd(2) = qg(2)
        End If
        ' Project onto the surface, then read the surface point's local z = sag.
        rc = gSurf.XYZToParam(qgd, u, v)
        If rc = FE_OK Then
            rc = gSurf.ParamToXYZ(u, v, sxyz)
            If rc = FE_OK Then
                spd(0) = sxyz(0)
                spd(1) = sxyz(1)
                spd(2) = sxyz(2)
                If gCSys = 0 Then
                    SagFunc = spd(2)
                Else
                    rc = gApp.feCoordTransform(0, spd, gCSys, spl)
                    SagFunc = spl(2)
                End If
            End If
        End If
    End If
End Function
