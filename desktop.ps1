# desktop-ops — Windows desktop control primitives.
# Each subcommand prints a single JSON line to stdout.
# Usage:
#   pwsh -File desktop.ps1 <subcommand> [--flag value ...]

[CmdletBinding()]
param(
    [Parameter(Position=0, Mandatory=$true)][string]$Command,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest
)

function Parse-Args([string[]]$argv) {
    $map = @{}
    for ($i = 0; $i -lt $argv.Count; $i++) {
        $key = $argv[$i]
        if ($key -like "--*") {
            $name = $key.Substring(2)
            if ($i + 1 -lt $argv.Count -and $argv[$i+1] -notlike "--*") {
                $map[$name] = $argv[$i+1]; $i++
            } else {
                $map[$name] = $true
            }
        }
    }
    return $map
}

function Emit($obj) { $obj | ConvertTo-Json -Compress -Depth 6 | Write-Output }
function Fail($msg) { Emit @{ok=$false; error=$msg}; exit 1 }

$opts = Parse-Args $Rest

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Win32 interop for mouse/keyboard/windows
if (-not ([System.Management.Automation.PSTypeName]'DesktopNative').Type) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public class DesktopNative {
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, int dwExtraInfo);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetShellWindow();
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public const uint MOUSEEVENTF_LEFTDOWN  = 0x02;
    public const uint MOUSEEVENTF_LEFTUP    = 0x04;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x08;
    public const uint MOUSEEVENTF_RIGHTUP   = 0x10;
    public const uint MOUSEEVENTF_MIDDLEDOWN= 0x20;
    public const uint MOUSEEVENTF_MIDDLEUP  = 0x40;
    public const uint MOUSEEVENTF_WHEEL     = 0x0800;
}
"@
}

