"""Plug &amp; Backup help in English. Pango markup uses <b>, <i> and <tt>; write «&» as &amp;amp;."""
from __future__ import annotations
from typing import Optional

SECTIONS = [
    ("start", "Getting started", """\
<b>Plug &amp; Backup</b> copies selected folders from your computer to removable drives — USB flash drives, SD cards and external drives — with one click or automatically when you connect a drive.

<b>Three terms</b>
• <b>Drive</b> — a partition on a flash drive or disk. The app identifies it by its UUID, not by its drive letter or name.
• <b>Profile</b> — settings for one drive: what to copy, where to put it and how to copy it. Each drive has one profile.
• <b>Job</b> — a pair of folders, one on your computer and one on the drive, with its own mode and exclusions.

<b>First steps</b>
1. Connect a drive. It appears on the left under “New drives”.
2. Click “Create a profile for this drive…” and give it a name.
3. Click “+ Add job” and choose a folder on your computer.
4. Click “Preview” to see what will be copied. Nothing on the drive changes.
5. Click “Back up”.

When a profile’s drive is not connected, you can see the profile and edit its jobs, but backup and restore buttons are unavailable."""),

    ("profiles", "Profiles and drives", """\
<b>Create a profile.</b> Create a profile from “New drives”. The app creates a backup folder on the drive (by default <tt>PlugBackup</tt>) with a marker at <tt>PlugBackup/.plugbackup/id</tt>. The rest of the drive remains yours.

<b>Marker.</b> The marker confirms that the copy on the drive belongs to this profile. Without a matching marker, the app does not delete anything on the drive. If the backup folder already contains data without a marker, the app asks whether to accept it as this profile’s copy. The marker is written only after an action you choose: creating a profile, binding it, “Accept as this profile’s copy…”, “Write marker” or “Update marker”, or backing up.

<b>Reformatted drive.</b> Formatting gives a drive a new UUID. If its device serial number still matches, the app offers “Rebind…”. To do this manually, open the profile’s ⋮ menu and choose “Bind to another drive…”.

<b>Keep a profile copy on the drive</b> is a profile setting. It lets you import the profile from the drive on a new computer. The copy contains the paths to your folders, which anyone can see if the drive is not encrypted.

<b>Rotate between two drives.</b> Open the profile’s ⋮ menu and choose “Create the same profile for another drive”, then bind the copy of the profile to the second drive.

<b>Export and import.</b> Open the profile’s ⋮ menu and choose “Export…” to save the profile to a file. Choose “Import profile…” from ☰ to open it with a preview. Automatic backup is off for imported profiles, so check the folders.

<b>Delete a profile.</b> This removes it from the app only; the copy and marker on the drive are left untouched.

<b>Drive details.</b> Click <b>ⓘ</b> next to the drive row to see its file system, free space and UUID, and to choose “Open in file manager” or “Eject”."""),

    ("jobs", "Jobs", """\
<b>Copy modes</b>
• <b>Mirror with version history</b> (default) — the copy matches the folder. Deleted and replaced files are moved to <tt>PlugBackup/.versions/&lt;date&gt;/</tt> and kept for the number of days set in the profile.
• <b>Mirror</b> — the copy exactly matches the folder. Files deleted on your computer are permanently deleted from the copy.
• <b>Accumulate</b> — new and changed files are copied; nothing is deleted from the copy.

<b>Detect changes</b> by size and modification time (fast), or by file contents (slow; reads every file).

<b>Symbolic links</b> can be handled automatically (on ext4 they are copied as links; on other file systems they are skipped), skipped, or followed so the linked-to files are copied.

<b>Exclusions</b> use rsync patterns, one per line:
• <tt>*.tmp</tt> — matches by name at any depth;
• <tt>node_modules/</tt> — a folder at any depth;
• <tt>/Archive/</tt> — only at the job’s top level.
The “Add the Development set” button adds common build folders. The “Also apply the common exclusion list” check box includes the shared list (☰ → “Common exclusions…”). Files already in a copy are not removed when you exclude them; see the Version history and maintenance section.

<b>Job check boxes</b> let you enable a job, include it in automatic backups (the <b>⟳ AUTO</b> marker), skip hidden files, avoid entering other drives mounted inside the folder, and skip files larger than a chosen number of MB.

<b>In a job row</b>, ▶ backs up only that job; ↑↓ changes its order; ✎ edits it; 🗑 removes the job (the copy on the drive remains); ⋮ opens actions for automatic backup, restore, opening the copy and maintenance."""),

    ("backup", "Backups and safeguards", """\
<b>Start a backup manually</b> with “Back up” for all enabled jobs, or ▶ on an individual job. The app shows the job, percentage, speed and time remaining. “Stop” interrupts copying. If there are no changes, a message appears at the top of the window.

<b>Preview</b> runs a trial and lists new, changed, deleted and skipped files, and compares their size with the available space. Nothing on the drive changes.

<b>Safeguards</b>
• <b>Empty source.</b> If a folder on your computer is empty but the copy contains files, the job is skipped and the copy is left untouched. This helps distinguish a mistake (a drive is disconnected or the wrong folder was removed) from an intentional cleanup.
• <b>Many deletions.</b> The app asks before deleting more than the set share of files in the copy (30% by default) or more than the set number (500). The hard limit is 5,000 deletions per run.
• <b>Free space</b> is checked in advance. If there is not enough, the app offers to delete old versions from version history.
• <b>File names.</b> On FAT32, exFAT and NTFS, files with unsupported characters (<tt>: * ? " &lt; &gt; |</tt>) are skipped and reported. If two names differ only by case (<tt>Report.txt</tt> and <tt>report.txt</tt>), the job is skipped until you rename or exclude one.
• <b>Drive.</b> Files are written only to the connected drive for the profile. If you disconnect it during a backup, the run stops. A replaced file appears in the copy only after it has been written in full.
• The computer stays awake while a backup is running.

If copying is interrupted, the copy may be incomplete. The next run copies the remaining files."""),

    ("auto", "Automatic backups and the tray", """\
<b>Turn it on.</b> In profile settings, enable “Back up automatically when the drive is inserted” (or click “Turn on automatic backup” in the “Backup” section). Only jobs marked <b>⟳ AUTO</b> are included. Set the marker in the job dialog or choose “Turn on automatic” from the job’s ⋮ menu.

<b>When you connect a drive</b>
1. If the drive has not mounted on its own after 5 seconds, the app mounts it. An encrypted drive is unlocked first (see “Encrypted drives (LUKS)”).
2. The app quietly compares the folders with the copy. If there is nothing to change, it shows nothing; the tray icon’s tooltip shows the up-to-date status and check time.
3. If there are changes, a notification shows the countdown and a summary, with “Cancel” and “Now” buttons.
4. When the backup finishes, a notification shows the result only if something was done or there were errors. If “Eject the drive after a successful automatic backup” is enabled, the drive is ejected.

<b>Profile settings</b> include the countdown before a run, “Not more often than every N hours (0 — no limit)”, and ejecting after a successful backup. Automatic backups never ask questions. If confirmation is needed (for example, before many deletions), the job is skipped and you are notified.

<b>Tray icon.</b> Its dot shows the status: no dot means idle; green means a drive is connected; orange means a backup is running; red means the last run had an error. Left-click to show or hide the window. Right-click for the menu: back up, stop, eject, pause automatic backups (for 1 hour, 4 hours or until quit), or quit.

<b>Close the window</b> with the close button to hide it in the tray; the app keeps running. To quit completely, choose “Quit” from the tray menu.

<b>Start at login</b> by choosing “Start at login” from ☰. The app starts directly in the tray.

<b>New drive without a profile.</b> You receive the “Create a backup profile for it?” notification. “Don’t ask for this drive” adds it to ☰ → “Ignored drives…”."""),

    ("maintenance", "Version history and maintenance", """\
<b>Version history</b> is stored in <tt>PlugBackup/.versions/&lt;date and time UTC&gt;/&lt;job&gt;/</tt>. Deleted and replaced files from jobs in “Mirror with version history” mode go there. Old history folders are removed automatically after a successful run when the retention period expires (30 days by default; change it in profile settings).

<b>Clear version history</b> from the profile’s ⋮ menu by choosing “Clear the version history…”. The list shows each folder’s date and size; select the folders to delete with their check boxes. Deleted files cannot be restored.

<b>Remove excluded files from the copy</b> from the job’s ⋮ menu. If you added an exclusion after files were copied, those files remain in the copy. This action shows the exact list and removes them (to version history in mirror-with-history mode).

<b>Clear this job’s copy</b> from the job’s ⋮ menu. Use this when the source folder is intentionally empty; the empty-source safeguard prevents a regular backup from clearing it. This removes everything in the job’s copy (to version history in mirror-with-history mode).

All three actions are manual, require confirmation and run only when the profile marker matches."""),

    ("restore", "Restore", """\
Choose “Restore…” in the “Backup” section, or “Restore this job…” from the job’s ⋮ menu.

1. Choose which jobs to restore and where: to their original folders or to another folder (by default <tt>~/Restored/&lt;job&gt;_&lt;date&gt;</tt>).
2. Click “Check” to see which files will be created or replaced. Nothing on your computer changes yet.
3. Click “Restore”.

<b>Safety</b>
• Nothing is deleted from your computer; files that are not in the copy are left in place.
• Files to be replaced are first saved to <tt>~/.local/share/plugbackup/restore-backup/&lt;time&gt;/</tt>. A README there explains how to put them back.
• The enabled “Do not replace files that are newer on this computer than in the copy” check box protects recent changes.

<b>What to know.</b> Restore uses the current state of the copy, not its state on a particular date. In “Accumulate” mode, the copy also contains files deleted long ago. Excluded files are not in the copy. Older file versions are in version history: profile ⋮ → “Open the copy on the drive” → <tt>.versions</tt>.

<b>Without the app.</b> The copy consists of regular files and folders. You can open and copy them on any computer, including Windows (except on ext4 drives)."""),

    ("journal", "Log", """\
“Log…” in the “Backup” section shows the profile’s runs: when they happened, how they were started (manually, automatically, from the command line, or as a restore), and the result:
• <b>●</b> completed; no changes means the run succeeded but had nothing to copy (including a quiet check when a drive was connected);
• <b>●</b> completed with notes (for example, files with unsupported names were skipped);
• <b>●</b> error;
• <b>●</b> skipped — a safeguard was triggered and needs your attention; the reason is shown;
• <b>●</b> stopped — by you or because the drive was disconnected.

For each run, the log shows the jobs, how many files were copied and deleted, skipped files and errors, and an “Open the full rsync log” button. For a restore, it shows the job, source copy and destination folder, how many files were created and replaced, and where the saved earlier versions are (click “Open”). Drag the divider between the run list and its details to move it. The log keeps the last 100 runs from the past 90 days."""),

    ("filesystems", "Drive file systems", """\
• <b>exFAT</b> — used by most flash drives and SD cards. Linux permissions and symbolic links are not preserved.
• <b>FAT32</b> — cannot store files 4 GiB or larger (they are skipped and reported); permissions and links are not preserved.
• <b>NTFS</b> — common on external drives. Linux permissions are not preserved.
• <b>ext4</b> — Linux only; preserves permissions and links. On a newly formatted ext4 drive, the root folder belongs to root. Change its owner in “Disks” or with <tt>sudo chown $USER: /media/$USER/&lt;drive&gt;</tt>.

Copies do not preserve file owners, ACLs or extended attributes, regardless of the file system. Hard links become separate files.

<b>☰ → “Show all disks”</b> also shows fixed disks, such as a second internal drive. The system partition is never shown."""),

    ("luks", "Encrypted drives (LUKS)", """\
You can encrypt an entire drive with LUKS. Without the password, nothing on it is visible except the partition size. These drives can only be read on Linux.

<b>Encrypt a drive.</b> The app does not encrypt drives itself; do this once in <b>“Disks”</b>. <b>Formatting erases everything on the drive.</b>
1. Open “Disks”, select the flash drive on the left, then select its partition.
2. Click the gear button below the partition and choose “Format Partition…”.
3. Choose “Internal disk for use with Linux systems only (Ext4)” and check “Password protect volume (LUKS)”. Or choose “Other” and select the file system you need, with the same check box.
4. Set a password and keep it somewhere safe.
If the app then says “You do not have permission to write to the drive.”, change the owner of the root folder (see the Drive file systems section).

<b>Unlock.</b> A locked drive appears with a lock icon and the status “Encrypted (LUKS), locked”. “Unlock…” on the profile page or in drive details asks for the password, unlocks the drive and mounts it. Create a profile for a new encrypted drive after unlocking it.

<b>Password in the keyring.</b> The password window and profile settings (under “Encryption”) have a “Remember the drive password in the keyring” check box. With a saved password, the drive unlocks without asking, including for automatic backups. The “Forget password” button removes it from the keyring. If the keyring is locked or its service is unavailable, the profile reports this and asks for the password in a window.

<b>Automatic backups.</b> When you connect a locked drive, the app gets the password from the keyring; if it is not there, it opens a password window. The countdown starts only after you enter the password. If the screen is locked, you receive a notification and the password window appears after you unlock the screen. Cancel the window to skip the automatic backup for this connection.

<b>Eject.</b> “Eject” unmounts the drive, locks it again and turns off the device’s power.

<b>Without the password, data cannot be recovered.</b> If you lose the password, neither the app nor other tools can read the copy."""),

    ("gocryptfs", "Encrypted folder (gocryptfs)", """\
You can keep the copy in an <b>encrypted folder</b> without formatting the drive. The app creates <tt>PlugBackup.enc</tt>; the rest of the drive remains unencrypted. This works on exFAT, FAT32, NTFS and ext4.

<b>What you need.</b> The <tt>gocryptfs</tt> package: <tt>sudo apt install gocryptfs</tt>. This mode is unavailable without it; profile settings explain why.

<b>What is visible without the password.</b> File contents and names are encrypted, but the number of files, their approximate sizes, folder structure and modification times are visible.

<b>Turn it on.</b> Connect the profile’s drive → “Profile settings…” → “Encryption” → “Encrypted folder on the drive (gocryptfs)” → “Save”. Then:
1. Set a password twice and, if needed, check “Remember the folder password in the keyring”.
2. The app shows a <b>master key</b>. Write it down or print it and keep it separately from the drive. The app does not store it and will not show it again.
3. If the drive already has a regular copy of the profile, the app offers to move it to the encrypted folder (after checking available space) and delete the unencrypted copy after checking it.
To switch back, choose “No encryption”: the copy is moved out of the encrypted folder.

<b>How it works.</b> The folder is opened with the password while backing up, previewing, restoring or maintaining the copy, and closed afterwards. “Open the copy on the drive” keeps it open until you click “Close” on the profile page, eject the drive or quit the app. If the app crashes, any folders left open are closed the next time it starts.

<b>Automatic backups</b> work as with an encrypted drive: the password comes from the keyring or a password window, and the countdown starts after you enter it.

<b>Open the copy without the app.</b>
• Linux: <tt>gocryptfs /media/$USER/&lt;drive&gt;/PlugBackup.enc ~/copy</tt> (you will be asked for the password); unmount with <tt>fusermount3 -u ~/copy</tt>.
• Windows: <b>cppcryptfs</b> opens the same <tt>PlugBackup.enc</tt> folder.

<b>If you lose the password,</b> you can open the copy only with the master key: <tt>gocryptfs -masterkey=stdin /media/$USER/&lt;drive&gt;/PlugBackup.enc ~/copy</tt>. Then paste the key and press Enter. Without the password or master key, the data cannot be recovered. Change the password with <tt>gocryptfs -passwd /media/$USER/&lt;drive&gt;/PlugBackup.enc</tt>.

<b>New computer.</b> If “Keep a copy of this profile on the drive” was enabled, connecting the drive shows a message that an encrypted Plug &amp; Backup folder is on the drive, with an “Import…” action. You will need the password."""),

    ("cli", "Command line", """\
<tt>plugbackup</tt> — window and tray
<tt>plugbackup --hidden</tt> — start in the tray
<tt>plugbackup --list</tt> — profiles and the status of their drives
<tt>plugbackup --backup "Work"</tt> — back up a profile without a window (by name or ID)
<tt>plugbackup --check</tt> — check system components
<tt>plugbackup --version</tt> — show the version

The command line never asks questions. If confirmation is needed, the job is skipped.

<b>Exit codes for</b> <tt>--backup</tt>: 0 — completed; 1 — error; 2 — invalid arguments; 3 — profile not found or drive not connected; 4 — jobs skipped (confirmation required); 5 — interrupted; 6 — profile already running."""),

    ("files", "Where data is stored", """\
<b>On your computer</b>
<tt>~/.config/plugbackup/config.json</tt> — app settings
<tt>~/.config/plugbackup/profiles.json</tt> — profiles and jobs (a <tt>.bak</tt> copy is kept beside it)
<tt>~/.local/state/plugbackup/log/</tt> — run history and full rsync output
<tt>~/.local/share/plugbackup/restore-backup/</tt> — files set aside during restore
<tt>~/.config/autostart/io.github.plugbackup.PlugBackup.desktop</tt> — start at login (if enabled)
Passwords for encrypted drives, if saved, are kept in the system keyring under an entry named for the profile.

<b>On the drive</b>
<tt>PlugBackup/&lt;job folders&gt;/</tt> — copies
<tt>PlugBackup/.versions/</tt> — version history
<tt>PlugBackup/.plugbackup/id</tt> — profile marker
<tt>PlugBackup/.plugbackup/profile.json</tt> — profile copy (if enabled)
<tt>PlugBackup.enc/</tt> — the same data in encrypted form when the copy is in an encrypted folder; the open folder is at <tt>/run/user/&lt;number&gt;/plugbackup/&lt;profile&gt;/</tt>

If the settings or profiles file is damaged, the app tells you and offers to restore it from its <tt>.bak</tt> copy."""),

    ("faq", "Frequently asked questions", """\
<b>Why was a job skipped because its source is empty?</b> The folder on your computer is empty, but the copy contains files. If the folder is empty on purpose, choose “Clear this job’s copy…” from the job’s ⋮ menu.

<b>Why does nothing happen when I connect a drive?</b> Check that automatic backup is enabled in the profile, that at least one job has the ⟳ AUTO marker, that automatic backups are not paused in the tray menu, and that “Not more often than every N hours (0 — no limit)” is not preventing a run. If there is nothing to change, the app stays quiet; check the tray icon’s tooltip.

<b>The drive will not eject completely.</b> If another partition on the same device (or in the same card reader) is connected, the app unmounts its partition but does not turn off the device’s power.

<b>How do I get a deleted file back?</b> If the job uses “Mirror with version history”, open the copy on the drive (profile ⋮) and find the file in <tt>.versions/&lt;date&gt;/</tt>. If the file is still in the copy, choose “Restore…”.

<b>I moved the app to a new computer.</b> Install the package and connect the drive. If it contains a profile copy, “Import…” appears. Otherwise, import a profile file you exported earlier.

<b>The tray icon is missing.</b> Make sure the “XApp Status” applet is on the Cinnamon panel (it is usually there by default)."""),
]

