# lifecycle.ps1 - PID-scoped process lifecycle for the Swarm OS dev stack (D1).
#
# Dot-sourced by start-dev.ps1 (record) and unified-stop.ps1 (stop).
# Contract:
#   - Records ONLY processes this stack started, one file per role under
#     <PidDir>\<role>.pid containing "<pid>|<processName>".
#   - Stop targets ONLY recorded PIDs - never process names (no Stop-Process
#     -Name, no taskkill /IM), never unrecorded processes.
#   - A recorded PID is killed only if the live process's name still matches
#     the recorded name (guards against PID reuse by an unrelated process).
#   - Tolerates: missing pid dir, missing/corrupt pid files, already-dead
#     processes, stale (reused) PIDs, and repeated stop runs (records are
#     removed as they are handled).
# Compatible with both PowerShell 5.1 (powershell.exe) and PowerShell 7 (pwsh).
#
# Environment-specific kill mechanism (verified empirically on this machine):
#   - taskkill.exe is BROKEN here (~62s "timeout period expired", target NOT
#     killed - reproduced on live and nonexistent PIDs).
#   - Get-CimInstance (WMI) also times out (>90s).
#   - Therefore the process TREE is enumerated via a Toolhelp32 kernel snapshot
#     (P/Invoke, ~1.4s) and each PID is terminated with `Stop-Process -Id <pid>
#     -Force` (PID-scoped, ~0.7s). `Stop-Process -Name` (name-based) is never
#     used - only PIDs proven to be descendants of a recorded root.

if (-not ("SwarmToolhelp" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public static class SwarmToolhelp {
    [DllImport("kernel32.dll")] public static extern IntPtr CreateToolhelp32Snapshot(uint dwFlags, uint th32ProcessID);
    [DllImport("kernel32.dll")] public static extern bool Process32First(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);
    [DllImport("kernel32.dll")] public static extern bool Process32Next(IntPtr hSnapshot, ref PROCESSENTRY32 lppe);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr hObject);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    public struct PROCESSENTRY32 {
        public uint dwSize; public uint cntUsage; public uint th32ProcessID;
        public IntPtr th32DefaultHeapID; public uint th32ModuleID; public uint cntThreads;
        public uint th32ParentProcessID; public int pcPriClassBase; public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
    }
    // Returns flat [pid, parentPid] rows of one kernel process snapshot.
    public static List<int[]> Snapshot() {
        var rows = new List<int[]>();
        IntPtr h = CreateToolhelp32Snapshot(0x2 /*TH32CS_SNAPPROCESS*/, 0);
        if (h == IntPtr.Zero || h == (IntPtr)(-1)) return rows;
        var pe = new PROCESSENTRY32();
        pe.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
        if (Process32First(h, ref pe)) {
            do {
                rows.Add(new int[] { (int)pe.th32ProcessID, (int)pe.th32ParentProcessID });
                pe.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
            } while (Process32Next(h, ref pe));
        }
        CloseHandle(h);
        return rows;
    }
}
"@
}

function Get-PidTree {
    # Returns the recorded root PID plus all its descendants (by parent-PID
    # links from one Toolhelp32 snapshot). PID-scoped: no name matching.
    param([Parameter(Mandatory = $true)][int]$RootPid)
    $rows = [SwarmToolhelp]::Snapshot()
    $children = @{}
    foreach ($r in $rows) { $children[$r[1]] += , $r[0] }
    $stack = New-Object System.Collections.Generic.Stack[int]
    $stack.Push($RootPid)
    $result = New-Object System.Collections.Generic.List[int]
    $seen = @{}
    while ($stack.Count -gt 0) {
        $cur = $stack.Pop()
        if ($seen.ContainsKey($cur)) { continue }
        $seen[$cur] = $true
        $result.Add($cur)
        if ($children.ContainsKey($cur)) {
            foreach ($c in $children[$cur]) { $stack.Push($c) }
        }
    }
    return $result
}

