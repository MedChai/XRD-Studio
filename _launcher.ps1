# ===================================================================
#  XRD Studio - Windows launcher (start / stop)
#
#  Called by start.bat / start-dev.bat / stop.bat.
#
#  ------------------------------------------------------------------
#  THIS FILE MUST STAY PURE ASCII. Do not paste Chinese text into it,
#  not even inside a comment.
#
#  Why: Windows PowerShell 5.1 reads a BOM-less file using the system
#  ANSI codepage (GBK on this machine), not UTF-8. A UTF-8 Chinese
#  comment decodes into garbage, and some byte sequences end on a
#  backtick -- which escapes the newline and swallows the NEXT line of
#  the file. That silently ate 8 hashtable entries below (23 -> 15
#  keys) while the script still parsed "OK". Chinese user-facing text
#  is therefore built from codepoints at runtime by the J helper and
#  printed as UTF-8 bytes (the .bat wrapper sets `chcp 65001` first).
#
#  Verify after any edit:  grep -n '[^ -~]' _launcher.ps1   (no output)
#  ------------------------------------------------------------------
#
#  Environment (exported by the .bat wrapper):
#    XRD_MODE      start | stop | dev   (anything but "stop" starts)
#    XRD_ROOT      repo root
#    XRD_PYTHON    engine\.venv\Scripts\python.exe
#    XRD_NPM       npm.cmd (absolute)
#    XRD_CARGO     cargo.exe (absolute)
#    XRD_TAURI_DIR app\src-tauri
#    XRD_DISTNAME  packaged exe file name
#    XRD_MODE_LBL  run | dev
#    XRD_DATA_ROOT %LOCALAPPDATA%\XRDStudio
#    XRD_LOG_DIR   %LOCALAPPDATA%\XRDStudio\logs
#    XRD_DIST_EXE  packaged exe path (empty in dev mode)
#
#  Output protocol (parsed by the .bat wrapper, ASCII only):
#    XRD-CHILD-PID=<n>
#    XRD-EXIT-CODE=<n>
# ===================================================================

$ErrorActionPreference = 'Stop'

# -------------------------------------------------------------------
# Required environment. Checked before anything else so a direct
# `powershell -File _launcher.ps1` fails with a readable message
# instead of "Join-Path: Cannot bind argument to parameter 'Path'".
# -------------------------------------------------------------------
$missing = @()
foreach ($v in 'XRD_MODE', 'XRD_ROOT', 'XRD_LOG_DIR', 'XRD_DATA_ROOT', 'XRD_TAURI_DIR') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($v))) {
        $missing += $v
    }
}
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host ('  [X] Missing environment: ' + ($missing -join ', '))
    Write-Host '      Launch this through start.bat / stop.bat, not directly.'
    Write-Host ''
    exit 2
}

$Mode = $env:XRD_MODE

# -------------------------------------------------------------------
# Console into UTF-8 for the duration of this script. Wrapped: an
# existing OutputEncoding cannot be changed on some hosts, which is
# non-fatal (the .bat wrapper already ran `chcp 65001`, so the worst
# case is missing glyphs).
# -------------------------------------------------------------------
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    $OutputEncoding = [Console]::OutputEncoding
} catch { }

# These two must be assigned BEFORE any Write-Host below: they are
# what keep an empty message from turning into a null argument.
[string]$esc = [string][char]27
[string]$script:Indent = '    '

# -------------------------------------------------------------------
# Message table. Every string is pure ASCII here and decoded at
# runtime, so the file survives any codepage. See the header note.
# -------------------------------------------------------------------
function J([string]$hexes) {
    $sb = New-Object System.Text.StringBuilder
    $parts = $hexes.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)
    foreach ($h in $parts) {
        [void]$sb.Append([char][Convert]::ToInt32($h, 16))
    }
    $sb.ToString()
}

