'==============================================================================
' SUM STRAIN ENERGY  --  element strain energy over a selection, by group/property
'==============================================================================
'
' PURPOSE
'   For a user-selected set of elements and one or more output sets, report
'   the summed element strain energy and its share of the model total for:
'     - the selection as a whole,
'     - each picked group (the WHOLE group, not limited to the selection),
'     - each property present in the selection (these rows add up to the
'       selection row).
'   Each row shows two percents: "Calc %" (our sum / sum over every element
'   with output) and "Nastran %" (sum of Nastran's per-element percent-of-total
'   vector).  One block per output set goes to the Messages window, so a modal
'   run gives a per-mode strain energy fraction table in one go.
'
' HOW IT WORKS
'   Vector IDs come from ResultsIDQuery.Elemental (VEO_STRAIN_ENERGY and
'   VEO_STRAIN_ENERGY_PERCENT), not hard-coded.  Element -> property is mapped
'   once up front with Set.AddRule(FGD_ELEM_BYPROP), so no per-element Get.
'   For each output set the Results Browsing Object loads both columns for
'   every element and the macro walks the rows once, adding each value to every
'   bucket (selection / group / property) its element belongs to.
'
' ASSUMES
'   - Element strain energy was requested in the solve (Nastran ESE=ALL).  If
'     ESE covered only a subset, "Total" and "Calc %" are relative to that
'     subset while "Nastran %" is relative to Nastran's subcase total.
'   - An element can sit in several picked groups, so group rows can sum past
'     100%.
'   - Static and modal output.  Complex / frequency response is not supported.
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (Ctrl+F11), paste, then Run.
'   Pick the elements, optionally groups (Cancel = none), answer the property
'   breakdown Yes/No, then pick output sets.  Nothing in the model is changed.
'
' API CALLS USED (Femap API Guide 2025)
'   feFemap, feSet(.Select/.AddGroup/.AddRule/.IsAdded/.Count/.Next/.Reset/
'   .Clear), feGroup(.Get/.title), feProp(.First/.Next/.ID/.title),
'   feOutputSet(.Get/.title), feResultsIDQuery(.Elemental), feResults(.Clear/
'   .AddColumnV2/.Populate/.GetColumn), feAppMessage
'==============================================================================