function Write-ServicePid {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PidDir,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][int]$ProcessId
    )
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) {
        # Process died before it could be recorded (e.g. lost its port bind).
        # Do NOT write a record we do not own, and do not clobber an existing
        # record for a still-running earlier instance.
        Write-Host "[lifecycle] $Role`: PID $ProcessId already exited - not recorded." -ForegroundColor DarkGray
        return
    }
    if (-not (Test-Path -LiteralPath $PidDir)) {
        New-Item -ItemType Directory -Force -Path $PidDir | Out-Null
    }
    $file = Join-Path $PidDir "$Role.pid"
    # Unique temp + move so a concurrent reader never sees a half-written record.
    $tmp = "$file.tmp.$PID"
    "$($proc.Id)|$($proc.ProcessName)" | Set-Content -LiteralPath $tmp -Encoding ASCII
    Move-Item -LiteralPath $tmp -Destination $file -Force
    Write-Host "[lifecycle] recorded $Role -> PID $($proc.Id) ($($proc.ProcessName))" -ForegroundColor DarkGray
}

function Stop-RecordedServices {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PidDir
    )
    if (-not (Test-Path -LiteralPath $PidDir)) {
        Write-Host "[stop] no PID records at $PidDir - nothing recorded to stop." -ForegroundColor DarkGray
        return
    }
    $records = Get-ChildItem -LiteralPath $PidDir -Filter "*.pid" -ErrorAction SilentlyContinue
    if (-not $records) {
        Write-Host "[stop] no PID records in $PidDir - nothing recorded to stop." -ForegroundColor DarkGray
        return
    }
    foreach ($file in $records) {
        $role = $file.BaseName
        $raw = $null
        try { $raw = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop } catch { $raw = $null }
        if (-not $raw -or $raw -notmatch '^\s*(\d+)\s*\|\s*([A-Za-z0-9._-]+)\s*$') {
            Write-Host "[stop] $role`: unreadable PID record - removed (tolerated)." -ForegroundColor Yellow
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
            continue
        }
        $procId = [int]$Matches[1]
        $recordedName = $Matches[2]

        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) {
            Write-Host "[stop] $role`: PID $procId already dead - removed record (tolerated)." -ForegroundColor DarkGray
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
            continue
        }
        if ($proc.ProcessName -ne $recordedName) {
            # PID was recycled by an unrelated process: refuse to kill it.
            Write-Host "[stop] $role`: PID $procId now belongs to '$($proc.ProcessName)' (recorded '$recordedName') - PID reused, refusing to kill an unrelated process; removed record." -ForegroundColor Yellow
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
            continue
        }

        # PID-scoped process TREE kill: snapshot the parent-PID links, collect
        # the recorded root plus every descendant (e.g. start-proxy ->
        # model_router -> cmd -> llama.exe), then terminate each by PID.
        # Stop-Process -Id is PID-scoped; name-based Stop-Process -Name is
        # never used. (taskkill/WMI are unusable on this machine - see header.)
        try {
            $tree = Get-PidTree -RootPid $procId
        } catch {
            Write-Host "[stop] $role`: tree enumeration failed ($_); killing root PID only." -ForegroundColor Yellow
            $tree = @($procId)
        }
        foreach ($pidToKill in $tree) {
            $target = Get-Process -Id $pidToKill -ErrorAction SilentlyContinue
            if ($target) {
                try {
                    Stop-Process -Id $pidToKill -Force -ErrorAction Stop
                } catch {
                    if ($pidToKill -eq $procId) {
                        Write-Host "[stop] $role`: Stop-Process failed for PID $procId - record kept." -ForegroundColor Red
                    }
                    # Descendant already exited between snapshot and kill: fine.
                }
            }
        }
        Start-Sleep -Milliseconds 300
        $stillAlive = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($stillAlive) {
            Write-Host "[stop] $role`: PID $procId ($recordedName) still alive after tree kill; record kept." -ForegroundColor Red
            continue
        }
        Write-Host "[stop] $role`: stopped PID $procId ($recordedName) and $($tree.Count - 1) descendant(s)." -ForegroundColor Green
        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue
    }
}
