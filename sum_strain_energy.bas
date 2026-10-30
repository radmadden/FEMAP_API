'==============================================================================
' SUM STRAIN ENERGY  --  element strain energy over a selection, by group/property
'==============================================================================
'
' PURPOSE
'   For a user-selected (and user-named) set of elements and one or more output
'   sets, report the summed element strain energy and its share of the model
'   total for:
'     - the selection as a whole (labelled with the name you type in),
'     - each picked group (the WHOLE group, not limited to the selection),
'     - each property present in the selection (these rows add up to the
'       selection row).
'   Each row shows two percents: "Calc %" (our sum / sum over every element
'   with output) and "Nastran %" (sum of Nastran's per-element percent-of-total
'   vector).
'
' OUTPUT
'   A block per output set ALWAYS goes to the Messages window.  On top of that
'   you choose one of:
'     - Messages window only,
'     - an Excel workbook, left open and unsaved, with two sheets:
'         Summary - Calc % matrix, rows = selection/groups/properties,
'                   columns = output sets, with a white->yellow->red color scale
'         Detail  - one long filterable table, one row per (output set, row)
'     - a CSV file with the same columns as the Detail sheet.
'   If Excel can't be started, the macro warns and writes the CSV instead.
'
' HOW IT WORKS
'   Vector IDs come from ResultsIDQuery.Elemental (VEO_STRAIN_ENERGY and
'   VEO_STRAIN_ENERGY_PERCENT), not hard-coded.  Element -> property is mapped
'   once up front with Set.AddRule(FGD_ELEM_BYPROP), so no per-element Get.
'   For each output set the Results Browsing Object loads both columns for
'   every element and the macro walks the rows once, adding each value to every
'   "bucket" (selection / group / property) its element belongs to.  The
'   per-set results are kept in module-level arrays so the Excel/CSV writers can
'   lay them out after all sets are done.
'
' ASSUMES
'   - Element strain energy was requested in the solve (Nastran ESE=ALL).  If
'     ESE covered only a subset, "Total" and "Calc %" are relative to that
'     subset while "Nastran %" is relative to Nastran's subcase total.
'   - An element can sit in several picked groups, so group rows can sum past
'     100%.  Groups that don't cover the model sum to less than 100%.
'   - Static and modal output.  Complex / frequency response is not supported.
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (Ctrl+F11), paste, then Run.
'   1. pick the elements          2. name the selection (blank = "Selection")
'   3. pick groups (Cancel = none) 4. property breakdown Yes/No
'   5. choose the output           6. pick output sets
'   Nothing in the model is changed.
'
' API CALLS USED (Femap API Guide 2025)
'   feFemap, feSet(.Select/.AddGroup/.AddRule/.IsAdded/.Count/.Last/.Next/
'   .Reset/.Clear), feGroup(.Get/.title), feProp(.First/.Next/.ID/.title),
'   feOutputSet(.Get/.title), feResultsIDQuery(.Elemental), feResults(.Clear/
'   .AddColumnV2/.Populate/.GetColumn), feAppGetModel, feAppGetModelName,
'   feAppMessage.  Excel is driven by late-bound COM (CreateObject).
'==============================================================================


'------------------------------------------------------------------------------
' Output choices (index of the option button in the output dialog)
'------------------------------------------------------------------------------
Const OUT_MSG As Long = 0       ' Messages window only
Const OUT_XL  As Long = 1       ' + Excel workbook
Const OUT_CSV As Long = 2       ' + CSV file


'------------------------------------------------------------------------------
' Collected results, shared with the Excel/CSV writers.
'
'   Buckets (the rows of every report) are indexed b = 0 .. gNB-1:
'     b = 0                     the selection
'     b = 1 .. gNGrp            the picked groups, in pick order
'     b = gNGrp+1 .. gNB-1      the properties found in the selection
'   Stored output sets are indexed s = 0 .. gNS-1 (sets with no strain energy
'   are skipped and never stored, so s is NOT the output set ID - see gSetID).
'------------------------------------------------------------------------------
Dim gNB As Long                 ' number of buckets
Dim gNGrp As Long               ' number of group buckets
Dim gLabel() As String          ' (b) row label, e.g. "Group 3: Skin"

