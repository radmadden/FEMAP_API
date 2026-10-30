'==============================================================================
' WRITE GROUP INCLUDES  --  export selected FEMAP groups as Nastran INCLUDE files
'==============================================================================
'
' PURPOSE
'   For a user-selected subset of groups, write:
'     - one mesh INCLUDE (.bdf) per group (its GRIDs + its elements only),
'     - shared materials.bdf / properties.bdf / csys.bdf,
'     - loads.bdf / constraints.bdf (all load & BC sets, filtered to cards that
'       touch the selected groups' nodes/elements),
'     - misc.bdf for any bulk card that matched none of the above,
'     - a Master .dat that INCLUDEs every file that got content.
'
' HOW IT WORKS (A2)
'   FEMAP has no group-filtered Nastran writer, so this macro lets FEMAP write
'   the WHOLE model once (feFileWriteNastran, small field, all load/BC sets),
'   then parses the BEGIN BULK..ENDDATA section and ROUTES each card to the
'   right file.  No card formats are hand-coded, so any element/card type that
'   FEMAP can write is handled.
'
' ASSUMES (per design review)
'   - Elements are disjoint across groups (groups numbered so no element is in
'     two selected groups).  Shared interface nodes live in their own group
'     (e.g. an "ICN" group) that you select like any other, so every node is in
'     exactly one selected group and no GRID is written twice.
'   - You pull the bulk into FEMAP before solving, so the Master is INCLUDEs only
'     (no CEND/BEGIN BULK/ENDDATA skeleton).
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (Ctrl+F11), paste, then Run.
'   For silent, small-field, all-loads output the macro temporarily sets two
'   options on the active analysis set (and restores them), or creates and then
'   deletes a temporary NX Static set if none is active.  Your model is left
'   as it was.
'
' API CALLS USED (Femap API Guide 2025)
'   feFemap, feGroup(.First/.Next/.ID/.title), feSet(.AddGroup/.First/.Next/.Add),
'   feNode, feElem, feAnalysisMgr(.Get/.Put/.InitAnalysisMgr + NasBulk* props),
'   Info_ActiveID/Info_NextID(FT_AMGR_DIR), feFileWriteNastran, feDelete
'==============================================================================


'------------------------------------------------------------------------------
' USER SETTINGS
'------------------------------------------------------------------------------
Const BRAND As Long = 8        ' feFileWriteNastran brand: 0=MSC, 8=NX


'------------------------------------------------------------------------------
' Fixed (shared) bucket indices and their file names
'------------------------------------------------------------------------------
Const BK_MAT  As Long = 0
Const BK_PROP As Long = 1
Const BK_CSYS As Long = 2
Const BK_LOAD As Long = 3
Const BK_CON  As Long = 4
Const BK_MISC As Long = 5
Const NBUCK   As Long = 6


'------------------------------------------------------------------------------
' Module-level state (shared with the routing helpers)
'------------------------------------------------------------------------------
' Ownership maps indexed by entity ID -> selected-group index, -1 = not selected.
' Plain arrays because FEMAP's WinWrap has no Collection; grown on demand.
Const OWN_NODE As Long = 0
Const OWN_ELEM As Long = 1
Dim gNodeOwner() As Long
Dim gElemOwner() As Long
Dim gMiscCards As String        ' distinct card names routed to misc, comma list
Dim gMiscCount As Long

Dim gOutDir As String

Dim gGrpCount As Long
Dim gGrpTitle() As String       ' sanitized file name (no extension) per group
Dim gGrpFnum() As Integer       ' open file number, 0 = not opened
Dim gGrpLines() As Long

Dim gBuckName(5) As String      ' file name per fixed bucket
Dim gBuckFnum(5) As Integer
Dim gBuckLines(5) As Long

Dim gWarnUnownedGrid As Long
Dim gDropLoad As Long           ' loads dropped (touch no selected node/elem)
Dim gDropCon As Long            ' constraints dropped


