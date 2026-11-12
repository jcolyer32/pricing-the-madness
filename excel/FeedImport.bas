Attribute VB_Name = "FeedImport"
'==========================================================================
'  FeedImport - pulls sheets from the pipeline's feed workbook into THIS
'  workbook. A replacement for Power Query, which is unreliable on Mac.
'
'  Setup (once):
'    1. Save your model workbook as .xlsm (File > Save As > Excel Macro-Enabled).
'    2. Tools > Macro > Visual Basic Editor > File > Import File... > pick this file.
'    3. Back in Excel: Tools > Macro > Macros > RefreshFeed > Run.
'       (The first time, Mac asks you to "Grant Access" to the file. Click Grant.)
'
'  Every refresh:  run "Refresh NCAAM Data", then run the RefreshFeed macro.
'
'  The macro OWNS the sheets it imports (it clears and rewrites them), so
'  never type your own work on those sheets. Reference them from other sheets.
'==========================================================================
Option Explicit

' --- edit these two lines if you move the folder or want more sheets -----
Const FEED_PATH As String = "/Users/YOUR_NAME/Documents/pricing-the-madness/output/ncaam_data.xlsx"   ' <- change to your path
Const SHEETS_TO_IMPORT As String = "Modelv26,Modelv27,Master,_Status"
' ------------------------------------------------------------------------

Sub RefreshFeed()
    Dim src As Workbook, dst As Workbook, ws As Worksheet, target As Worksheet
    Dim nm As Variant, data As Variant, imported As String

    Set dst = ThisWorkbook
    If Dir(FEED_PATH) = "" Then
        MsgBox "Feed file not found:" & vbLf & FEED_PATH & vbLf & vbLf & _
               "Run 'Refresh NCAAM Data' first.", vbExclamation
        Exit Sub
    End If

    Application.ScreenUpdating = False
    On Error GoTo Fail
    Set src = Workbooks.Open(FEED_PATH, UpdateLinks:=False, ReadOnly:=True)

    For Each nm In Split(SHEETS_TO_IMPORT, ",")
        nm = Trim(nm)
        Set ws = Nothing
        On Error Resume Next
        Set ws = src.Worksheets(CStr(nm))
        On Error GoTo Fail
        If ws Is Nothing Then
            MsgBox "Sheet '" & nm & "' isn't in the feed. Did the refresh run after it was added?", vbExclamation
        Else
            Set target = GetOrCreateSheet(dst, CStr(nm))
            ' Old Power Query tables on these sheets can't resize to new data:
            ' turn them into plain ranges first (formulas pointing at the sheet keep working).
            Do While target.ListObjects.Count > 0
                target.ListObjects(1).Unlist
            Loop
            target.Cells.ClearContents
            data = ws.UsedRange.Value
            target.Range("A1").Resize(ws.UsedRange.Rows.Count, ws.UsedRange.Columns.Count).Value = data
            target.Rows(1).Font.Bold = True
            imported = imported & vbLf & "  " & nm & " (" & ws.UsedRange.Rows.Count - 1 & " rows)"
        End If
    Next nm

    src.Close SaveChanges:=False
    Application.ScreenUpdating = True
    MsgBox "Imported:" & imported & vbLf & vbLf & "Check _Status for any FAILED sources.", vbInformation
    Exit Sub

Fail:
    Application.ScreenUpdating = True
    If Not src Is Nothing Then src.Close SaveChanges:=False
    MsgBox "Import failed: " & Err.Description, vbCritical
End Sub

Private Function GetOrCreateSheet(wb As Workbook, sheetName As String) As Worksheet
    On Error Resume Next
    Set GetOrCreateSheet = wb.Worksheets(sheetName)
    On Error GoTo 0
    If GetOrCreateSheet Is Nothing Then
        Set GetOrCreateSheet = wb.Worksheets.Add(After:=wb.Worksheets(wb.Worksheets.Count))
        GetOrCreateSheet.Name = sheetName
    End If
End Function
