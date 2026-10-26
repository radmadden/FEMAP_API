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
' API CALLS USED (Femap API Guide 2025)
'   feFemap, feDataSurf (csys, vector, InterpPercent, InterpMin,
'     VarArbitrary3DCoord, PutDataSurf, GetDataSurf, Evaluate, GetData),
'   feLoadSet (Put), feLoadNTemp (PutAllArray), feNode (Get, x/y/z),
'   feSet (Select, First, Next), feCSys (Get, type),
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
            Coord(0) = nd.x
            Coord(1) = nd.y
            Coord(2) = nd.z
            ' GetData interpolates the surface at this (global) location; the
            ' surface's csys handles the frame. ans = temperature at the node.
            rc = ds.GetData(0, id, Coord, ans, vXYZ)
            If rc = FE_OK Then
                NodeID(nGot)   = id
                Temp(nGot)     = ans
                Expanded(nGot) = False
                Layer(nGot)    = 1
                Color(nGot)    = LOAD_COLOR
                LoadDef(nGot)  = 0
                FuncID(nGot)   = 0
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

    Dim msg As String
    msg = "Cloud points read: " & nPts & "  (skipped " & nBad & " unreadable)" & vbCrLf & _
          "Data Surface ID:   " & dsID & "  (CSys " & surfCSys & _
          ", %=" & INTERP_PERCENT & ", min=" & INTERP_MIN & " nearest neighbor)" & vbCrLf & _
          "Load set ID:       " & lsID & "  (" & ls.title & ")" & vbCrLf & _
          "Nodes loaded:      " & nGot & " of " & nNodes & " selected" & vbCrLf & _
          "Temperature range: " & Format(tmin, "0.000") & " to " & Format(tmax, "0.000")
    MsgBox msg

End Sub