Sub Main

    Dim App As femap.model
    Set App = feFemap()

    '--------------------------------------------------------------------------
    ' STEP 2: pick groups via a multi-select dialog
    '--------------------------------------------------------------------------
    Dim gp As femap.Group
    Set gp = App.feGroup

    ' Two passes over the model's groups because we must size the arrays before
    ' filling them. Group.First()/Next() return nonzero while an entity is current
    ' and leave its .ID/.title readable.
    ' pass 1: count the groups
    Dim nGrp As Long, rc As Long
    nGrp = 0
    rc = gp.First()
    Do While rc
        nGrp = nGrp + 1
        rc = gp.Next()
    Loop
    If nGrp = 0 Then
        MsgBox "This model has no groups."
        Exit Sub
    End If

    ' pass 2: collect each group's ID and a "<id>: <title>" label for the list box
    Dim grpID() As Long, grpTtl() As String
    ReDim grpID(nGrp - 1)
    ReDim grpTtl(nGrp - 1)
    Dim k As Long
    k = 0
    rc = gp.First()
    Do While rc
        grpID(k) = gp.ID
        grpTtl(k) = CStr(gp.ID) & ": " & gp.title
        k = k + 1
        rc = gp.Next()
    Loop

    ' MultiListBox lets the user ctrl/shift-click any number of groups. After the
    ' dialog, dlg.Picked is an array holding the INDEXES (into grpTtl/grpID) of the
    ' items selected. Dialog() returns -1 for OK, 0 for Cancel.
    Begin Dialog GRPDLG 240, 210, "Write Group Includes"
        Text        10,   6, 220, 10, "Select groups to export:"
        MultiListBox 10,  20, 220, 160, grpTtl(), .Picked
        OKButton    40, 188,  70, 14
        CancelButton 130, 188, 70, 14
    End Dialog
    Dim dlg As GRPDLG
    If Dialog(dlg) = 0 Then Exit Sub        ' cancelled

    ' Turn the selected indexes into actual group IDs. If nothing was picked,
    ' dlg.Picked is an empty array and UBound() raises - trap that as "none".
    Dim nSel As Long
    nSel = -1
    On Error Resume Next
    nSel = UBound(dlg.Picked)
    On Error GoTo 0
    If nSel < 0 Then
        MsgBox "No groups selected."
        Exit Sub
    End If

    Dim selID() As Long
    ReDim selID(nSel)
    Dim i As Long
    For i = 0 To nSel
        selID(i) = grpID(dlg.Picked(i))     ' index in the list -> group ID
    Next i
    gGrpCount = nSel + 1

    '--------------------------------------------------------------------------
    ' STEP 3: output folder + master name
    '--------------------------------------------------------------------------
    gOutDir = InputBox("Output folder for the include files:", _
                       "Write Group Includes", "C:\temp\includes")
    If gOutDir = "" Then Exit Sub
    If Right(gOutDir, 1) <> "\" Then gOutDir = gOutDir & "\"
    If Dir(gOutDir, vbDirectory) = "" Then
        On Error Resume Next
        MkDir gOutDir
        On Error GoTo 0
    End If
    If Dir(gOutDir, vbDirectory) = "" Then
        MsgBox "Could not create/open folder: " & gOutDir
        Exit Sub
    End If

    Dim masterName As String
    masterName = InputBox("Master filename (.dat):", _
                          "Write Group Includes", "master.dat")
    If masterName = "" Then Exit Sub
    If LCase(Right(masterName, 4)) <> ".dat" Then masterName = masterName & ".dat"

    '--------------------------------------------------------------------------
    ' STEP 4: ownership maps + file names
    '--------------------------------------------------------------------------
    ReDim gNodeOwner(0)
    ReDim gElemOwner(0)
    gNodeOwner(0) = -1
    gElemOwner(0) = -1
    gMiscCards = ""
    gMiscCount = 0

    ReDim gGrpTitle(gGrpCount - 1)
    ReDim gGrpFnum(gGrpCount - 1)
    ReDim gGrpLines(gGrpCount - 1)

    gBuckName(BK_MAT)  = "materials.bdf"
    gBuckName(BK_PROP) = "properties.bdf"
    gBuckName(BK_CSYS) = "csys.bdf"
    gBuckName(BK_LOAD) = "loads.bdf"
    gBuckName(BK_CON)  = "constraints.bdf"
    gBuckName(BK_MISC) = "misc.bdf"
    For i = 0 To NBUCK - 1
        gBuckFnum(i) = 0
        gBuckLines(i) = 0
    Next i

    ' For each selected group, pull its node IDs and element IDs into the ownership
    ' maps (ID -> this group's index). feSet.AddGroup copies all IDs of one entity
    ' type out of a group; .First/.Next then walks them. These maps are what Step 6
    ' uses to decide which group file a GRID or element belongs in.
    Dim ns As femap.Set, es As femap.Set
    Dim id As Long, gi As Long
    For gi = 0 To gGrpCount - 1
        gGrpFnum(gi) = 0
        gGrpLines(gi) = 0
        gGrpTitle(gi) = UniqueGroupName(App, selID(gi), gi)

        Set ns = App.feSet
        ns.Clear
        ns.AddGroup FT_NODE, selID(gi)
        id = ns.First()
        Do While id > 0
            AddOwner OWN_NODE, id, gi
            id = ns.Next()
        Loop

        Set es = App.feSet
        es.Clear
        es.AddGroup FT_ELEM, selID(gi)
        id = es.First()
        Do While id > 0
            AddOwner OWN_ELEM, id, gi
            id = es.Next()
        Loop
    Next gi

    '--------------------------------------------------------------------------
    ' STEP 5: FEMAP writes the full deck (temp file)
    '--------------------------------------------------------------------------
    Dim tmpFull As String
    tmpFull = gOutDir & "_full_temp.bdf"

    ' feFileWriteNastran writes the WHOLE model, governed by the ACTIVE analysis
    ' set, and runs silently only if an analysis set is active. We need two of its
    ' options forced: small field, and "write all load & BC sets" (so loads.bdf /
    ' constraints.bdf see every set, not just the active one). To avoid disturbing
    ' the model we either tweak the existing active set and put its values back, or
    ' create a throwaway NX set and delete it afterward.
    Dim am As femap.AnalysisMgr
    Set am = App.feAnalysisMgr

    Dim oldActive As Long, haveActive As Boolean, createdTemp As Boolean
    Dim savedLF As Long, savedAll As Long, tempSetID As Long
    haveActive = False
    createdTemp = False

    oldActive = App.Info_ActiveID(FT_AMGR_DIR)   ' 0 if the model has no active set
    If oldActive > 0 Then
        If am.Get(oldActive) = FE_OK Then haveActive = True
    End If

    If haveActive Then
        ' Existing active set: remember the two flags, override, restore later.
        savedLF = am.NasBulkLargeField
        savedAll = am.NasBulkWriteAllLoadBCSets
        am.NasBulkLargeField = 0                 ' 0 = small (8-char) field
        am.NasBulkWriteAllLoadBCSets = True
        am.Put(oldActive)
    Else
        ' No active set: build a temporary NX Nastran / Static one. Putting it
        ' makes it the active set, which is what suppresses the export dialog.
        am.InitAnalysisMgr FAP_NX_NASTRAN, FAT_STATIC, True
        am.NasBulkLargeField = 0
        am.NasBulkWriteAllLoadBCSets = True
        tempSetID = App.Info_NextID(FT_AMGR_DIR)
        am.Put(tempSetID)
        createdTemp = True
    End If

    App.feFileWriteNastran BRAND, tmpFull        ' the full deck -> temp file

    ' Put the model back exactly as we found it.
    If createdTemp Then
        Dim ds As femap.Set                      ' feDelete works on a set of IDs
        Set ds = App.feSet
        ds.Clear
        ds.Add(tempSetID)
        App.feDelete FT_AMGR_DIR, ds.ID          ' remove the throwaway set
    Else
        am.Get(oldActive)                        ' reload and restore the saved flags
        am.NasBulkLargeField = savedLF
        am.NasBulkWriteAllLoadBCSets = savedAll
        am.Put(oldActive)
    End If

    If Dir(tmpFull) = "" Then
        MsgBox "FEMAP did not write the temporary deck:" & vbCrLf & tmpFull
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 6: parse BEGIN BULK..ENDDATA and route each logical card
    '--------------------------------------------------------------------------
    ' We only care about the bulk data: skip everything until "BEGIN BULK" and
    ' stop at "ENDDATA" (the executive/case-control sections aren't wanted - you
    ' pull the bulk into FEMAP yourself). Cards are accumulated into "buffer" one
    ' LOGICAL card at a time (a parent line plus its continuations) so ProcessCard
    ' can see the whole card at once - important for the load/constraint filter,
    ' whose referenced nodes may sit on a continuation line.
    Dim f As Integer, ln As String, u As String
    Dim inBulk As Boolean, buffer As String
    inBulk = False
    buffer = ""

    f = FreeFile
    Open tmpFull For Input As #f
    Do While Not EOF(f)
        Line Input #f, ln
        u = UCase(Trim(ln))

        If Not inBulk Then
            If InStr(u, "BEGIN BULK") > 0 Then inBulk = True   ' bulk starts after this
        Else
            If InStr(u, "ENDDATA") > 0 Then
                Exit Do                         ' end of bulk - done reading
            ElseIf Trim(ln) = "" Then
                ' blank line: ignore
            ElseIf Left(Trim(ln), 1) = "$" Then
                ' comment line: drop (keeps the include files clean)
            ElseIf IsContinuation(ln) Then
                buffer = buffer & Chr(10) & ln  ' same card, next physical line
            Else
                ' a new card begins: flush the one we were building, then start fresh
                If buffer <> "" Then ProcessCard buffer
                buffer = ln
            End If
        End If
    Loop
    Close #f
    If buffer <> "" Then ProcessCard buffer     ' flush the final card

    '--------------------------------------------------------------------------
    ' STEP 7: Master + cleanup
    '--------------------------------------------------------------------------
    ' Close every include file we opened (fnum <> 0 means it got at least one card).
    For gi = 0 To gGrpCount - 1
        If gGrpFnum(gi) <> 0 Then Close #gGrpFnum(gi)
    Next gi
    For i = 0 To NBUCK - 1
        If gBuckFnum(i) <> 0 Then Close #gBuckFnum(i)
    Next i

    ' Write the master .dat: one INCLUDE per file that actually got content, as
    ' bare quoted file names (all includes live beside the master in the same
    ' folder). Groups first, then the shared buckets.
    Dim m As Integer
    m = FreeFile
    Open gOutDir & masterName For Output As #m
    Print #m, "$ Master include file - generated by write_group_includes.bas"
    For gi = 0 To gGrpCount - 1
        If gGrpFnum(gi) <> 0 Then Print #m, "INCLUDE '" & gGrpTitle(gi) & ".bdf'"
    Next gi
    For i = 0 To NBUCK - 1
        If gBuckFnum(i) <> 0 Then Print #m, "INCLUDE '" & gBuckName(i) & "'"
    Next i
    Close #m

    ' The full deck was only an intermediate - delete it (ignore any error).
    On Error Resume Next
    Kill tmpFull
    On Error GoTo 0

    '--------------------------------------------------------------------------
    ' STEP 8: summary
    '--------------------------------------------------------------------------
    Dim msg As String
    msg = "Wrote to: " & gOutDir & vbCrLf & _
          "Master:   " & masterName & vbCrLf & vbCrLf & _
          "Groups (" & gGrpCount & "):" & vbCrLf
    For gi = 0 To gGrpCount - 1
        If gGrpFnum(gi) <> 0 Then
            msg = msg & "  " & gGrpTitle(gi) & ".bdf  (" & gGrpLines(gi) & " lines)" & vbCrLf
        End If
    Next gi
    msg = msg & vbCrLf & "Shared:" & vbCrLf
    For i = 0 To NBUCK - 1
        If gBuckFnum(i) <> 0 Then
            msg = msg & "  " & gBuckName(i) & "  (" & gBuckLines(i) & " lines)" & vbCrLf
        End If
    Next i
    If gWarnUnownedGrid > 0 Then
        msg = msg & vbCrLf & "WARNING: " & gWarnUnownedGrid & _
              " GRID(s) had no selected owner (sent to misc.bdf)." & vbCrLf & _
              "An element may reference a node whose group you didn't select."
    End If
    If gDropLoad > 0 Or gDropCon > 0 Then
        msg = msg & vbCrLf & "Filtered out (no selected node/elem): " & _
              gDropLoad & " load(s), " & gDropCon & " constraint(s)."
    End If
    If gMiscCount > 0 Then
        msg = msg & vbCrLf & "Unrouted card types in misc.bdf: " & _
              Replace(gMiscCards, ",", ", ")
    End If
    MsgBox msg

