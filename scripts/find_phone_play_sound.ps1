param(
    [string]$DeviceName = "",
    [int]$TimeoutSeconds = 45,
    [int]$PostClickWaitSeconds = 4
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

function ConvertTo-Result {
    param(
        [bool]$Ok,
        [string]$Message,
        [string]$Device = "",
        [string]$Status = "",
        [string]$ErrorText = ""
    )
    [pscustomobject]@{
        ok = $Ok
        message = $Message
        device = $Device
        status = $Status
        error = $ErrorText
    } | ConvertTo-Json -Compress
}

function Find-ByControlTypeAndName {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [System.Windows.Automation.ControlType]$ControlType,
        [string]$Name
    )
    $conditions = @(
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ControlType)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $Name))
    )
    $condition = New-Object System.Windows.Automation.AndCondition($conditions)
    return $Root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
}

function Find-FirstByNameLike {
    param(
        [System.Windows.Automation.AutomationElement]$Root,
        [System.Windows.Automation.ControlType]$ControlType,
        [string]$Pattern
    )
    $typeCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ControlType)
    $items = $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $typeCondition)
    foreach ($item in $items) {
        if ($item.Current.Name -like $Pattern) {
            return $item
        }
    }
    return $null
}

function Invoke-SafeElement {
    param([System.Windows.Automation.AutomationElement]$Element)
    if ($null -eq $Element) {
        return $false
    }
    try {
        $pattern = $Element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
        return $true
    } catch {
        try {
            $pattern = $Element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
            $pattern.Select()
            return $true
        } catch {
            return $false
        }
    }
}

function Get-ChromeWindows {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windowCondition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window)
    $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $windowCondition)
    $chromeWindows = New-Object System.Collections.Generic.List[System.Windows.Automation.AutomationElement]
    foreach ($window in $windows) {
        if ($window.Current.Name -like "*Google Chrome") {
            $chromeWindows.Add($window)
        }
    }
    return $chromeWindows
}

function Find-FindHubWindow {
    $chromeWindows = Get-ChromeWindows
    foreach ($window in $chromeWindows) {
        if ($window.Current.Name -like "*Find Hub*") {
            return $window
        }
        $findHubTab = Find-FirstByNameLike -Root $window -ControlType ([System.Windows.Automation.ControlType]::TabItem) -Pattern "Find Hub:*"
        if ($null -ne $findHubTab) {
            [void](Invoke-SafeElement -Element $findHubTab)
            Start-Sleep -Milliseconds 700
            return $window
        }
    }
    return $null
}

function Read-FindHubStatus {
    param([System.Windows.Automation.AutomationElement]$Root)
    $interesting = @("Device has stopped ringing", "Device is ringing", "Last seen just now", "Contacting device...")
    foreach ($text in $interesting) {
        $element = Find-FirstByNameLike -Root $Root -ControlType ([System.Windows.Automation.ControlType]::Text) -Pattern $text
        if ($null -ne $element) {
            return $element.Current.Name
        }
    }
    return ""
}

$deadline = (Get-Date).AddSeconds([Math]::Max(10, $TimeoutSeconds))
$selectedWindow = $null
$selectedDevice = ""

while ((Get-Date) -lt $deadline) {
    $selectedWindow = Find-FindHubWindow
    if ($null -ne $selectedWindow) {
        break
    }
    Start-Sleep -Milliseconds 500
}

if ($null -eq $selectedWindow) {
    ConvertTo-Result -Ok $false -Message "Google Find Hub window was not found." -ErrorText "find_hub_window_missing"
    exit 1
}

try {
    [void]$selectedWindow.SetFocus()
} catch {
}

while ((Get-Date) -lt $deadline) {
    $deviceButton = $null
    if (-not [string]::IsNullOrWhiteSpace($DeviceName)) {
        $deviceButton = Find-ByControlTypeAndName -Root $selectedWindow -ControlType ([System.Windows.Automation.ControlType]::Button) -Name ("Device image " + $DeviceName)
    } else {
        $deviceButton = Find-FirstByNameLike -Root $selectedWindow -ControlType ([System.Windows.Automation.ControlType]::Button) -Pattern "Device image *"
    }
    if ($null -ne $deviceButton) {
        $selectedDevice = $deviceButton.Current.Name -replace "^Device image\s*", ""
        if (-not (Invoke-SafeElement -Element $deviceButton)) {
            ConvertTo-Result -Ok $false -Message "Could not select the phone in Google Find Hub." -Device $selectedDevice -ErrorText "device_select_failed"
            exit 1
        }
        Start-Sleep -Milliseconds 1200
        break
    }
    Start-Sleep -Milliseconds 500
}

if ([string]::IsNullOrWhiteSpace($selectedDevice)) {
    $message = "No phone device was visible in Google Find Hub."
    if (-not [string]::IsNullOrWhiteSpace($DeviceName)) {
        $message = "The configured phone was not visible in Google Find Hub."
    }
    ConvertTo-Result -Ok $false -Message $message -Device $DeviceName -ErrorText "device_missing"
    exit 1
}

$playButton = $null
while ((Get-Date) -lt $deadline) {
    $playButton = Find-ByControlTypeAndName -Root $selectedWindow -ControlType ([System.Windows.Automation.ControlType]::Button) -Name "Play sound"
    if ($null -ne $playButton) {
        break
    }
    Start-Sleep -Milliseconds 500
}

if ($null -eq $playButton) {
    $status = Read-FindHubStatus -Root $selectedWindow
    ConvertTo-Result -Ok $false -Message "Play sound button was not visible for the selected phone." -Device $selectedDevice -Status $status -ErrorText "play_sound_missing"
    exit 1
}

if (-not (Invoke-SafeElement -Element $playButton)) {
    ConvertTo-Result -Ok $false -Message "Could not click Play sound in Google Find Hub." -Device $selectedDevice -ErrorText "play_sound_click_failed"
    exit 1
}

$statusDeadline = (Get-Date).AddSeconds([Math]::Max(1, $PostClickWaitSeconds))
$finalStatus = ""
while ((Get-Date) -lt $statusDeadline) {
    $finalStatus = Read-FindHubStatus -Root $selectedWindow
    if ($finalStatus -and $finalStatus -ne "Contacting device...") {
        break
    }
    Start-Sleep -Milliseconds 500
}
ConvertTo-Result -Ok $true -Message "Play sound was triggered in Google Find Hub." -Device $selectedDevice -Status $finalStatus