Dim gNS As Long                 ' number of output sets actually stored
Dim gSetID() As Long            ' (s) FEMAP output set ID
Dim gSetTitle() As String       ' (s) output set title
Dim gTot() As Double            ' (s) SE summed over every element with output
Dim gHavePc() As Boolean        ' (s) True if the Nastran percent vector exists
Dim gSE() As Double             ' (s, b) summed strain energy
Dim gPC() As Double             ' (s, b) summed Nastran percent (already in %)
Dim gN() As Long                ' (s, b) elements in the bucket that had output

Dim gSelName As String          ' user's name for the selection
Dim gSelCount As Long           ' number of selected elements
Dim gModelPath As String        ' model file path, "" if untitled/unknown


Sub Main

    Dim App As femap.model
    Set App = feFemap()

    '--------------------------------------------------------------------------
    ' STEP 1: prompts
    '   Elements are required; everything else has a sensible default so the
    '   macro can be clicked straight through.
    '--------------------------------------------------------------------------

    ' Set.Select shows FEMAP's standard entity picker, so picking by group,
    ' property, material, etc. already works.  True = start with an empty set.
    ' It returns FE_OK, or FE_CANCEL if the user pressed Cancel.
    Dim eSet As femap.Set
    Set eSet = App.feSet
    If eSet.Select(FT_ELEM, True, "Select Elements to Sum Strain Energy") <> FE_OK Then Exit Sub
    If eSet.Count() = 0 Then
        MsgBox "No elements selected."
        Exit Sub
    End If
    gSelCount = eSet.Count()

    ' Name for the selection row.  InputBox returns "" both for Cancel and for
    ' a blank answer, and either way we just keep the default.
    gSelName = Trim(InputBox("Name for this selection:", "Sum Strain Energy", "Selection"))
    If gSelName = "" Then gSelName = "Selection"

    ' Groups are optional: Cancel or an empty pick just means no group rows.
    ' (Select with True clears the set first, so after Cancel it is empty.)
    Dim gpSet As femap.Set
    Set gpSet = App.feSet
    Dim rc As Long
    rc = gpSet.Select(FT_GROUP, True, "Groups for Breakdown (Cancel = none)")

    Dim doProps As Boolean
    doProps = (MsgBox("Break down the selection by property?", vbYesNo, _
                      "Sum Strain Energy") = vbYes)

    ' Output choice.  An OptionGroup stores the 0-based index of the chosen
    ' button in .Choice, which lines up with the OUT_* constants.  Dialog()
    ' returns 0 for Cancel.
    Begin Dialog OUTDLG 210, 92, "Sum Strain Energy - Output"
        Text         10,  6, 190, 10, "Write the results to:"
        OptionGroup .Choice
            OptionButton 20, 20, 180, 10, "Messages window only"
            OptionButton 20, 34, 180, 10, "Messages window + Excel workbook"
            OptionButton 20, 48, 180, 10, "Messages window + CSV file"
        OKButton     35, 68, 60, 14
        CancelButton 115, 68, 60, 14
    End Dialog
    Dim od As OUTDLG
    od.Choice = OUT_MSG
    If Dialog(od) = 0 Then Exit Sub
    Dim outChoice As Long
    outChoice = od.Choice
    ' Echo the choice so a run that writes no file shows why.
    App.feAppMessage(FCM_NORMAL, "Sum Strain Energy output choice = " & CStr(outChoice) & _
                     " (0 Messages only, 1 Excel, 2 CSV)")

    ' Output sets: FT_OUT_CASE (28) is the "output set" entity type, so the
    ' same picker gives a multi-select list of them.
    Dim osSet As femap.Set
    Set osSet = App.feSet
    If osSet.Select(FT_OUT_CASE, True, "Select Output Sets") <> FE_OK Then Exit Sub
    If osSet.Count() = 0 Then
        MsgBox "No output sets selected."
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 2: buckets (the report rows)
    '   0 = selection, 1..nGrp = groups, then one slot per property that owns
    '   at least one selected element.  Building group sets and the property map
    '   here, once, keeps the per-output-set loop to pure array lookups.
    '--------------------------------------------------------------------------
    gNGrp = gpSet.Count()

    ' One element set per picked group.  AddGroup(FT_ELEM, id) copies ALL of
    ' the group's element IDs into the set, so group rows cover the whole group
    ' regardless of the element pick.
    Dim grpSets() As Object
    ReDim gLabel(gNGrp)
    If gNGrp > 0 Then ReDim grpSets(gNGrp - 1)
    gLabel(0) = gSelName

    Dim gp As femap.Group
    Set gp = App.feGroup
    Dim g As Long
    g = 0
    gpSet.Reset()
    Do While gpSet.Next()
        Set grpSets(g) = App.feSet
        rc = grpSets(g).AddGroup(FT_ELEM, gpSet.CurrentID)
        gLabel(1 + g) = "Group " & CStr(gpSet.CurrentID)
        If gp.Get(gpSet.CurrentID) = FE_OK Then gLabel(1 + g) = gLabel(1 + g) & ": " & gp.title
        g = g + 1
    Loop

    ' Property slots.  propOf(elemID) = bucket index, 0 = none.  Only selected
    ' elements are ever looked up, so the array only needs to reach the
    ' selection's highest ID, which Set.Last() returns (sets are kept sorted).
    ' This array replaces a Collection/Dictionary, which FEMAP's WinWrap Basic
    ' does not have.
    Dim maxSel As Long
    maxSel = eSet.Last()
    Dim propOf() As Long
    ReDim propOf(maxSel)
    Dim nProp As Long
    nProp = 0
    If doProps Then
        Dim pr As femap.Prop
        Set pr = App.feProp
        Dim ps As femap.Set
        Set ps = App.feSet
        Dim slot As Long, hit As Boolean
        ' Prop.First()/Next() return nonzero while a property is current.
        rc = pr.First()
        Do While rc
            ' AddRule(propID, FGD_ELEM_BYPROP) fills ps with every element that
            ' references this property - the same rule FEMAP's "Element by
            ' Property" selection uses.  AddRule adds, so clear ps first.
            ps.Clear()
            ps.AddRule(pr.ID, FGD_ELEM_BYPROP)
            hit = False
            slot = 1 + gNGrp + nProp
            ps.Reset()
            Do While ps.Next()
                If eSet.IsAdded(ps.CurrentID) Then
                    propOf(ps.CurrentID) = slot
                    hit = True
                End If
            Loop
            ' Only give the property a row if it owns a selected element.
            If hit Then
                ReDim Preserve gLabel(slot)
                gLabel(slot) = "Prop " & CStr(pr.ID) & ": " & pr.title
                nProp = nProp + 1
            End If
            rc = pr.Next()
        Loop
    End If

    gNB = 1 + gNGrp + nProp

    ' Per-set result storage, sized for every picked output set.  WinWrap's
    ' ReDim Preserve can only grow the LAST dimension, so size the set
    ' dimension up front and count the sets actually stored in gNS.
    Dim nSetMax As Long
    nSetMax = osSet.Count()
    ReDim gSetID(nSetMax - 1)
    ReDim gSetTitle(nSetMax - 1)
    ReDim gTot(nSetMax - 1)
    ReDim gHavePc(nSetMax - 1)
    ReDim gSE(nSetMax - 1, gNB - 1)
    ReDim gPC(nSetMax - 1, gNB - 1)
    ReDim gN(nSetMax - 1, gNB - 1)
    gNS = 0

    '--------------------------------------------------------------------------
    ' STEP 3: vector IDs
    '   Since v2020.1 output vector IDs depend on solver and version, so look
    '   them up rather than hard-coding (api ~1723).  Elemental() takes no
    '   output set: it says which ID FEMAP uses for the quantity, and whether a
    '   given set actually holds it is checked by AddColumnV2 below.
    '--------------------------------------------------------------------------
    Dim idq As femap.ResultsIDQuery
    Set idq = App.feResultsIDQuery
    Dim seVec As Long, pcVec As Long
    seVec = idq.Elemental(VEO_STRAIN_ENERGY)
    pcVec = idq.Elemental(VEO_STRAIN_ENERGY_PERCENT)
    If seVec <= 0 Then
        MsgBox "Could not determine the strain energy output vector ID."
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 4: sum per output set (and print the Messages window block)
    '   The Results Browsing Object (RBO) is FEMAP's fast bulk reader: add
    '   columns, Populate once, then pull a whole column into arrays.
    '--------------------------------------------------------------------------
    Dim rbo As femap.Results
    Set rbo = App.feResults
    Dim os As femap.OutputSet
    Set os = App.feOutputSet

    App.feAppMessage(FCM_HIGHLIGHT, "Strain energy sum: " & gSelName & " = " & _
                     CStr(gSelCount) & " elements, vectors SE=" & CStr(seVec) & _
                     " SE%=" & CStr(pcVec))

    Dim nCols As Long, seCols As Variant, pcCols As Variant
    Dim vIDs As Variant, vVals As Variant
    Dim pIDs As Variant, pVals As Variant
    Dim havePc As Boolean
    Dim i As Long, b As Long, id As Long, s As Long
    Dim v As Double, p As Double, sumAll As Double
    Dim nSkip As Long
    Dim ttl As String

    nSkip = 0
    osSet.Reset()
    Do While osSet.Next()
        ttl = ""
        If os.Get(osSet.CurrentID) = FE_OK Then ttl = os.title

        ' Clear() empties the RBO so it can be reused for this set.
        ' AddColumnV2 returns FE_NOT_EXIST if this set has no strain energy
        ' vector (ESE not requested for that subcase/mode).  seCols(0) is the
        ' index of the column it added.  The percent column is optional.
        rc = rbo.Clear()
        rc = rbo.AddColumnV2(osSet.CurrentID, seVec, False, nCols, seCols)
        If rc <> FE_OK Then
            App.feAppMessage(FCM_WARNING, "Set " & CStr(osSet.CurrentID) & _
                             ": no strain energy output - skipped (" & ttl & ")")
            nSkip = nSkip + 1
        Else
            havePc = False
            If pcVec > 0 Then
                havePc = (rbo.AddColumnV2(osSet.CurrentID, pcVec, False, nCols, pcCols) = FE_OK)
            End If

            ' No DataNeeded() filter: load EVERY element so the model total is
            ' available for Calc %.
            rc = rbo.Populate()

            ' This set's storage slot.
            s = gNS
            gSetID(s) = osSet.CurrentID
            gSetTitle(s) = ttl
            sumAll = 0

            ' GetColumn returns two 0-based arrays: element IDs and values.
            ' Both columns come from the same populated object, so row i is the
            ' same element in each.
            If rbo.GetColumn(seCols(0), vIDs, vVals) = FE_OK Then
                If havePc Then havePc = (rbo.GetColumn(pcCols(0), pIDs, pVals) = FE_OK)
                For i = 0 To UBound(vIDs)
                    id = vIDs(i)
                    v = vVals(i)
                    p = 0
                    If havePc Then p = pVals(i)
                    sumAll = sumAll + v

                    ' An element can land in several buckets: the selection,
                    ' its property (only if selected), and any picked group.
                    If eSet.IsAdded(id) Then
                        AddTo s, 0, v, p
                        If nProp > 0 Then
                            b = propOf(id)
                            If b > 0 Then AddTo s, b, v, p
                        End If
                    End If
                    For g = 0 To gNGrp - 1
                        If grpSets(g).IsAdded(id) Then AddTo s, 1 + g, v, p
                    Next g
                Next i
            End If
            gTot(s) = sumAll
            gHavePc(s) = havePc
            gNS = gNS + 1

            App.feAppMessage(FCM_NORMAL, "Set " & CStr(gSetID(s)) & ": " & ttl & _
                             "   Total SE = " & Format(sumAll, "0.000000E+00"))
            App.feAppMessage(FCM_NORMAL, "  Bucket, SE, Calc %, Nastran %, Elems w/ data")
            For b = 0 To gNB - 1
                PrintRow App, s, b
            Next b
        End If
    Loop

    If nSkip > 0 Then
        App.feAppMessage(FCM_WARNING, CStr(nSkip) & " output set(s) had no " & _
                         "strain energy. Request ESE in the analysis case.")
    End If

    '--------------------------------------------------------------------------
    ' STEP 5: file output
    '   Done after all sets so the Summary sheet can lay sets out as columns.
    '   If Excel can't be started, fall back to CSV so the run isn't wasted.
    '--------------------------------------------------------------------------
    If outChoice = OUT_MSG Then Exit Sub
    If gNS = 0 Then
        App.feAppMessage(FCM_WARNING, "No output set had strain energy - no file written.")
        Exit Sub
    End If

    gModelPath = ModelPath(App)

    If outChoice = OUT_XL Then
        App.feAppMessage(FCM_NORMAL, "Starting Excel...")
        If Not WriteExcel(App) Then
            App.feAppMessage(FCM_WARNING, "Excel could not be started - writing a CSV instead.")
            outChoice = OUT_CSV
        End If
    End If
    If outChoice = OUT_CSV Then WriteCsv App