End Sub


'------------------------------------------------------------------------------
' ProcessCard - classify ONE logical card and send it to the right output file.
'
'   "buffer" is the whole logical card: its parent line plus any continuation
'   lines, joined with Chr(10) (see the parse loop in Step 6). The first line
'   carries the card name (field 1, columns 1-8) and, for most cards, the
'   defining ID in field 2 (columns 9-16).
'
'   ORDER MATTERS. The shared-entity categories (CSys, material, property, load,
'   constraint) are tested by NAME *before* the "is it an element?" fallback,
'   because Nastran ID spaces overlap: a property ID and an element ID can both
'   be, say, 5. Catching properties/materials/etc. by their card name first means
'   the element fallback (field-2 ID lookup) only ever sees true elements.
'------------------------------------------------------------------------------
Sub ProcessCard(buffer As String)
    ' Pull just the first physical line out of the logical card.
    Dim firstLine As String, p As Long
    p = InStr(buffer, Chr(10))
    If p > 0 Then firstLine = Left(buffer, p - 1) Else firstLine = buffer

    ' Field 1 = card name. UCase so "cquad4"/"CQuad4" all match the lists below.
    Dim card As String
    card = UCase(Trim(Left(firstLine, 8)))
    If card = "" Then Exit Sub

    ' Field 2 = the card's own ID (GRID id, element id, prop id, ...). Pad the
    ' line first so Mid() can't run off the end on a short/truncated line.
    Dim id2 As Long
    id2 = CLng(Val(Trim(Mid(firstLine & "          ", 9, 8))))

    Dim gi As Long

    ' --- GRID: route to the group that owns this node ---------------------------
    ' Every selected node has exactly one owner (your ICN discipline), so a GRID
    ' lands in one group file only -> no duplicate GRID across includes. A GRID
    ' with no selected owner means an element references a node from a group you
    ' did not pick; park it in misc and flag it rather than lose it silently.
    If card = "GRID" Then
        gi = OwnerOf(OWN_NODE, id2)
        If gi >= 0 Then
            WriteToGroup gi, buffer
        Else
            WriteToBuck BK_MISC, buffer
            gWarnUnownedGrid = gWarnUnownedGrid + 1
        End If
        Exit Sub
    End If

    ' --- Coordinate systems -> csys.bdf ----------------------------------------
    If InList(card, "CORD1R,CORD1C,CORD1S,CORD2R,CORD2C,CORD2S,CORD3G") Then
        WriteToBuck BK_CSYS, buffer
        Exit Sub
    End If

    ' --- Materials -> materials.bdf  (MAT1/2/8/9, MATT*, MATS*, ... all "MAT*") -
    If Left(card, 3) = "MAT" Then
        WriteToBuck BK_MAT, buffer
        Exit Sub
    End If

    ' --- Properties -> properties.bdf ------------------------------------------
    ' Listed explicitly (not "starts with P") because PLOAD* and PLOTEL also start
    ' with P: an explicit list keeps loads and the PLOTEL element out of here.
    If InList(card, "PSHELL,PSOLID,PBAR,PBARL,PBEAM,PBEAML,PBEND,PCOMP,PCOMPG," & _
                    "PROD,PTUBE,PBUSH,PBUSH1D,PELAS,PDAMP,PVISC,PMASS,PGAP," & _
                    "PSHEAR,PWELD,PFAST,PLPLANE,PLSOLID,PCONEAX,PRAC2D,PRAC3D," & _
                    "PBCOMP,PHBDY,PCONV,PCONVM,PAABSF,PACABS,PMIC") Then
        WriteToBuck BK_PROP, buffer
        Exit Sub
    End If

    ' --- Loads -> loads.bdf (filtered) -----------------------------------------
    ' Node/element-based loads are kept only if they actually touch a selected
    ' entity (that's the "by node/elem" filter). Model-global and combination
    ' loads (GRAV/RFORCE/ACCEL/LOAD/DLOAD/...) reference no single entity, so they
    ' are always kept. A load that matches nothing is dropped and counted.
    If InList(card, "FORCE,FORCE1,FORCE2,MOMENT,MOMENT1,MOMENT2,SPCD,SLOAD," & _
                    "TEMP,TEMPP1,TEMPRB,PLOAD,PLOAD1,PLOAD2,PLOAD4,PLOADX1," & _
                    "QVOL,QBDY1,QBDY2,QBDY3,QHBDY,QVECT,GRAV,RFORCE,ACCEL," & _
                    "ACCEL1,TEMPD,LOAD,DLOAD,LSEQ,DAREA,TLOAD1,TLOAD2,RLOAD1," & _
                    "RLOAD2,DPHASE,DELAY") Then
        If InList(card, "GRAV,RFORCE,ACCEL,ACCEL1,TEMPD,LOAD,DLOAD,LSEQ," & _
                        "TLOAD1,TLOAD2,RLOAD1,RLOAD2,DPHASE,DELAY,DAREA") Then
            WriteToBuck BK_LOAD, buffer                 ' global/combination: always keep
        ElseIf CardTouchesAny(buffer) Then
            WriteToBuck BK_LOAD, buffer                 ' touches a selected node/elem
        Else
            gDropLoad = gDropLoad + 1                   ' not on any selected entity
        End If
        Exit Sub
    End If

    ' --- Constraints -> constraints.bdf (filtered) -----------------------------
    ' SPCADD/MPCADD only combine other set IDs (no nodes) -> always keep. A plain
    ' SPC/SPC1/MPC is kept only if it constrains a selected node.
    If InList(card, "SPC,SPC1,SPCADD,MPC,MPCADD") Then
        If card = "SPCADD" Or card = "MPCADD" Then
            WriteToBuck BK_CON, buffer                  ' combination: always keep
        ElseIf CardTouchesAny(buffer) Then
            WriteToBuck BK_CON, buffer
        Else
            gDropCon = gDropCon + 1
        End If
        Exit Sub
    End If

    ' --- Element fallback ------------------------------------------------------
    ' Anything left with a field-2 ID that we recorded as a selected element is an
    ' element of that group (CQUAD4, CHEXA, CBUSH, RBE2/RBE3, CONM2, ... - no need
    ' to enumerate element types, FEMAP already grouped them by ID).
    gi = OwnerOf(OWN_ELEM, id2)
    If gi >= 0 Then
        WriteToGroup gi, buffer
        Exit Sub
    End If

    ' --- Everything else -------------------------------------------------------
    ' PARAM, GRDSET, SET, TABLE*, unknown cards: keep in misc.bdf so nothing is
    ' ever silently lost, and remember the card name for the summary.
    WriteToBuck BK_MISC, buffer
    RecordMisc card
