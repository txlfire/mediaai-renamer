# MediaAI Renamer M1 User Manual

Version: v0.1.0 Preview
Stage: M1 Local Directory Scanning and Page Framework
Date: 2026-06-24

---

## 1. What You Can Do Now

The M1 version is used to scan local or mounted media directories, identify video files, and view media sources, scan tasks, scan results, and operation logs in the interface.

This version will NOT modify your actual file names or perform any renaming operations.

---

## 2. Access Addresses

Default Web Address:

> http://localhost:8971

Development Preview Address:

> http://127.0.0.1:5173

Backend API Default Port:

> 8970

---

## 3. Page Layout

The page is divided into left and right sections.

Left Side - Navigation Area:

- Logo and product name
- Media Sources menu
- Scan Tasks menu
- Scan Results menu
- Backend status indicator
- Language placeholder
- Version number
- Theme toggle button (light/dark)
- Logout entry

Right Side - Workspace:

- Clicking a menu item on the left switches the corresponding page on the right
- Global search box in the upper right corner (M1 only provides the entry; search functionality will be added later)

---

## 4. Sidebar Collapse and Expand

Click the small triangle at the top of the sidebar to expand or collapse the menu.

Expanded State:

- Shows menu icons and labels
- Displays status text, version number, theme button, and current user

Collapsed State:

- Shows icons only
- Status displayed as a small dot
- Hovering over an icon shows a tooltip

---

## 5. Theme Switching

The theme toggle button is located next to the version number in the lower left corner.

Click it to switch between Light and Dark themes.

The dark theme has been applied to:

- Sidebar
- Workspace
- Tables
- Forms
- Input fields
- Log drawer
- Popup layers

---

## 6. Backend Status

The status indicator in the lower left corner shows the backend connection state:

- Green: Backend connection is normal
- Red: Backend connection is abnormal
- Gray: Status unknown or currently detecting

When the sidebar is expanded, the status text is displayed alongside the indicator.

---

## 7. Adding a Media Source

Navigate to the "Media Sources" page.

Fill in the following fields:

- Name: Used to distinguish directories, e.g., "Movies" or "TV Series"
- Directory Path: Local path or mounted directory path
- Status: Enabled or Disabled

Click "Save Media Source" to save the media source to the system.

Notes:

- Directory path cannot be empty
- Directory must exist
- Path must be a directory, not a single file

---

## 8. Starting a Scan

Navigate to the "Scan Tasks" page.

Steps:

1. Select a saved media source from the dropdown
2. Click "Start Full Scan"
3. Wait for the scan to complete
4. View scan statistics in the task table

The task table displays:

- Task ID
- Status
- Scanned count
- Video file count
- Warning count
- Batch size
- Batch interval
- Start time
- End time

---

## 9. Scan Rules

M1 uses batch scanning.

Default Settings:

- 100 files per batch
- 1 second interval between batches

Supported Video Formats:
.mp4, .mkv, .avi, .mov, .wmv, .flv, .m4v, .ts, .m2ts, .rmvb, .mpg, .mpeg, .webm

Extension case sensitivity does not affect recognition.

---

## 10. Viewing Scan Results

Navigate to the "Scan Results" page.

The table displays identified video files with:

- File name
- Format
- File size
- Task ID
- File path
- Modification time

M1 only supports viewing results; naming preview and file renaming are NOT supported.

---

## 11. Viewing Operation Logs

Navigate to the "Scan Tasks" page.

Click "View Log".

The log will appear as a drawer panel.

Supported Operations:

- Refresh log
- Export as TXT

---

## 12. Logout Entry

The logout entry and current user are displayed in the lower left corner.

The current default user is:
admin

M1 does not yet have a user login or permission system, so clicking the logout entry will not perform an actual logout action.

---

## 13. Frequently Asked Questions

13.1 Failed to Save Media Source
Please check:

- Whether the path is empty
- Whether the path exists
- Whether the path is a directory
- Whether the program has access permissions for the directory

13.2 No Results After Scanning
Please check:

- Whether the media source directory contains supported video formats
- Whether the file extensions are in M1's supported list
- Whether the correct media source was selected
- Whether the scan task has completed

13.3 Backend Status Abnormal
Please check:

- Whether the backend service is running
- Whether the backend port is 8970
- Whether the page can access /api/health

13.4 Color Issues in Dark Theme
Please refresh the page. If the issue persists, record the page and control locations where the problem occurs for unified optimization in future updates.

---

## 14. Current Limitations

M1 does NOT support:

- Searching actual results
- Language switching
- User login and permissions
- Metadata matching
- AI recognition
- Naming preview
- Actual renaming
- Incremental scanning

---

## 15. Validation Status

M1 local functionality validation has been completed:

* ✅ Backend tests passed
* ✅  Frontend API tests passed
* ✅ Theme state tests passed
* ✅ Frontend build passed

Docker Compose configuration is ready, but startup validation still needs to be performed in a Docker-capable deployment environment.

