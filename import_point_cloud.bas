'==============================================================================
' IMPORT XYZ POINT CLOUD INTO FEMAP (WITH OPTIONAL COORDINATE SYSTEM TRANSFORM)
'==============================================================================
'
' PURPOSE
'   Reads a plain CSV/TXT file of X,Y,Z locations (one point per line, three
'   columns, no header) and creates a FEMAP geometry Point for each one. Use it
'   to overlay measured/thermal sensor locations on your FE model and visually
'   check that the two are spatially compatible.
'
' FILE FORMAT EXPECTED
'       1.250, 3.400, 0.750
'       1.300, 3.450, 0.750
'       ...
'   - Exactly three numeric columns per line: X, Y, Z
'   - Delimiters can be commas, tabs, spaces, or semicolons (or a mix)
'   - Blank lines and lines that are not three numbers are skipped and counted
'
' COORDINATE SYSTEM HANDLING (IMPORTANT)
'   FEMAP Point objects are ALWAYS stored in GLOBAL RECTANGULAR coordinates
'   (Femap API Guide, "Point Object Properties"). You cannot store a point
'   "in" another coordinate system. So this script asks which CSys your FILE
'   coordinates are defined in, then converts every point to global before
'   creating it:
'       CSys = 0  -> file is already global, no conversion
'       CSys = N  -> file coords are in FEMAP CSys N (rectangular, cylindrical,
'                    or spherical) and get converted to global
'   If your file is global and you type a non-zero CSys, the cloud will land in
'   the wrong place. When in doubt, enter 0.
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (or Ctrl+F11)
'   Paste this whole script into the editor, then click Run (green arrow).
'
' API REFERENCES USED (Femap API Guide 2025)
'   feFemap()            - gets a handle to the running FEMAP session
'   App.fePoint          - creates an (empty) Point object to fill and Put
'   Point.x/.y/.z        - global coordinates of the point
'   Point.layer/.color   - display properties
'   Point.NextEmptyID    - next unused point ID
'   Point.Put(id)        - writes the point to the model
'   feCoordTransform     - converts a coordinate between two coordinate systems
'   feViewRegenerate     - redraws the graphics window
'==============================================================================


'------------------------------------------------------------------------------
' USER SETTINGS - edit these three constants to suit your model
'------------------------------------------------------------------------------

' Layer the new points are placed on. Putting them on their own layer lets you
' turn the whole cloud on/off in View > Visibility without touching the model.
Const LAYER_ID  As Long = 9000

' FEMAP palette color index for the points. 12 may not be red in your palette;
' change this to any index that stands out against your mesh.
Const PT_COLOR  As Long = 12

' Multiplier applied to every coordinate value BEFORE the CSys transform.
' 1.0 = no change. Example: 0.0393701 converts millimeters to inches.
' NOTE: for cylindrical/spherical source systems this would also scale the
' angle columns, which is wrong. Only use it with rectangular source systems.
Const SCALE_FAC As Double = 1.0