End Sub


'------------------------------------------------------------------------------
' Routing helpers - append a logical card to a group file or a shared bucket.
'
'   Files are opened LAZILY: the first card routed to a file opens it and stores
'   its file number (gGrpFnum / gBuckFnum). A stored 0 means "not opened yet".
'   The payoff is that a group or bucket that never receives a card produces no
'   file on disk and no INCLUDE line in the master - empty categories vanish.
'   Every opened file is closed once, in Step 7.
'------------------------------------------------------------------------------
Sub WriteToGroup(gi As Long, buffer As String)
    If gGrpFnum(gi) = 0 Then                 ' first card for this group -> open it
        Dim fn As Integer
        fn = FreeFile
        Open gOutDir & gGrpTitle(gi) & ".bdf" For Output As #fn
        gGrpFnum(gi) = fn
    End If
    gGrpLines(gi) = gGrpLines(gi) + WriteBuffer(gGrpFnum(gi), buffer)
End Sub

Sub WriteToBuck(b As Long, buffer As String)
    If gBuckFnum(b) = 0 Then                 ' first card for this bucket -> open it
        Dim fn As Integer
        fn = FreeFile
        Open gOutDir & gBuckName(b) For Output As #fn
        gBuckFnum(b) = fn
    End If
    gBuckLines(b) = gBuckLines(b) + WriteBuffer(gBuckFnum(b), buffer)
