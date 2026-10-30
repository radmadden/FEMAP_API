'==============================================================================
' MAKE BOLT CONNECTIONS  --  CBUSH + RBE3 bolts between two shell meshes
'==============================================================================
'
' PURPOSE
'   Build fastener connections for a whole bolt pattern in one run. For each
'   bolt you supply a center Point on each side of a joint; the macro:
'     1. pairs each side-A Point with its nearest side-B Point (the bolt axis),
'     2. finds, on each shell, the plate element the bolt axis PIERCES,
'     3. builds a weighted RBE3 "spider" on each shell: the reference grid is
'        the DEPENDENT node, the pierced element's corner nodes are the
'        INDEPENDENT grids, weighted by the element's SHAPE FUNCTIONS at the
'        pierce point (so the weighted centroid lands exactly on the pierce
'        point -- no spurious moment; see research doc), and
'     4. drops a CBUSH of a specified property between the two reference grids.
'   New node/element IDs follow a start-ID + per-bolt stride you choose.
'
' WHY SHAPE-FUNCTION WEIGHTS (not inverse distance)
'   RBE3 treats the weighted centroid of the independents as the effective load
'   point. Inverse-distance weights do NOT put that centroid on the pierce point
'   (e.g. bolt at (0.25,0.25) on a unit square -> IDW centroid ~ (0.35,0.35)),
'   so you get a mesh-dependent moment that nothing flags. Shape-function weights
'   N_i(xi,eta) satisfy Sum(N_i * x_i) = x_pierce exactly (isoparametric map), so
'   the centroid is on the pierce point and a force at the reference grid
'   distributes as the consistent nodal load. TRIA3 -> area coordinates (exact);
'   QUAD4 -> bilinear N_i, with the (xi,eta) found by Newton iteration in the
'   element mean plane (handles a non-parallelogram / warped quad).
'
' RBE3 DETAILS
'   * Independent (corner) grids contribute translation DOF 123 only. A moment
'     pushed through the spider would react as w_i*r_i forces on nodes an element
'     apart -- small moments become big nodal forces. Keep the bush rotational
'     stiffness ~0 too (that is in YOUR CBUSH property).
'   * The reference grid is dependent in all 6 DOF (REFC = 123456). This is
'     deliberate: with ~0 bush rotational stiffness, leaving the reference 456
'     out of REFC would leave them unconstrained (grounded-rotation singularity).
'     With REFC=123456 the spider defines the reference rotations from the
'     translational independents (standard RBE3).
'
' OFFSET_MODE  (reference-grid placement)
'   0 = MIDPLANE SPANNING (default, robust): each reference grid sits on its
'       shell's mid-surface at the pierce point. The two grids are distinct and
'       separated by the midplane gap, so the CBUSH self-orients along the bolt
'       axis -- no orientation CSys needed.
'   1 = FAYING-SURFACE OFFSET: each reference grid is pushed t/2 off its shell
'       midplane toward the other plate (t = pierced element's property
'       thickness, pval(0)), onto the faying surface. This represents the real
'       eccentricity couple in the RBE3. If the plates touch, the two faying
'       points coincide -> zero-length CBUSH, which needs an orientation CSys
'       (SpringCID). For that case the macro asks you for an existing rectangular
'       CSys to reuse, or auto-creates one per bolt aligned to that bolt's axis.
'
'   ** UNVERIFIED (verify before trusting): a gap-spanning CBUSH with zero
'   rotational stiffness is believed to carry force but NOT the F*gap couple, so
'   a Mode-0 bolt may be off in moment equilibrium by F*gap. Check a single bolt:
'   apply shear, confirm moment balance in GPFORCE and SPC forces. If confirmed,
'   prefer Mode 1 (the eccentricity couple is then represented, not a mesh
'   artifact). The macro keeps both modes and just reminds you. **
'
' INPUT (all picked at run time)
'   * CBUSH property ID (must already exist).
'   * Offset mode (0/1).
'   * Side-A plate elements (the shell), then side-A bolt Points.
'   * Side-B plate elements, then side-B bolt Points.
'   * Numbering: first node ID, node stride, first element ID, element stride.
'
' OUTPUT
'   Per bolt: 2 reference nodes, 2 RBE3 elements, 1 CBUSH (and, Mode 1 coincident
'   only, 1 orientation CSys). Nothing is created if any target ID is already in
'   use -- the macro reports the clash and aborts first.
'
' HOW TO RUN
'   FEMAP > File > Programming > API Programming (Ctrl+F11), paste, then Run.
'
' STATUS
'   Drafted, not yet run in FEMAP. First-run checks: RBE3 shows as interpolation
'   with the expected weights (Entity Editor); CBUSH orientation looks right;
'   run the single-bolt GPFORCE/SPC moment-balance check above.
'
' API CALLS USED (Femap API Guide 2025)
'   feFemap, fePoint(x/y/z), feSet(Select FT_POINT/FT_ELEM, First/Next),
'   feElem(Get, type, topology, propID, node, Release, RigidInterpolate,
'     PutNodeList, ClearNodeList, SpringUseCID, SpringCID, color, layer, Put),
'   feNode(Get, x/y/z, Put), feProp(Get, type, pval), feCSys(Axis, Put),
'   Info_NextID(FT_CSYS), feAppLock/feAppUnlock, feViewRegenerate
'==============================================================================


'------------------------------------------------------------------------------
' USER SETTINGS
'------------------------------------------------------------------------------
Const OFFSET_MODE_DEF As Long   = 0        ' default at the prompt: 0 spanning, 1 faying
Const INSIDE_TOL      As Double = 0.02     ' containment slack on xi/eta (quad) / lambda (tri)
Const WEIGHT_MIN      As Double = 0.03     ' warn: a shape weight this small -> near-edge collapse
Const MAX_PIERCE_DIST As Double = 1.0E30   ' accept a non-containing nearest element only within this
Const COINCIDENT_TOL  As Double = 1.0E-6   ' refA/refB closer than this -> zero-length CBUSH
Const PAIR_WARN_FAC   As Double = 3.0      ' warn if a pair gap exceeds this * median gap

