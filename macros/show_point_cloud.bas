'==============================================================================
' SHOW POINT CLOUD  --  plot an x,y,z,T cloud as FEMAP geometry Points
'==============================================================================
'
' PURPOSE
'   Overlay the thermal point cloud from npz_to_3D.py on the FE model as FEMAP
'   geometry Points, to visually check that it lands on the right mirror in the
'   right place/orientation.  This is a PLACEMENT check only -- all points are a
'   single color and the T column is ignored.  (The macro does NOT re-map the
'   cloud; npz_to_3D.py already centered it on the vertex and built the circle.)
'
' INPUT FILE  (from npz_to_3D.py)
'   One point per line, columns  x, y, z, T  (a header line "x,y,z,T" is fine --
'   non-numeric lines are skipped).  Any delimiter (comma/tab/space/semicolon).
'   Coordinates are in the MIRROR-LOCAL frame: origin at the vertex, x/y in the
'   mirror plane, z from the front surface into the thickness.  The 4th column
'   (T) is read past but not used.
'
' MIRROR LOCAL CSYS  (enter at the prompt; 0 = global)
'   Must be RECTANGULAR, origin at the mirror vertex, +Z along the optical axis.
'   The macro transforms each local x,y,z into global with feCoordTransform, so
'   the cloud sits on the real mirror.
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
' USER SETTINGS
'------------------------------------------------------------------------------
Const LAYER_ID  As Long   = 9100   ' layer for the cloud; toggle in View > Visibility
Const PT_COLOR  As Long   = 12     ' single Femap palette index for every point
Const SCALE_FAC As Double = 1.0    ' scales file values on read (0.0393701 = mm->in)
Const STRIDE    As Long   = 1      ' keep every Nth point (>1 thins a dense file)


Sub Main

    '--------------------------------------------------------------------------
    ' STEP 1: Connect to FEMAP and create the reusable objects
    '--------------------------------------------------------------------------
    Dim App As femap.model
    Set App = feFemap()

    Dim pt As femap.Point          ' reused for every point we create
    Set pt = App.fePoint

    Dim cs As femap.CSys           ' used to check the CSys exists and its type
    Set cs = App.feCSys


    '--------------------------------------------------------------------------
    ' STEP 2: Ask for the file and the mirror local CSys
    '--------------------------------------------------------------------------
    Dim fname As String
    fname = InputBox("Full path to the x,y,z,T file (csv or txt):", _
                     "Show Point Cloud", "C:\temp\M1_VizSkeleton_120s.csv")
    If fname = "" Then Exit Sub
    If Dir(fname) = "" Then
        MsgBox "File not found: " & fname
        Exit Sub
    End If

    Dim csysStr As String
    csysStr = InputBox("Mirror local CSys ID (rectangular, origin at vertex, +Z = optical axis)." & vbCrLf & _
                       "0 = global:", "Show Point Cloud", "0")
    If csysStr = "" Then Exit Sub
    If Not IsNumeric(csysStr) Then
        MsgBox "CSys ID must be a number."
        Exit Sub
    End If
    Dim fromCSys As Long
    fromCSys = CLng(csysStr)

    ' Validate the CSys BEFORE creating anything. IDs 0-2 are built-in global
    ' (0 = rectangular, 1 = cylindrical, 2 = spherical); user CSys are ID > 2.
    ' Our coordinates are cartesian, so the CSys must be RECTANGULAR.
    If fromCSys < 0 Then
        MsgBox "CSys ID can't be negative."
        Exit Sub
    ElseIf fromCSys = 1 Or fromCSys = 2 Then
        MsgBox "This macro needs a RECTANGULAR CSys (0 or a user rectangular CSys)."
        Exit Sub
    ElseIf fromCSys > 2 Then
        If cs.Get(fromCSys) <> FE_OK Then
            MsgBox "CSys " & fromCSys & " does not exist in this model."
            Exit Sub
        End If
        If cs.type <> 0 Then
            MsgBox "CSys " & fromCSys & " is not rectangular."
            Exit Sub
        End If
    End If


    '--------------------------------------------------------------------------
    ' STEP 3: Read the file and create one point per valid line
    '--------------------------------------------------------------------------
    ' fromXYZ = coordinates in the source CSys (input, a plain Double array)
    ' gXYZ    = coordinates in global (OUTPUT - must be a Variant, per the guide)
    Dim fromXYZ(2) As Double
    Dim gXYZ As Variant

    Dim fnum As Integer, ln As String, parts() As String
    Dim ok As Boolean, k As Long
    Dim nRead As Long, nBad As Long, nOK As Long, nSkip As Long
    Dim rowIdx As Long
    rowIdx = -1

    ' Lock the UI so thousands of Puts don't redraw one at a time.
    ' Every exit path below must call feAppUnlock.
    App.feAppLock

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
            ' Need at least x,y,z (3 numeric columns); T (col 4) is ignored.
            ok = (UBound(parts) >= 2)
            If ok Then
                For k = 0 To 2
                    If Not IsNumeric(parts(k)) Then ok = False
                Next k
            End If

            If ok Then
                nRead = nRead + 1
                rowIdx = rowIdx + 1
                ' STRIDE: keep every Nth valid row (rowIdx 0,1,2,... )
                If STRIDE > 1 And (rowIdx Mod STRIDE) <> 0 Then
                    nSkip = nSkip + 1
                Else
                    fromXYZ(0) = CDbl(parts(0)) * SCALE_FAC
                    fromXYZ(1) = CDbl(parts(1)) * SCALE_FAC
                    fromXYZ(2) = CDbl(parts(2)) * SCALE_FAC
                    If Not PlacePoint(App, pt, fromCSys, fromXYZ, gXYZ) Then GoTo CSysFail
                    nOK = nOK + 1
                End If
            Else
                nBad = nBad + 1                       ' header, text, too few columns
            End If
        End If
    Loop
    Close #fnum

    App.feAppUnlock
    App.feViewRegenerate(0)

    If nRead = 0 Then
        MsgBox "No valid lines found. Expected at least 3 numeric columns (x,y,z) per line."
        Exit Sub
    End If


    '--------------------------------------------------------------------------
    ' STEP 4: Report
    '--------------------------------------------------------------------------
    Dim msg As String
    msg = "File: " & fname & vbCrLf & _
          "Valid data lines: " & nRead & "  (skipped " & nBad & " unreadable)" & vbCrLf & _
          "Points created:   " & nOK & vbCrLf
    If STRIDE > 1 Then msg = msg & "Thinned by STRIDE=" & STRIDE & " (skipped " & nSkip & ")" & vbCrLf
    msg = msg & "CSys: " & fromCSys & "   Layer: " & LAYER_ID & "   Color: " & PT_COLOR
    MsgBox msg
    Exit Sub

CSysFail:
    ' Reached only if feCoordTransform reports the CSys doesn't exist
    Close #fnum
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