End Sub

' Print every physical line of the logical card verbatim (FEMAP already formatted
' it - we never reformat), and return how many lines were written for the tally.
Function WriteBuffer(fn As Integer, buffer As String) As Long
    Dim parts() As String, i As Long
    parts = Split(buffer, Chr(10))
    For i = 0 To UBound(parts)
        Print #fn, parts(i)
    Next i
    WriteBuffer = UBound(parts) + 1
End Function


'------------------------------------------------------------------------------
' Ownership maps - arrays indexed by entity ID holding the owning selected-group
' index, -1 = not a selected entity. kind = OWN_NODE or OWN_ELEM picks the array
' (globals rather than array parameters, so ReDim Preserve acts on the real one).
'------------------------------------------------------------------------------
Sub AddOwner(kind As Long, id As Long, gi As Long)
    If id <= 0 Then Exit Sub
    Dim n As Long, k As Long
    If kind = OWN_NODE Then
        n = UBound(gNodeOwner)
        If id > n Then
            ' grow geometrically so ascending IDs don't ReDim on every add
            ReDim Preserve gNodeOwner(MaxL(id, 2 * n))
            For k = n + 1 To UBound(gNodeOwner)
                gNodeOwner(k) = -1
            Next k
        End If
        ' a node that somehow appears in two selected groups keeps its FIRST
        ' owner (and so is written only once)
        If gNodeOwner(id) < 0 Then gNodeOwner(id) = gi
    Else
        n = UBound(gElemOwner)
        If id > n Then
            ReDim Preserve gElemOwner(MaxL(id, 2 * n))
            For k = n + 1 To UBound(gElemOwner)
                gElemOwner(k) = -1
            Next k
        End If
        If gElemOwner(id) < 0 Then gElemOwner(id) = gi
    End If