Sub Main

    Dim App As femap.model
    Set App = feFemap()

    '--------------------------------------------------------------------------
    ' STEP 1: prompts
    '--------------------------------------------------------------------------
    Dim eSet As femap.Set
    Set eSet = App.feSet
    If eSet.Select(FT_ELEM, True, "Select Elements to Sum Strain Energy") <> FE_OK Then Exit Sub
    If eSet.Count() = 0 Then
        MsgBox "No elements selected."
        Exit Sub
    End If

    ' Groups are optional: Cancel or an empty pick just means no group rows.
    Dim gpSet As femap.Set
    Set gpSet = App.feSet
    Dim rc As Long
    rc = gpSet.Select(FT_GROUP, True, "Groups for Breakdown (Cancel = none)")

    Dim doProps As Boolean
    doProps = (MsgBox("Break down the selection by property?", vbYesNo, _
                      "Sum Strain Energy") = vbYes)

    Dim osSet As femap.Set
    Set osSet = App.feSet
    If osSet.Select(FT_OUT_CASE, True, "Select Output Sets") <> FE_OK Then Exit Sub
    If osSet.Count() = 0 Then
        MsgBox "No output sets selected."
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 2: buckets.  0 = selection, 1..nGrp = groups, then property slots.
    '--------------------------------------------------------------------------
    Dim nGrp As Long
    nGrp = gpSet.Count()

    ' One element set per picked group (the whole group).
    Dim grpSets() As Object
    Dim bLabel() As String
    ReDim bLabel(nGrp)
    If nGrp > 0 Then ReDim grpSets(nGrp - 1)
    bLabel(0) = "Selection"

    Dim gp As femap.Group
    Set gp = App.feGroup
    Dim g As Long
    g = 0
    gpSet.Reset()
    Do While gpSet.Next()
        Set grpSets(g) = App.feSet
        rc = grpSets(g).AddGroup(FT_ELEM, gpSet.CurrentID)
        bLabel(1 + g) = "Group " & CStr(gpSet.CurrentID)
        If gp.Get(gpSet.CurrentID) = FE_OK Then bLabel(1 + g) = bLabel(1 + g) & ": " & gp.title
        g = g + 1
    Loop

    ' Property slots: only properties that own at least one selected element.
    ' propOf(elemID) = bucket index, 0 = none.  Only selected elements are
    ' looked up, so the array only needs to reach the selection's highest ID.
    ' (FEMAP's WinWrap has no Collection/Dictionary.)
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
        rc = pr.First()
        Do While rc
            ps.Clear()
            ps.AddRule(pr.ID, FGD_ELEM_BYPROP)
            hit = False
            slot = 1 + nGrp + nProp
            ps.Reset()
            Do While ps.Next()
                If eSet.IsAdded(ps.CurrentID) Then
                    propOf(ps.CurrentID) = slot
                    hit = True
                End If
            Loop
            If hit Then
                ReDim Preserve bLabel(slot)
                bLabel(slot) = "Prop " & CStr(pr.ID) & ": " & pr.title
                nProp = nProp + 1
            End If
            rc = pr.Next()
        Loop
    End If

    Dim nB As Long
    nB = 1 + nGrp + nProp
    Dim bSE() As Double, bPC() As Double, bN() As Long
    ReDim bSE(nB - 1)
    ReDim bPC(nB - 1)
    ReDim bN(nB - 1)

    '--------------------------------------------------------------------------
    ' STEP 3: vector IDs
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
    ' STEP 4: sum per output set
    '--------------------------------------------------------------------------
    Dim rbo As femap.Results
    Set rbo = App.feResults
    Dim os As femap.OutputSet
    Set os = App.feOutputSet

    App.feAppMessage(FCM_HIGHLIGHT, "Strain energy sum: " & CStr(eSet.Count()) & _
                     " selected elements, vectors SE=" & CStr(seVec) & _
                     " SE%=" & CStr(pcVec))

    Dim nCols As Long, seCols As Variant, pcCols As Variant
    Dim vIDs As Variant, vVals As Variant
    Dim pIDs As Variant, pVals As Variant
    Dim havePc As Boolean
    Dim i As Long, b As Long, id As Long
    Dim v As Double, p As Double, sumAll As Double
    Dim nSkip As Long
    Dim ttl As String

    nSkip = 0
    osSet.Reset()
    Do While osSet.Next()
        ttl = ""
        If os.Get(osSet.CurrentID) = FE_OK Then ttl = os.title

        ' AddColumnV2 fails if this set has no strain energy vector (ESE not
        ' requested for that subcase/mode).  The percent column is optional.
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

            rc = rbo.Populate()

            For b = 0 To nB - 1
                bSE(b) = 0
                bPC(b) = 0
                bN(b) = 0
            Next b
            sumAll = 0

            ' Both columns come from the same populated object, so row i is
            ' the same element in each.
            If rbo.GetColumn(seCols(0), vIDs, vVals) = FE_OK Then
                If havePc Then havePc = (rbo.GetColumn(pcCols(0), pIDs, pVals) = FE_OK)
                For i = 0 To UBound(vIDs)
                    id = vIDs(i)
                    v = vVals(i)
                    p = 0
                    If havePc Then p = pVals(i)
                    sumAll = sumAll + v

                    If eSet.IsAdded(id) Then
                        AddTo bSE, bPC, bN, 0, v, p
                        If nProp > 0 Then
                            b = propOf(id)
                            If b > 0 Then AddTo bSE, bPC, bN, b, v, p
                        End If
                    End If
                    For g = 0 To nGrp - 1
                        If grpSets(g).IsAdded(id) Then AddTo bSE, bPC, bN, 1 + g, v, p
                    Next g
                Next i
            End If

            App.feAppMessage(FCM_NORMAL, "Set " & CStr(osSet.CurrentID) & ": " & ttl & _
                             "   Total SE = " & Format(sumAll, "0.000000E+00"))
            App.feAppMessage(FCM_NORMAL, "  Bucket, SE, Calc %, Nastran %, Elems w/ data")
            For b = 0 To nB - 1
                PrintRow App, bLabel(b), bSE(b), sumAll, bPC(b), havePc, bN(b)
            Next b
        End If
    Loop

    If nSkip > 0 Then
        App.feAppMessage(FCM_WARNING, CStr(nSkip) & " output set(s) had no " & _
                         "strain energy. Request ESE in the analysis case.")
    End If

End Sub


'------------------------------------------------------------------------------
' Add one element's values into bucket b.
'------------------------------------------------------------------------------
Sub AddTo(bSE() As Double, bPC() As Double, bN() As Long, b As Long, _
          v As Double, p As Double)
    bSE(b) = bSE(b) + v
    bPC(b) = bPC(b) + p
    bN(b) = bN(b) + 1
End Sub


'------------------------------------------------------------------------------
' One comma-separated bucket line in the Messages window.
'------------------------------------------------------------------------------
Sub PrintRow(App As femap.model, label As String, se As Double, total As Double, _
             pc As Double, havePc As Boolean, n As Long)
    Dim calc As String, nas As String
    If total <> 0 Then
        calc = Format(100# * se / total, "0.00") & "%"
    Else
        calc = "n/a"
    End If
    If havePc Then
        nas = Format(pc, "0.00") & "%"
    Else
        nas = "n/a"
    End If
    App.feAppMessage(FCM_NORMAL, "  " & label & ", " & Format(se, "0.000000E+00") & _
                     ", " & calc & ", " & nas & ", " & CStr(n))
End Sub