Const REF_NODE_COLOR  As Long   = 120      ' Femap palette index for new reference nodes
Const RBE_COLOR       As Long   = 46       ' ... RBE3 elements
Const CBUSH_COLOR     As Long   = 10       ' ... CBUSH elements


'------------------------------------------------------------------------------
' Module-level state
'------------------------------------------------------------------------------
Dim gApp As femap.model

' Combined element-geometry cache for BOTH sides (filled once in Main). Corner
' coords are flattened 4 slots per element (slot 3 unused for a tri).
Dim gCount As Long
Dim gEID()  As Long          ' element ID
Dim gSide() As Long          ' 0 = side A, 1 = side B
Dim gNC()   As Long          ' corner count (3 or 4)
Dim gPx()   As Double        ' corner x, index i*4 + k
Dim gPy()   As Double
Dim gPz()   As Double
Dim gNx()   As Double        ' unit mean-plane normal
Dim gNy()   As Double
Dim gNz()   As Double
Dim gCx()   As Double        ' centroid
Dim gCy()   As Double
Dim gCz()   As Double
Dim gThk()  As Double        ' pierced-element property thickness (pval(0)), 0 if unknown

Dim gPropThk As Collection   ' propID (as string) -> thickness, so feProp.Get runs once per prop


Sub Main

    Set gApp = feFemap()
    Dim rc As Long

    '--------------------------------------------------------------------------
    ' STEP 1: CBUSH property
    '--------------------------------------------------------------------------
    Dim pstr As String
    pstr = InputBox("CBUSH property ID (must already exist):", "Make Bolt Connections", "")
    If pstr = "" Then Exit Sub
    If Not IsNumeric(pstr) Then
        MsgBox "Property ID must be a number."
        Exit Sub
    End If
    Dim cbushProp As Long
    cbushProp = CLng(pstr)

    Dim pr As femap.Prop
    Set pr = gApp.feProp
    If pr.Get(cbushProp) <> FE_OK Then
        MsgBox "Property " & cbushProp & " does not exist in this model."
        Exit Sub
    End If
    ' pr.type for a spring/bush property is not the element type; just note if it
    ' is clearly not a line/spring property so the user can confirm it is a PBUSH.
    ' (We do not hard-fail -- property typing varies by solver target.)

    '--------------------------------------------------------------------------
    ' STEP 2: offset mode
    '--------------------------------------------------------------------------
    Dim mstr As String
    mstr = InputBox("Reference-grid placement:" & vbCrLf & _
                    "  0 = midplane spanning CBUSH (default, robust)" & vbCrLf & _
                    "  1 = faying-surface offset (t/2), ~zero-length CBUSH", _
                    "Make Bolt Connections - mode", CStr(OFFSET_MODE_DEF))
    If mstr = "" Then Exit Sub
    If Not IsNumeric(mstr) Then
        MsgBox "Mode must be 0 or 1."
        Exit Sub
    End If
    Dim offMode As Long
    offMode = CLng(mstr)
    If offMode < 0 Or offMode > 1 Then
        MsgBox "Mode must be 0 or 1."
        Exit Sub
    End If

    ' For the faying / coincident case the zero-length CBUSH needs an orientation
    ' CSys. Let the user supply one (reused for every coincident bolt -- correct
    ' only if the bolts share an axis, e.g. a planar joint) or auto-create one per
    ' bolt aligned to that bolt's axis. Only used when ends actually coincide.
    Dim userCSys As Long
    userCSys = 0
    If offMode = 1 Then
        Dim csStr As String
        csStr = InputBox("Orientation CSys for any zero-length (coincident) CBUSH:" & vbCrLf & _
                         "  enter an existing rectangular CSys ID to reuse for all, or" & vbCrLf & _
                         "  0 to auto-create one per bolt aligned to its axis", _
                         "Make Bolt Connections - orientation", "0")
        If csStr = "" Or Not IsNumeric(csStr) Then Exit Sub
        userCSys = CLng(csStr)
        If userCSys > 0 Then
            Dim csChk As femap.CSys
            Set csChk = gApp.feCSys
            If csChk.Get(userCSys) <> FE_OK Then
                MsgBox "CSys " & userCSys & " does not exist in this model."
                Exit Sub
            End If
        End If
    End If

    '--------------------------------------------------------------------------
    ' STEP 3: selections -- shell elements + bolt Points, per side
    '--------------------------------------------------------------------------
    Dim esA As femap.Set
    Set esA = gApp.feSet
    If esA.Select(FT_ELEM, True, "Select SIDE-A plate elements (the shell)") <> FE_OK Then Exit Sub
    Dim psA As femap.Set
    Set psA = gApp.feSet
    If psA.Select(FT_POINT, True, "Select SIDE-A bolt-center Points") <> FE_OK Then Exit Sub

    Dim esB As femap.Set
    Set esB = gApp.feSet
    If esB.Select(FT_ELEM, True, "Select SIDE-B plate elements (the shell)") <> FE_OK Then Exit Sub
    Dim psB As femap.Set
    Set psB = gApp.feSet
    If psB.Select(FT_POINT, True, "Select SIDE-B bolt-center Points") <> FE_OK Then Exit Sub

    '--------------------------------------------------------------------------
    ' STEP 4: numbering
    '--------------------------------------------------------------------------
    Dim startNode As Long, nodeStride As Long, startElem As Long, elemStride As Long
    Dim s As String
    s = InputBox("First NEW node ID (reference grids):", "Numbering", "900001")
    If s = "" Or Not IsNumeric(s) Then Exit Sub
    startNode = CLng(s)
    s = InputBox("Node stride per bolt (>= 2; two ref nodes per bolt):", "Numbering", "2")
    If s = "" Or Not IsNumeric(s) Then Exit Sub
    nodeStride = CLng(s)
    s = InputBox("First NEW element ID (RBE3s + CBUSH):", "Numbering", "900001")
    If s = "" Or Not IsNumeric(s) Then Exit Sub
    startElem = CLng(s)
    s = InputBox("Element stride per bolt (>= 3; two RBE3 + one CBUSH per bolt):", "Numbering", "3")
    If s = "" Or Not IsNumeric(s) Then Exit Sub
    elemStride = CLng(s)
    If nodeStride < 2 Then
        MsgBox "Node stride must be >= 2."
        Exit Sub
    End If
    If elemStride < 3 Then
        MsgBox "Element stride must be >= 3."
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 5: cache the candidate element geometry (one pass over both sides)
    '--------------------------------------------------------------------------
    Set gPropThk = New Collection

    ' count first so we can size the arrays once
    Dim nA As Long, nB As Long, id As Long
    nA = 0
    id = esA.First()
    While id > 0
        nA = nA + 1
        id = esA.Next()
    Wend
    nB = 0
    id = esB.First()
    While id > 0
        nB = nB + 1
        id = esB.Next()
    Wend
    If nA = 0 Or nB = 0 Then
        MsgBox "Both sides need plate elements selected (A=" & nA & ", B=" & nB & ")."
        Exit Sub
    End If

    Dim cap As Long
    cap = nA + nB
    ReDim gEID(cap - 1)
    ReDim gSide(cap - 1)
    ReDim gNC(cap - 1)
    ReDim gPx(4 * cap - 1)
    ReDim gPy(4 * cap - 1)
    ReDim gPz(4 * cap - 1)
    ReDim gNx(cap - 1)
    ReDim gNy(cap - 1)
    ReDim gNz(cap - 1)
    ReDim gCx(cap - 1)
    ReDim gCy(cap - 1)
    ReDim gCz(cap - 1)
    ReDim gThk(cap - 1)
    gCount = 0

    Dim nSkipA As Long, nSkipB As Long
    nSkipA = 0
    nSkipB = 0

    gApp.feAppLock

    id = esA.First()
    While id > 0
        If Not CacheElem(id, 0) Then nSkipA = nSkipA + 1
        id = esA.Next()
    Wend
    id = esB.First()
    While id > 0
        If Not CacheElem(id, 1) Then nSkipB = nSkipB + 1
        id = esB.Next()
    Wend

    gApp.feAppUnlock

    '--------------------------------------------------------------------------
    ' STEP 6: read the bolt Points for both sides
    '--------------------------------------------------------------------------
    Dim ptObj As femap.Point
    Set ptObj = gApp.fePoint

    Dim nPA As Long, nPB As Long
    nPA = CountSet(psA)
    nPB = CountSet(psB)
    If nPA = 0 Or nPB = 0 Then
        MsgBox "Both sides need bolt Points selected (A=" & nPA & ", B=" & nPB & ")."
        Exit Sub
    End If

    Dim ax() As Double, ay() As Double, az() As Double
    Dim bx() As Double, by() As Double, bz() As Double
    ReDim ax(nPA - 1)
    ReDim ay(nPA - 1)
    ReDim az(nPA - 1)
    ReDim bx(nPB - 1)
    ReDim by(nPB - 1)
    ReDim bz(nPB - 1)

    Dim k As Long
    k = 0
    id = psA.First()
    While id > 0
        If ptObj.Get(id) = FE_OK Then
            ax(k) = ptObj.x
            ay(k) = ptObj.y
            az(k) = ptObj.z
            k = k + 1
        End If
        id = psA.Next()
    Wend
    nPA = k
    k = 0
    id = psB.First()
    While id > 0
        If ptObj.Get(id) = FE_OK Then
            bx(k) = ptObj.x
            by(k) = ptObj.y
            bz(k) = ptObj.z
            k = k + 1
        End If
        id = psB.Next()
    Wend
    nPB = k

    '--------------------------------------------------------------------------
    ' STEP 7: pair A points to nearest unused B point (bolt axis = A -> B)
    '--------------------------------------------------------------------------
    Dim pairB() As Long, bUsed() As Boolean, pairGap() As Double
    ReDim pairB(nPA - 1)
    ReDim pairGap(nPA - 1)
    ReDim bUsed(nPB - 1)
    Dim i As Long, j As Long
    For j = 0 To nPB - 1
        bUsed(j) = False
    Next j

    Dim nPaired As Long
    nPaired = 0
    For i = 0 To nPA - 1
        Dim best As Long, bestD As Double, d As Double
        best = -1
        bestD = 1.0E30
        For j = 0 To nPB - 1
            If Not bUsed(j) Then
                d = Dist3(ax(i), ay(i), az(i), bx(j), by(j), bz(j))
                If d < bestD Then
                    bestD = d
                    best = j
                End If
            End If
        Next j
        pairB(i) = best
        pairGap(i) = bestD
        If best >= 0 Then
            bUsed(best) = True
            nPaired = nPaired + 1
        End If
    Next i

    If nPaired = 0 Then
        MsgBox "No bolt pairs could be formed."
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 8: pre-check every target ID is free (abort before creating anything)
    '--------------------------------------------------------------------------
    Dim clash As String
    clash = ""
    Dim boltN As Long
    boltN = 0
    For i = 0 To nPA - 1
        If pairB(i) >= 0 Then
            Dim nBase As Long, eBase As Long
            nBase = startNode + boltN * nodeStride
            eBase = startElem + boltN * elemStride
            If Not NodeFree(nBase)     Then clash = clash & "node " & nBase & "  "
            If Not NodeFree(nBase + 1) Then clash = clash & "node " & (nBase + 1) & "  "
            If Not ElemFree(eBase)     Then clash = clash & "elem " & eBase & "  "
            If Not ElemFree(eBase + 1) Then clash = clash & "elem " & (eBase + 1) & "  "
            If Not ElemFree(eBase + 2) Then clash = clash & "elem " & (eBase + 2) & "  "
            boltN = boltN + 1
            If Len(clash) > 300 Then
                clash = clash & "..."
                Exit For
            End If
        End If
    Next i
    If clash <> "" Then
        MsgBox "Target IDs already in use -- nothing created. Clashes:" & vbCrLf & clash & vbCrLf & _
               "Pick a start ID / stride that clears the existing model."
        Exit Sub
    End If

    '--------------------------------------------------------------------------
    ' STEP 9: build each bolt
    '--------------------------------------------------------------------------
    ' One pass over the paired points. For each bolt: normalize the axis, pierce
    ' each shell to get the attach element + shape-function weights, place the two
    ' reference grids (midplane or faying surface), then create 2 nodes, 2 RBE3
    ' spiders and 1 CBUSH at the IDs Step 8 already proved free. feAppLock quiets
    ' the UI for the whole build (one redraw at the end) -- the same pattern the
    ' thermal macro uses for its node loop.
    Dim madeNodes As Long, madeRBE As Long, madeCBUSH As Long, madeCSys As Long
    Dim missA As Long, missB As Long, nEdge As Long, nColl As Long
    madeNodes = 0
    madeRBE = 0
    madeCBUSH = 0
    madeCSys = 0
    missA = 0
    missB = 0
    nEdge = 0
    nColl = 0

    ' outputs from FindPierce
    Dim idxA As Long, idxB As Long
    Dim wA(3) As Double, wB(3) As Double
    Dim pAx As Double, pAy As Double, pAz As Double
    Dim pBx As Double, pBy As Double, pBz As Double
    Dim nAx As Double, nAy As Double, nAz As Double
    Dim nBx As Double, nBy As Double, nBz As Double
    Dim thkA As Double, thkB As Double
    Dim edgeA As Boolean, edgeB As Boolean

    gApp.feAppLock

    boltN = 0
    For i = 0 To nPA - 1
        If pairB(i) >= 0 Then
            j = pairB(i)

            ' bolt axis, A -> B
            Dim dx As Double, dy As Double, dz As Double, dl As Double
            dx = bx(j) - ax(i)
            dy = by(j) - ay(i)
            dz = bz(j) - az(i)
            dl = Sqr(dx * dx + dy * dy + dz * dz)
            If dl > 0# Then
                dx = dx / dl
                dy = dy / dl
                dz = dz / dl
            End If

            FindPierce ax(i), ay(i), az(i), dx, dy, dz, 0, idxA, wA, _
                       pAx, pAy, pAz, nAx, nAy, nAz, thkA, edgeA
            FindPierce bx(j), by(j), bz(j), dx, dy, dz, 1, idxB, wB, _
                       pBx, pBy, pBz, nBx, nBy, nBz, thkB, edgeB

            If idxA < 0 Then missA = missA + 1
            If idxB < 0 Then missB = missB + 1

            If idxA >= 0 And idxB >= 0 Then
                If edgeA Or edgeB Then nEdge = nEdge + 1

                ' reference-grid positions
                Dim rAx As Double, rAy As Double, rAz As Double
                Dim rBx As Double, rBy As Double, rBz As Double
                rAx = pAx : rAy = pAy : rAz = pAz
                rBx = pBx : rBy = pBy : rBz = pBz
                If offMode = 1 Then
                    ' push t/2 off each midplane toward the OTHER plate along the
                    ' shell normal (signed so it points across the joint)
                    Dim sgnA As Double, sgnB As Double
                    sgnA = SignOf(nAx * dx + nAy * dy + nAz * dz)        ' A toward +axis (B)
                    sgnB = SignOf(nBx * (-dx) + nBy * (-dy) + nBz * (-dz)) ' B toward -axis (A)
                    rAx = pAx + nAx * sgnA * (thkA / 2#)
                    rAy = pAy + nAy * sgnA * (thkA / 2#)
                    rAz = pAz + nAz * sgnA * (thkA / 2#)
                    rBx = pBx + nBx * sgnB * (thkB / 2#)
                    rBy = pBy + nBy * sgnB * (thkB / 2#)
                    rBz = pBz + nBz * sgnB * (thkB / 2#)
                End If

                Dim nBase As Long, eBase As Long
                nBase = startNode + boltN * nodeStride
                eBase = startElem + boltN * elemStride

                ' reference nodes
                rc = MakeRefNode(nBase, rAx, rAy, rAz)
                rc = MakeRefNode(nBase + 1, rBx, rBy, rBz)
                madeNodes = madeNodes + 2

                ' RBE3 spiders -- independents = pierced element's corner nodes.
                ' We re-read the corner node IDs from the element here (the cache
                ' stored only their coordinates, not the IDs) so the spider ties to
                ' the actual mesh nodes, in the element's own corner order -- the
                ' same order the shape-function weights wA()/wB() were computed in.
                Dim corA() As Long, corB() As Long
                ReDim corA(gNC(idxA) - 1)
                ReDim corB(gNC(idxB) - 1)
                For k = 0 To gNC(idxA) - 1
                    corA(k) = CornerNode(idxA, k)
                Next k
                For k = 0 To gNC(idxB) - 1
                    corB(k) = CornerNode(idxB, k)
                Next k
                rc = MakeRBE3(nBase,     gNC(idxA), corA, wA, eBase)
                rc = MakeRBE3(nBase + 1, gNC(idxB), corB, wB, eBase + 1)
                madeRBE = madeRBE + 2

                ' CBUSH -- when the two reference grids are distinct (any real
                ' separation) the element self-orients along the GA->GB line, so
                ' no CSys is needed. Only a genuinely zero-length (coincident)
                ' bolt -- mode 1 with the plates touching -- needs an orientation
                ' CSys, supplied or auto-built along this bolt's axis.
                Dim gap As Double, csysID As Long, coinc As Boolean
                gap = Dist3(rAx, rAy, rAz, rBx, rBy, rBz)
                coinc = (gap < COINCIDENT_TOL)
                csysID = 0
                If coinc Then
                    If userCSys > 0 Then
                        csysID = userCSys               ' reuse the supplied CSys
                    Else
                        csysID = MakeAxisCSys(rAx, rAy, rAz, dx, dy, dz)
                        madeCSys = madeCSys + 1          ' one auto-created per bolt
                    End If
                    nColl = nColl + 1
                End If
                rc = MakeCBUSH(nBase, nBase + 1, cbushProp, coinc, csysID, eBase + 2)
                madeCBUSH = madeCBUSH + 1

                boltN = boltN + 1
            End If
        End If
    Next i

    gApp.feAppUnlock
    gApp.feViewRegenerate(0)

    '--------------------------------------------------------------------------
    ' STEP 10: report
    '--------------------------------------------------------------------------
    Dim modeStr As String
    If offMode = 0 Then modeStr = "0  midplane spanning CBUSH" Else modeStr = "1  faying-surface offset (t/2)"

    Dim msg As String
    msg = "Bolt pattern built." & vbCrLf & _
          "Offset mode:    " & modeStr & vbCrLf & _
          "Pairs formed:   " & nPaired & " of " & nPA & " side-A points (" & nPB & " side-B points)" & vbCrLf & _
          "Bolts created:  " & madeCBUSH & vbCrLf & _
          "  reference nodes: " & madeNodes & vbCrLf & _
          "  RBE3 elements:   " & madeRBE & vbCrLf & _
          "  CBUSH elements:  " & madeCBUSH & vbCrLf
    If madeCSys > 0 Then msg = msg & "  orientation CSys (zero-length bolts): " & madeCSys & vbCrLf
    msg = msg & "Cached plate elements: A=" & nA & " (skipped " & nSkipA & " non-plate), " & _
                "B=" & nB & " (skipped " & nSkipB & ")" & vbCrLf
    If missA > 0 Or missB > 0 Then
        msg = msg & "** " & missA & " bolt(s) missed the side-A shell, " & missB & _
                    " missed side-B -- those bolts were skipped. Check the Point/element selections. **" & vbCrLf
    End If
    If nEdge > 0 Then
        msg = msg & "** " & nEdge & " bolt(s) pierce near an element edge/corner: a shape weight < " & _
                    WEIGHT_MIN & ", so the load collapses onto 1-2 nodes (mesh-dependent hot spot). " & _
                    "Trust the CBUSH force, not the local shell stress. **" & vbCrLf
    End If
    If nColl > 0 Then
        If userCSys > 0 Then
            msg = msg & nColl & " zero-length CBUSH(es) oriented with supplied CSys " & userCSys & "." & vbCrLf
        Else
            msg = msg & nColl & " zero-length CBUSH(es) oriented with an auto-created per-bolt CSys." & vbCrLf
        End If
    End If
    msg = msg & vbCrLf & _
          "VERIFY (unverified assumption): a zero-rotational-stiffness CBUSH may carry force but " & _
          "not the F*gap couple. Check one bolt -- apply shear, confirm moment balance in GPFORCE " & _
          "and SPC forces. If it matters, prefer mode 1."
    MsgBox msg

End Sub


'==============================================================================
' CACHE / SELECTION HELPERS
'==============================================================================

' Read one element's corner geometry into the module cache. Returns False (and
' caches nothing) for a non-plate topology or an unreadable node.
Function CacheElem(eid As Long, side As Long) As Boolean
    CacheElem = False
    Dim el As femap.Elem
    Set el = gApp.feElem
    If el.Get(eid) <> FE_OK Then Exit Function

    Dim topo As Long, nc As Long
    topo = el.topology
    If topo = 2 Or topo = 3 Then
        nc = 3                      ' Tri3 / Tri6 -- 3 corners
    ElseIf topo = 4 Or topo = 5 Then
        nc = 4                      ' Quad4 / Quad8 -- 4 corners
    Else
        Exit Function               ' not a plate shape we handle
    End If

    Dim nd As femap.Node
    Set nd = gApp.feNode
    Dim px(3) As Double, py(3) As Double, pz(3) As Double
    Dim k As Long, nid As Long
    For k = 0 To nc - 1
        nid = el.node(k)
        If nd.Get(nid) <> FE_OK Then Exit Function
        px(k) = nd.x
        py(k) = nd.y
        pz(k) = nd.z
    Next k

    ' centroid
    Dim cx As Double, cy As Double, cz As Double
    cx = 0# : cy = 0# : cz = 0#
    For k = 0 To nc - 1
        cx = cx + px(k)
        cy = cy + py(k)
        cz = cz + pz(k)
    Next k
    cx = cx / nc : cy = cy / nc : cz = cz / nc

    ' mean-plane normal: quad from diagonal cross (P2-P0)x(P3-P1); tri from edges
    Dim ux As Double, uy As Double, uz As Double
    Dim vx As Double, vy As Double, vz As Double
    If nc = 4 Then
        ux = px(2) - px(0) : uy = py(2) - py(0) : uz = pz(2) - pz(0)
        vx = px(3) - px(1) : vy = py(3) - py(1) : vz = pz(3) - pz(1)
    Else
        ux = px(1) - px(0) : uy = py(1) - py(0) : uz = pz(1) - pz(0)
        vx = px(2) - px(0) : vy = py(2) - py(0) : vz = pz(2) - pz(0)
    End If
    Dim nx As Double, ny As Double, nz As Double
    Cross ux, uy, uz, vx, vy, vz, nx, ny, nz
    Dim nl As Double
    nl = Sqr(nx * nx + ny * ny + nz * nz)
    If nl <= 0# Then Exit Function          ' degenerate element
    nx = nx / nl : ny = ny / nl : nz = nz / nl

    ' store
    Dim gi As Long
    gi = gCount
    gEID(gi) = eid
    gSide(gi) = side
    gNC(gi) = nc
    For k = 0 To 3
        If k < nc Then
            gPx(gi * 4 + k) = px(k)
            gPy(gi * 4 + k) = py(k)
            gPz(gi * 4 + k) = pz(k)
        Else
            gPx(gi * 4 + k) = 0#
            gPy(gi * 4 + k) = 0#
            gPz(gi * 4 + k) = 0#
        End If
    Next k
    gNx(gi) = nx : gNy(gi) = ny : gNz(gi) = nz
    gCx(gi) = cx : gCy(gi) = cy : gCz(gi) = cz
    gThk(gi) = PropThickness(el.propID)
    gCount = gCount + 1
    CacheElem = True
End Function

' Corner node ID for cached element gi, corner k (re-read from the element).
Function CornerNode(gi As Long, k As Long) As Long
    Dim el As femap.Elem
    Set el = gApp.feElem
    If el.Get(gEID(gi)) = FE_OK Then
        CornerNode = el.node(k)
    Else
        CornerNode = 0
    End If
End Function

' Plate thickness = property pval(0); cached per propID so feProp.Get runs once.
Function PropThickness(propID As Long) As Double
    Dim key As String
    key = CStr(propID)
    On Error GoTo lookup
    PropThickness = CDbl(gPropThk.Item(key))
    Exit Function
lookup:
    On Error GoTo 0
    Dim pr As femap.Prop
    Set pr = gApp.feProp
    Dim t As Double
    t = 0#
    If pr.Get(propID) = FE_OK Then t = pr.pval(0)
    On Error Resume Next
    gPropThk.Add t, key
    On Error GoTo 0
    PropThickness = t
End Function

Function CountSet(st As femap.Set) As Long
    Dim n As Long, id As Long
    n = 0
    id = st.First()
    While id > 0
        n = n + 1
        id = st.Next()
    Wend
    CountSet = n
End Function


'==============================================================================
' PIERCE / WEIGHTS
'==============================================================================

' For a bolt point (px,py,pz) and axis (dx,dy,dz), among cached elements on the
' requested side, choose the one the axis pierces (prefer containing the pierce;
' else nearest). Returns the cache index (-1 none), shape-function weights w(0..3),
' the pierce point, the element normal, its thickness, and a near-edge flag.
Sub FindPierce(px As Double, py As Double, pz As Double, _
               dx As Double, dy As Double, dz As Double, _
               wantSide As Long, outIdx As Long, outW() As Double, _
               outPx As Double, outPy As Double, outPz As Double, _
               outNx As Double, outNy As Double, outNz As Double, _
               outThk As Double, outEdge As Boolean)

    outIdx = -1
    Dim bestInside As Long, bestAny As Long
    Dim dInside As Double, dAny As Double
    bestInside = -1
    bestAny = -1
    dInside = 1.0E30
    dAny = 1.0E30

    Dim w(3) As Double
    Dim inside As Boolean, edge As Boolean, valid As Boolean, dist As Double
    Dim qx As Double, qy As Double, qz As Double
    Dim i As Long

    ' Track two candidates as we scan this side's cached elements:
    '   bestInside = nearest element that actually CONTAINS the pierce point,
    '   bestAny    = nearest element whatever the containment.
    ' "Nearest" = smallest distance from the bolt point to where the axis crosses
    ' that element's plane, so when the axis grazes several elements we take the
    ' one it really goes through by the point, not one edge-on far away.
    For i = 0 To gCount - 1
        If gSide(i) = wantSide Then
            ComputeElemFit i, px, py, pz, dx, dy, dz, w, inside, edge, dist, qx, qy, qz, valid
            If valid Then
                If inside And dist < dInside Then
                    dInside = dist
                    bestInside = i
                End If
                If dist < dAny Then
                    dAny = dist
                    bestAny = i
                End If
            End If
        End If
    Next i

    ' Prefer a containing element (unambiguous). Fall back to the nearest
    ' non-containing one only when nothing contained the pierce -- e.g. a bolt
    ' just off the meshed edge -- and only within MAX_PIERCE_DIST.
    If bestInside >= 0 Then
        outIdx = bestInside
    ElseIf bestAny >= 0 And dAny < MAX_PIERCE_DIST Then
        outIdx = bestAny
    End If

    If outIdx >= 0 Then
        ' The scan kept only indices and distances, not each element's full fit,
        ' to stay cheap. Recompute the winner's fit once to fill the outputs.
        ComputeElemFit outIdx, px, py, pz, dx, dy, dz, w, inside, edge, dist, qx, qy, qz, valid
        Dim k As Long
        For k = 0 To 3
            outW(k) = w(k)
        Next k
        outPx = qx : outPy = qy : outPz = qz
        outNx = gNx(outIdx) : outNy = gNy(outIdx) : outNz = gNz(outIdx)
        outThk = gThk(outIdx)
        outEdge = edge
    End If
End Sub

' One element's fit: pierce point, shape-function weights at it, containment and
' near-edge flags, and the point->pierce distance. valid=False if the axis is
' parallel to the element plane or the element is degenerate.
Sub ComputeElemFit(gi As Long, px As Double, py As Double, pz As Double, _
                   dx As Double, dy As Double, dz As Double, _
                   w() As Double, inside As Boolean, edge As Boolean, _
                   dist As Double, qx As Double, qy As Double, qz As Double, valid As Boolean)

    valid = False
    inside = False
    edge = False
    Dim k As Long
    For k = 0 To 3
        w(k) = 0#
    Next k

    Dim nc As Long
    nc = gNC(gi)
    Dim nx As Double, ny As Double, nz As Double
    nx = gNx(gi) : ny = gNy(gi) : nz = gNz(gi)

    ' Intersect the bolt line  X = point + s*axis  with the element mean plane
    ' (through the centroid, normal n). s = ((centroid - point).n)/(axis.n); the
    ' pierce point q = point + s*axis. If axis.n ~ 0 the bolt runs parallel to the
    ' plate and never pierces it -> this element is not a candidate.
    Dim denom As Double
    denom = dx * nx + dy * ny + dz * nz
    If Abs(denom) < 0.000001 Then Exit Sub        ' axis parallel to the plane

    Dim wx As Double, wy As Double, wz As Double
    wx = gCx(gi) - px : wy = gCy(gi) - py : wz = gCz(gi) - pz
    Dim s As Double
    s = (wx * nx + wy * ny + wz * nz) / denom
    qx = px + s * dx
    qy = py + s * dy
    qz = pz + s * dz

    ' To get the isoparametric coords we need the element and the pierce point in
    ' a common 2D in-plane frame. Build an orthonormal (ex, ey) lying in the mean
    ' plane: ex = edge 0->1 with its out-of-plane (normal) part removed, then
    ' ey = n x ex. Everything is measured about the centroid.
    Dim b0x As Double, b0y As Double, b0z As Double
    b0x = gPx(gi * 4 + 1) - gPx(gi * 4 + 0)
    b0y = gPy(gi * 4 + 1) - gPy(gi * 4 + 0)
    b0z = gPz(gi * 4 + 1) - gPz(gi * 4 + 0)
    Dim ad As Double
    ad = b0x * nx + b0y * ny + b0z * nz
    Dim exx As Double, exy As Double, exz As Double
    exx = b0x - ad * nx
    exy = b0y - ad * ny
    exz = b0z - ad * nz
    Dim el As Double
    el = Sqr(exx * exx + exy * exy + exz * exz)
    If el <= 0# Then Exit Sub
    exx = exx / el : exy = exy / el : exz = exz / el
    Dim eyx As Double, eyy As Double, eyz As Double
    Cross nx, ny, nz, exx, exy, exz, eyx, eyy, eyz   ' ey = n x ex (unit, since n & ex orthonormal)

    ' project corners and the pierce point into (ex, ey) about the centroid
    Dim qu(3) As Double, qv(3) As Double
    Dim rx As Double, ry As Double, rz As Double
    For k = 0 To nc - 1
        rx = gPx(gi * 4 + k) - gCx(gi)
        ry = gPy(gi * 4 + k) - gCy(gi)
        rz = gPz(gi * 4 + k) - gCz(gi)
        qu(k) = rx * exx + ry * exy + rz * exz
        qv(k) = rx * eyx + ry * eyy + rz * eyz
    Next k
    rx = qx - gCx(gi) : ry = qy - gCy(gi) : rz = qz - gCz(gi)
    Dim pu As Double, pv As Double
    pu = rx * exx + ry * exy + rz * exz
    pv = rx * eyx + ry * eyy + rz * eyz

    ' Weights = shape functions at the pierce point (the whole point of this
    ' macro -- see header). Quad: invert the bilinear map to (xi,eta), then
    ' evaluate N_i; contained when |xi|,|eta| <= 1 (+ slack). Tri: area
    ' coordinates are the shape functions directly, contained when all >= 0.
    If nc = 4 Then
        Dim xi As Double, eta As Double, conv As Boolean
        InvMapQuad qu, qv, pu, pv, xi, eta, conv
        ShapeQuad xi, eta, w
        inside = (Abs(xi) <= 1# + INSIDE_TOL And Abs(eta) <= 1# + INSIDE_TOL)
    Else
        BaryTri qu, qv, pu, pv, w
        inside = (w(0) >= -INSIDE_TOL And w(1) >= -INSIDE_TOL And w(2) >= -INSIDE_TOL)
    End If

    ' Near-edge/corner flag: if the smallest weight is tiny the load collapses
    ' onto 1-2 nodes (mesh-dependent hot spot). Reported, not blocked.
    Dim mn As Double
    mn = w(0)
    For k = 1 To nc - 1
        If w(k) < mn Then mn = w(k)
    Next k
    edge = (mn < WEIGHT_MIN)

    dist = Dist3(px, py, pz, qx, qy, qz)
    valid = True
End Sub

' Invert the bilinear quad map: find (xi,eta) with Sum N_i(xi,eta)*q_i = p, where
' q are the four corners' 2D in-plane coords and p is the pierce point. A general
' quad is not a parallelogram, so there is no closed form -- solve the 2x2
' nonlinear system by Newton's method. At each step the residual is
' f(xi,eta) = Sum N_i*q_i - p; J is its Jacobian (d/dxi, d/deta); we solve
' J*[dxi;deta] = -f (2x2 by Cramer's rule) and step. Starts at the center (0,0),
' converges in a few iterations; conv=False only means it hit the 25-iter cap
' (a hopelessly degenerate quad) -- weights are still usable. N_i are the
' standard bilinear functions N0=(1-xi)(1-eta)/4, N1=(1+xi)(1-eta)/4, etc.
Sub InvMapQuad(qu() As Double, qv() As Double, pu As Double, pv As Double, _
               xi As Double, eta As Double, conv As Boolean)
    xi = 0#
    eta = 0#
    conv = False
    Dim it As Long
    For it = 1 To 25
        ' shape functions at the current (xi,eta)
        Dim om As Double, op As Double, em As Double, ep As Double
        om = 1# - xi : op = 1# + xi          ' (1-xi), (1+xi)
        em = 1# - eta : ep = 1# + eta         ' (1-eta), (1+eta)
        Dim n0 As Double, n1 As Double, n2 As Double, n3 As Double
        n0 = om * em / 4# : n1 = op * em / 4# : n2 = op * ep / 4# : n3 = om * ep / 4#
        ' residual f = Sum N_i q_i - p  (want this -> 0)
        Dim fu As Double, fv As Double
        fu = n0 * qu(0) + n1 * qu(1) + n2 * qu(2) + n3 * qu(3) - pu
        fv = n0 * qv(0) + n1 * qv(1) + n2 * qv(2) + n3 * qv(3) - pv
        ' shape-function derivatives wrt xi (..x) and eta (..e)
        Dim dn0x As Double, dn1x As Double, dn2x As Double, dn3x As Double
        Dim dn0e As Double, dn1e As Double, dn2e As Double, dn3e As Double
        dn0x = -em / 4# : dn1x = em / 4# : dn2x = ep / 4# : dn3x = -ep / 4#
        dn0e = -om / 4# : dn1e = -op / 4# : dn2e = op / 4# : dn3e = om / 4#
        ' Jacobian J = [a b; c d] = d(Sum N_i q_i)/d(xi,eta)
        Dim a As Double, b As Double, c As Double, dd As Double
        a = dn0x * qu(0) + dn1x * qu(1) + dn2x * qu(2) + dn3x * qu(3)
        b = dn0e * qu(0) + dn1e * qu(1) + dn2e * qu(2) + dn3e * qu(3)
        c = dn0x * qv(0) + dn1x * qv(1) + dn2x * qv(2) + dn3x * qv(3)
        dd = dn0e * qv(0) + dn1e * qv(1) + dn2e * qv(2) + dn3e * qv(3)
        Dim det As Double
        det = a * dd - b * c
        If Abs(det) < 1.0E-20 Then Exit For          ' collapsed element -> give up
        ' solve J*[dxi;deta] = -f  (Cramer), step, test convergence
        Dim dxi As Double, deta As Double
        dxi = (-fu * dd + b * fv) / det
        deta = (c * fu - a * fv) / det
        xi = xi + dxi
        eta = eta + deta
        If Abs(dxi) + Abs(deta) < 1.0E-10 Then
            conv = True
            Exit For
        End If
    Next it
End Sub

' Bilinear quad shape functions N_i at (xi,eta) -- the RBE3 independent weights.
' Corner order matches FEMAP node(0..3): (-1,-1),(+1,-1),(+1,+1),(-1,+1).
Sub ShapeQuad(xi As Double, eta As Double, w() As Double)
    w(0) = (1# - xi) * (1# - eta) / 4#
    w(1) = (1# + xi) * (1# - eta) / 4#
    w(2) = (1# + xi) * (1# + eta) / 4#
    w(3) = (1# - xi) * (1# + eta) / 4#
End Sub

' Linear-tri area (barycentric) coordinates of the pierce point, which ARE the
' tri's shape functions -- exact, no iteration. Solve p - P0 = l1*(P1-P0) +
' l2*(P2-P0) for (l1,l2) by Cramer; the third weight is 1 - l1 - l2. w(3) unused.
Sub BaryTri(qu() As Double, qv() As Double, pu As Double, pv As Double, w() As Double)
    Dim e1u As Double, e1v As Double, e2u As Double, e2v As Double
    e1u = qu(1) - qu(0) : e1v = qv(1) - qv(0)
    e2u = qu(2) - qu(0) : e2v = qv(2) - qv(0)
    Dim det As Double
    det = e1u * e2v - e2u * e1v
    Dim l1 As Double, l2 As Double
    If Abs(det) < 1.0E-20 Then
        l1 = 0# : l2 = 0#
    Else
        Dim ru As Double, rv As Double
        ru = pu - qu(0) : rv = pv - qv(0)
        l1 = (ru * e2v - e2u * rv) / det
        l2 = (e1u * rv - ru * e1v) / det
    End If
    w(1) = l1
    w(2) = l2
    w(0) = 1# - l1 - l2
    w(3) = 0#
End Sub


'==============================================================================
' ENTITY CREATION
'==============================================================================

Function MakeRefNode(id As Long, x As Double, y As Double, z As Double) As Long
    Dim nd As femap.Node
    Set nd = gApp.feNode
    nd.x = x
    nd.y = y
    nd.z = z
    nd.layer = 1
    nd.color = REF_NODE_COLOR
    MakeRefNode = nd.Put(id)
End Function

' RBE3: node(0)=reference (dependent, REFC=123456); node list = corner nodes as
' interpolation independents, DOF 123 each, weighted by shape functions.
Function MakeRBE3(refNode As Long, nc As Long, corners() As Long, w() As Double, elemID As Long) As Long
    Dim el As femap.Elem
    Set el = gApp.feElem
    el.ClearNodeList(-1)
    el.type = 29                 ' Rigid
    el.topology = 13             ' RigidList
    el.propID = 0                ' rigid elements carry no property
    el.RigidInterpolate = True   ' -> RBE3 (interpolation), not RBE2
    el.color = RBE_COLOR
    el.layer = 1
    el.node(0) = refNode
    Dim i As Long
    For i = 0 To 5
        el.Release(0, i) = 1     ' REFC = 123456 on the reference grid
    Next i

    Dim nd() As Long, fc() As Long, wt() As Double, df() As Long
    ReDim nd(nc - 1)
    ReDim fc(nc - 1)
    ReDim wt(nc - 1)
    ReDim df(6 * nc - 1)
    For i = 0 To nc - 1
        nd(i) = corners(i)
        fc(i) = 0
        wt(i) = w(i)
        df(6 * i + 0) = 1        ' DOF 1,2,3 (translations) on each independent
        df(6 * i + 1) = 1
        df(6 * i + 2) = 1
        df(6 * i + 3) = 0
        df(6 * i + 4) = 0
        df(6 * i + 5) = 0
    Next i

    Dim vN As Variant, vF As Variant, vW As Variant, vD As Variant
    vN = nd
    vF = fc
    vW = wt
    vD = df
    Dim rc As Long
    rc = el.PutNodeList(0, nc, vN, vF, vW, vD)
    MakeRBE3 = el.Put(elemID)
End Function

' CBUSH: Spring/Damper element referencing the given property, spanning the two
' reference grids. If the ends are coincident, orient it with an orientation CSys.
Function MakeCBUSH(refA As Long, refB As Long, propID As Long, coincident As Boolean, csysID As Long, elemID As Long) As Long
    Dim el As femap.Elem
    Set el = gApp.feElem
    el.ClearNodeList(-1)
    el.type = 6                  ' Spring/Damper
    el.topology = 0              ' Line2
    el.propID = propID
    el.color = CBUSH_COLOR
    el.layer = 1
    el.node(0) = refA
    el.node(1) = refB
    If coincident Then
        el.SpringNoOrient = False
        el.SpringUseCID = True
        el.SpringCID = csysID
    End If
    MakeCBUSH = el.Put(elemID)
End Function

' Rectangular CSys with +X along (dx,dy,dz) at the given origin; returns its ID.
Function MakeAxisCSys(ox As Double, oy As Double, oz As Double, dx As Double, dy As Double, dz As Double) As Long
    Dim cs As femap.CSys
    Set cs = gApp.feCSys
    Dim origin(2) As Double, axpt(2) As Double
    origin(0) = ox : origin(1) = oy : origin(2) = oz
    axpt(0) = ox + dx : axpt(1) = oy + dy : axpt(2) = oz + dz
    Dim rc As Long
    rc = cs.Axis(0, 0, origin, axpt)    ' 0=rectangular, 0=X axis toward axpt
    Dim id As Long
    id = gApp.Info_NextID(FT_CSYS)
    rc = cs.Put(id)
    MakeAxisCSys = id
End Function


'==============================================================================
' SMALL MATH / ID HELPERS
'==============================================================================

Function NodeFree(id As Long) As Boolean
    Dim nd As femap.Node
    Set nd = gApp.feNode
    NodeFree = (nd.Get(id) <> FE_OK)
End Function

Function ElemFree(id As Long) As Boolean
    Dim el As femap.Elem
    Set el = gApp.feElem
    ElemFree = (el.Get(id) <> FE_OK)
End Function

Function Dist3(ax As Double, ay As Double, az As Double, bx As Double, by As Double, bz As Double) As Double
    Dim dx As Double, dy As Double, dz As Double
    dx = ax - bx : dy = ay - by : dz = az - bz
    Dist3 = Sqr(dx * dx + dy * dy + dz * dz)
End Function

Function SignOf(v As Double) As Double
    If v < 0# Then
        SignOf = -1#
    Else
        SignOf = 1#
    End If
End Function

Sub Cross(ax As Double, ay As Double, az As Double, bx As Double, by As Double, bz As Double, _
          rx As Double, ry As Double, rz As Double)
    rx = ay * bz - az * by
    ry = az * bx - ax * bz
    rz = ax * by - ay * bx
End Sub