End Sub

Function OwnerOf(kind As Long, id As Long) As Long
    OwnerOf = -1                    ' out of range -> not a selected node/element
    If id <= 0 Then Exit Function
    If kind = OWN_NODE Then
        If id <= UBound(gNodeOwner) Then OwnerOf = gNodeOwner(id)
    Else
        If id <= UBound(gElemOwner) Then OwnerOf = gElemOwner(id)
    End If
End Function

Function MaxL(a As Long, b As Long) As Long
    If a > b Then MaxL = a Else MaxL = b
End Function


'------------------------------------------------------------------------------
' CardTouchesAny - does this load/constraint reference any selected node or elem?
'
'   The filter is deliberately CONSERVATIVE: it only drops a card when it can
'   prove none of the card's IDs are selected, so a relevant card is never lost.
'   - Scans fields 2..9 of every line (field 1 is the card name, field 10 the
'     continuation marker - neither is an ID).
'   - Only plain integers are tested; a value with "." or "E" is a real number
'     (a force value, a coordinate), not an ID.
'   - Over-matching is harmless: if a load-set ID happens to equal a selected
'     node ID the card is kept, which is safe. Under-matching would lose data.
'   - A "THRU" range is kept outright rather than expanded - cheap and safe.
'------------------------------------------------------------------------------
Function CardTouchesAny(buffer As String) As Boolean
    Dim parts() As String, i As Long, j As Long, s As String, v As Long
    parts = Split(buffer, Chr(10))
    For i = 0 To UBound(parts)
        If InStr(UCase(parts(i)), "THRU") > 0 Then
            CardTouchesAny = True       ' range present: keep (don't expand it)
            Exit Function
        End If
        For j = 2 To 9                  ' skip field 1 (name) and 10 (cont marker)
            s = Trim(Fld(parts(i), j))
            If s <> "" And IsNumeric(s) Then
                If InStr(s, ".") = 0 And InStr(UCase(s), "E") = 0 Then  ' integer = an ID
                    v = CLng(Val(s))
                    If OwnerOf(OWN_NODE, v) >= 0 Then CardTouchesAny = True : Exit Function
                    If OwnerOf(OWN_ELEM, v) >= 0 Then CardTouchesAny = True : Exit Function
                End If
            End If
        Next j
    Next i
    CardTouchesAny = False              ' no selected ID anywhere -> safe to drop