$T = @{
    Banner2     = J '542F 52A8 5668'                                             # qi dong qi  (launcher)
    Tauri       = J '0074 0061 0075 0072 0069'                                   # tauri
    Starting    = J '6B63 5728 542F 52A8 5E94 7528 2026'                         # starting app...
    ModeRun     = J '6A21 5F0F FF1A 5DF2 6784 5EFA 5305'                         # mode: packaged build
    ModeDev     = J '6A21 5F0F FF1A 5F00 53D1 6A21 5F0F'                         # mode: development
    AlreadyRun  = J '5DF2 7ECF 6709 4E00 4E2A 5B9E 4F8B 5728 8FD0 884C'         # an instance is already running
    AlreadyHint = J '2D 20 5982 679C 7A97 53E3 6CA1 6709 51FA 73B0 FF0C 5148 8FD0 884C 20 0073 0074 006F 0070 002E 0062 0061 0074'
    Stopping    = J '6B63 5728 5173 95ED 5E94 7528 2026'                         # stopping app...
    Stopped     = J '5DF2 5173 95ED'                                             # stopped
    NoRunning   = J '6CA1 6709 6B63 5728 8FD0 884C 7684 5B9E 4F8B'               # nothing is running
    KilledTree  = J '5DF2 7ED3 675F 8FDB 7A0B 6811'                              # process tree ended
    KilledEng   = J '5DF2 6536 62FE 6B8B 7559 5F15 64CE FF08 7B 30 7D 20 4E2A FF09'
    Orphan      = J '8FD8 6709 6B8B 7559 7684 5F15 64CE 8FDB 7A0B'               # leftover engine processes
    OrphanHint  = J '2D 2D 20 82E5 9700 5F3A 5236 6E05 7406 FF0C 8FD0 884C 3A 20 0074 0061 0073 006B 006B 0069 006C 006C 20 2F 0046 20 2F 0049 004D 20 0070 0079 0074 0068 006F 006E 002E 0065 0078 0065'
    HintStop    = J '63D0 793A FF1A 5173 95ED 8BF7 53CC 51FB 20 0073 0074 006F 0070 002E 0062 0061 0074'
    CliStarted  = J '547D 4EE4 884C 5DF2 542F 52A8 20 2D 20 7B49 5F85 7A97 53E3 51FA 73B0 2026'
    WaitExit    = J '7B49 5F85 5E94 7528 9000 51FA 2026'                         # waiting for app to exit...
    Exited      = J '5E94 7528 5DF2 9000 51FA FF0C 9000 51FA 7801'               # app exited, exit code
    TauriFail   = J '542F 52A8 5931 8D25'                                        # failed to start
    StopKept    = J '6B63 5E38 9000 51FA 5931 8D25 FF0C 5F3A 5236 7ED3 675F 8FDB 7A0B 6811'
    PidLabel    = J '8FDB 7A0B 20 0050 0049 0044'                                # process PID
    DataRootLbl = J '6570 636E 76EE 5F55'                                        # data root
    WaitHint    = J '9996 6B21 542F 52A8 6216 6539 52A8 4EE3 7801 540E 9700 8981 91CD 65B0 7F16 8BD1 FF0C 53EF 80FD 9700 8981 51E0 5206 949F'
}

# -------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------
$root    = $env:XRD_ROOT
$logDir  = $env:XRD_LOG_DIR
$pidFile = Join-Path $logDir 'launcher.pid'
$modeLbl = if ($env:XRD_MODE_LBL -eq 'run') { 'run' } else { 'dev' }
$devPort = 1420
$stdoutLog = Join-Path $logDir 'tauri.stdout.log'
$stderrLog = Join-Path $logDir 'tauri.stderr.log'

function Say([string]$text)     { Write-Host ($script:Indent + $text) }
function SayOk([string]$text)   { Write-Host ($script:Indent + $script:esc + '[32m' + $text + $script:esc + '[0m') }
function SayWarn([string]$text) { Write-Host ($script:Indent + $script:esc + '[33m' + $text + $script:esc + '[0m') }
function SayErr([string]$text)  { Write-Host ($script:Indent + $script:esc + '[31m' + $text + $script:esc + '[0m') }
function Rule {
    Write-Host ''
    Write-Host ('  ' + $script:esc + '[36mXRD Studio  |  ' + $T.Banner2 + $script:esc + '[0m')
    Write-Host ''
}