'------------------------------------------------------------------------------
' MAIN PROGRAM - FEMAP runs the Sub named Main automatically
'------------------------------------------------------------------------------
Sub Main

    '--------------------------------------------------------------------------
    ' STEP 1: Connect to FEMAP
    '--------------------------------------------------------------------------
    ' "App" is the Femap application object. Every API call goes through it.
    Dim App As femap.model
    Set App = feFemap()    ' attaches to the FEMAP session this script runs in

    ' A Point object acts as a reusable "template": we fill in its properties
    ' (x, y, z, layer, color) and then Put it into the model. We reuse the same
    ' object for every line in the file and just overwrite the values.
    Dim pt As femap.Point
    Set pt = App.fePoint


    '--------------------------------------------------------------------------
    ' STEP 2: Ask the user for the file path and source coordinate system
    '--------------------------------------------------------------------------
    Dim fname As String
    fname = InputBox("Full path to XYZ file (csv or txt):", "Import Point Cloud", "C:\temp\thermal_points.csv")

    ' InputBox returns an empty string if the user clicks Cancel
    If fname = "" Then Exit Sub

    ' Dir() returns "" when the file does not exist - catch typos early
    If Dir(fname) = "" Then
        MsgBox "File not found: " & fname
        Exit Sub
    End If

    ' Ask which CSys the file's numbers are expressed in. Default 0 = global.
    Dim csysStr As String
    csysStr = InputBox("CSys ID the file coordinates are defined in (0 = global):", "Import Point Cloud", "0")
    If csysStr = "" Then Exit Sub

    ' Guard against letters or garbage being typed in
    If Not IsNumeric(csysStr) Then
        MsgBox "CSys ID must be a number."
        Exit Sub
    End If

    Dim fromCSys As Long
    fromCSys = CLng(csysStr)   ' convert the text to an integer ID


    '--------------------------------------------------------------------------
    ' STEP 3: Declare working variables used inside the read loop
    '--------------------------------------------------------------------------
    Dim fnum As Integer        ' file handle number for Open/Close
    Dim ln As String           ' one raw line of text from the file
    Dim parts() As String      ' the line split into its pieces (X, Y, Z)

    ' fromXYZ holds the coordinates as read from the file (in the source CSys).
    ' gXYZ holds the same point after conversion to global.
    ' The API requires these to be SEPARATE variables - feCoordTransform will
    ' not accept the same array for both input and output.
    Dim fromXYZ(2) As Double   ' indices 0,1,2 = X,Y,Z (or R,theta,Z etc.)
    Dim gXYZ(2) As Double

    Dim rc As Long             ' return code from feCoordTransform
    Dim nOK As Long            ' count of points successfully created
    Dim nBad As Long           ' count of lines skipped as unreadable
    Dim nXform As Long         ' count of points that went through a transform


    '--------------------------------------------------------------------------
    ' STEP 4: Open the file and process it one line at a time
    '--------------------------------------------------------------------------
    fnum = FreeFile            ' ask VB for an unused file number
    Open fname For Input As #fnum

    Do While Not EOF(fnum)     ' loop until the end of the file

        Line Input #fnum, ln   ' read one full line into ln

        '----------------------------------------------------------------------
        ' 4a. Clean the line so ANY common delimiter works
        '     Goal: turn "1.0   2.0<TAB>3.0" or "1.0;2.0;3.0" into "1.0,2.0,3.0"
        '----------------------------------------------------------------------
        ln = Replace(ln, vbTab, ",")   ' tabs   -> commas
        ln = Replace(ln, ";", ",")     ' semicolons -> commas
        ln = Trim(ln)                  ' strip leading/trailing spaces

        ' Collapse runs of spaces down to a single space, so "1.0    2.0"
        ' doesn't become "1.0,,,,2.0" in the next step
        Do While InStr(ln, "  ") > 0
            ln = Replace(ln, "  ", " ")
        Loop

        ln = Replace(ln, " ", ",")     ' remaining single spaces -> commas

        ' Mixed delimiters such as ", " can leave double commas; collapse them
        Do While InStr(ln, ",,") > 0
            ln = Replace(ln, ",,", ",")
        Loop

        '----------------------------------------------------------------------
        ' 4b. Skip empty lines silently (they are not errors)
        '----------------------------------------------------------------------
        If ln <> "" Then

            ' Split on commas. A valid line gives a 0-based array with at least
            ' 3 entries, so the highest index (UBound) must be 2 or more.
            parts = Split(ln, ",")

            If UBound(parts) >= 2 Then

                ' Make sure all three pieces are real numbers. This also
                ' rejects header rows like "X,Y,Z" if one sneaks in.
                If IsNumeric(parts(0)) And IsNumeric(parts(1)) And IsNumeric(parts(2)) Then

                    '----------------------------------------------------------
                    ' 4c. Convert text to numbers and apply the unit scale
                    '----------------------------------------------------------
                    fromXYZ(0) = CDbl(parts(0)) * SCALE_FAC   ' X (or R)
                    fromXYZ(1) = CDbl(parts(1)) * SCALE_FAC   ' Y (or theta)
                    fromXYZ(2) = CDbl(parts(2)) * SCALE_FAC   ' Z (or phi/Z)

                    '----------------------------------------------------------
                    ' 4d. Convert to GLOBAL coordinates if needed
                    '----------------------------------------------------------
                    If fromCSys = 0 Then
                        ' File is already global - just copy the values across
                        gXYZ(0) = fromXYZ(0)
                        gXYZ(1) = fromXYZ(1)
                        gXYZ(2) = fromXYZ(2)
                    Else
                        ' Ask FEMAP to convert from CSys "fromCSys" to CSys 0
                        ' (global rectangular). Result is written into gXYZ.
                        rc = App.feCoordTransform(fromCSys, fromXYZ, 0, gXYZ)

                        ' FE_NOT_EXIST means the CSys ID you entered isn't in
                        ' the model. Stop cleanly rather than create garbage.
                        If rc = FE_NOT_EXIST Then
                            Close #fnum    ' always close the file before leaving
                            MsgBox "CSys " & fromCSys & " does not exist. Nothing more imported." & vbCrLf & "Points created before the error: " & nOK
                            App.feViewRegenerate(0)
                            Exit Sub
                        End If

                        nXform = nXform + 1
                    End If

                    '----------------------------------------------------------
                    ' 4e. Create the FEMAP point
                    '----------------------------------------------------------
                    pt.x = gXYZ(0)         ' global X
                    pt.y = gXYZ(1)         ' global Y
                    pt.z = gXYZ(2)         ' global Z
                    pt.color = PT_COLOR    ' display color
                    pt.layer = LAYER_ID    ' put it on the dedicated layer

                    ' NextEmptyID finds the first unused point ID so we never
                    ' overwrite existing geometry. Put() writes it to the model.
                    pt.Put(pt.NextEmptyID)

                    nOK = nOK + 1

                Else
                    ' Three pieces exist but at least one isn't a number
                    nBad = nBad + 1
                End If

            Else
                ' Fewer than three values on the line
                nBad = nBad + 1
            End If

        End If
    Loop

    Close #fnum   ' done reading - release the file


    '--------------------------------------------------------------------------
    ' STEP 5: Redraw the screen and report results
    '--------------------------------------------------------------------------
    ' Without this the new points exist but may not appear until you redraw.
    App.feViewRegenerate(0)

    MsgBox "Created " & nOK & " points (" & nXform & " transformed from CSys " & fromCSys & " to global)." & vbCrLf & "Skipped " & nBad & " bad lines."

End Sub
