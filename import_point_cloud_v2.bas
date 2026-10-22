'==============================================================================
' IMPORT THERMAL POINT CLOUD INTO FEMAP
'   Mode 1: plain X,Y,Z file             -> points as-is (optionally from a CSys)
'   Mode 2: flat-plate X,Z thermal file  -> mapped onto a circular mirror,
'                                           Y filled in, then placed in the
'                                           mirror local CSys
'==============================================================================
'
' PURPOSE
'   Overlay thermal-model sample locations on the FE model as FEMAP geometry
'   Points, so you can visually check spatial compatibility.
'
'------------------------------------------------------------------------------
' MODE 1 - X,Y,Z FILE
'------------------------------------------------------------------------------
'   One point per line, three numeric columns, no header:
'       1.250, 3.400, 0.750
'   Values are read as being in the CSys you enter (0 = global) and are
'   converted to global before the points are created. If that CSys is
'   cylindrical or spherical, FEMAP reads the three columns as (R, theta, Z)
'   or (R, theta, phi), with angles in degrees.
'
'------------------------------------------------------------------------------
' MODE 2 - FLAT-PLATE X,Z FILE  (the idealized thermal model)
'------------------------------------------------------------------------------
'   One point per line, two numeric columns, no header:
'       xp, zp
'     xp = distance along the plate in the flow direction
'     zp = depth into the plate (positive = deeper)
'   The thermal model has no variation across the width of the plate (its Y),
'   and its plate length equals the mirror diameter D. So:
'
'     1. xp is shifted so the plate leading edge (X0) lands on the mirror edge:
'            u = xp - X0 - R            (u runs from -R to +R across the mirror)
'     2. Because T does not vary across the width, every (xp, zp) point is
'        copied to a set of Y stations. The stations sit on one uniform grid
'        across the full diameter and are clipped to the circle:
'            keep y only where  u^2 + y^2 <= R^2
'        so short chords near the edge get fewer copies, and the grid stays
'        evenly spaced (it doesn't bunch up at the edge).
'     3. The flow direction is rotated by FLOW_ANGLE about the mirror axis
'        (0 = flow along mirror local +X).
'     4. Depth is applied from the optical surface:
'            z = sag(r) + Z_SIGN * zp
'        sag uses the conic sag equation with MIRROR_ROC and MIRROR_K.
'        ROC = 0 means a flat mirror, so sag = 0.
'        Depth is applied along the mirror axis, not along the local surface
'        normal. For slow mirrors the difference is negligible (cosine of the
'        surface slope).
'     5. The mirror-local (x, y, z) is converted to global with
'        feCoordTransform, using the mirror CSys you enter.
'
'   MIRROR LOCAL CSYS ASSUMPTIONS (mode 2) - check these against your model:
'     - RECTANGULAR coordinate system (the script checks this)
'     - origin at the mirror vertex (center of the optical surface)
'     - +Z along the optical axis
'     - X/Y in the plane of the mirror; FLOW_ANGLE is measured from +X
'
'------------------------------------------------------------------------------
' WHY THE EARLIER VERSION FAILED FOR CSYS <> 0
'------------------------------------------------------------------------------
'   Femap API Guide, "Arrays and Variants": arrays that the API RETURNS must be
'   received in a Variant. The old script passed a fixed Double array as the
'   OUTPUT of feCoordTransform, so the transformed values never came back.
'   Here the output (gXYZ) is a Variant. The input array can stay Double.
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (Ctrl+F11), paste, then Run.
'
' API CALLS USED (Femap API Guide 2025)
'   feFemap, fePoint, feCSys, CSys.Get/.type, Point.x/y/z/layer/color,
'   Point.NextEmptyID, Point.Put, feCoordTransform, feAppLock/feAppUnlock,
'   feViewRegenerate
'==============================================================================


'------------------------------------------------------------------------------
' USER SETTINGS - edit to suit the model (units: whatever your model uses, in)
'------------------------------------------------------------------------------

Const LAYER_ID   As Long   = 9000   ' layer for the cloud; toggle in View > Visibility
Const PT_COLOR   As Long   = 12     ' Femap palette index; change if hard to see
Const SCALE_FAC  As Double = 1.0    ' scales file values on read (0.0393701 = mm->in)
                                    ' mode 1: use only with rectangular CSys

' ---- Mode 2 (flat plate -> mirror) settings ----
Const MIRROR_ROC As Double = 0.0    ' vertex radius of curvature, 0 = flat.
                                    ' Sign sets the direction of sag: + means the
                                    ' surface curves toward +Z at the edge
                                    ' (concave mirror facing +Z with ROC > 0).