# -------------------------------------------------------------------
# Process helpers
# -------------------------------------------------------------------
function Get-ProcessTreePids([int]$rootPid) {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
             Select-Object ProcessId, ParentProcessId)
    $seen = New-Object 'System.Collections.Generic.HashSet[int]'
    $queue = New-Object 'System.Collections.Generic.Queue[int]'
    $queue.Enqueue($rootPid)
    [void]$seen.Add($rootPid)
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        foreach ($p in $all) {
            if ([int]$p.ParentProcessId -eq $cur -and -not $seen.Contains([int]$p.ProcessId)) {
                [void]$seen.Add([int]$p.ProcessId)
                $queue.Enqueue([int]$p.ProcessId)
            }
        }
    }
    @($seen)
}

function Get-ProcessCommandLine([int]$processId) {
    try {
        $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $processId) -ErrorAction Stop
        if ($p) { return [string]$p.CommandLine }
    } catch { }
    return ''
}

function Get-ProcessNameSafe([int]$processId) {
    try {
        $p = Get-Process -Id $processId -ErrorAction Stop
        return [string]$p.ProcessName
    } catch { }
    return 'unknown'
}

# Get listeners through netstat rather than Get-NetTCPConnection. The latter
# can hide IPv6 listeners on some Windows installations, while Vite may bind
# to [::1] and still block localhost:1420.
function Get-ListeningPids([int]$port) {
    $pattern = '^\s*TCP\s+\S+:' + $port + '\s+\S+\s+LISTENING\s+(\d+)\s*$'
    $pids = @()
    try {
        foreach ($line in @(netstat -ano 2>$null)) {
            if ($line -match $pattern) { $pids += [int]$matches[1] }
        }
    } catch { }
    @($pids | Sort-Object -Unique)
}

function Is-ProjectViteProcess([int]$processId) {
    $cmd = Get-ProcessCommandLine $processId
    if (-not $cmd) { return $false }
    $rootPattern = [regex]::Escape($root.TrimEnd('\'))
    return ($cmd -match ('(?i)' + $rootPattern)) -and
           ($cmd -match '(?i)(node|npm|vite)')
}

function Show-ProcessOwner([int]$processId) {
    $name = Get-ProcessNameSafe $processId
    $cmd = Get-ProcessCommandLine $processId
    SayErr ('Port ' + $devPort + ' is already in use by PID ' + $processId + ' (' + $name + ').')
    if ($cmd) {
        Say ('Command: ' + $cmd)
    } else {
        Say 'Command line is unavailable for this process.'
    }
}

function Ensure-DevPortFree {
    $owners = @(Get-ListeningPids $devPort)
    if ($owners.Count -eq 0) { return $true }

    $unknown = $false
    foreach ($owner in $owners) {
        if (Is-ProjectViteProcess $owner) {
            SayWarn ('Cleaning stale project dev process PID ' + $owner + '.')
            Stop-Tree $owner
        } else {
            Show-ProcessOwner $owner
            $unknown = $true
        }
    }

    if ($unknown) {
        Say 'Stop the process above, then run the launcher again.'
        return $false
    }

    Start-Sleep -Milliseconds 250
    $left = @(Get-ListeningPids $devPort)
    if ($left.Count -gt 0) {
        foreach ($owner in $left) { Show-ProcessOwner $owner }
        return $false
    }
    return $true
}

function Show-LogTail([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    Write-Host ''
    Say ('--- ' + $path + ' (last 80 lines) ---')
    Get-Content -LiteralPath $path -Tail 80 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ($script:Indent + $_) }
}

function Stop-Tree([int]$rootPid) {
    $pids = @(Get-ProcessTreePids $rootPid)
    [array]::Reverse($pids)              # children first
    foreach ($p in $pids) {
        if ($p -eq $PID) { continue }
        try { Stop-Process -Id $p -Force -ErrorAction Stop } catch { }
    }
    SayOk ($T.KilledTree + ' (' + $pids.Count + ')')
}

# The python analysis engine is identified by its command line, which is
# the only reliable handle when the app was launched by something other
# than these scripts (`npm run tauri dev` by hand, a previous session).
function Get-OrphanEngines {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
          $_.CommandLine -and
          ($_.CommandLine -match 'xrd_engine' -or $_.Name -eq 'xrd-engine.exe')
      } |
      Select-Object -ExpandProperty ProcessId)
}

