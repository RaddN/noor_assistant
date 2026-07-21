[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MessageFile,

    [string]$WindowTitleContains = "Microsoft Teams",

    [switch]$PreferChrome,

    [switch]$NoEnter,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Result {
    param(
        [bool]$Ok,
        [string]$Message,
        [string]$ErrorText = "",
        [object]$Process = $null,
        [int]$ExitCode = 0
    )

    $payload = [ordered]@{
        ok = $Ok
        message = $Message
        error = $ErrorText
    }
    if ($Process) {
        $payload.process_name = $Process.ProcessName
        $payload.process_id = $Process.Id
        $payload.window_title = $Process.MainWindowTitle
    }
    $payload | ConvertTo-Json -Compress
    exit $ExitCode
}

function Title-Contains {
    param([string]$Title, [string[]]$Needles)
    if ([string]::IsNullOrWhiteSpace($Title)) {
        return $false
    }
    foreach ($needle in $Needles) {
        if (-not [string]::IsNullOrWhiteSpace($needle) -and $Title.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

if (-not (Test-Path -LiteralPath $MessageFile)) {
    Write-Result -Ok $false -Message "Message file is missing." -ErrorText $MessageFile -ExitCode 2
}

$messageText = Get-Content -LiteralPath $MessageFile -Raw
if ([string]::IsNullOrWhiteSpace($messageText)) {
    Write-Result -Ok $false -Message "Message is empty." -ExitCode 2
}

$needles = @($WindowTitleContains, "Microsoft Teams", "Teams") |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    Select-Object -Unique

$windows = @(Get-Process | Where-Object {
    $_.MainWindowHandle -ne 0 -and (Title-Contains -Title $_.MainWindowTitle -Needles $needles)
})

if (-not $windows) {
    Write-Result -Ok $false -Message "No Teams window was found." -ErrorText "Looked for window title containing: $($needles -join ', ')" -ExitCode 3
}

$target = $windows |
    Sort-Object -Property @{
        Expression = {
            if ($PreferChrome -and $_.ProcessName -match "^(chrome|msedge)$") { 0 }
            elseif (-not $PreferChrome -and $_.ProcessName -match "^(ms-teams|Teams)$") { 0 }
            elseif ($_.ProcessName -match "^(ms-teams|Teams)$") { 1 }
            elseif ($_.ProcessName -match "^(chrome|msedge)$") { 2 }
            else { 3 }
        }
    }, Id |
    Select-Object -First 1

if ($DryRun) {
    Write-Result -Ok $true -Message "Teams window found." -Process $target
}

Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class NoorTeamsWindow {
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

$activated = $false
try {
    [NoorTeamsWindow]::ShowWindowAsync($target.MainWindowHandle, 9) | Out-Null
    Start-Sleep -Milliseconds 150
    $activated = [NoorTeamsWindow]::SetForegroundWindow($target.MainWindowHandle)
} catch {
    $activated = $false
}
if (-not $activated) {
    try {
        $activated = [Microsoft.VisualBasic.Interaction]::AppActivate([int]$target.Id)
    } catch {
        $activated = $false
    }
}
if (-not $activated) {
    try {
        $activated = [Microsoft.VisualBasic.Interaction]::AppActivate($target.MainWindowTitle)
    } catch {
        $activated = $false
    }
}
if (-not $activated) {
    Write-Result -Ok $false -Message "Could not activate Teams window." -Process $target -ExitCode 4
}

Start-Sleep -Milliseconds 450

if ($target.ProcessName -match "^(chrome|msedge)$") {
    [System.Windows.Forms.SendKeys]::SendWait("%+r")
} else {
    [System.Windows.Forms.SendKeys]::SendWait("^r")
}

Start-Sleep -Milliseconds 250

$hadTextClipboard = $false
$previousClipboardText = ""
try {
    $previousClipboardText = Get-Clipboard -Raw -Format Text -ErrorAction Stop
    $hadTextClipboard = $true
} catch {
    $hadTextClipboard = $false
}

try {
    Set-Clipboard -Value $messageText
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 200
    if (-not $NoEnter) {
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    }
    Start-Sleep -Milliseconds 150
} finally {
    if ($hadTextClipboard) {
        Set-Clipboard -Value $previousClipboardText
    }
}

$doneMessage = if ($NoEnter) { "Teams message pasted." } else { "Teams message sent." }
Write-Result -Ok $true -Message $doneMessage -Process $target