Const MIRROR_K   As Double = 0.0    ' conic constant: 0 = sphere, -1 = parabola
Const Z_SIGN     As Double = -1.0   ' direction depth goes into the substrate:
                                    ' -1 = depth goes toward -Z (optical surface
                                    ' faces +Z, glass is behind it)
Const FLOW_ANGLE As Double = 0.0    ' degrees from mirror local +X to the flow
                                    ' direction, about +Z


'------------------------------------------------------------------------------
' MAIN PROGRAM
'------------------------------------------------------------------------------
Sub Main

    '--------------------------------------------------------------------------
    ' STEP 1: Connect to FEMAP and create the reusable objects
    '--------------------------------------------------------------------------
    Dim App As femap.model
    Set App = feFemap()

    Dim pt As femap.Point          ' reused for every point we create
    Set pt = App.fePoint

    Dim cs As femap.CSys           ' used only to check that the CSys exists and its type
    Set cs = App.feCSys

    Dim PI As Double
    PI = 4# * Atn(1#)


    '--------------------------------------------------------------------------
    ' STEP 2: Ask for mode, file and CSys
    '--------------------------------------------------------------------------
    Dim modeStr As String
    modeStr = InputBox("Input file type:" & vbCrLf & _
                       "  1 = X,Y,Z (3 columns)" & vbCrLf & _
                       "  2 = flat-plate X,Z thermal (2 columns) -> map to mirror", _
                       "Import Point Cloud", "2")
    If modeStr = "" Then Exit Sub
    If modeStr <> "1" And modeStr <> "2" Then
        MsgBox "Enter 1 or 2."
        Exit Sub
    End If
    Dim importMode As Long
    importMode = CLng(modeStr)

    Dim fname As String
    fname = InputBox("Full path to the data file (csv or txt):", "Import Point Cloud", "C:\temp\thermal_points.csv")
    If fname = "" Then Exit Sub
    If Dir(fname) = "" Then
        MsgBox "File not found: " & fname
        Exit Sub
    End If

    ' In mode 1 this is "the CSys the file is in". In mode 2 it is the mirror
    ' local CSys that the mapped points are built in.
    Dim csysPrompt As String
    If importMode = 1 Then
        csysPrompt = "CSys ID the file coordinates are defined in (0 = global):"
    Else
        csysPrompt = "Mirror local CSys ID (rectangular, origin at vertex, +Z = optical axis):"
    End If

    Dim csysStr As String
    csysStr = InputBox(csysPrompt, "Import Point Cloud", "0")
    If csysStr = "" Then Exit Sub
    If Not IsNumeric(csysStr) Then
        MsgBox "CSys ID must be a number."
        Exit Sub
    End If
    Dim fromCSys As Long
    fromCSys = CLng(csysStr)

    ' Check the CSys BEFORE reading anything. IDs 0-2 are FEMAP's built-in
    ' global systems: 0 = rectangular, 1 = cylindrical, 2 = spherical.
    ' The API guide says user CSys are always ID > 2.
    If fromCSys > 2 Then
        If cs.Get(fromCSys) <> FE_OK Then
            MsgBox "CSys " & fromCSys & " does not exist in this model."
            Exit Sub
        End If
        ' Mode 2 builds rectangular x,y,z, so the mirror CSys must be rectangular
        ' (type 0). Otherwise FEMAP would read our x,y,z as R,theta,Z.
        If importMode = 2 And cs.type <> 0 Then
            MsgBox "CSys " & fromCSys & " is not rectangular. Mode 2 needs a rectangular mirror CSys."
            Exit Sub
        End If
    ElseIf fromCSys = 1 Or fromCSys = 2 Then
        If importMode = 2 Then
            MsgBox "Mode 2 needs a rectangular CSys (0 or a user rectangular CSys)."
            Exit Sub
        End If
    ElseIf fromCSys < 0 Then
        MsgBox "CSys ID can't be negative."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 3: Read the whole file into arrays first
    '   Reading everything up front lets mode 2 find the plate's X range
    '   (to locate the leading edge) before any points are made.
    '--------------------------------------------------------------------------
    Dim nCol As Long
    nCol = importMode + 1                 ' mode 1 needs 3 columns, mode 2 needs 2

    Dim c1() As Double, c2() As Double, c3() As Double   ' column storage
    Dim nRead As Long, nBad As Long
    Dim fnum As Integer, ln As String, parts() As String
    Dim ok As Boolean, k As Long

    fnum = FreeFile
    Open fname For Input As #fnum
    Do While Not EOF(fnum)
        Line Input #fnum, ln

        ' Normalize any delimiter (tab, semicolon, spaces) to single commas
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
            ok = (UBound(parts) >= nCol - 1)          ' enough columns?
            If ok Then
                For k = 0 To nCol - 1                 ' all of them numeric?
                    If Not IsNumeric(parts(k)) Then ok = False
                Next k
            End If

            If ok Then
                ' Grow the arrays by one and store the values (scaled)
                ReDim Preserve c1(nRead), c2(nRead), c3(nRead)
                c1(nRead) = CDbl(parts(0)) * SCALE_FAC
                c2(nRead) = CDbl(parts(1)) * SCALE_FAC
                If nCol = 3 Then c3(nRead) = CDbl(parts(2)) * SCALE_FAC
                nRead = nRead + 1
            Else
                nBad = nBad + 1                       ' header, text, too few columns
            End If
        End If
    Loop
    Close #fnum

    If nRead = 0 Then
        MsgBox "No valid lines found. Mode " & importMode & " expects " & nCol & " numeric columns."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 4 (mode 2 only): Get the mirror geometry and mapping inputs
    '--------------------------------------------------------------------------
    Dim D As Double, R As Double, X0 As Double
    Dim nY As Long, dy As Double
    Dim xMin As Double, xMax As Double

    If importMode = 2 Then
        ' Find the plate's X range in the data
        xMin = c1(0): xMax = c1(0)
        For k = 1 To nRead - 1
            If c1(k) < xMin Then xMin = c1(k)
            If c1(k) > xMax Then xMax = c1(k)
        Next k

        Dim s As String
        s = InputBox("Mirror diameter D (model units):", "Mirror Mapping", Format(xMax - xMin, "0.0###"))
        If s = "" Or Not IsNumeric(s) Then Exit Sub
        D = CDbl(s)
        If D <= 0 Then
            MsgBox "Diameter must be positive."
            Exit Sub
        End If
        R = D / 2#

        ' X0 = plate X value that sits on the mirror edge (the leading edge).
        ' Defaults to the smallest X in the file.
        s = InputBox("Plate X at the mirror's leading edge (X0):" & vbCrLf & _
                     "Data X range: " & Format(xMin, "0.0###") & " to " & Format(xMax, "0.0###"), _
                     "Mirror Mapping", Format(xMin, "0.0###"))
        If s = "" Or Not IsNumeric(s) Then Exit Sub
        X0 = CDbl(s)

        ' Sanity check: the plate length should match D. If it doesn't, the
        ' mapping is still done, but warn the user.
        If Abs((xMax - X0) - D) > 0.02 * D Or X0 > xMin + 0.02 * D Then
            If MsgBox("Data X span from X0 (" & Format(xMax - X0, "0.0###") & _
                      ") doesn't match D (" & Format(D, "0.0###") & ") within 2%." & vbCrLf & _
                      "Points outside the mirror circle will be dropped. Continue?", vbYesNo) = vbNo Then Exit Sub
        End If

        ' Number of Y stations across the full diameter. An odd number puts a
        ' station on Y = 0, so even the edge points (chord = 0) get one copy.
        s = InputBox("Y stations across the full diameter (odd number recommended):" & vbCrLf & _
                     "Total points will be roughly " & nRead & " x 0.79 x this number.", _
                     "Mirror Mapping", "21")
        If s = "" Or Not IsNumeric(s) Then Exit Sub
        nY = CLng(s)
        If nY < 1 Then nY = 1
        If nY = 1 Then
            dy = 0#
        Else
            dy = D / (nY - 1)
        End If
    End If


    '--------------------------------------------------------------------------
    ' STEP 5: Create the points
    '--------------------------------------------------------------------------
    ' fromXYZ = coordinates in the source CSys (input, a plain Double array works)
    ' gXYZ    = coordinates in global (OUTPUT - must be a Variant, per the guide)
    Dim fromXYZ(2) As Double
    Dim gXYZ As Variant
    Dim rc As Long, nOK As Long, nDropped As Long

    ' Mode 2 working values
    Dim u As Double, yv As Double, j As Long
    Dim cosA As Double, sinA As Double
    Dim r2 As Double, sag As Double, cc As Double, arg As Double
    ' WinWrap folds constant expressions when it compiles the script, so
    ' "1# / MIRROR_ROC" with MIRROR_ROC = 0 throws Divide by zero even inside
    ' an If that never runs. Copying the Const into a normal variable stops
    ' that: the division then only happens at run time, behind the If check.
    Dim roc As Double
    roc = MIRROR_ROC

    cosA = Cos(FLOW_ANGLE * PI / 180#)
    sinA = Sin(FLOW_ANGLE * PI / 180#)

    ' Lock the UI so thousands of Puts don't redraw one at a time.
    ' Every exit path below must call feAppUnlock.
    App.feAppLock

    For k = 0 To nRead - 1

        If importMode = 1 Then
            '------------------------------------------------------------------
            ' MODE 1: one file line -> one point
            '------------------------------------------------------------------
            fromXYZ(0) = c1(k)
            fromXYZ(1) = c2(k)
            fromXYZ(2) = c3(k)
            If Not PlacePoint(App, pt, fromCSys, fromXYZ, gXYZ) Then GoTo CSysFail
            nOK = nOK + 1

        Else
            '------------------------------------------------------------------
            ' MODE 2: one file line -> one point at each Y station on the chord
            '------------------------------------------------------------------
            ' 5a. Position across the mirror along the flow, centered on the vertex
            u = c1(k) - X0 - R

            If Abs(u) > R * 1.000001 Then
                ' This plate X lies outside the mirror (span mismatch). Skip it.
                nDropped = nDropped + 1
            Else
                For j = 0 To nY - 1
                    ' 5b. Y station on the uniform grid across the diameter
                    If nY = 1 Then
                        yv = 0#
                    Else
                        yv = -R + j * dy
                    End If

                    ' Keep only stations that fall inside the circle
                    If u * u + yv * yv <= R * R * 1.000001 Then

                        ' 5c. Rotate (flow, cross-flow) into mirror local X/Y
                        fromXYZ(0) = u * cosA - yv * sinA
                        fromXYZ(1) = u * sinA + yv * cosA

                        ' 5d. Sag of the optical surface at this radius (conic)
                        '     sag = c r^2 / (1 + sqrt(1 - (1+K) c^2 r^2)),  c = 1/ROC
                        sag = 0#
                        If roc <> 0# Then
                            cc = 1# / roc      ' roc is a runtime copy of MIRROR_ROC (see STEP 5)
                            r2 = fromXYZ(0) ^ 2 + fromXYZ(1) ^ 2
                            arg = 1# - (1# + MIRROR_K) * cc * cc * r2
                            If arg < 0# Then arg = 0#      ' guard: beyond the conic's valid aperture
                            sag = cc * r2 / (1# + Sqr(arg))
                        End If

                        ' 5e. Depth into the substrate measured from the surface
                        fromXYZ(2) = sag + Z_SIGN * c2(k)

                        ' 5f. Mirror local -> global, then create the point
                        If Not PlacePoint(App, pt, fromCSys, fromXYZ, gXYZ) Then GoTo CSysFail
                        nOK = nOK + 1
                    End If
                Next j
            End If
        End If
    Next k

    App.feAppUnlock
    App.feViewRegenerate(0)

    '--------------------------------------------------------------------------
    ' STEP 6: Report
    '--------------------------------------------------------------------------
    Dim msg As String
    msg = "Read " & nRead & " data lines (skipped " & nBad & " unreadable)." & vbCrLf & _
          "Created " & nOK & " points on layer " & LAYER_ID & "."
    If importMode = 2 Then
        msg = msg & vbCrLf & "Mapped to D = " & Format(D, "0.0###") & ", " & nY & " Y stations, CSys " & fromCSys & "."
        If nDropped > 0 Then msg = msg & vbCrLf & nDropped & " data lines fell outside the mirror and were dropped."
    End If
    MsgBox msg
    Exit Sub

CSysFail:
    ' Reached only if feCoordTransform reports the CSys doesn't exist
    App.feAppUnlock
    App.feViewRegenerate(0)
    MsgBox "Coordinate transform failed for CSys " & fromCSys & ". Points created before the failure: " & nOK
End Sub


'------------------------------------------------------------------------------
' PlacePoint - converts one coordinate from srcCSys to global and stores it
'   Returns False if the transform fails (CSys missing).
'   gXYZ is passed in so the Variant is reused, not reallocated each call.
'------------------------------------------------------------------------------
Function PlacePoint(App As femap.model, pt As femap.Point, srcCSys As Long, _
                    fromXYZ() As Double, gXYZ As Variant) As Boolean
    Dim rc As Long

    If srcCSys = 0 Then
        ' Already global rectangular: no transform needed
        pt.x = fromXYZ(0)
        pt.y = fromXYZ(1)
        pt.z = fromXYZ(2)
    Else
        ' Source CSys -> global (0). Input and output must be different
        ' variables (API guide), and the output must be a Variant.
        rc = App.feCoordTransform(srcCSys, fromXYZ, 0, gXYZ)
        If rc <> FE_OK Then
            PlacePoint = False
            Exit Function
        End If
        pt.x = gXYZ(0)
        pt.y = gXYZ(1)
        pt.z = gXYZ(2)
    End If

    pt.color = PT_COLOR
    pt.layer = LAYER_ID
    pt.Put(pt.NextEmptyID)       ' next unused ID, so existing geometry is never overwritten
    PlacePoint = True
End Function