End Function


'------------------------------------------------------------------------------
' Small-field (8-column) Nastran helpers. We forced small field on the write
' (NasBulkLargeField = 0), so every card is 10 fixed fields of 8 characters:
'   field 1 = card name,  fields 2..9 = data,  field 10 = continuation marker.
'------------------------------------------------------------------------------

' Field n (1-based) of a small-field line; "" if the line is too short to reach it.
Function Fld(line As String, n As Long) As String
    Dim startc As Long
    startc = (n - 1) * 8 + 1
    If Len(line) < startc Then
        Fld = ""
    Else
        Fld = Mid(line, startc, 8)
    End If
End Function

' True if "line" continues the previous card rather than starting a new one.
' Nastran continuations are flagged either by a +/* in column 1, or by leaving
' field 1 (columns 1-8) blank. Blank lines are filtered out before we get here.
Function IsContinuation(line As String) As Boolean
    Dim c1 As String
    c1 = Left(line, 1)
    If c1 = "+" Or c1 = "*" Then
        IsContinuation = True       ' explicit continuation / large-field marker
    ElseIf Trim(Left(line & "        ", 8)) = "" Then
        IsContinuation = True       ' blank field 1 = continuation
    Else
        IsContinuation = False
    End If
End Function

' Whole-word membership test against a comma-list, e.g. InList("SPC1","SPC,SPC1").
' The commas on both ends stop "SPC" from matching inside "SPC1".
Function InList(card As String, listStr As String) As Boolean
    InList = (InStr("," & listStr & ",", "," & card & ",") > 0)