End Sub


'------------------------------------------------------------------------------
' AddTo - add one element's values into bucket b of stored set s.
'   v = its strain energy, p = its Nastran percent (0 if no percent vector).
'------------------------------------------------------------------------------
Sub AddTo(s As Long, b As Long, v As Double, p As Double)
    gSE(s, b) = gSE(s, b) + v
    gPC(s, b) = gPC(s, b) + p
    gN(s, b) = gN(s, b) + 1
End Sub


'------------------------------------------------------------------------------
' BucketType - "Selection", "Group" or "Property" for bucket b.  Goes in the
' Type column of the Detail sheet / CSV so rows can be filtered by kind even
' though the selection row carries the user's own name.
'------------------------------------------------------------------------------
Function BucketType(b As Long) As String
    If b = 0 Then
        BucketType = "Selection"
    ElseIf b <= gNGrp Then
        BucketType = "Group"
    Else
        BucketType = "Property"
    End If
End Function


'------------------------------------------------------------------------------
' PrintRow - one comma-separated bucket line in the Messages window.
'   Calc % = bucket SE / total SE.  Nastran % = summed Nastran percents, which
'   are already in percent units.  "n/a" when not available.
'------------------------------------------------------------------------------
Sub PrintRow(App As femap.model, s As Long, b As Long)
    Dim calc As String, nas As String
    If gTot(s) <> 0 Then
        calc = Format(100# * gSE(s, b) / gTot(s), "0.00") & "%"
    Else
        calc = "n/a"
    End If
    If gHavePc(s) Then
        nas = Format(gPC(s, b), "0.00") & "%"
    Else
        nas = "n/a"
    End If
    App.feAppMessage(FCM_NORMAL, "  " & gLabel(b) & ", " & Format(gSE(s, b), "0.000000E+00") & _
                     ", " & calc & ", " & nas & ", " & CStr(gN(s, b)))
End Sub


'------------------------------------------------------------------------------
' ModelPath - full path of the active model file, or "" if it is untitled or
' can't be read.
'   feAppGetModel returns the active model's internal ID (an INT8, api ~39723),
'   and feAppGetModelName turns that ID into its file name (api ~39788).  An
'   unsaved model comes back as "Untitled".  The ID is held in a Variant and
'   errors are trapped, so a type mismatch on the 64-bit ID just falls back to
'   "" instead of stopping the macro.
'------------------------------------------------------------------------------
Function ModelPath(App As femap.model) As String
    Dim m As Variant, nm As String
    ModelPath = ""
    On Error GoTo done
    If App.feAppGetModel(m) <> FE_OK Then Exit Function
    If App.feAppGetModelName(m, nm) <> FE_OK Then Exit Function
    If InStr(nm, "\") > 0 Then ModelPath = nm     ' "Untitled" has no folder
done:
End Function


'------------------------------------------------------------------------------
' DetailRow - fill row r of a 2-D Variant array with the Detail/CSV columns for
' stored set s, bucket b:
'   0 SetID | 1 Set Title | 2 Bucket | 3 Type | 4 SE | 5 Total SE |
'   6 Calc % | 7 Nastran % | 8 Elems w/ data
' Percents are stored as FRACTIONS (0.2703) because Excel's "0.00%" format
' multiplies by 100 for display; the CSV writer converts back to 27.03.
' Unavailable percents are left Empty so they show as blank cells.
'------------------------------------------------------------------------------
Sub DetailRow(d() As Variant, r As Long, s As Long, b As Long)
    d(r, 0) = gSetID(s)
    d(r, 1) = gSetTitle(s)
    d(r, 2) = gLabel(b)
    d(r, 3) = BucketType(b)
    d(r, 4) = gSE(s, b)
    d(r, 5) = gTot(s)
    If gTot(s) <> 0 Then d(r, 6) = gSE(s, b) / gTot(s)
    If gHavePc(s) Then d(r, 7) = gPC(s, b) / 100#
    d(r, 8) = gN(s, b)
End Sub


'------------------------------------------------------------------------------
' WriteExcel - build the Summary + Detail workbook and leave it open, unsaved.
'   Returns False only if Excel could not be started (caller falls back to
'   CSV).  Any later Excel error is reported and the workbook is left as far as
'   it got.
'
'   Excel is driven by late-bound COM: CreateObject("Excel.Application")
'   starts a new, hidden Excel; every property/method after that is resolved at
'   run time, so no Excel reference or type library is needed in FEMAP.  Excel
'   constants (xlEdgeTop etc.) aren't defined here, so their numeric values are
'   used with a comment.
'
'   Speed: each COM call to Excel is slow, so each sheet's data is built in a
'   2-D Variant array and written with ONE Range.Value assignment rather than
'   cell by cell.  ScreenUpdating is off while filling.
'------------------------------------------------------------------------------
Function WriteExcel(App As femap.model) As Boolean
    Dim xl As Object, wb As Object, wsS As Object, wsD As Object
    WriteExcel = False

    On Error Resume Next
    Set xl = CreateObject("Excel.Application")
    On Error GoTo 0
    If xl Is Nothing Then Exit Function
    WriteExcel = True

    On Error GoTo xlFail
    ' UserControl = True hands this Excel to the user.  Without it, an Excel
    ' started by CreateObject shuts itself down as soon as the macro ends and
    ' the last reference (xl) is released - the workbook would flash up and
    ' vanish.  Set it before anything else so even a partial workbook stays.
    xl.UserControl = True
    xl.Visible = True
    xl.ScreenUpdating = False
    xl.DisplayAlerts = False            ' no "delete sheet?" confirmation

    ' A new workbook has 1..n sheets depending on the user's Excel settings.
    ' Make exactly two: Worksheets.Add inserts before the active sheet, and
    ' extra sheets are deleted from the end.
    Set wb = xl.Workbooks.Add
    Do While wb.Worksheets.Count < 2
        wb.Worksheets.Add
    Loop
    Do While wb.Worksheets.Count > 2
        wb.Worksheets(wb.Worksheets.Count).Delete
    Loop
    Set wsS = wb.Worksheets(1)
    Set wsD = wb.Worksheets(2)
    wsS.Name = "Summary"
    wsD.Name = "Detail"

    Dim d() As Variant
    Dim r As Long, c As Long, s As Long, b As Long
    Dim nR As Long, nC As Long

    '--- Summary sheet ---------------------------------------------------------
    ' Layout (1-based Excel rows):
    '   1  title            2  model             3  selection name, count, date
    '   5  header: "Row" | "SetID: title" per stored set
    '   6  Total SE per set
    '   7+ one row per bucket, Calc % as fractions
    Const HDR As Long = 5
    wsS.Cells(1, 1).Value = "Strain energy share (Calc %)"
    wsS.Cells(1, 1).Font.Bold = True
    wsS.Cells(1, 1).Font.Size = 14
    If gModelPath <> "" Then
        wsS.Cells(2, 1).Value = "Model: " & gModelPath
    Else
        wsS.Cells(2, 1).Value = "Model: (untitled)"
    End If
    wsS.Cells(3, 1).Value = gSelName & ": " & CStr(gSelCount) & " elements    " & _
                            Format(Now, "yyyy-mm-dd hh:nn")

    ' Header + Total row + one row per bucket; column 0 = labels.
    nR = 2 + gNB
    nC = 1 + gNS
    ReDim d(nR - 1, nC - 1)
    d(0, 0) = "Bucket"
    d(1, 0) = "Total SE"
    For s = 0 To gNS - 1
        d(0, 1 + s) = CStr(gSetID(s)) & ": " & gSetTitle(s)
        d(1, 1 + s) = gTot(s)
        For b = 0 To gNB - 1
            If gTot(s) <> 0 Then d(2 + b, 1 + s) = gSE(s, b) / gTot(s)
        Next b
    Next s
    For b = 0 To gNB - 1
        d(2 + b, 0) = gLabel(b)
    Next b
    wsS.Range(wsS.Cells(HDR, 1), wsS.Cells(HDR + nR - 1, nC)).Value = d

    ' Header row: bold on a light blue fill, wrapped so long set titles don't
    ' blow out the column widths.
    With wsS.Range(wsS.Cells(HDR, 1), wsS.Cells(HDR, nC))
        .Font.Bold = True
        .Interior.Color = RGB(221, 235, 247)
        .WrapText = True
        .VerticalAlignment = -4108      ' xlCenter
    End With
    ' Total SE row: italic, scientific.
    wsS.Range(wsS.Cells(HDR + 1, 1), wsS.Cells(HDR + 1, nC)).Font.Italic = True
    wsS.Range(wsS.Cells(HDR + 1, 2), wsS.Cells(HDR + 1, nC)).NumberFormat = "0.000E+00"
    ' Make the selection row's label bold so the headline number stands out.
    wsS.Cells(HDR + 2, 1).Font.Bold = True

    ' The percent block: % format plus a 3-color scale (white low, yellow mid,
    ' red high) so the hot spots jump out.  AddColorScale(3) creates the rule;
    ' ColorScaleCriteria(1..3) are its low/mid/high points.  RGB() gives the
    ' BGR long Excel expects.
    Dim pctRng As Object, cs As Object
    Set pctRng = wsS.Range(wsS.Cells(HDR + 2, 2), wsS.Cells(HDR + nR - 1, nC))
    pctRng.NumberFormat = "0.0%"
    Set cs = pctRng.FormatConditions.AddColorScale(3)
    cs.ColorScaleCriteria(1).FormatColor.Color = RGB(255, 255, 255)
    cs.ColorScaleCriteria(2).FormatColor.Color = RGB(255, 235, 132)
    cs.ColorScaleCriteria(3).FormatColor.Color = RGB(248, 105, 107)

    ' Thin rule above the first group row and the first property row, so the
    ' three kinds of rows read as separate blocks.  Borders(8) = xlEdgeTop,
    ' LineStyle 1 = xlContinuous, Weight 2 = xlThin.
    If gNGrp > 0 Then RuleAbove wsS, HDR + 2 + 1, nC
    If gNB - 1 - gNGrp > 0 Then RuleAbove wsS, HDR + 2 + 1 + gNGrp, nC

    ' Size columns to their contents, but cap them so one long title doesn't
    ' make the sheet unreadable (the header wraps instead).
    wsS.Columns(1).AutoFit
    For c = 2 To nC
        wsS.Columns(c).ColumnWidth = 14
    Next c
    If wsS.Columns(1).ColumnWidth > 45 Then wsS.Columns(1).ColumnWidth = 45

    ' Freeze the header, Total row and label column so they stay visible when
    ' scrolling.  FreezePanes acts on the active window at the selected cell.
    wsS.Activate
    xl.ActiveWindow.FreezePanes = False
    wsS.Cells(HDR + 2, 2).Select
    xl.ActiveWindow.FreezePanes = True

    '--- Detail sheet ----------------------------------------------------------
    ' Header in row 1, then one row per (stored set, bucket).
    nR = 1 + gNS * gNB
    ReDim d(nR - 1, 8)
    d(0, 0) = "SetID"
    d(0, 1) = "Set Title"
    d(0, 2) = "Bucket"
    d(0, 3) = "Type"
    d(0, 4) = "SE"
    d(0, 5) = "Total SE"
    d(0, 6) = "Calc %"
    d(0, 7) = "Nastran %"
    d(0, 8) = "Elems w/ data"
    r = 1
    For s = 0 To gNS - 1
        For b = 0 To gNB - 1
            DetailRow d, r, s, b
            r = r + 1
        Next b
    Next s
    wsD.Range(wsD.Cells(1, 1), wsD.Cells(nR, 9)).Value = d

    With wsD.Range(wsD.Cells(1, 1), wsD.Cells(1, 9))
        .Font.Bold = True
        .Interior.Color = RGB(221, 235, 247)
    End With
    wsD.Range(wsD.Cells(2, 5), wsD.Cells(nR, 6)).NumberFormat = "0.000E+00"
    wsD.Range(wsD.Cells(2, 7), wsD.Cells(nR, 8)).NumberFormat = "0.00%"
    ' AutoFilter on the whole table: dropdowns on each header for filtering
    ' by set, bucket, or Type.
    wsD.Range(wsD.Cells(1, 1), wsD.Cells(nR, 9)).AutoFilter
    wsD.Columns("A:I").AutoFit

    wsD.Activate
    xl.ActiveWindow.FreezePanes = False
    wsD.Cells(2, 1).Select
    xl.ActiveWindow.FreezePanes = True

    ' Land the user on the Summary sheet.
    wsS.Activate
    wsS.Cells(1, 1).Select
    xl.ScreenUpdating = True
    xl.DisplayAlerts = True
    ' Excel may open behind FEMAP; bring it to the front.  (Best effort - some
    ' Windows focus rules block this, so ignore any error.)
    On Error Resume Next
    AppActivate xl.Caption
    On Error GoTo 0
    App.feAppMessage(FCM_HIGHLIGHT, "Excel workbook written (unsaved): Summary + Detail " & _
                     "sheets. Check the taskbar if it isn't in front.")
    Exit Function

xlFail:
    ' Something went wrong partway: report it, but leave Excel visible and
    ' usable with whatever was written.
    App.feAppMessage(FCM_ERROR, "Excel output stopped: " & Err.Description)
    On Error Resume Next
    xl.ScreenUpdating = True
    xl.DisplayAlerts = True
End Function


'------------------------------------------------------------------------------
' RuleAbove - thin top border across columns 1..nC of row r.
'   Borders(8) = xlEdgeTop, LineStyle 1 = xlContinuous, Weight 2 = xlThin.
'------------------------------------------------------------------------------
Sub RuleAbove(ws As Object, r As Long, nC As Long)
    With ws.Range(ws.Cells(r, 1), ws.Cells(r, nC)).Borders(8)
        .LineStyle = 1
        .Weight = 2
    End With
End Sub


'------------------------------------------------------------------------------
' WriteCsv - write the Detail table as a CSV.
'   Prompts for the path; default = model folder + "<model>_strain_energy.csv",
'   or C:\temp\strain_energy.csv for an untitled model.  Blank/Cancel skips.
'   Percents are written as plain numbers (27.03, not 0.2703) so the file reads
'   naturally in a text editor; unavailable values are left as empty fields.
'------------------------------------------------------------------------------
Sub WriteCsv(App As femap.model)
    Dim defPath As String, path As String
    defPath = "C:\temp\strain_energy.csv"
    If gModelPath <> "" Then
        ' Strip the extension (.modfem) and append the suffix.
        Dim dot As Long
        dot = InStrRev(gModelPath, ".")
        If dot > InStrRev(gModelPath, "\") Then
            defPath = Left(gModelPath, dot - 1) & "_strain_energy.csv"
        Else
            defPath = gModelPath & "_strain_energy.csv"
        End If
    End If
    path = Trim(InputBox("CSV file to write:", "Sum Strain Energy", defPath))
    If path = "" Then Exit Sub

    ' Build the rows with the same helper as the Excel Detail sheet, then
    ' format each field as text.
    Dim d() As Variant
    ReDim d(0, 8)
    ' (Named txt, not line - Line is a Basic keyword, as in Line Input.)
    Dim f As Integer, s As Long, b As Long, txt As String

    On Error GoTo csvFail
    f = FreeFile
    Open path For Output As #f
    Print #f, "SetID,Set Title,Bucket,Type,SE,Total SE,Calc %,Nastran %,Elems w/ data"
    For s = 0 To gNS - 1
        For b = 0 To gNB - 1
            ReDim d(0, 8)               ' fresh row, so unset cells are Empty
            DetailRow d, 0, s, b
            txt = CStr(d(0, 0)) & "," & CsvQuote(CStr(d(0, 1))) & "," & _
                  CsvQuote(CStr(d(0, 2))) & "," & d(0, 3) & "," & _
                  Format(d(0, 4), "0.000000E+00") & "," & _
                  Format(d(0, 5), "0.000000E+00") & "," & _
                  PctField(d(0, 6)) & "," & PctField(d(0, 7)) & "," & _
                  CStr(d(0, 8))
            Print #f, txt
        Next b
    Next s
    Close #f
    App.feAppMessage(FCM_NORMAL, "CSV written: " & path)
    Exit Sub

csvFail:
    App.feAppMessage(FCM_ERROR, "Could not write CSV " & path & ": " & Err.Description)
    On Error Resume Next
    Close #f
End Sub


'------------------------------------------------------------------------------
' PctField - a fraction from DetailRow as a CSV percent number ("27.0300"),
' or "" if it was left Empty (not available).
'------------------------------------------------------------------------------
Function PctField(x As Variant) As String
    If IsEmpty(x) Then
        PctField = ""
    Else
        PctField = Format(100# * x, "0.0000")
    End If
End Function


'------------------------------------------------------------------------------
' CsvQuote - wrap a field in double quotes if it contains a comma or a quote
' (doubling any embedded quotes), so titles like "Skin, upper" stay in one
' column.
'------------------------------------------------------------------------------
Function CsvQuote(t As String) As String
    If InStr(t, ",") > 0 Or InStr(t, """") > 0 Then
        CsvQuote = """" & Replace(t, """", """""") & """"
    Else
        CsvQuote = t
    End If
End Function
