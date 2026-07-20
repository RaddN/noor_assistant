$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = @()

foreach ($voice in $synth.GetInstalledVoices()) {
    $info = $voice.VoiceInfo
    $voices += [PSCustomObject]@{
        name = $info.Name
        culture = $info.Culture.Name
        gender = $info.Gender.ToString()
        age = $info.Age.ToString()
        enabled = $voice.Enabled
    }
}

$voices | ConvertTo-Json -Depth 4