# Shared by GTK and Qt: this section explains how to continue a copy on either OS.
SECTIONS.append(("cross-os", "Shared drive: Linux ↔ Windows", """\
A copy on FAT32, exFAT or NTFS can be continued and restored on Linux or Windows. On a new computer, import the profile from the drive or a file, then choose folders for its jobs; source paths depend on the OS. Do not back up the same profile from two PCs at once: in Mirror mode, they can delete each other’s files.

Windows cannot read ext4. Linux permissions, owners and ACLs, and Windows ACLs and file attributes are not preserved when moving between OSes.

FAT32 stores local time without a time zone. After switching OS, file times may appear different and files may look changed. Check the preview before the first run. If most files are marked changed and the difference looks like a time-zone shift, check the time and time zone before starting the backup."""))


_WINDOWS_TEXT = {
    "start": ("Getting started", """\
<b>Plug &amp; Backup</b> copies selected folders from your computer to removable drives — USB flash drives, SD cards and external drives — manually or automatically when you connect a drive.

<b>Three terms</b>
• <b>Drive</b> — a Windows volume on a flash drive or disk. The app identifies it by its file-system serial number, not by its drive letter.
• <b>Profile</b> — settings for one drive: what to copy, where to put it and how to copy it.
• <b>Job</b> — a folder on your computer and a folder on the drive, with its own mode and exclusions.

<b>First steps</b>
1. Connect a drive; it appears on the left under “New drives”.
2. Create a profile for it and give the profile a name.
3. Add a job and choose a folder on your computer.
4. Open “Preview”, then click “Back up”.

The main window has the “File · View · Help” menus. While the profile’s drive is disconnected, you can edit jobs, but backup and restore are unavailable."""),
    "profiles": ("Profiles and drives", """\
A profile is linked to the file-system serial number. FAT32 and exFAT use a 32-bit number. For NTFS, the app reads the full 64-bit number when possible; if only 32 bits are available, it also checks the profile marker on the drive. An ambiguous match requires you to choose a drive manually.

Changing the drive letter does not affect backups: writes use the Windows volume path, not the drive letter. Formatting changes the file-system serial number. If the device serial still matches, the app may offer to rebind the profile.

A profile copy on the drive helps you import it on another computer. Check the job folders after importing. Do not run one profile for the same drive from two PCs at once."""),
    "jobs": ("Jobs", """\
Each job has a source folder on your computer, a destination folder on the drive, a copy mode and a list of exclusions.

Choose mirror with version history, mirror or accumulate. You can also choose how to detect changes, skip hidden files, avoid nested junctions and set a maximum file size. Enter exclusions one pattern per line.

In a job row, use the buttons to run that job, edit it or remove it. To change the order, drag the row by ⠿ on the left or use “Move up/down” in the row’s ⋮ menu; jobs run in this order. Removing a job does not delete its copy from the drive."""),
    "auto": ("Automatic backups and the tray", """\
In profile settings, enable “Back up automatically when the drive is inserted”. Only jobs marked ⟳ AUTO are included.

If the profile drive is encrypted with BitLocker and locked, unlock it in File Explorer. When the volume becomes available, the app detects it and automatic backup can start according to the profile settings.

When changes are found, the app shows a countdown before the run. If confirmation is needed, automatic backup skips the job and tells you. If Windows refuses a safe eject, the app shows a message; close files and programs that are using the drive.

The tray icon is in the notification area. Closing the window hides it; use the icon menu to quit. Start at login is turned on in “File → Settings…”."""),
    "backup": ("Backups and safeguards", """\
Start a backup manually or set it to run automatically when you connect the drive. Open Preview first to see new, changed, deleted and skipped files.

• If the source is empty but the copy contains files, the job is skipped.
• The app asks before a large deletion; automatic backups skip a job that needs confirmation.
• Files held open by another program are skipped with a reason.
• OneDrive files marked “online-only” are skipped; the app does not request that they be downloaded.
• Junctions and other reparse points in a source are not followed. Reparse points inside the copy are not followed either.
• The engine supports long Windows paths using the <tt>\\\\?\\</tt> prefix; folder-picker dialogs may have their own limits.
• Writes use the volume path. If the drive disconnects or a volume check fails, the run stops.

Each file is replaced as a whole. After an interruption, the job copy may be incomplete; run the backup again."""),
    "restore": ("Restore", """\
Choose “Restore…” in the backup section or from a job’s menu. Select jobs and destination folders, review the file list, then start the restore.

Restore does not delete files from your computer. Files that will be replaced are first saved in the app’s data folder. The copy contains regular files and folders; Windows cannot open a drive formatted as ext4."""),
    "journal": ("Log", """\
The log shows when each run happened, how it was started and its result: completed, completed with notes, error, skipped or stopped. For each run it lists the jobs, copied and deleted files, skipped files and errors. On Windows, the log contains the app’s action list."""),
    "filesystems": ("Drive file systems", """\
The Windows version works with NTFS, exFAT and FAT32 drives. Windows cannot read ext4.

FAT32 cannot store files that are 4 GiB or larger. Copies contain regular files and folders, but do not preserve permissions, owners, ACLs, NTFS alternate data streams or file attributes.

FAT32 stores local time without a time zone. File times may shift when you switch between Linux and Windows; check the preview before backing up. See “Shared drive: Linux ↔ Windows”."""),
    "bitlocker": ("Drives with BitLocker", """\
The app does not encrypt drives. Unlock a locked BitLocker drive in File Explorer. Once it is available, backup starts according to the profile settings.

Keep the BitLocker recovery key and password safe: without them, you cannot open the copy. If automatic unlock is enabled, the drive may open without a password on this computer.

You can create BitLocker To Go on Windows Pro or Enterprise; on Windows 7, Ultimate or Enterprise. You can open a BitLocker drive on any Windows edition."""),
    "cli": ("Command line", """\
Use <tt>plugbackup-cli.exe</tt> for command-line operations:
<tt>plugbackup-cli.exe --list</tt> — profiles and drive status
<tt>plugbackup-cli.exe --backup "Work"</tt> — back up a profile without a window (by name or ID)
<tt>plugbackup-cli.exe --check</tt> — check system components
<tt>plugbackup-cli.exe --version</tt> — show the version

The same options and exit codes are available as in the other builds. The command line never asks questions; a job that needs confirmation is skipped."""),
    "files": ("Where data is stored", """\
<b>On your computer</b>
<tt>%APPDATA%\\PlugBackup\\</tt> — settings and profiles
<tt>%LOCALAPPDATA%\\PlugBackup\\state\\</tt> — state and logs
<tt>%LOCALAPPDATA%\\PlugBackup\\restore-backup\\</tt> — files set aside during restore
Start at login uses <tt>HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run</tt>.

<b>On the drive</b>
<tt>PlugBackup\\&lt;job folders&gt;\\</tt> — copies
<tt>PlugBackup\\.versions\\</tt> — version history
<tt>PlugBackup\\.plugbackup\\id</tt> — profile marker
<tt>PlugBackup\\.plugbackup\\profile.json</tt> — profile copy, if enabled."""),
    "faq": ("Frequently asked questions", """\
<b>Why is there no encrypted gocryptfs folder?</b> This feature is not available in the Windows version yet. You can open an existing gocryptfs copy on Windows with cppcryptfs.

<b>Why does Windows say “Unknown publisher”?</b> The installer is unsigned. Compare its SHA256 hash with the accompanying <tt>SHA256SUMS</tt> file.

<b>Why did safe eject fail?</b> Windows reports which program is holding the drive. Close that file or program and try ejecting again.

<b>Why was a file skipped?</b> The log gives the reason. Close the program using the file, or download the OneDrive file to this computer, then try again."""),
    "install": ("Installing and updating", """\
The installer is for the current user and does not require administrator rights. SmartScreen may show “Unknown publisher” because the app is unsigned. Before running the installer, check its SHA256 hash against the accompanying <tt>SHA256SUMS</tt> file.

Uninstall the app from “Programs and Features” or “Apps”. Settings are kept unless you separately choose to remove them.

Windows 7 SP1 requires update KB2533623 or a replacement update."""),
}


def sections(platform: Optional[str] = None):
    """Return help sections for an OS; default to the current platform."""
    import sys
    platform = platform or sys.platform
    if not platform.startswith("win"):
        return list(SECTIONS)
    result = []
    for key, title, markup in SECTIONS:
        if key == "gocryptfs":
            continue
        if key == "luks":
            key = "bitlocker"
        replacement = _WINDOWS_TEXT.get(key)
        if replacement:
            title, markup = replacement
        result.append((key, title, markup))
    install = _WINDOWS_TEXT["install"]
    result.append(("install", install[0], install[1]))
    return result