# Path-based, so it can never touch another project's binary. Catches
# the case where the launcher console was closed by hand: the npm tree
# dies but the app window (and its sidecar) keeps running.
function Get-AppProcesses {
    $prefix = $env:XRD_TAURI_DIR.TrimEnd('\') + '\target\'
    @(Get-CimInstance Win32_Process -Filter "Name='app.exe' OR Name='xrd-engine.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, 'OrdinalIgnoreCase') } |
      Select-Object -ExpandProperty ProcessId)
}

# ===================================================================
#  STOP
# ===================================================================
if ($Mode -eq 'stop') {
    Rule
    Say $T.Stopping
    $stopped = $false

    if (Test-Path -LiteralPath $pidFile) {
        $raw = Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue
        $fields = @()
        if ($raw) { $fields = @($raw.Trim() -split '\s+') }
        if ($fields.Count -ge 2) {
            $launcherPid = 0
            $stamp = [long]0
            if ([int]::TryParse($fields[0], [ref]$launcherPid) -and
                [long]::TryParse($fields[1], [ref]$stamp)) {
                $p = Get-Process -Id $launcherPid -ErrorAction SilentlyContinue
                # Start-time comparison guards against PID reuse: Windows
                # recycles PIDs, and killing a recycled one would take out
                # an unrelated process tree.
                if ($p -and $p.StartTime.ToFileTime() -eq $stamp) {
                    Stop-Tree $launcherPid
                    $stopped = $true
                }
            }
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }

    # Independent path-based sweep, for when the launcher console was
    # closed by hand and the recorded PID is already gone.
    foreach ($p in @(Get-AppProcesses)) {
        if ($p -eq $PID) { continue }
        Stop-Tree $p
        $stopped = $true
    }

    # Also clean a project-owned Vite process that survived after the
    # launcher console was closed. Unknown listeners are reported only.
    foreach ($p in @(Get-ListeningPids $devPort)) {
        if (Is-ProjectViteProcess $p) {
            Stop-Tree $p
            $stopped = $true
        } else {
            Show-ProcessOwner $p
        }
    }

    $orphans = @(Get-OrphanEngines)
    foreach ($p in $orphans) {
        if ($p -eq $PID) { continue }
        try { Stop-Process -Id $p -Force -ErrorAction Stop } catch { }
    }
    if ($orphans.Count -gt 0) { SayOk ($T.KilledEng -f $orphans.Count) }

    if (-not $stopped -and $orphans.Count -eq 0) {
        SayWarn $T.NoRunning
    } elseif ($stopped) {
        SayOk $T.Stopped
    }

    $left = @(Get-OrphanEngines)
    if ($left.Count -gt 0) {
        SayWarn $T.Orphan
        Say $T.OrphanHint
    }

    Write-Host ''
    exit 0
}

# ===================================================================
#  START
# ===================================================================
$mutex = $null
$created = $false
try {
    # Held for the whole run: a second double-click must not start a
    # second app (which would fight over port 1420 / the SQLite store).
    $mutex = New-Object System.Threading.Mutex($true, 'Local\XRDStudioLauncher', [ref]$created)

    if (-not $created) {
        Rule
        SayWarn $T.AlreadyRun
        Say $T.AlreadyHint
        Write-Host ''
        exit 0
    }

    # The mutex only covers a live launcher. If the launcher console was
    # closed by hand the app keeps running while the mutex is released,
    # and a second start would fight over port 1420. Catch that here.
    if (@(Get-AppProcesses).Count -gt 0) {
        Rule
        SayWarn $T.AlreadyRun
        Say $T.AlreadyHint
        Write-Host ''
        exit 0
    }

    Rule
    Say $T.Starting
    if ($modeLbl -eq 'run') { Say $T.ModeRun } else { Say $T.ModeDev }

    if ($modeLbl -ne 'run') {
        if (-not (Ensure-DevPortFree)) {
            Write-Host ''
            Write-Output 'XRD-EXIT-CODE=3'
            exit 3
        }
    }

    $workDir = Join-Path $root 'app'

    if ($env:XRD_DIST_EXE -and (Test-Path -LiteralPath $env:XRD_DIST_EXE)) {
        $launch   = $env:XRD_DIST_EXE
        $argsList = @()
    } else {
        $launch   = $env:XRD_NPM
        $argsList = @('run', 'tauri', '--', 'dev')
        Say ''
        SayWarn $T.WaitHint
    }

    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }

    Remove-Item -LiteralPath $stdoutLog,$stderrLog -Force -ErrorAction SilentlyContinue

    # Start-Process can fail on Windows when the inherited environment
    # contains both PATH and Path keys. Use ProcessStartInfo instead. For
    # dev mode, cmd.exe owns the file redirection so stdout/stderr cannot
    # block a long-running Vite process.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.WorkingDirectory = $workDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    # Keep npm and cargo discoverable, but remove duplicate PATH/Path keys
    # before assigning the child environment. Some Windows hosts expose both
    # spellings and StartProcess then throws a duplicate-key exception.
    $childPath = (Split-Path $env:XRD_NPM) + ';' +
                 (Split-Path $env:XRD_CARGO) + ';' +
                 [Environment]::GetEnvironmentVariable('Path', 'Process')
    $pathKeys = @($psi.EnvironmentVariables.Keys |
                  Where-Object { $_ -ieq 'PATH' })
    foreach ($key in $pathKeys) {
        [void]$psi.EnvironmentVariables.Remove($key)
    }
    $psi.EnvironmentVariables['PATH'] = $childPath

    if ($modeLbl -ne 'run') {
        $psi.FileName = $env:ComSpec
        $cmd = 'call "' + $launch + '" run tauri -- dev 1>"' + $stdoutLog + '" 2>"' + $stderrLog + '"'
        $psi.Arguments = '/d /c "' + $cmd + '"'
    } else {
        $psi.FileName = $launch
    }
    $proc = [System.Diagnostics.Process]::Start($psi)
    # "<pid> <startTimeFileTime>" -- the second field is what makes a
    # later stop.bat safe against PID reuse.
    try {
        Set-Content -LiteralPath $pidFile -Encoding ascii -ErrorAction Stop `
                    -Value ($proc.Id.ToString() + ' ' + $proc.StartTime.ToFileTime().ToString())
    } catch {
        SayWarn ('Could not write launcher state file: ' + $pidFile)
        Say 'The app will still run; stop.bat can use process and port cleanup.'
    }

    Write-Host ''
    Say ($T.PidLabel + ' ' + $proc.Id)
    Say ($T.DataRootLbl + ' ' + $env:XRD_DATA_ROOT)
    Say $T.HintStop
    Write-Host ''
    Write-Output ('XRD-CHILD-PID=' + $proc.Id)

    if ($modeLbl -ne 'run') { Say $T.CliStarted }
    Say $T.WaitExit

    $proc.WaitForExit()
    $code = $proc.ExitCode
    if ($null -eq $code) { $code = -1 }

    # On Windows the sidecar (python -m xrd_engine) and the vite/node
    # server outlive the app when the window is merely closed, so sweep
    # the tree once the app is gone.
    $tree = @(Get-ProcessTreePids $proc.Id)
    if ($tree.Count -gt 1) { Stop-Tree $proc.Id }
    foreach ($p in @(Get-OrphanEngines)) {
        if ($p -eq $PID) { continue }
        try { Stop-Process -Id $p -Force -ErrorAction Stop } catch { }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue

    Write-Host ''
    # -1 (0xFFFFFFFF) is what Windows reports when a process is
    # terminated from outside -- stop.bat, Task Manager, a closing
    # console. That is a normal shutdown, not a startup failure, so it
    # must not be reported as one and must not pause the wrapper.
    if ($code -eq 0 -or $code -eq -1) {
        SayOk ($T.Exited + ' ' + $code)
        $code = 0
    } else {
        SayWarn ($T.Exited + ' ' + $code + '. ' + $T.Tauri + ' ' + $T.TauriFail + '.')
        Show-LogTail $stderrLog
        Show-LogTail $stdoutLog
    }
    Write-Output ('XRD-EXIT-CODE=' + $code)
    exit $code
}
finally {
    if ($mutex) {
        if ($created) {
            try { $mutex.ReleaseMutex() } catch { }
        }
        $mutex.Dispose()
    }
}