switch ($Command) {
    "info" {
        $s = [System.Windows.Forms.Screen]::PrimaryScreen
        Emit @{
            ok=$true
            width=$s.Bounds.Width
            height=$s.Bounds.Height
            workingWidth=$s.WorkingArea.Width
            workingHeight=$s.WorkingArea.Height
            displayCount=[System.Windows.Forms.Screen]::AllScreens.Count
        }
    }

    "screenshot" {
        $path = if ($opts.path) { $opts.path } else {
            Join-Path $env:TEMP ("screenshot_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".png")
        }
        $s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $s.Width, $s.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen(0, 0, 0, 0, $bmp.Size)
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        Emit @{ok=$true; path=$path; width=$s.Width; height=$s.Height}
    }

    "mouse-move" {
        if (-not $opts.x -or -not $opts.y) { Fail "require --x --y" }
        [DesktopNative]::SetCursorPos([int]$opts.x, [int]$opts.y) | Out-Null
        Emit @{ok=$true; x=[int]$opts.x; y=[int]$opts.y}
    }

    "mouse-click" {
        if ($opts.x -and $opts.y) {
            [DesktopNative]::SetCursorPos([int]$opts.x, [int]$opts.y) | Out-Null
            Start-Sleep -Milliseconds 40
        }
        $btn = if ($opts.button) { [string]$opts.button } else { "left" }
        $down = switch ($btn) { "right" {0x08} "middle" {0x20} default {0x02} }
        $up   = switch ($btn) { "right" {0x10} "middle" {0x40} default {0x04} }
        [DesktopNative]::mouse_event($down, 0, 0, 0, 0)
        Start-Sleep -Milliseconds 30
        [DesktopNative]::mouse_event($up,   0, 0, 0, 0)
        if ($opts.double) {
            Start-Sleep -Milliseconds 40
            [DesktopNative]::mouse_event($down, 0, 0, 0, 0)
            Start-Sleep -Milliseconds 30
            [DesktopNative]::mouse_event($up,   0, 0, 0, 0)
        }
        Emit @{ok=$true; button=$btn; doubled=[bool]$opts.double}
    }

    "scroll" {
        $amount = if ($opts.amount) { [int]$opts.amount } else { -120 }
        [DesktopNative]::mouse_event(0x0800, 0, 0, $amount, 0)
        Emit @{ok=$true; amount=$amount}
    }

    "type" {
        if (-not $opts.text) { Fail "require --text" }
        [System.Windows.Forms.SendKeys]::SendWait([string]$opts.text)
        Emit @{ok=$true; typed=[string]$opts.text}
    }

    "key" {
        if (-not $opts.keys) { Fail "require --keys (SendKeys syntax, e.g. '^c' or '%{F4}')" }
        [System.Windows.Forms.SendKeys]::SendWait([string]$opts.keys)
        Emit @{ok=$true; keys=[string]$opts.keys}
    }

    "launch" {
        if (-not $opts.app) { Fail "require --app" }
        $p = Start-Process -FilePath ([string]$opts.app) -PassThru -ErrorAction Stop
        Emit @{ok=$true; app=[string]$opts.app; pid=$p.Id}
    }

    "windows" {
        $list = New-Object System.Collections.Generic.List[object]
        $proc = [DesktopNative+EnumWindowsProc]{
            param($hWnd, $lParam)
            if (-not [DesktopNative]::IsWindowVisible($hWnd)) { return $true }
            $len = [DesktopNative]::GetWindowTextLength($hWnd)
            if ($len -eq 0) { return $true }
            $sb = New-Object System.Text.StringBuilder ($len + 1)
            [DesktopNative]::GetWindowText($hWnd, $sb, $sb.Capacity) | Out-Null
            $list.Add(@{hwnd=[int64]$hWnd; title=$sb.ToString()})
            return $true
        }
        [DesktopNative]::EnumWindows($proc, [IntPtr]::Zero) | Out-Null
        Emit @{ok=$true; windows=$list.ToArray()}
    }

    "focus" {
        if (-not $opts.title) { Fail "require --title (substring match)" }
        $target = [string]$opts.title
        $found = $null
        $proc = [DesktopNative+EnumWindowsProc]{
            param($hWnd, $lParam)
            if (-not [DesktopNative]::IsWindowVisible($hWnd)) { return $true }
            $len = [DesktopNative]::GetWindowTextLength($hWnd)
            if ($len -eq 0) { return $true }
            $sb = New-Object System.Text.StringBuilder ($len + 1)
            [DesktopNative]::GetWindowText($hWnd, $sb, $sb.Capacity) | Out-Null
            if ($sb.ToString() -like "*$target*" -and $null -eq $script:found) {
                $script:found = @{hwnd=$hWnd; title=$sb.ToString()}
                return $false
            }
            return $true
        }
        [DesktopNative]::EnumWindows($proc, [IntPtr]::Zero) | Out-Null
        if (-not $script:found) { Fail "no window matched '$target'" }
        [DesktopNative]::ShowWindow($script:found.hwnd, 9) | Out-Null   # SW_RESTORE
        [DesktopNative]::SetForegroundWindow($script:found.hwnd) | Out-Null
        Emit @{ok=$true; hwnd=[int64]$script:found.hwnd; title=$script:found.title}
    }

    "clipboard-get" {
        $t = Get-Clipboard -Raw -ErrorAction SilentlyContinue
        Emit @{ok=$true; text=$t}
    }

    "clipboard-set" {
        if (-not $opts.text) { Fail "require --text" }
        Set-Clipboard -Value ([string]$opts.text)
        Emit @{ok=$true}
    }

    "processes" {
        $procs = Get-Process | Where-Object { $_.MainWindowTitle -ne "" } |
            Select-Object Id, ProcessName, MainWindowTitle |
            ForEach-Object { @{pid=$_.Id; name=$_.ProcessName; title=$_.MainWindowTitle} }
        Emit @{ok=$true; processes=$procs}
    }

    default {
        Emit @{ok=$false; error="unknown command: $Command"; commands=@(
            "info","screenshot","mouse-move","mouse-click","scroll",
            "type","key","launch","windows","focus",
            "clipboard-get","clipboard-set","processes"
        )}
        exit 1
    }
}