End Function


'------------------------------------------------------------------------------
' Record a DISTINCT card name that landed in misc.bdf, so the summary can list
' what wasn't recognized. InList skips repeats, leaving one entry per card type.
'------------------------------------------------------------------------------
Sub RecordMisc(card As String)
    If InList(card, gMiscCards) Then Exit Sub
    If gMiscCount > 0 Then gMiscCards = gMiscCards & ","
    gMiscCards = gMiscCards & card
    gMiscCount = gMiscCount + 1
End Sub


'------------------------------------------------------------------------------
' UniqueGroupName - a filesystem-safe, unique base name for a group's .bdf file
' (no extension). Derived from the group's title, with two collisions handled:
'   1. the title sanitizes to a reserved bucket name (materials, csys, ...), or
'   2. two selected groups sanitize to the same name.
' Either way we append "_<groupID>", which is guaranteed unique.
'------------------------------------------------------------------------------
Function UniqueGroupName(App As femap.model, groupID As Long, gi As Long) As String
    Dim gp As femap.Group
    Set gp = App.feGroup

    ' Start from the sanitized title; fall back to "group_<id>" if it has none.
    Dim base As String
    If gp.Get(groupID) = FE_OK Then base = SanitizeName(gp.title) Else base = ""
    If base = "" Then base = "group_" & CStr(groupID)

    ' Collision 1: don't let a group file overwrite a shared bucket file.
    Dim reserved As String
    reserved = ",materials,properties,csys,loads,constraints,misc,"
    Dim nm As String
    nm = base
    If InStr(reserved, "," & LCase(nm) & ",") > 0 Then nm = base & "_" & CStr(groupID)

    ' Collision 2: compare against the names already assigned to earlier groups
    ' (gGrpTitle(0..gi-1), filled as we loop through the selection in Main).
    Dim j As Long, clash As Boolean
    clash = True
    Do While clash
        clash = False
        For j = 0 To gi - 1
            If LCase(gGrpTitle(j)) = LCase(nm) Then
                nm = base & "_" & CStr(groupID)
                clash = False           ' the ID is unique, so this resolves it in one pass
                Exit For
            End If
        Next j
    Loop
    UniqueGroupName = nm
End Function

' SanitizeName - make a title safe as a file name: keep [0-9 A-Z a-z _ -], turn
' every other character into "_", collapse runs of "_", and trim leading/trailing
' "_". E.g. "Wing Skin (upper)" -> "Wing_Skin_upper".
Function SanitizeName(s As String) As String
    Dim i As Long, c As String, out As String
    out = ""
    For i = 1 To Len(s)
        c = Mid(s, i, 1)
        If (c >= "0" And c <= "9") Or (c >= "A" And c <= "Z") _
           Or (c >= "a" And c <= "z") Or c = "_" Or c = "-" Then
            out = out & c
        Else
            out = out & "_"
        End If
    Next i
    Do While InStr(out, "__") > 0
        out = Replace(out, "__", "_")
    Loop
    If Left(out, 1) = "_" Then out = Mid(out, 2)
    If Right(out, 1) = "_" Then out = Left(out, Len(out) - 1)
    SanitizeName = out
End Function
