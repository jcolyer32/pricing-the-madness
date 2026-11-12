Attribute VB_Name = "CleanUp"
'==========================================================================
'  CleanUp - ONE-TIME tidy of NCAAM26 - corrected.xlsm
'  Deletes last season's static / unused sheets and the leftover Power Query
'  queries and connections, then saves. Run it once, then remove this module.
'  (Excel does the deleting itself, so tables, names and links stay consistent.)
'==========================================================================
Option Explicit

Sub CleanUpWorkbook()
    Dim toDelete As Variant, nm As Variant, ws As Worksheet
    Dim deleted As String, missing As String, i As Long, nQ As Long, nC As Long, nN As Long
    Dim qCount As Long
    Dim wb As Object        ' late-bound, so Excel versions without .Queries still run the rest
    Set wb = ThisWorkbook

    toDelete = Array("Spread Calculator", "Dropdown Lists", "ESPN_Schedule", "ESPN_Games", "WINP", _
                     "Composite Model", "SoS (2)", "OFF Efficiency", "DEF Efficiency", "EFF FG%", _
                     "Floor %", "ESPN Team Stats", "MM WINP All Time")

    If MsgBox("This deletes 13 old sheets and all leftover queries/connections from" & vbLf & _
              ThisWorkbook.Name & vbLf & vbLf & _
              "Kept: Modelv26, Modelv27, Master, _Status, Spread Calculator v2." & vbLf & vbLf & _
              "Continue?", vbYesNo + vbQuestion, "Clean up workbook") <> vbYes Then Exit Sub

    ' make sure a sheet we keep is active (Excel can't delete the active sheet in some states)
    On Error Resume Next
    ThisWorkbook.Worksheets("Spread Calculator v2").Activate
    On Error GoTo 0

    Application.DisplayAlerts = False
    For Each nm In toDelete
        Set ws = Nothing
        On Error Resume Next
        Set ws = ThisWorkbook.Worksheets(CStr(nm))
        On Error GoTo 0
        If ws Is Nothing Then
            missing = missing & vbLf & "  " & nm
        Else
            ws.Delete
            deleted = deleted & vbLf & "  " & nm
        End If
    Next nm

    ' leftover Power Query queries and connections (not all Excel versions expose
    ' these to macros, so errors are skipped quietly)
    On Error Resume Next
    qCount = 0
    qCount = wb.Queries.Count          ' stays 0 if this Excel has no Queries object
    Err.Clear
    For i = qCount To 1 Step -1
        wb.Queries(i).Delete
        If Err.Number = 0 Then nQ = nQ + 1
        Err.Clear
    Next i
    For i = ThisWorkbook.Connections.Count To 1 Step -1
        ThisWorkbook.Connections(i).Delete
        If Err.Number = 0 Then nC = nC + 1
        Err.Clear
    Next i
    ' named ranges that pointed at the deleted sheets
    For i = ThisWorkbook.Names.Count To 1 Step -1
        If InStr(ThisWorkbook.Names(i).RefersTo, "#REF!") > 0 Then
            ThisWorkbook.Names(i).Delete
            If Err.Number = 0 Then nN = nN + 1
            Err.Clear
        End If
    Next i
    On Error GoTo 0
    Application.DisplayAlerts = True

    ThisWorkbook.Save
    MsgBox "Deleted sheets:" & IIf(deleted = "", " none", deleted) & vbLf & _
           IIf(missing = "", "", vbLf & "Already gone:" & missing & vbLf) & vbLf & _
           "Queries removed: " & nQ & "   Connections removed: " & nC & "   Broken names removed: " & nN & vbLf & vbLf & _
           "Saved. Check Data > Queries & Connections; delete anything still listed by hand." & vbLf & _
           "You can now remove the CleanUp module.", vbInformation, "Clean up done"
End Sub
